# -*- coding: utf-8 -*-
"""
agent_pm/supervisor.py

ФОНОВЫЙ reasoning-супервизор цели.

Живёт рядом с judge.py, но принципиально другой:

- judge   — судит ФИНАЛЬНЫЙ ответ post-factum, блокирующе, один раз;
- supervisor — смотрит на ТРАЕКТОРИЮ ВНУТРИ цикла раз в N шагов,
  в фоне (asyncio.create_task), НЕблокирующе. Основной цикл
  никогда его не ждёт: если супервизор нашёл проблему — на следующем
  шаге его директива мягко подмешивается SystemMessage-ом.

Свойства:
- читает СНАПШОТ messages (копия), не мутирует его;
- гоняется на ТОЙ ЖЕ основной LLM, что и агент (agent._main_ainvoke) —
  чтобы контролёр рассуждал на том же уровне, что и сам агент;
- fail-open: любая ошибка / невалидный JSON / status=ok → возврат None
  (ничего не подмешивается, цикл не ломается).

Контракт:
    run_supervisor(agent, user_input, messages_snapshot, loop_state) -> str | None
    None  — всё ок, подмешивать нечего;
    str   — корректирующая директива для SystemMessage-инъекции.
"""

import json
import logging
import re

from langchain.schema import HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage

from .prompts.supervisor_prompt import (
    SUPERVISOR_SYSTEM_PROMPT,
    SUPERVISOR_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

# Сколько последних сообщений берём в дайджест траектории
_DIGEST_LAST_MSGS = 14
# Жёсткие лимиты, чтобы не раздуть дешёвый вызов
_MAX_MSG_CHARS = 700
_MAX_DIGEST_CHARS = 6000
_MAX_DIRECTIVE_CHARS = 600


def _extract_json(text: str) -> dict | None:
    """Вытаскивает первый JSON-объект из ответа модели (терпим к markdown)."""
    if not text:
        return None
    # снимаем ```json ... ``` обёртку, если есть
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _build_digest(messages_snapshot: list, loop_state: dict) -> str:
    """Строит компактный дайджест деятельности агента из снапшота.

    Берём последние N сообщений: текст агента, вызванные инструменты
    и их результаты. Каждое сообщение обрезаем до _MAX_MSG_CHARS.
    """
    lines: list[str] = []
    tail = messages_snapshot[-_DIGEST_LAST_MSGS:] if messages_snapshot else []
    for m in tail:
        mtype = getattr(m, "type", m.__class__.__name__)
        content = getattr(m, "content", "")
        if isinstance(content, list):
            # мультимодальный контент — склеиваем текстовые части
            parts = []
            for it in content:
                if isinstance(it, dict) and it.get("type") == "text":
                    parts.append(it.get("text", ""))
            content = " ".join(parts)
        content = (str(content) or "").strip()

        # вызванные тулы (у AIMessage)
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            names = ", ".join(tc.get("name", "?") for tc in tool_calls)
            lines.append(f"[АГЕНТ вызвал инструменты]: {names}")
        if isinstance(m, ToolMessage) or mtype == "tool":
            tool_name = getattr(m, "name", "?")
            lines.append(f"[РЕЗУЛЬТАТ {tool_name}]: {content[:_MAX_MSG_CHARS]}")
        elif content:
            tag = "АГЕНТ" if mtype in ("ai", "AIMessage") else mtype.upper()
            lines.append(f"[{tag}]: {content[:_MAX_MSG_CHARS]}")

    # добавляем reasoning_trace (мысли агента перед тулами), если есть
    rtrace = loop_state.get("reasoning_trace") if loop_state else None
    if rtrace:
        last_thought = str(rtrace[-1])[:_MAX_MSG_CHARS]
        if last_thought.strip():
            lines.append(f"[ПОСЛЕДНЯЯ МЫСЛЬ АГЕНТА]: {last_thought}")

    digest = "\n".join(lines)
    return digest[-_MAX_DIGEST_CHARS:] if digest else "(пока пусто)"


def _extract_user_goal(user_input) -> str:
    """Нормализует цель пользователя в строку (user_input может быть мультимодальным)."""
    if isinstance(user_input, str):
        return user_input
    if isinstance(user_input, list):
        parts = []
        for it in user_input:
            if isinstance(it, dict) and it.get("type") == "text":
                parts.append(it.get("text", ""))
            elif isinstance(it, str):
                parts.append(it)
        return " ".join(parts)
    return str(user_input)


async def run_supervisor(agent, user_input, messages_snapshot: list, loop_state: dict) -> str | None:
    """Фоновый надзор за траекторией агента. Возвращает директиву или None.

    Вызывается через asyncio.create_task из agent_event_loop. НИКОГДА
    не должен пробрасывать исключение наружу (fail-open).
    """
    try:
        goal = _extract_user_goal(user_input).strip()
        if not goal:
            return None

        digest = _build_digest(messages_snapshot, loop_state)
        iteration = loop_state.get("iteration", 0) if loop_state else 0
        hard_limit = loop_state.get("hard_limit", 100) if loop_state else 100

        user_msg = SUPERVISOR_USER_TEMPLATE.format(
            user_input=goal[:_MAX_DIGEST_CHARS],
            iteration=iteration,
            hard_limit=hard_limit,
            digest=digest,
        )
        sup_messages = [
            SystemMessage(content=SUPERVISOR_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ]

        resp = await agent._main_ainvoke(sup_messages)
        raw = getattr(resp, "content", "") or ""
        if isinstance(raw, list):
            raw = " ".join(
                it.get("text", "") for it in raw if isinstance(it, dict) and it.get("type") == "text"
            )

        verdict = _extract_json(raw)
        if not verdict:
            logger.info("[supervisor] невалидный JSON — fail-open (None)")
            return None

        status = str(verdict.get("status", "ok")).lower().strip()
        if status != "correct":
            return None

        directive = str(verdict.get("directive", "")).strip()
        reason = str(verdict.get("reason", "")).strip()
        if not directive:
            # статус correct, но директива пустая — нечего подмешивать
            return None

        logger.info(f"[supervisor] status=correct reason={reason!r} → подмешиваем директиву")
        out = directive[:_MAX_DIRECTIVE_CHARS]
        if reason:
            out = f"({reason})\n{out}"
        return out

    except Exception as e:
        logger.error(f"[supervisor] fail-open по исключению: {type(e).__name__}: {e}")
        return None
