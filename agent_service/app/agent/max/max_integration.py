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
