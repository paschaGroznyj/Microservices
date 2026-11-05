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


def _mark_message_cc(msg: BaseMessage) -> BaseMessage:
    if isinstance(msg, ToolMessage):
        blocks = [{
            "type": "tool_result",
            "content": msg.content,
            "tool_use_id": msg.tool_call_id,
            "is_error": getattr(msg, "status", None) == "error",
            "cache_control": EPHEMERAL,
        }]
        return msg.model_copy(update={"content": blocks})
    blocks = _with_cc(msg.content)
    if blocks:
        return msg.model_copy(update={"content": blocks})
    return msg


def apply_anthropic_cache_control(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Ставит до 4 точек cache_control (лимит Anthropic = 4):
    1) out[0] — статический SystemMessage (якорь между запросами);
    2) последний HumanMessage — якорь входного контекста текущего хода
       (не снимается на шагах iter>=1, чтобы весь входной контекст с историей
       читался из кеша на каждом шаге цикла);
    3) конец предыдущего тул-шага (чтобы на шаге k попадать в кеш, записанный
       на шаге k-1);
    4) конец текущего тул-шага out[-1] (запись в кеш для следующего шага k+1).
    """
    out = list(messages)
    if not out:
        return out

    targets: list[int] = []

    # 1) Статический системный блок (первое сообщение)
    if isinstance(out[0], SystemMessage):
        targets.append(0)

    # 2) Последний HumanMessage (граница входного контекста перед циклом тулов)
    last_human_idx: int | None = None
    for i in range(len(out) - 1, -1, -1):
        if isinstance(out[i], HumanMessage):
            last_human_idx = i
            break
    if last_human_idx is not None and last_human_idx not in targets:
        targets.append(last_human_idx)

    # 3-4) Рубежи тул-шагов после последнего HumanMessage (последний ToolMessage каждого шага)
    search_start = (last_human_idx + 1) if last_human_idx is not None else 1
    step_end_tools: list[int] = []
    for i in range(search_start, len(out)):
        if isinstance(out[i], ToolMessage):
            is_end_of_step = (i == len(out) - 1) or not isinstance(out[i + 1], ToolMessage)
            if is_end_of_step:
                step_end_tools.append(i)

    remaining_slots = max(0, 4 - len(targets))
    for idx in step_end_tools[-remaining_slots:]:
        if idx not in targets:
            targets.append(idx)

    # Если после HumanMessage ещё не было ToolMessage, но последнее сообщение — не в targets
    if not step_end_tools and len(out) > 1 and (len(out) - 1) not in targets and len(targets) < 4:
        if isinstance(out[-1], (HumanMessage, ToolMessage)):
            targets.append(len(out) - 1)

    for idx in targets[:4]:
        out[idx] = _mark_message_cc(out[idx])
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
