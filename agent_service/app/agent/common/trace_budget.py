"""Бюджет tool_trace в токенах активной модели.

Запись (выход из запроса): трейс до TRACE_MSG_TOKENS хранится сырым. Длиннее —
последние вызовы целиком (до TRACE_RAW_TAIL_TOKENS) остаются сырыми, всё, что
раньше, сжимается LLM в md-таблицу (до TRACE_HEAD_TOKENS). Режем только по
границам вызовов (элементы списка tool_trace).

Чтение (сборка истории): трейс кладётся в content ответа бота, чтобы его видела
модель и считал trim_messages. Полный трейс — только у TRACE_FULL_LAST последних
ответов бота, у более старых — только сжатая голова до TRACE_OLD_TOKENS.

Токены = символы / chars_per_token модели (с поправкой калибровки), та же база,
что у trim_messages.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from typing import Awaitable, Callable, Iterable, List, Optional

from langchain_core.messages import AIMessage, BaseMessage

from .tool_output import clip_text

TRACE_MSG_TOKENS = int(os.getenv("TRACE_MSG_TOKENS", "3000"))
TRACE_RAW_TAIL_TOKENS = int(os.getenv("TRACE_RAW_TAIL_TOKENS", "1500"))
TRACE_HEAD_TOKENS = int(os.getenv("TRACE_HEAD_TOKENS", "1500"))
TRACE_OLD_TOKENS = int(os.getenv("TRACE_OLD_TOKENS", "500"))
TRACE_FULL_LAST = int(os.getenv("TRACE_FULL_LAST", "2"))

SEP = "; "
TAIL_MARK = "\n\n[последние вызовы, сырые]\n"
CONTENT_MARK = "\n\n[служебно: след инструментов этого ответа, пользователю не показывался]\n"

logger = logging.getLogger(__name__)


def tokens(text: str, cpt: float) -> int:
    return math.ceil(len(text or "") / cpt) if text else 0


def clip_tokens(text: str, max_tokens: int, cpt: float) -> str:
    return clip_text(text, int(max_tokens * cpt))


def split_tail(items: List[str], tail_tokens: int, cpt: float) -> tuple[List[str], List[str]]:
    """Отбирает с конца целые вызовы, пока влезают в tail_tokens.

    Если не влезает даже последний вызов, он один уходит в хвост с обрезкой —
    последний шаг важнее всего, без него хвост теряет смысл.
    """
    tail: List[str] = []
    used = 0
    for item in reversed(items):
        cost = tokens(item, cpt) + (tokens(SEP, cpt) if tail else 0)
        if used + cost > tail_tokens:
            break
        tail.insert(0, item)
        used += cost
    if not tail and items:
        tail = [clip_tokens(items[-1], tail_tokens, cpt)]
        return items[:-1], tail
    return items[: len(items) - len(tail)], tail


def head_of(stored: str) -> str:
    """Сжатая голова сохранённого трейса (для старых сообщений)."""
    return stored.split(TAIL_MARK, 1)[0] if TAIL_MARK in stored else stored


def compress_trace_fast(items: List[str], cpt: float) -> tuple[Optional[str], bool]:
    """Та же запись, что compress_trace, но без LLM: голова обрезается по токенам.

    Возвращает (строка для БД, нужно ли фоновое LLM-сжатие головы). Ответ уходит
    пользователю с этой строкой сразу; сводку головы agent досчитывает фоном и
    обновляет chats.tool_trace (см. main.py, _refine_tool_trace).
    """
    if not items:
        return None, False
    raw = SEP.join(items)
    if tokens(raw, cpt) <= TRACE_MSG_TOKENS:
        return raw, False
    head_items, tail_items = split_tail(items, TRACE_RAW_TAIL_TOKENS, cpt)
    tail = SEP.join(tail_items)
    head_raw = SEP.join(head_items)
    if not head_raw:
        return tail, False
    return f"{clip_tokens(head_raw, TRACE_HEAD_TOKENS, cpt)}{TAIL_MARK}{tail}", True


async def compress_trace(
    items: List[str],
    cpt: float,
    summarize: Callable[[str], Awaitable[Optional[str]]],
) -> Optional[str]:
    """Трейс текущего хода → строка для БД (см. докстринг модуля)."""
    if not items:
        return None
    raw = SEP.join(items)
    if tokens(raw, cpt) <= TRACE_MSG_TOKENS:
        return raw
    head_items, tail_items = split_tail(items, TRACE_RAW_TAIL_TOKENS, cpt)
    tail = SEP.join(tail_items)
    head_raw = SEP.join(head_items)
    summary = None
    if head_raw:
        try:
            summary = await summarize(head_raw)
        except Exception as e:  # fail-open: сохранение сообщения не роняем
            logger.error(f"[trace-budget] ошибка сжатия головы: {e}")
        summary = (summary or "").strip() or clip_tokens(head_raw, TRACE_HEAD_TOKENS, cpt)
        summary = clip_tokens(summary, TRACE_HEAD_TOKENS, cpt)
    out = f"{summary}{TAIL_MARK}{tail}" if summary else tail
    logger.info(
        f"[trace-budget] {tokens(raw, cpt)} ток → {tokens(out, cpt)} ток "
        f"(сырых вызовов в хвосте {len(tail_items)}/{len(items)}, cpt={cpt:.2f})"
    )
    return out


def attach_traces(messages: Iterable[BaseMessage], cpt: float) -> List[BaseMessage]:
    """Вклеивает tool_trace в content ответов бота под бюджет читающей модели."""
    msgs = list(messages)
    seen = 0
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        kw = getattr(m, "additional_kwargs", None) or {}
        trace = kw.get("tool_trace")
        if not isinstance(m, AIMessage) or not trace or kw.get("trace_in_content"):
            continue
        if not isinstance(m.content, str):
            continue
        seen += 1
        trace = str(trace)
        if seen <= TRACE_FULL_LAST:
            part = clip_tokens(trace, TRACE_MSG_TOKENS, cpt)
        else:
            part = clip_tokens(head_of(trace), TRACE_OLD_TOKENS, cpt)
        msgs[i] = AIMessage(
            content=f"{m.content}{CONTENT_MARK}{part}",
            additional_kwargs={**kw, "trace_in_content": True},
        )
    return msgs


# Запись chats.tool_trace делает chats_service из Kafka, поэтому строка может
# появиться позже, чем закончится LLM-сводка: несколько попыток с паузой.
TRACE_REFINE_RETRIES = 10
TRACE_REFINE_DELAY_SEC = 1.0


async def refine_tool_trace(
    task: "asyncio.Future[Optional[str]]",
    message_id: str,
    save: Callable[[str, str], Awaitable[bool]],
) -> None:
    """Фон: дождаться LLM-сводки головы трейса и записать её вместо обрезанной головы.

    save(message_id, trace) -> False, пока строки ответа в chats ещё нет.
    """
    try:
        trace = await task
        if not trace:
            return
        for _ in range(TRACE_REFINE_RETRIES):
            if await save(message_id, trace):
                logger.info(f"[trace-budget] {message_id}: tool_trace обновлён сводкой ({len(trace)} симв)")
                return
            await asyncio.sleep(TRACE_REFINE_DELAY_SEC)
        logger.warning(f"[trace-budget] {message_id}: строка ответа не появилась, сводка не записана")
    except Exception as e:
        logger.warning(f"[trace-budget] {message_id}: фоновая сводка трейса: {e}")
