"""Request-local, bounded, credential-safe logging. No prompt/reasoning at INFO."""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
import logging
from .event_log import DailyEventHandler
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone

LOG_CONTEXT = contextvars.ContextVar('agent_log_context', default={})
_SECRET = re.compile(r'authorization|api.?key|access.?key|password|secret|signature|(^|_)token$', re.I)
_CREDENTIAL = re.compile(r'''(?ix)(\b(?:authorization|x-api-key|api[_-]?key|access[_-]?key|password|secret|signature|GITHUB_TOKEN|CLOUD_LLM_ACCESS_KEY)\b["']?\s*[:=]\s*["']?)(?:bearer\s+)?[^\s,"'<>}]+''')
_BEARER = re.compile(r'(?i)\bbearer\s+[\w./+~=:-]+')


def redact(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if _SECRET.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        # Known credentials are scrubbed even when embedded in free text/SSE.
        for name, secret in os.environ.items():
            if _SECRET.search(name) and len(secret) >= 8:
                value = value.replace(secret, '[REDACTED]')
        return _BEARER.sub('Bearer [REDACTED]', _CREDENTIAL.sub(r'\1[REDACTED]', value))
    return value


def fingerprint(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    return {'chars': len(raw), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}


def diagnostic_enabled():
    allowed = {s.strip() for s in os.getenv('AGENT_DIAGNOSTICS_CHATS', '').split(',') if s.strip()}
    return str(LOG_CONTEXT.get().get('chat_id', '')) in allowed


def log_event(event, **fields):
    try:
        logging.getLogger('agent.events').info(event, extra={'event': event, 'fields': redact(fields)})
    except Exception:
        pass  # observability must never break a request


class ContextFilter(logging.Filter):
    def filter(self, record):
        record.agent_context = {k: v for k, v in LOG_CONTEXT.get().items() if k != 'query_text'}
        record.agent_message = str(redact(record.getMessage()))[:1800].replace('\r', '\\r').replace('\n', '\\n')
        return True


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        ctx = getattr(record, 'agent_context', {})
        tags = ' '.join(f'{k}={ctx[k]}' for k in ('chat_id', 'request_id', 'iteration', 'attempt', 'tool_call_id') if ctx.get(k) is not None)
        fields = getattr(record, 'fields', None)
        if fields:
            fields = {k: v for k, v in fields.items() if k not in {'messages', 'system', 'url'}}
        detail = ' ' + json.dumps(fields, ensure_ascii=False, default=str) if fields else ''
        return f'{self.formatTime(record)} [{record.levelname}] {tags} {getattr(record, "agent_message", record.getMessage())}{detail}'


class JSONFormatter(logging.Formatter):
    def format(self, record):
        data = {'ts': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                'level': record.levelname, 'logger': record.name,
                **getattr(record, 'agent_context', {}),
                'event': getattr(record, 'event', 'log'),
                **getattr(record, 'fields', {})}
        if not hasattr(record, 'event'):
            data['message'] = getattr(record, 'agent_message', str(redact(record.getMessage()))[:1800])
        return json.dumps(redact(data), ensure_ascii=False, default=str)


def configure_logging():
    root = logging.getLogger()
    root.setLevel(os.getenv('AGENT_LOG_LEVEL', 'INFO').upper())
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        if not any(isinstance(f, ContextFilter) for f in handler.filters):
            handler.addFilter(ContextFilter())
        if handler.__class__ is logging.StreamHandler:
            handler.setFormatter(ConsoleFormatter())
    # Legacy AGENT_LOG_JSONL remains a directory locator (basename is ignored).
    legacy = os.getenv('AGENT_LOG_JSONL')
    path = os.getenv('AGENT_LOG_DIR', str(Path(legacy).parent) if legacy else '/tmp/agent-logs')
    if path and not any(getattr(h, '_agent_jsonl', False) for h in root.handlers):
        try:
            h = DailyEventHandler(path,
                days=int(os.getenv('AGENT_LOG_RETENTION_DAYS', '7')),
                max_bytes=int(os.getenv('AGENT_LOG_MAX_TOTAL_BYTES', str(500 * 1024 * 1024))))
            h._agent_jsonl = True
            h.addFilter(ContextFilter())
            h.setFormatter(JSONFormatter())
            root.addHandler(h)
        except (OSError, ValueError) as exc:
            log_event('logging.file_unavailable', error_type=type(exc).__name__)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)


def request_logged(fn):
    """Scopes also direct process_query calls; ContextVars isolate concurrent tasks."""
    signature = inspect.signature(fn)
    @functools.wraps(fn)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        values = bound.arguments
        payload = values.get('payload') or values
        current = dict(LOG_CONTEXT.get())
        # A fresh external request must not inherit a previous chat/query.
        if fn.__name__ == 'handle_message':
            current = {}
        for key in ('chat_id', 'request_id', 'user_id', 'agent_model'):
            if payload.get(key) is not None:
                current[key] = str(payload[key])
        # Clear step/attempt inherited from a completed nested operation.
        for key in ('iteration', 'attempt', 'tool_call_id'):
            current.pop(key, None)
        if phase_query := payload.get('message', payload.get('user_input', payload.get('query', payload.get('text')))):
            current['query_text'] = phase_query
        token = LOG_CONTEXT.set(current)
        started = time.monotonic()
        phase = 'request' if fn.__name__ == 'handle_message' else 'agent'
        log_event(phase + '.start', input=fingerprint(payload.get('message', payload.get('user_input', payload.get('text', '')))))
        outcome = 'ok'
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:
            outcome = type(exc).__name__
            log_event(phase + '.error', error_type=outcome)
            raise
        finally:
            log_event(phase + '.end', outcome=outcome, duration_ms=round((time.monotonic() - started) * 1000))
            LOG_CONTEXT.reset(token)
    return wrapped


def tool_logged(fn):
    @functools.wraps(fn)
    async def wrapped(self, tools_by_name, tool_call, request_id):
        token = LOG_CONTEXT.set({**LOG_CONTEXT.get(), 'request_id': request_id,
                                 'tool_call_id': tool_call.get('id'), 'tool': tool_call.get('name')})
        started = time.monotonic()
        log_event('tool.start', name=tool_call.get('name'), args=fingerprint(tool_call.get('args', {})))
        try:
            result = await fn(self, tools_by_name, tool_call, request_id)
            log_event('tool.end', duration_ms=round((time.monotonic()-started)*1000), result=fingerprint(result))
            return result
        except BaseException as exc:
            log_event('tool.error', error_type=type(exc).__name__)
            raise
        finally:
            LOG_CONTEXT.reset(token)
    return wrapped


def llm_logged(fn):
    @functools.wraps(fn)
    async def wrapped(llm, messages, *args, **kwargs):
        token = LOG_CONTEXT.set({**LOG_CONTEXT.get(), 'iteration': kwargs.get('iteration', 0), 'llm_role': 'main'})
        try:
            from .observability_http import instrument_llm
            instrument_llm(llm)
            log_event('llm.start', messages_count=len(messages))
            result = await fn(llm, messages, *args, **kwargs)
            content = getattr(result, 'content', '')
            text = content if isinstance(content, str) else ''.join(b.get('text', '') for b in content if isinstance(b, dict))
            calls = getattr(result, 'tool_calls', []) or []
            log_event('llm.result', text=fingerprint(text),
                      content_types=[b.get('type') for b in content if isinstance(b, dict)] if isinstance(content, list) else ['text'],
                      tools=[{'id': c.get('id'), 'name': c.get('name')} for c in calls],
                      invalid_tool_calls=len(getattr(result, 'invalid_tool_calls', []) or []),
                      stop_reason=(getattr(result, 'response_metadata', {}) or {}).get('stop_reason') or (getattr(result, 'response_metadata', {}) or {}).get('finish_reason'),
                      usage=getattr(result, 'usage_metadata', None),
                      suspicious_empty_claim=bool(text and not calls and re.search(r'пуст(?:ой|ым|ого) запрос|запрос (?:был )?пуст|empty (?:user )?(?:request|query)|нічого не вказано', text, re.I)))
            return result
        finally:
            LOG_CONTEXT.reset(token)
    return wrapped
