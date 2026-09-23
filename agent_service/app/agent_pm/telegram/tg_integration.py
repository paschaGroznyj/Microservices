"""
agent_service/app/agent_pm/telegram/tg_integration.py
─────────────────────────────────────────────────────
Вся Telegram-специфичная логика агента в одном модуле (по требованию Павла).

Отвечает за:
  1. DEFAULT_TG_FORMAT_PROMPT — дефолтный HTML-промпт форматирования для TG
     (вшивается ВСЕМ телеграм-чатам, агент его не меняет).
  2. get_tg_chat_settings(db, chat_id) — чтение per-chat настроек
     (system_prompt / agent_model / selected_tools / trigger_mode) НАПРЯМУЮ
     из БД пулом агента (таблица telegram_chat_settings, её создаёт chats_service).
  3. build_tg_system_suffix(...) — сборка блока, который подмешивается в
     системный промпт агента, когда сообщение пришло из Telegram
     (source == "telegram"): HTML-инструкция + per-chat кастомный промпт.
  4. build_tg_prefix(...) — префикс сообщения (вместо CatchPM-шапки):
     помечает источник Telegram, автора, тип чата.
  5. should_respond(...) — оркестратор на llm_lite: по буферу последних
     сообщений группы решает, обращаются ли к агенту / просят ли действие.

ВАЖНО: админские тулы для Telegram запрещены. allow_all для telegram выключен —
тул-сет берётся строго из настроек чата (см. langchain_agent, source=="telegram").
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("tg_integration")

# ── 1. Дефолтный HTML-промпт форматирования (вшивается всем TG-чатам) ──────────
DEFAULT_TG_FORMAT_PROMPT = """ИНСТРУКЦИЯ ПО ФОРМАТИРОВАНИЮ (HTML MODE):
1. Всегда используй HTML-теги для оформления текста.
2. Основные теги:
   - <b>Жирный текст</b>
   - <i>Курсив</i>
   - <u>Подчеркивание</u>
   - <code>Инлайн-код</code>
   - <pre>Блок кода</pre>
   - <span class="tg-spoiler">Спойлер</span>
   - <a href="URL">Текст ссылки</a>
3. СТРОГО ЗАПРЕЩЕНО:
   - Использовать Markdown (*, _, #, ```).
   - Использовать теги списков и структуры: <ul>, <ol>, <li>, <p>, <div>, <br>, <h1>-<h6>.
4. Оформление списков:
   - Для списков используй ОБЫЧНЫЕ переносы строк и символы-маркеры: "•", "-", или эмодзи (например: "• Пункт 1\\n• Пункт 2").
5. Экранирование:
   - ОБЯЗАТЕЛЬНО экранируй спецсимволы в обычном тексте:
     " < " -> &lt;
     " > " -> &gt;
     " & " -> &amp;"""

# Дефолтная модель для Telegram (Павел: по дефолту coder)
DEFAULT_TG_MODEL = "coder"
# Дефолтный безопасный тул-сет для TG (пусто = агент без спец-тулов; настраивается через /start)
DEFAULT_TG_TOOLS: list[str] = []
# Тулы, которые НИКОГДА не отдаём в Telegram (админка/опасное).
TG_FORBIDDEN_TOOLS = {"execute_sql_query", "get_db_schema"}


# ── 2. Чтение per-chat настроек напрямую из БД пулом агента ────────────────────
async def get_tg_chat_settings(db, chat_id: str) -> dict:
    """
    Читает telegram_chat_settings напрямую (пул agent_service, та же БД).
    Возвращает dict с ключами system_prompt/agent_model/selected_tools/
    trigger_mode/orchestrator_context_n. При отсутствии строки — дефолты.
    Fail-open: при любой ошибке возвращает дефолты (агент продолжает работать).
    """
    defaults = {
        "system_prompt": None,
        "agent_model": DEFAULT_TG_MODEL,
        "selected_tools": DEFAULT_TG_TOOLS,
        "trigger_mode": "orchestrator",
        "orchestrator_context_n": 6,
    }
    try:
        pool = getattr(db, "pool", None)
        if pool is None:
            return defaults
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT system_prompt, agent_model, selected_tools, "
                "trigger_mode, orchestrator_context_n "
                "FROM telegram_chat_settings WHERE chat_id=$1",
                str(chat_id),
            )
        if not row:
            return defaults
        d = dict(row)
        # selected_tools может прийти как JSON-строка
        st = d.get("selected_tools")
        if isinstance(st, str):
            try:
                st = json.loads(st)
            except Exception:
                st = None
        if not st:
            st = DEFAULT_TG_TOOLS
        # отфильтровать запрещённые (админские) тулы на всякий случай
        st = [t for t in st if t not in TG_FORBIDDEN_TOOLS]
        return {
            "system_prompt": d.get("system_prompt"),
            "agent_model": d.get("agent_model") or DEFAULT_TG_MODEL,
            "selected_tools": st,
            "trigger_mode": d.get("trigger_mode") or "orchestrator",
            "orchestrator_context_n": d.get("orchestrator_context_n") or 6,
        }
    except Exception as e:
        log.warning(f"[tg] get_tg_chat_settings fail chat={chat_id}: {e}")
        return defaults


# ── 3. Блок для системного промпта (HTML-инструкция + кастом per-chat) ─────────
def build_tg_system_suffix(custom_prompt: str | None) -> str:
    """
    Собирает добавку к системному промпту для TG-чата:
    сначала дефолтный HTML-промпт форматирования, затем кастомный per-chat.
    """
    parts = [DEFAULT_TG_FORMAT_PROMPT]
    if custom_prompt and custom_prompt.strip():
        parts.append("ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА ЭТОГО ЧАТА:\n" + custom_prompt.strip())
    return "\n\n".join(parts)


# ── 4. Префикс сообщения (вместо CatchPM-шапки) ───────────────────────────────
def build_tg_prefix(username: str | None, chat_id: str, chat_type: str = "group") -> str:
    """Шапка источника для TG-сообщения (аналог CatchPM-шапки, но для телеги)."""
    who = username or "Аноним"
    kind = "группа" if chat_type == "group" else "личка"
    return f"[Telegram, {kind}, Пользователь: {who}, chat_id: {chat_id}]\n"


# ── 5. Оркестратор на llm_lite (решает: отвечать или молчать) ──────────────────
ORCHESTRATOR_PROMPT = """Ты — фильтр-оркестратор группового Telegram-чата, где присутствует ИИ-ассистент.
Твоя задача: по последним сообщениям чата решить, ОБРАЩАЮТСЯ ли участники к ассистенту
или просят его что-то сделать (вопрос к боту, просьба, задача, продолжение диалога с ботом).

Ответь СТРОГО одним словом:
  YES — если к ассистенту обращаются / просят помочь / задают вопрос, на который он должен ответить.
  NO  — если это обычная болтовня участников между собой, не адресованная ассистенту.

Не объясняй, не добавляй ничего кроме YES или NO."""


async def should_respond(agent, buffer_lines: list[str]) -> bool:
    """
    Оркестратор: дёргает llm_lite агента (_lite_ainvoke), скармливает буфер
    последних сообщений группы. Возвращает True, если агент должен ответить.

    buffer_lines — список строк вида "Ник: текст" (из Redis-буфера, готовит бот
    и передаёт в эндпоинт /api/tg/should_respond).
    Fail-open в сторону молчания: при ошибке возвращаем False (не спамим).
    """
    if not buffer_lines:
        return False
    context_text = "\n".join(buffer_lines[-12:])  # ограничим на всякий
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=ORCHESTRATOR_PROMPT),
            HumanMessage(content="Последние сообщения чата:\n" + context_text +
                         "\n\nОбращаются ли к ассистенту? Ответь YES или NO."),
        ]
        resp = await agent._lite_ainvoke(messages)
        text = (getattr(resp, "content", None) or str(resp) or "").strip().upper()
        return text.startswith("YES")
    except Exception as e:
        log.warning(f"[tg] orchestrator fail: {e}")
        return False


# ── 6. Проактивная инициация (будильники завтрак/обед/ужин) ──────────────
# Слоты: 09:00 / 13:00 / 18:00 МСК. Тон зависит от слота.
SLOT_TONE = {
    "morning": "Утро (завтрак). Тон бодрый, задаёшь рабочий настрой на день.",
    "lunch":   "Обед. Тон лёгкий, можно подвести промежуточный итог дня.",
    "dinner":  "Вечер (ужин). Тон тёплый, подводишь итоги дня / планы на завтра.",
}


def build_initiation_prompt(slot: str, username: str | None, chat_type: str = "group",
                            addressed: bool = True) -> str:
    """
    Формирует задачу агенту при проактивной инициации (сам начал беседу).

    addressed=True  — адресно конкретному участнику (ждём ответ, работает «грусть»).
    addressed=False — броадкаст в чат (мем/дайджест/новость без адресата, ответ не ждём).

    Агент сам решает ЧТО делать (вопрос / свежая инфа из источников / мем),
    опираясь на свои тулы (из настроек чата) и per-chat system_prompt (чем заниматься по триггеру).
    История чата (прошлые ответы человека) подтянется памятью по chat_id.
    """
    tone = SLOT_TONE.get(slot, "")
    who = username or "участник"
    if addressed:
        return (
            f"[ПРОАКТИВНАЯ ИНИЦИАЦИЯ] {tone}\n"
            f"Сейчас ты САМ начинаешь рабочую беседу с @{who}. "
            f"Обратись к нему лично и живо. Можешь: поднять рабочий вопрос по прошлым обсуждениям, "
            f"принести свежую инфу по теме чата, спросить как двигаются задачи. Коротко, без воды."
        )
    return (
        f"[ПРОАКТИВНАЯ РАССЫЛКА] {tone}\n"
        f"Сейчас ты САМ пишешь в чат (без конкретного адресата). "
        f"Принеси в чат что-то полезное или весёлое по теме чата: свежую новость/дайджест из источников, "
        f"мем в тему, короткий типс. Ответа ни от кого не ждёшь — это вброс в общий чат."
    )


def build_sad_followup_prompt(username: str | None) -> str:
    """Агент «грустит»: человек не ответил на инициацию за окно (5-10 мин). Один раз."""
    who = username or "друг"
    return (
        f"[ПРОАКТИВНАЯ ГРУСТЬ] Ты писал @{who}, но он так и не ответил. "
        f"Напиши ОДНО короткое добродушно-грустное сообщение (с лёгким юмором, без обид), "
        f"мол, жаль что проигнорили. Не навязывайся, одной репликой."
    )
