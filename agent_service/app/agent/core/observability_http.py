"""HTTP hooks below SDK parsing. Raw captures are opt-in, private and bounded.

Raw captures contain user data. Do not expose the directory via a web endpoint.
Authorization headers are never captured. Body captures are redacted (not byte-identical);
SHA-256 and byte counts refer to the original wire body.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import threading
import zlib

import httpx
from .observability import LOG_CONTEXT, diagnostic_enabled, fingerprint, log_event, redact


def _limit(name, default):
    try:
        return max(1024, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_CAPTURE_LOCK = threading.Lock()

def _capture(kind, body, meta, selected):
    with _CAPTURE_LOCK:
        _capture_locked(kind, body, meta, selected)


def _capture_locked(kind, body, meta, selected):
    if not selected:
        return
    try:
        directory = Path(os.getenv('AGENT_DIAGNOSTICS_DIR', '/tmp/agent-diagnostics'))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        ttl = _limit('AGENT_DIAGNOSTICS_TTL_SECONDS', 86400)
        budget = _limit('AGENT_DIAGNOSTICS_MAX_TOTAL_BYTES', 50 * 1024 * 1024)
        cap = _limit('AGENT_DIAGNOSTICS_MAX_BYTES', 2 * 1024 * 1024)
        files = sorted((p for p in directory.glob('*.json') if p.is_file() and not p.is_symlink()), key=lambda p: p.stat().st_mtime)
        for p in files[:]:
            if time.time() - p.stat().st_mtime > ttl:
                p.unlink(missing_ok=True)
                files.remove(p)
        # Capture redaction is explicit. Invalid UTF8 / capped tails are preserved as replacement chars.
        text = body[:cap].decode('utf-8', errors='replace')
        try:
            clean_body = redact(json.loads(text))
        except (ValueError, TypeError):
            clean_body = redact(text)
        data = {**meta, 'kind': kind, 'body_redacted': True, 'body': clean_body}
        raw = json.dumps(data, ensure_ascii=False, default=str).encode()
        # JSON escaping can expand the body; bound the entire file as well.
        if len(raw) > cap:
            data['body'] = '[omitted: serialized capture exceeds per-file limit]'
            data['capture_truncated'] = True
            raw = json.dumps(data, ensure_ascii=False, default=str).encode()
        if len(raw) > min(cap, budget):
            return
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total + len(raw) <= budget:
                break
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
        path = directory / (uuid.uuid4().hex + '.' + kind + '.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
    except Exception:
        log_event('diagnostics.capture_error')


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream, meta, selected, content_type, content_encoding=""):
        self.stream, self.meta, self.selected = stream, meta, selected
        self.is_sse = 'text/event-stream' in content_type
        self.buffer = bytearray()
        self.digest = hashlib.sha256()
        self.size = 0
        self.eof = False
        self.terminal = False
        self.tail = b''
        self.encoding = content_encoding.lower()
        self.decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if self.encoding == 'gzip' else (zlib.decompressobj() if self.encoding == 'deflate' else None)
        self.decode_error = self.encoding not in {'', 'identity', 'gzip', 'deflate'}
        self.decoded_size = 0
        self.finished = False
        self.cap = _limit('AGENT_DIAGNOSTICS_MAX_BYTES', 2 * 1024 * 1024)
        self.started = time.monotonic()

    def finish(self, error=None):
        if self.finished:
            return
        self.finished = True
        meta = {**self.meta, 'response_bytes': self.size, 'sha256': self.digest.hexdigest(),
                'eof': self.eof, 'sse_terminal': self.terminal if self.is_sse else None,
                'decoded_bytes': self.decoded_size, 'content_encoding': self.encoding,
                'decode_error': self.decode_error,
                'incomplete_sse': (self.is_sse and not self.terminal) if not self.decode_error else None,
                'duration_ms': round((time.monotonic()-self.started)*1000),
                'capture_enabled': self.selected, 'capture_truncated': self.selected and self.decoded_size > len(self.buffer), 'error_type': error}
        log_event('llm.http.end', **meta)
        try:
            _capture('response', bytes(self.buffer), meta, self.selected)
        except Exception:
            log_event('diagnostics.capture_error')

    async def __aiter__(self):
        error = None
        try:
            async for chunk in self.stream:
                self.digest.update(chunk)
                self.size += len(chunk)
                # Inspect a separate decoder; never touch httpx's decoder or emitted bytes.
                # Bound each decompressed inspection fragment to 64KB.
                try:
                    def decoded_fragments():
                        if self.decoder is None:
                            yield chunk
                        else:
                            pending = chunk
                            while pending:
                                yield self.decoder.decompress(pending, 65536)
                                pending = self.decoder.unconsumed_tail
                    for fragment in decoded_fragments():
                        self.decoded_size += len(fragment)
                        marker = self.tail + fragment
                        if b'data: [DONE]' in marker or b'event: message_stop' in marker or b'"type":"message_stop"' in marker or b'"type": "message_stop"' in marker:
                            self.terminal = True
                        self.tail = marker[-128:]
                        if self.selected and len(self.buffer) < self.cap:
                            self.buffer.extend(fragment[:self.cap-len(self.buffer)])
                except Exception:
                    self.decode_error = True
                yield chunk  # never modify, buffer or eagerly consume the SDK stream
            self.eof = True
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            self.finish(error)

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            self.finish(None if self.eof else 'StreamClosedEarly')


async def _request(request):
    if request.url.path.rstrip('/').split('/')[-1] not in {'messages', 'completions', 'responses'}:
        return
    try:
        body = request.content  # SDK JSON bodies are already materialized. Never consume unknown streams.
        payload = json.loads(body)
        messages = payload.get('messages', [])
        roles = {}
        summaries = []
        for message in messages:
            role = message.get('role', 'unknown')
            roles[role] = roles.get(role, 0) + 1
            summaries.append({'role': role, **fingerprint(message.get('content', '')),
                              'tool_call_id': message.get('tool_call_id'),
                              'tool_calls': [c.get('id') for c in message.get('tool_calls', [])]})
        users = [m for m in messages if m.get('role') == 'user']
        tools = [t.get('name') or t.get('function', {}).get('name') for t in payload.get('tools', [])]
        current = LOG_CONTEXT.get().get('query_text')
        def contains_query(value):
            if isinstance(value, str):
                return bool(current) and current in value
            if isinstance(value, (dict, list)):
                return any(contains_query(v) for v in (value.values() if isinstance(value, dict) else value))
            return False
        # Only serializable safe identifiers, never the query_text itself.
        meta = {k: v for k, v in LOG_CONTEXT.get().items() if k != 'query_text'}
        meta.update({'http_id': uuid.uuid4().hex, 'url': str(request.url.copy_with(query=None, username=None, password=None)),
                     'model': payload.get('model'), 'messages_count': len(messages), 'roles': roles,
                     'messages': summaries, 'last_user': fingerprint(users[-1].get('content')) if users else None,
                     'current_query_present': contains_query(messages) if current else None,
                     'tools': tools, 'payload_bytes': len(body), 'payload_sha256': hashlib.sha256(body).hexdigest(),
                     'system': fingerprint(payload.get('system', '')), 'stream': payload.get('stream', False)})
        selected = diagnostic_enabled()
        request.extensions['agent_observed'] = (meta, selected)
        log_event('llm.http.request', **meta)
        _capture('request', body, meta, selected)
    except Exception:
        log_event('llm.http.inspect_error')


async def _response(response):
    try:
        state = response.request.extensions.get('agent_observed')
        if not state:
            return
        meta, selected = state
        meta = {**meta, 'status': response.status_code,
                'provider_request_id': response.headers.get('x-request-id') or response.headers.get('request-id'),
                'content_type': response.headers.get('content-type', '')}
        log_event('llm.http.response', **{k: meta[k] for k in ('http_id', 'status', 'provider_request_id', 'content_type')})
        response.stream = ObservedStream(response.stream, meta, selected, meta['content_type'], response.headers.get('content-encoding', ''))
    except Exception:
        log_event('llm.http.hook_error')


def instrument_async_client(client):
    if isinstance(client, httpx.AsyncClient) and _request not in client.event_hooks['request']:
        client.event_hooks['request'].append(_request)
        client.event_hooks['response'].append(_response)
    return client


def instrument_llm(llm):
    """Use existing SDK-owned clients: retain SDK timeout, proxy and connection lifecycle."""
    try:
        base = getattr(llm, 'bound', llm)
        for attr in ('_async_client', 'root_async_client'):
            sdk = getattr(base, attr, None)
            client = getattr(sdk, '_client', None)
            instrument_async_client(client)
    except Exception:
        log_event('llm.http.instrument_error')
    return llm
