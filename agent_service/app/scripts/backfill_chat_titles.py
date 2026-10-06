#!/usr/bin/env python3
"""
Бэкфилл автозаголовков чатов (chat_sidebar_meta.auto_title).

Проходит по всем чатам, у которых ещё НЕТ auto_title, берёт первый обмен
(первое сообщение пользователя + первый ответ бота), генерит короткий
заголовок лайт-моделью и пишет его в chat_sidebar_meta.

ЗАПУСК ВНУТРИ КОНТЕЙНЕРА АГЕНТА (там есть и БД по env, и cloud.ru-ключи):

    docker compose exec agent_service python -m app.scripts.backfill_chat_titles
    # или c флагами:
    docker compose exec agent_service \
        python -m app.scripts.backfill_chat_titles --dry-run
    docker compose exec agent_service \
        python -m app.scripts.backfill_chat_titles --concurrency 4 --limit 200
    docker compose exec agent_service \
        python -m app.scripts.backfill_chat_titles --user-id 56

Флаги:
    --dry-run       не писать в БД, только показать что сгенерилось
    --concurrency N параллельных генераций (по умолчанию 5)
    --limit N       обработать не больше N чатов (0 = все)
    --user-id N     только чаты этого пользователя
    --overwrite     перегенерить даже там, где auto_title уже есть

Скрипт идемпотентен: без --overwrite чаты с уже заполненным auto_title
пропускаются, поэтому его можно перезапускать.
"""

import argparse
import asyncio
import logging
import sys

import asyncpg

# Импорты работают при запуске как модуль из /app: python -m app.scripts.backfill_chat_titles
from app.agent.core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
from app.agent.core.agent_provider import (
    get_llm_lite_model,
    get_llm_lite_model_gpt_mini,
    get_llm_lite_model_gemini_flash,
)
from app.agent.title_generator import generate_chat_title

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill_titles")


# --- Лайт-модель с цепочкой фолбэков (как AgentPM._lite_ainvoke, но автономно) ---

_LITE_CHAIN = None


def _lite_chain():
    global _LITE_CHAIN
    if _LITE_CHAIN is None:
        _LITE_CHAIN = [
            get_llm_lite_model(),
            get_llm_lite_model_gpt_mini(),
            get_llm_lite_model_gemini_flash(),
        ]
    return _LITE_CHAIN


async def lite_invoke(messages):
    """Вызов лайт-модели с фолбэками. Совместим по сигнатуре с _lite_ainvoke."""
    last_error = None
    for i, model in enumerate(_lite_chain()):
        try:
            resp = await model.ainvoke(messages)
            if i > 0:
                log.warning(f"Лайт-модель: сработал фолбэк #{i}")
            return resp
        except Exception as e:
            last_error = e
            log.error(f"Лайт-модель #{i} упала: {type(e).__name__}: {e}")
    raise last_error if last_error else RuntimeError("Лайт-модель: пустая цепочка")


# --- Работа с БД ---

async def fetch_targets(pool, user_id, limit, overwrite):
    """Список чатов для обработки: (chat_id, user_id).

    Берём чаты, где есть и сообщение юзера, и ответ бота (полный первый
    обмен), и (без overwrite) ещё нет auto_title в chat_sidebar_meta.
    """
    where = ["c.is_from_bot = TRUE"]  # гарантируем что бот хоть раз ответил
    params = []
    if user_id is not None:
        params.append(user_id)
        where.append(f"c_m.user_id = ${len(params)}")

    overwrite_clause = "" if overwrite else "AND m.auto_title IS NULL"

    limit_clause = ""
    if limit and limit > 0:
        params.append(limit)
        limit_clause = f"LIMIT ${len(params)}"

    # q = f"""
    #     SELECT DISTINCT c.chat_id, c.user_id
    #     FROM chats c
    #     LEFT JOIN chat_sidebar_meta m
    #            ON m.chat_id = c.chat_id AND m.user_id = c.user_id
    #     WHERE {' AND '.join(where)}
    #       {overwrite_clause}
    #     ORDER BY c.chat_id
    #     {limit_clause}
    # """
    
    q = f"""
    select DISTINCT c_m.chat_id, c_m.user_id
    from chat_sidebar_meta c_m right join chats c 
    on c_m.chat_id = c.chat_id
    where {' AND '.join(where)} and auto_title is NULL and c.is_from_bot = TRUE
    order by c_m.chat_id;
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(q, *params)
    return [(r["chat_id"], r["user_id"]) for r in rows]


async def fetch_first_exchange(pool, chat_id):
    """Первое сообщение пользователя и первый ответ бота в чате."""
    async with pool.acquire() as conn:
        user_msg = await conn.fetchval("""
            SELECT message_text FROM chats
            WHERE chat_id = $1 AND is_from_bot = FALSE
              AND message_text IS NOT NULL AND message_text <> ''
            ORDER BY created_at ASC, id ASC
            LIMIT 1
        """, chat_id)
        bot_msg = await conn.fetchval("""
            SELECT message_text FROM chats
            WHERE chat_id = $1 AND is_from_bot = TRUE
              AND message_text IS NOT NULL AND message_text <> ''
            ORDER BY created_at ASC, id ASC
            LIMIT 1
        """, chat_id)
    return user_msg, bot_msg


async def save_title(pool, chat_id, user_id, title, overwrite):
    """Записать auto_title в chat_sidebar_meta.

    Без overwrite сохраняем через COALESCE (не затираем существующий),
    как это делает set_chat_auto_title в chats_service.
    """
    if overwrite:
        set_clause = "auto_title = EXCLUDED.auto_title"
    else:
        set_clause = "auto_title = COALESCE(chat_sidebar_meta.auto_title, EXCLUDED.auto_title)"
    async with pool.acquire() as conn:
        await conn.execute(f"""
            INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, auto_title)
            VALUES ($1, $2, FALSE, FALSE, $3)
            ON CONFLICT (chat_id, user_id)
            DO UPDATE SET {set_clause},
                          updated_at = CURRENT_TIMESTAMP
        """, chat_id, user_id, title)


# --- Основной прогон ---

async def process_one(pool, sem, chat_id, user_id, dry_run, overwrite, stats):
    async with sem:
        try:
            user_msg, bot_msg = await fetch_first_exchange(pool, chat_id)
            if not user_msg:
                stats["skipped_empty"] += 1
                log.info(f"chat={chat_id}: нет сообщения пользователя — пропуск")
                return
            title = await generate_chat_title(lite_invoke, user_msg, bot_msg or "")
            if not title:
                stats["failed"] += 1
                log.warning(f"chat={chat_id}: заголовок не сгенерён — пропуск")
                return
            if dry_run:
                stats["generated"] += 1
                log.info(f"[dry-run] chat={chat_id} user={user_id} -> {title!r}")
                return
            await save_title(pool, chat_id, user_id, title, overwrite)
            stats["written"] += 1
            log.info(f"chat={chat_id} user={user_id} -> {title!r}")
        except Exception as e:
            stats["failed"] += 1
            log.error(f"chat={chat_id}: ошибка обработки: {type(e).__name__}: {e}")


async def main():
    ap = argparse.ArgumentParser(description="Бэкфилл автозаголовков чатов")
    ap.add_argument("--dry-run", action="store_true", help="не писать в БД")
    ap.add_argument("--concurrency", type=int, default=5, help="параллельных генераций")
    ap.add_argument("--limit", type=int, default=0, help="максимум чатов (0=все)")
    ap.add_argument("--user-id", type=int, default=None, help="только этот пользователь")
    ap.add_argument("--overwrite", action="store_true", help="перегенерить даже если auto_title есть")
    args = ap.parse_args()

    pool = await asyncpg.create_pool(
        user=DB_USER, password=DB_PASSWORD,
        host=DB_HOST, port=DB_PORT, database=DB_NAME,
        min_size=1, max_size=max(2, args.concurrency + 1),
        command_timeout=60,
    )
    try:
        targets = await fetch_targets(pool, args.user_id, args.limit, args.overwrite)
        log.info(
            f"К обработке чатов: {len(targets)} "
            f"(dry_run={args.dry_run}, overwrite={args.overwrite}, "
            f"concurrency={args.concurrency}, user_id={args.user_id})"
        )
        if not targets:
            log.info("Нечего обрабатывать — все чаты уже имеют auto_title.")
            return

        stats = {"written": 0, "generated": 0, "skipped_empty": 0, "failed": 0}
        sem = asyncio.Semaphore(args.concurrency)
        await asyncio.gather(*[
            process_one(pool, sem, cid, uid, args.dry_run, args.overwrite, stats)
            for cid, uid in targets
        ])

        log.info("=" * 50)
        log.info(
            "ИТОГ: "
            f"записано={stats['written']}, "
            f"сгенерено(dry)={stats['generated']}, "
            f"пусто={stats['skipped_empty']}, "
            f"ошибок={stats['failed']}, "
            f"всего={len(targets)}"
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Прервано пользователем", file=sys.stderr)
        sys.exit(1)
