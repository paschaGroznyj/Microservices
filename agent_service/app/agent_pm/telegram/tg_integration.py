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
        "agent_name": None,
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
                "SELECT system_prompt, agent_name, agent_model, selected_tools, "
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
            "agent_name": d.get("agent_name"),
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
ORCHESTRATOR_PROMPT_BASE = """Ты — фильтр-оркестратор группового Telegram-чата, где присутствует ИИ-ассистент.
Твоя задача: по последним сообщениям чата решить, ОБРАЩАЮТСЯ ли участники к ассистенту
или просят его что-то сделать (вопрос к боту, просьба, задача, продолжение диалога с ботом).{name_line}

Ответь СТРОГО одним словом:
  YES — если к ассистенту обращаются / просят помочь / задают вопрос, на который он должен ответить.
  NO  — если это обычная болтовня участников между собой, не адресованная ассистенту.

Не объясняй, не добавляй ничего кроме YES или NO."""


def build_orchestrator_prompt(agent_name: str | None = None) -> str:
    """Собирает промпт оркестратора. Если у агента задано имя —
    подсказываем оркестратору, что обращение по имени = обращение к ассистенту.
    Пустое имя — дефолтный промпт без спец-имени."""
    name_line = ""
    if agent_name and agent_name.strip():
        nm = agent_name.strip()
        name_line = (
            f"\n\nВ этом чате ассистента зовут «{nm}». Если участники обращаются к «{nm}» по имени "
            f"(в любом падеже, регистре или сокращении) — это обращение к ассистенту, отвечай YES."
        )
    return ORCHESTRATOR_PROMPT_BASE.format(name_line=name_line)


async def should_respond(agent, buffer_lines: list[str], agent_name: str | None = None) -> bool:
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
            SystemMessage(content=build_orchestrator_prompt(agent_name)),
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
# Приписка к проактивным промптам: ориентироваться на бортовой журнал чата.
# Проактив триггерится дефолтными промптами по времени,
# но журнал агента (источники/предпочтения/темы) прицепляется в process_query.
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

# Дефолтные ЗАДАЧИ для preset-будильников (когда у триггера prompt пустой).
# Живут здесь, в сервисе агента (не в БД). Из ТГ можно навесить доп-промпт
# СВЕРХУ через custom_prompt — он не заменяет дефолт, а дополняет его.
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
    """Проактив по ИНДИВИДУАЛЬНОМУ триггеру.
    custom_prompt задаёт ЧТО делать. preset_key (breakfast/lunch/dinner):
    если задан, дефолтная задача этого приёма пищи подмешивается ПЕРЕД
    custom_prompt — так пустой preset-будильник всё равно осмысленно отрабатывает.
    addressed=True -> адресно @username (ждём ответ, работает грусть).
    """
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
    """Агент «грустит»: человек не ответил на инициацию за окно (5-10 мин). Один раз."""
    who = username or "друг"
    return (
        f"[ПРОАКТИВНАЯ ГРУСТЬ] Ты писал @{who}, но он так и не ответил. "
        f"Напиши ОДНО короткое добродушно-грустное сообщение (с лёгким юмором, без обид), "
        f"мол, жаль что проигнорили. Не навязывайся, одной репликой."
    )


# ── 7. Реакции на сообщения бота (lite-анализ + feedback большому агенту) ──────
# REACTIONS_BLOCK_V1
# Лайт-модель оценивает реакцию (emoji + текст ответа агента) → sentiment.
# Собранные реакции копятся в Redis и подмешиваются большому агенту в next-запросе.

REACTION_SENTIMENT_PROMPT = (
    "Ты — лаконичный анализатор реакций. На сообщение ассистента в чате поставили emoji-реакцию.\n"
    "Определи тональность реакции по emoji и тексту сообщения.\n"
    "Ответь СТРОГО одной строкой JSON без markdown:\n"
    '{"sentiment": "pos|neg|neutral", "note": "коротко почему (до 10 слов)"}\n'
    "pos = понравилось/одобрение/смешно. "
    "neg = не понравилось/критика/негатив. "
    "neutral = нейтрально/непонятно."
)


async def analyze_reaction(agent, emoji: str, action: str, agent_text: str) -> dict:
    """Лайт-анализ реакции. Возвращает {sentiment, note}.

    action: 'add' (поставил) | 'remove' (снял).
    Fail-open: при ошибке → neutral.
    """
    snippet = (agent_text or "").replace("\n", " ").strip()
    if len(snippet) > 500:
        snippet = snippet[:500] + "…"
    act_ru = "поставил" if action == "add" else "снял"
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [
            SystemMessage(content=REACTION_SENTIMENT_PROMPT),
            HumanMessage(content=(
                f"Сообщение ассистента: {snippet!r}\n"
                f"Реакция: {emoji} ({act_ru}).\n"
                f"Верни JSON."
            )),
        ]
        resp = await agent._lite_ainvoke(messages)
        raw = (getattr(resp, "content", None) or str(resp) or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        import re as _re
        m = _re.search(r"\{.*\}", raw, _re.S)
        data = json.loads(m.group(0) if m else raw)
        sent = str(data.get("sentiment", "neutral")).lower()
        if sent not in ("pos", "neg", "neutral"):
            sent = "neutral"
        return {"sentiment": sent, "note": str(data.get("note", ""))[:120]}
    except Exception as e:
        log.warning(f"[tg] analyze_reaction fail: {e}")
        return {"sentiment": "neutral", "note": ""}


def build_reactions_feedback(reactions: list[dict]) -> str:
    """Собирает блок обратной связи для большого агента в next-запросе.

    reactions — список dict {username, emoji, action, sentiment, note, agent_snippet}.
    """
    if not reactions:
        return ""
    pos = [r for r in reactions if r.get("sentiment") == "pos"]
    neg = [r for r in reactions if r.get("sentiment") == "neg"]
    lines = ["[ОБРАТНАЯ СВЯЗЬ ПО РЕАКЦИЯМ] Еуу, броу — люди отреагировали на твои прошлые сообщения:"]
    for r in reactions[:20]:
        who = r.get("username") or "кто-то"
        emoji = r.get("emoji") or ""
        act = "поставил" if r.get("action") == "add" else "снял"
        snip = (r.get("agent_snippet") or "").strip()
        if len(snip) > 80:
            snip = snip[:80] + "…"
        tag = {"pos": "➕", "neg": "➖", "neutral": "○"}.get(r.get("sentiment"), "○")
        extra = f" ({r.get('note')})" if r.get("note") else ""
        lines.append(f"  {tag} {who} {act} {emoji} на «{snip}»{extra}")
    lines.append(f"Итого: 👍 {len(pos)} позитивных, 👎 {len(neg)} негативных.")
    lines.append(
        "Учти это в тоне следующего ответа (что зашло — развивай, что нет — корректируй). "
        "НЕ отчитывайся о реакциях явно, просто учитывай настроение."
    )
    return "\n".join(lines)


def build_reaction_proactive_prompt(kind: str, reactions: list[dict]) -> str:
    """Проактивный триггер по реакциям.

    kind='pos' — накопилось 3+ позитивных на одно сообщение → развить тему.
    kind='neg' — одна негативная → шутливо уточнить, что не так (с юмором).
    """
    r0 = reactions[0] if reactions else {}
    snip = (r0.get("agent_snippet") or "").strip()
    if len(snip) > 160:
        snip = snip[:160] + "…"
    emojis = " ".join(x.get("emoji", "") for x in reactions if x.get("emoji"))
    if kind == "neg":
        who = r0.get("username") or "кто-то"
        return (
            f"[РЕАКЦИЯ-ТРИГГЕР: НЕГАТИВ] На твоё сообщение «{snip}» "
            f"{who} поставил {emojis or 'негативную реакцию'}. "
            f"Напиши ОДНО короткое живое сообщение в стиле «эй, а чего так?» — "
            f"с лёгким юмором и самоиронией, без обид и агрессии. "
            f"Можешь шутливо уточнить, что не так. Коротко."
            f"\n{JOURNAL_HINT}"
        )
    return (
        f"[РЕАКЦИЯ-ТРИГГЕР: ПОЗИТИВ] Твоё сообщение «{snip}» зашло — "
        f"набрало {len(reactions)} позитивных реакций ({emojis}). "
        f"Народу тема понравилась — развей её: принеси продолжение, "
        f"доп-факт, углубление или свежий поворот по этой теме. Одним сообщением, живо."
        f"\n{JOURNAL_HINT}"
    )
