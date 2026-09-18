"""
Автогенерация короткого заголовка чата по первому обмену (human + ai).

Логика вынесена из AgentPM (langchain_agent.py), чтобы не держать
её в основном цикле process_query и переиспользовать из офлайн-скрипта
миграции (scripts/backfill_chat_titles.py).

Публичные функции:
    build_title_messages(user_input, final_response) -> list[BaseMessage] | None
        Собирает пару system+human для лайт-модели. None, если юзер-текст пуст.

    sanitize_title(raw) -> str
        Причёсывает сырой ответ модели (кавычки, переносы, точка, длина).

    is_first_turn(pool, chat_id) -> bool
        True, если в chats ещё нет ответов бота для этого чата
        (т.е. текущий ход — первый и заголовок ещё не генерился).

    generate_chat_title(lite_invoke, user_input, final_response) -> str | None
        Генерит заголовок через переданный async-вызов лайт-модели.
        Fail-open: любая ошибка / пустой результат -> None.
"""

import logging

from langchain.schema import HumanMessage, SystemMessage

from .prompts.title_prompt import TITLE_SYSTEM_PROMPT, TITLE_USER_TEMPLATE


def build_title_messages(user_input: str, final_response: str):
    """Собрать сообщения для лайт-модели по первому обмену.

    Возвращает None, если сообщение пользователя пустое (генерить нечего).
    """
    u = (user_input or "").strip()[:2000]
    a = (final_response or "").strip()[:2000]
    if not u:
        return None
    prompt = TITLE_USER_TEMPLATE.format(user_msg=u, ai_msg=a)
    return [
        SystemMessage(content=TITLE_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]


def sanitize_title(raw) -> str:
    """Причесать сырой ответ модели до аккуратного заголовка.

    Принимает как строку, так и LLM-ответ (берёт .content).
    """
    title = raw if isinstance(raw, str) else getattr(raw, "content", None)
    title = str(title or "").strip()
    # Убираем кавычки/переносы, режем длину.
    title = title.replace(chr(34), "").replace(chr(10), " ").strip()
    if title.endswith("."):
        title = title[:-1].strip()
    return title[:80]


async def is_first_turn(pool, chat_id) -> bool:
    """Первый ход = в chats ещё нет ответов бота для этого чата."""
    if pool is None or chat_id is None:
        return False
    async with pool.acquire() as conn:
        bot_count = await conn.fetchval(
            "SELECT COUNT(*) FROM chats WHERE chat_id = $1 AND is_from_bot = TRUE",
            chat_id,
        )
    return not (bot_count and int(bot_count) > 0)


async def generate_chat_title(lite_invoke, user_input: str, final_response: str):
    """Сгенерировать заголовок чата через переданный async-вызов лайт-модели.

    lite_invoke: awaitable-функция list[BaseMessage] -> LLM-ответ
                 (например AgentPM._lite_ainvoke).
    Возвращает строку-заголовок либо None. Fail-open.
    """
    try:
        messages = build_title_messages(user_input, final_response)
        if messages is None:
            return None
        resp = await lite_invoke(messages)
        title = sanitize_title(resp)
        return title or None
    except Exception as e:
        logging.error(f"[title] не удалось сгенерировать заголовок чата: {e}")
        return None
