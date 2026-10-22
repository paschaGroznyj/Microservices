"""MAX-специфичная интеграция на стороне агента.

Общие с Telegram шаблоны (проактивные промпты будильников/грусти/триггеров)
живут в agent.common.proactive — здесь реэкспорт, чтобы MAX-код дёргал
их из своего модуля. Всё что отлично от TG (шапка источника) — здесь.

В MAX нет HTML-разметки-suffix как было в TG (build_tg_system_suffix отключён),
агент генерит в md — MAX поддерживает markdown через NewMessageBody.format.
"""

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
    """\u0427\u0438\u0442\u0430\u0435\u0442 max_chat_settings \u043d\u0430\u043f\u0440\u044f\u043c\u0443\u044e (\u043f\u0443\u043b agent_service, \u0442\u0430 \u0436\u0435 \u0411\u0414).
    \u0412\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442 dict \u0441 \u043a\u043b\u044e\u0447\u0430\u043c\u0438 system_prompt/agent_name/agent_model/selected_tools.
    chat_id \u0437\u0434\u0435\u0441\u044c \u2014 \u0424\u0418\u0417\u0418\u0427\u0415\u0421\u041a\u0418\u0419 max_chat_id (\u043d\u0435 session_id), \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043f\u0440\u0438\u0432\u044f\u0437\u0430\u043d\u044b \u043a \u043d\u0435\u043c\u0443.
    Fail-open: \u043f\u0440\u0438 \u043b\u044e\u0431\u043e\u0439 \u043e\u0448\u0438\u0431\u043a\u0435 \u2014 \u0434\u0435\u0444\u043e\u043b\u0442\u044b.
    """
    defaults = {"system_prompt": None, "agent_name": None}
    try:
        pool = getattr(db, "pool", None)
        if pool is None or chat_id is None:
            return defaults
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT system_prompt, agent_name FROM max_chat_settings WHERE chat_id=$1",
                str(chat_id),
            )
        if not row:
            return defaults
        d = dict(row)
        return {"system_prompt": d.get("system_prompt"), "agent_name": d.get("agent_name")}
    except Exception:
        return defaults
