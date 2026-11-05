"""Учёт токенов и разметка кеша промпта.

normalize_usage() приводит usage из любого источника к одному виду:
  - AIMessage / AIMessageChunk (langchain usage_metadata),
  - сырой OpenAI-совместимый usage (prompt_tokens / completion_tokens /
    prompt_tokens_details.cached_tokens),
  - сырой Anthropic usage (input_tokens + cache_read_input_tokens +
    cache_creation_input_tokens).
input_tokens всегда включает закешированные токены (как в langchain),
cache_read_tokens и cache_creation_tokens — их подмножество.

apply_anthropic_cache_control() ставит cache_control на статический системный
блок (якорь, переживает запросы) и на последнее сообщение (кеш истории внутри
цикла агента). Сообщения не мутирует, возвращает новый список.
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage

EPHEMERAL = {"type": "ephemeral"}

EMPTY_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
}


def _i(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _from_metadata(um: dict) -> dict[str, int]:
    det = um.get("input_token_details") or {}
    return {
        "input_tokens": _i(um.get("input_tokens")),
        "output_tokens": _i(um.get("output_tokens")),
        "cache_read_tokens": _i(det.get("cache_read")),
        "cache_creation_tokens": _i(det.get("cache_creation")),
    }


def _from_raw(u: dict) -> dict[str, int]:
    if "prompt_tokens" in u or "completion_tokens" in u:
        det = u.get("prompt_tokens_details") or {}
        return {
            "input_tokens": _i(u.get("prompt_tokens")),
            "output_tokens": _i(u.get("completion_tokens")),
            "cache_read_tokens": _i(det.get("cached_tokens") or u.get("cache_read_input_tokens")),
            "cache_creation_tokens": _i(det.get("cache_creation_tokens") or u.get("cache_creation_input_tokens")),
        }
    cr = _i(u.get("cache_read_input_tokens"))
    cc = _i(u.get("cache_creation_input_tokens"))
    if "input_token_details" in u:
        return _from_metadata(u)
    return {
        # Anthropic отдаёт input_tokens БЕЗ кешированной части
        "input_tokens": _i(u.get("input_tokens")) + cr + cc,
        "output_tokens": _i(u.get("output_tokens")),
        "cache_read_tokens": cr,
        "cache_creation_tokens": cc,
    }


def normalize_usage(source: Any) -> dict[str, int]:
    """Usage из AIMessage, ответа chat/completions (dict) или самого usage-словаря."""
    if source is None:
        return dict(EMPTY_USAGE)
    um = getattr(source, "usage_metadata", None)
    if isinstance(um, dict) and um:
        return _from_metadata(um)
    if isinstance(source, BaseMessage):
        rm = getattr(source, "response_metadata", None) or {}
        raw = rm.get("usage") or (rm.get("token_usage") if isinstance(rm.get("token_usage"), dict) else None)
        return _from_raw(raw) if isinstance(raw, dict) else dict(EMPTY_USAGE)
    if isinstance(source, dict):
        if isinstance(source.get("usage"), dict):
            return _from_raw(source["usage"])
        return _from_raw(source)
    return dict(EMPTY_USAGE)


def add_usage(acc: dict, usage: dict[str, int]) -> dict:
    for k in EMPTY_USAGE:
        acc[k] = acc.get(k, 0) + usage.get(k, 0)
    return acc


# ───────────────────────── cache_control (Anthropic) ─────────────────────────

def _with_cc(content: Any) -> list | None:
    """Копия content-списка с cache_control на последнем блоке."""
    if isinstance(content, str):
        return [{"type": "text", "text": content, "cache_control": EPHEMERAL}] if content else None
    if isinstance(content, list) and content:
        blocks = [dict(b) if isinstance(b, dict) else {"type": "text", "text": str(b)} for b in content]
        for b in reversed(blocks):
            if b.get("type") in ("text", "image", "image_url", "tool_result", "document"):
                b["cache_control"] = EPHEMERAL
                return blocks
    return None


def apply_anthropic_cache_control(messages: list[BaseMessage]) -> list[BaseMessage]:
    out = list(messages)
    if not out:
        return out
    # 1) статический системный блок (первое сообщение)
    first = out[0]
    if isinstance(first, SystemMessage):
        blocks = _with_cc(first.content)
        if blocks:
            out[0] = first.model_copy(update={"content": blocks})
    # 2) последнее сообщение: кеш истории между шагами цикла
    if len(out) > 1:
        last = out[-1]
        if isinstance(last, ToolMessage):
            blocks = [{
                "type": "tool_result",
                "content": last.content,
                "tool_use_id": last.tool_call_id,
                "is_error": getattr(last, "status", None) == "error",
                "cache_control": EPHEMERAL,
            }]
            out[-1] = last.model_copy(update={"content": blocks})
        elif isinstance(last, HumanMessage):
            blocks = _with_cc(last.content)
            if blocks:
                out[-1] = last.model_copy(update={"content": blocks})
    return out


def append_to_system_tail(messages: list[BaseMessage], text: str) -> list[BaseMessage]:
    """Дописывает text в ПОСЛЕДНЕЕ из ведущих SystemMessage (динамический блок).

    Первый SystemMessage — статический промпт под кешем, его не трогаем:
    любая правка в нём сбрасывает кеш всего префикса. Если системное сообщение
    одно (только статика), вставляем новое сразу после него. Список мутируется.
    """
    n = 0
    while n < len(messages) and isinstance(messages[n], SystemMessage):
        n += 1
    if n <= 1:
        messages.insert(n, SystemMessage(content=text))
        return messages
    tail = messages[n - 1]
    if isinstance(tail.content, list):
        content = list(tail.content) + [{"type": "text", "text": text}]
    else:
        base = str(tail.content or "").rstrip()
        content = f"{base}\n\n{text}" if base else text
    messages[n - 1] = SystemMessage(content=content)
    return messages
