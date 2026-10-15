"""
agent_service/app/agent/common/proactive.py
Platform-agnostic proactive templates shared by Telegram and MAX integrations.

Only platform-independent proactive prompt builders live here. Platform-specific
bits (TG HTML format, chat-settings DB access, source prefixes) stay in their
own integration modules and re-export these for backward compatibility.
"""
from __future__ import annotations

JOURNAL_HINT = (
    "ВАЖНО: перед тем как писать — "
    "сверься со своим бортовым журналом "
    "этого чата (он в контексте выше). "
    "Если там есть предпочтения, источники, темы "
    "или договорённости — "
    "опирайся на них (тон, тематика, конкретные ресурсы)."
)

SLOT_TONE = {
    "morning":   "Утро (завтрак). Тон бодрый, задаёшь рабочий настрой на день.",
    "breakfast": "Утро (завтрак). Тон бодрый, задаёшь рабочий настрой на день.",
    "lunch":     "Обед. Тон лёгкий, можно подвести промежуточный итог дня.",
    "dinner":    "Вечер (ужин). Тон тёплый, подводишь итоги дня / планы на завтра.",
}

PRESET_DEFAULT_TASK = {
    "breakfast": (
        "Это утренний будильник (завтрак). Поздоровайся с чатом бодро и "
        "принеси короткий утренний вброс по теме чата: свежую новость/дайджест "
        "из источников, полезный типс или мем в тему. Задай рабочий настрой на день."
    ),
    "lunch": (
        "Это обеденный будильник. Загляни в чат с лёгким тоном: подведи "
        "промежуточный итог дня, принеси что-то интересное/полезное по теме чата "
        "или короткую свежую новость."
    ),
    "dinner": (
        "Это вечерний будильник (ужин). Тёплый тон: подведи итоги дня, "
        "накидай планы/мысли на завтра или принеси приятный вечерний контент "
        "по теме чата."
    ),
}


def build_initiation_prompt(slot: str, username: str | None, chat_type: str = "group",
                            addressed: bool = True) -> str:
    """Задача агенту при проактивной инициации (сам начал беседу)."""
    tone = SLOT_TONE.get(slot, "")
    who = username or "участник"
    if addressed:
        return (
            f"[ПРОАКТИВНАЯ ИНИЦИАЦИЯ] {tone}\n"
            f"Сейчас ты САМ начинаешь рабочую беседу с @{who}. "
            f"Обратись к нему лично и живо. Можешь: поднять рабочий вопрос по прошлым обсуждениям, "
            f"принести свежую инфу по теме чата, спросить как двигаются задачи. Коротко, без воды."
            f"\n{JOURNAL_HINT}"
        )
    return (
        f"[ПРОАКТИВНАЯ РАССЫЛКА] {tone}\n"
        f"Сейчас ты САМ пишешь в чат (без конкретного адресата). "
        f"Принеси в чат что-то полезное или весёлое по теме чата: свежую новость/дайджест из источников, "
        f"мем в тему, короткий типс. Ответа ни от кого не ждёшь — это вброс в общий чат."
        f"\n{JOURNAL_HINT}"
    )


def build_custom_trigger_prompt(custom_prompt: str, username: str | None = None,
                                chat_type: str = "group", addressed: bool = False,
                                preset_key: str | None = None) -> str:
    """Проактив по ИНДИВИДУАЛЬНОМУ триггеру. preset_key подмешивает дефолтную задачу приёма пищи."""
    who = username or "участник"
    head = "[ПРОАКТИВНАЯ ИНИЦИАЦИЯ ПО ТРИГГЕРУ]"
    if addressed:
        addr = f"Обратись лично к @{who}. "
    else:
        addr = "Пишешь в общий чат без конкретного адресата. "
    base_task = PRESET_DEFAULT_TASK.get(preset_key or "", "")
    custom = (custom_prompt or "").strip()
    if base_task and custom:
        task = base_task + "\n\nДополнительно от пользователя:\n" + custom
    elif base_task:
        task = base_task
    else:
        task = custom
    return (
        f"{head}\n"
        f"Сработал твой запланированный триггер. Задача:\n"
        f"{task}\n\n"
        f"{addr}Действуй по задаче выше: можешь использовать свои инструменты "
        f"(поиск/парсинг/генерация), чтобы собрать нужную инфу к этому моменту. "
        f"Пиши живо и по делу."
        f"\n{JOURNAL_HINT}"
    )


def build_sad_followup_prompt(username: str | None) -> str:
    """Агент «грустит»: человек не ответил на инициацию за окно. Один раз."""
    who = username or "друг"
    return (
        f"[ПРОАКТИВНАЯ ГРУСТЬ] Ты писал @{who}, но он так и не ответил. "
        f"Напиши ОДНО короткое добродушно-грустное сообщение (с лёгким юмором, без обид), "
        f"мол, жаль что проигнорили. Не навязывайся, одной репликой."
    )
