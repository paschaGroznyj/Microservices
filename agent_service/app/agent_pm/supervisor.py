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
from .core.settings import TOOL_DESCRIPTIONS

logger = logging.getLogger(__name__)

# Сколько последних сообщений берём в дайджест траектории
_DIGEST_LAST_MSGS = 14
# Жёсткие лимиты, чтобы не раздуть дешёвый вызов
_MAX_MSG_CHARS = 700
_MAX_DIGEST_CHARS = 6000
_MAX_DIRECTIVE_CHARS = 600
_MAX_TOOLS_CATALOG_CHARS = 2500
# Сколько последних ПАР (human/ai) подмешивать как предыдущий контекст диалога
_RECENT_CONTEXT_PAIRS = 3
_MAX_CONTEXT_MSG_CHARS = 500
_MAX_RECENT_CONTEXT_CHARS = 3000


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


def _build_tools_catalog(loop_state: dict) -> str:
    """Собирает каталог ТОЛЬКО доступных агенту тулов с описаниями.

    Имена берём из loop_state['tool_names'] (реальный отфильтрованный
    набор агента), описания — из TOOL_DESCRIPTIONS. Выключенные
    пользователем тулы в каталог НЕ попадают — контекст не засоряется.
    """
    names = (loop_state or {}).get("tool_names") or []
    lines: list[str] = []
    for name in names:
        desc = TOOL_DESCRIPTIONS.get(name)
        if desc:
            lines.append(f"- {name}: {desc}")
        else:
            # тул без описания (напр. pm_research — намеренно не описан) — только имя
            lines.append(f"- {name}")
    catalog = "\n".join(lines)
    if not catalog:
        return "(нет данных о доступных инструментах)"
    return catalog[:_MAX_TOOLS_CATALOG_CHARS]


def _msg_text(m) -> str:
    """Извлекает текстовый контент из сообщения (терпим к мультимодальности)."""
    content = getattr(m, "content", "")
    if isinstance(content, list):
        parts = []
        for it in content:
            if isinstance(it, dict) and it.get("type") == "text":
                parts.append(it.get("text", ""))
        content = " ".join(parts)
    return (str(content) or "").strip()


def _build_recent_context(messages_snapshot: list) -> str:
    """Собирает последние _RECENT_CONTEXT_PAIRS пар human/ai из истории.

    Нужно, чтобы супервизор понимал контекст при коротких уточняющих
    ответах юзера (напр. «да»): берём ПРЕДЫДУЩИЕ человек/агент
    реплики, а не только текущий ввод. Служебные tool-сообщения
    игнорируем — нужен именно диалог, а не траектория (она в дайджесте).
    """
    if not messages_snapshot:
        return "(нет предыдущего контекста)"
    # берём только human/ai-реплики с непустым текстом
    dialog = []
    for m in messages_snapshot:
        mtype = getattr(m, "type", m.__class__.__name__)
        if isinstance(m, ToolMessage) or mtype == "tool":
            continue
        if mtype in ("human", "HumanMessage"):
            role = "human"
        elif mtype in ("ai", "AIMessage"):
            role = "ai"
        else:
            continue
        # у AIMessage с tool_calls текст часто пустой — пропускаем
        text = _msg_text(m)
        if not text:
            continue
        dialog.append((role, text))
    if not dialog:
        return "(нет предыдущего контекста)"
    # ПОСЛЕДНИЕ N пар = последние 2*N реплик диалога
    tail = dialog[-(_RECENT_CONTEXT_PAIRS * 2):]
    lines = []
    for role, text in tail:
        tag = "ПОЛЬЗОВАТЕЛЬ" if role == "human" else "АГЕНТ"
        lines.append(f"[{tag}]: {text[:_MAX_CONTEXT_MSG_CHARS]}")
    ctx = "\n".join(lines)
    return ctx[-_MAX_RECENT_CONTEXT_CHARS:] if ctx else "(нет предыдущего контекста)"


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
        tools_catalog = _build_tools_catalog(loop_state)
        recent_context = _build_recent_context(messages_snapshot)
        iteration = loop_state.get("iteration", 0) if loop_state else 0
        hard_limit = loop_state.get("hard_limit", 100) if loop_state else 100

        user_msg = SUPERVISOR_USER_TEMPLATE.format(
            user_input=goal[:_MAX_DIGEST_CHARS],
            iteration=iteration,
            hard_limit=hard_limit,
            recent_context=recent_context,
            tools_catalog=tools_catalog,
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
        # Явное логирование СЫРОГО ответа супервизора
        logger.info(f"[supervisor] iter={iteration} raw_response={str(raw)[:1000]!r}")

        verdict = _extract_json(raw)
        if not verdict:
            logger.info("[supervisor] невалидный JSON — fail-open (None)")
            return None

        status = str(verdict.get("status", "ok")).lower().strip()
        logger.info(
            f"[supervisor] iter={iteration} verdict="
            f"{{status={status!r}, reason={str(verdict.get('reason',''))[:200]!r}, "
            f"directive={str(verdict.get('directive',''))[:300]!r}}}"
        )
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
