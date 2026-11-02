"""MAX-специфичная интеграция на стороне агента.

Общие с Telegram шаблоны (проактивные промпты будильников/грусти/триггеров)
живут в agent.common.proactive — здесь реэкспорт, чтобы MAX-код дёргал
их из своего модуля. Всё что отлично от TG (шапка источника) — здесь.

В MAX нет HTML-разметки-suffix как было в TG (build_tg_system_suffix отключён),
агент генерит в md — MAX поддерживает markdown через NewMessageBody.format.
"""

import json

# Реэкспорт общих проактивных шаблонов (источник истины — common.proactive).
from agent.common.proactive import (  # noqa: F401
    JOURNAL_HINT,
    SLOT_TONE,
    PRESET_DEFAULT_TASK,
    build_initiation_prompt,
    build_custom_trigger_prompt,
    build_sad_followup_prompt,
)

def build_max_prefix(username: str | None, chat_id: str, chat_type: str = "group") -> str:
    """Шапка источника для MAX-сообщения (аналог CatchPM/TG-шапки, но для MAX)."""
    who = username or "Аноним"
    kind = "группа" if chat_type == "group" else "личка"
    return f"[MAX, {kind}, Пользователь: {who}, chat_id: {chat_id}]\n"

async def get_max_chat_settings(db, chat_id: str) -> dict:
    """Читает max_chat_settings напрямую (пул agent_service, та же БД).
    Возвращает dict с настройками: system_prompt, agent_name, agent_model, selected_tools,
    trigger_mode, orchestrator_context_n, proactive_enabled, timezone_name.
    chat_id здесь — ФИЗИЧЕСКИЙ max_chat_id (не session_id), настройки привязаны к нему.
    Fail-open: при любой ошибке — дефолты.
    """
    defaults = {
        "system_prompt": None,
        "agent_name": None,
        "agent_model": "coder",
        "selected_tools": [],
        "trigger_mode": "mention",
        "orchestrator_context_n": 8,
        "proactive_enabled": False,
        "timezone_name": "Europe/Moscow",
    }
    try:
        pool = getattr(db, "pool", None)
        if pool is None or chat_id is None:
            return defaults
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT system_prompt, agent_name, agent_model, selected_tools, "
                "trigger_mode, orchestrator_context_n, proactive_enabled, timezone_name "
                "FROM max_chat_settings WHERE chat_id=$1",
                str(chat_id),
            )
        if not row:
            return defaults
        d = dict(row)
        st = d.get("selected_tools")
        if isinstance(st, str):
            try:
                d["selected_tools"] = json.loads(st)
            except Exception:
                d["selected_tools"] = defaults["selected_tools"]
        return {**defaults, **{k: v for k, v in d.items() if v is not None}}
    except Exception:
        return defaults
