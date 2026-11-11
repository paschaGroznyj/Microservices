import re
import asyncio
import asyncpg
from app.core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME, LOOP_SERVICE_USER_ID
import logging
from datetime import datetime, timezone
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


_THUMBHASH_RE = re.compile(r"^[A-Za-z0-9+/]{8,64}={0,2}$")


def _thumbhash(f: dict) -> str | None:
    """ThumbHash (base64, ~25–35 байт) из метаданных; мусор и длинные строки → None."""
    v = f.get("thumbhash")
    if isinstance(v, str) and _THUMBHASH_RE.match(v):
        return v
    return None


def _file_size(f: dict) -> int | None:
    """Размер вложения в байтах из метаданных (size / file_size / size_bytes)."""
    for k in ("size", "file_size", "size_bytes"):
        v = f.get(k)
        if v is None or isinstance(v, bool):
            continue
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            return n
    return None


def _pixels(f: dict, key: str) -> int | None:
    """width/height в пикселях: целое 1..65535, иначе None."""
    v = f.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= 65535 else None


class DataBaseChats:
    async def init_db_pool(self):
        """Инициализация пула подключений к базе данных."""
        max_retries = 5
        retry_delay = 2.0

        for attempt in range(1, max_retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    user=DB_USER,
                    password=DB_PASSWORD,
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME,
                    min_size=5,
                    max_size=20,
                    max_queries=50000,
                    max_inactive_connection_lifetime=300,
                    command_timeout=60,
                    timeout=60,
                    statement_cache_size=100
                )

                # Инициализация структуры базы данных
                async with self.pool.acquire() as conn:
                    await DataBaseChats.__init_db(conn)
                    return

            except Exception as e:
                print("Пока не удалось подключиться")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                else:
                    raise

    @staticmethod
    async def __init_db(conn):
        # DDL-схема таблиц чатов, файлов, опросов и воркспейсов
        # централизована в postgres/sql/02_chats/01_chats_core.sql.
        # Здесь только идемпотентные миграции для уже созданных баз
        # (init-скрипты postgres выполняются лишь на пустом томе).
        try:
            await conn.execute("ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS file_size BIGINT")
        except Exception as e:
            logging.warning(f"chat_files.file_size: миграция не выполнена: {e}")
        try:
            await conn.execute("ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS thumbhash TEXT")
        except Exception as e:
            logging.warning(f"chat_files.thumbhash: миграция не выполнена: {e}")
        try:
            await conn.execute("ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS width INTEGER")
            await conn.execute("ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS height INTEGER")
        except Exception as e:
            logging.warning(f"chat_files.width/height: миграция не выполнена: {e}")
        try:
            # Режим запроса: agent (полный агент) | model (MauDel без тулов).
            await conn.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS type_query TEXT DEFAULT 'agent'")
        except Exception as e:
            logging.warning(f"chats.type_query: миграция не выполнена: {e}")

    async def save_poll_answer(
        self,
        chat_id: str,
        user_id: int,
        user_message_id: str | None,
        poll_answer: dict,
    ) -> dict | None:
        if not chat_id or user_id is None or not isinstance(poll_answer, dict):
            return None
        bot_message_id = str(poll_answer.get("bot_message_id") or "").strip()
        if not bot_message_id:
            return None
        try:
            poll_index = int(poll_answer.get("poll_index") or 0)
        except (TypeError, ValueError):
            poll_index = 0
        raw_sel = poll_answer.get("selected_index")
        try:
            selected_index = int(raw_sel) if raw_sel is not None else None
        except (TypeError, ValueError):
            selected_index = None
        selected_value = str(poll_answer.get("selected_value") or "").strip()
        is_custom = bool(poll_answer.get("is_custom", False))
        is_skipped = bool(poll_answer.get("is_skipped", False))
        payload_json = json.dumps(poll_answer, ensure_ascii=False)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chat_poll_answers (
                    chat_id, bot_message_id, poll_index, user_id, user_message_id,
                    selected_index, selected_value, is_custom, is_skipped, poll_payload
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
                ON CONFLICT (chat_id, bot_message_id, poll_index) DO NOTHING
                RETURNING id, chat_id, bot_message_id, poll_index, user_id, user_message_id,
                          selected_index, selected_value, is_custom, is_skipped, created_at;
                """,
                str(chat_id),
                bot_message_id,
                poll_index,
                int(user_id),
                user_message_id,
                selected_index,
                selected_value,
                is_custom,
                is_skipped,
                payload_json,
            )
            return dict(row) if row else None

    async def is_poll_answered(self, chat_id: str, bot_message_id: str, poll_index: int = 0) -> bool:
        if not chat_id or not bot_message_id:
            return False
        val = await self.pool.fetchval(
            "SELECT 1 FROM chat_poll_answers WHERE chat_id=$1 AND bot_message_id=$2 AND poll_index=$3 LIMIT 1;",
            str(chat_id), str(bot_message_id), int(poll_index),
        )
        return val is not None

    async def get_poll_answers(self, chat_id: str) -> list[dict]:
        if not chat_id:
            return []
        rows = await self.pool.fetch(
            """
            SELECT bot_message_id, poll_index, user_id, user_message_id,
                   selected_index, selected_value, is_custom, is_skipped, created_at
            FROM chat_poll_answers
            WHERE chat_id = $1
            ORDER BY id ASC;
            """,
            str(chat_id),
        )
        return [
            {
                "bot_message_id": r["bot_message_id"],
                "poll_index": r["poll_index"],
                "user_id": r["user_id"],
                "user_message_id": r["user_message_id"],
                "selected_index": r["selected_index"],
                "selected_value": r["selected_value"],
                "is_custom": r["is_custom"],
                "is_skipped": r["is_skipped"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]

    # ═══ САМОЦИКЛ АГЕНТА (agent_loop_tasks) ═══════════════════════════════
    async def upsert_loop_task(self, chat_id: str, user_id: int, instruction: str) -> dict | None:
        """Завести/обновить активную установку петли для чата.

        На чат держим РОВНО ОДНУ активную установку (гарантия — частичный
        unique-индекс ux_agent_loop_tasks_chat_active). Старую гасим, новую вставляем.
        Это и есть начальная установка от пользователя; consumer её НЕ меняет.
        """
        if not chat_id or user_id is None or not (instruction or "").strip():
            return None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE agent_loop_tasks SET is_active=FALSE, updated_at=now() "
                    "WHERE chat_id=$1 AND is_active;", chat_id)
                row = await conn.fetchrow(
                    "INSERT INTO agent_loop_tasks (chat_id, user_id, instruction) "
                    "VALUES ($1,$2,$3) RETURNING id, chat_id, user_id, instruction, is_active;",
                    chat_id, user_id, instruction.strip())
        return dict(row) if row else None

    async def get_active_loop_task(self, chat_id: str) -> dict | None:
        """Активная установка петли для чата (или None). Читает consumer перед реентри."""
        if not chat_id:
            return None
        row = await self.pool.fetchrow(
            "SELECT id, chat_id, user_id, instruction, is_active "
            "FROM agent_loop_tasks WHERE chat_id=$1 AND is_active LIMIT 1;", chat_id)
        return dict(row) if row else None

    async def deactivate_loop_task(self, chat_id: str) -> bool:
        """Остановить петлю для чата (килл-свитч/стоп-кнопка/выход по бюджету)."""
        if not chat_id:
            return False
        res = await self.pool.execute(
            "UPDATE agent_loop_tasks SET is_active=FALSE, updated_at=now() "
            "WHERE chat_id=$1 AND is_active;", chat_id)
        return res.endswith("1") or res.split()[-1] not in ("0", "")

    # ═══ ТАРИФИКАЦИЯ (model_tariffs) ══════════════════════════════════════
    async def estimate_spend_today(self, chat_id: str | None = None) -> float:
        """Своя оценка расхода в рублях за сегодня (МСК) по tokens_usage × тарифам.

        Второе условие бюджета петли: между часовыми обновлениями Activity API
        мы сами суммируем токены из chats и переводим в деньги. chat_id=None —
        по всему отделу (все чаты), иначе только по одному чату.
        """
        if chat_id:
            rows = await self.pool.fetch(
                """
                SELECT COALESCE(c.model_type, 'universal') AS abs_model,
                    SUM(COALESCE((c.tokens_usage->>'input_tokens')::bigint, 0))  AS in_tok,
                    SUM(COALESCE((c.tokens_usage->>'output_tokens')::bigint, 0)) AS out_tok
                FROM chats c
                WHERE c.chat_id = $1
                AND c.is_from_bot = TRUE
                AND c.tokens_usage IS NOT NULL
                AND c.created_at >= COALESCE(
                        (SELECT t.created_at FROM agent_loop_tasks t
                        WHERE t.chat_id = $1 AND t.is_active = TRUE
                        ORDER BY t.created_at DESC LIMIT 1),
                        date_trunc('day', (now() AT TIME ZONE 'Europe/Moscow'))
                            AT TIME ZONE 'Europe/Moscow')
                GROUP BY 1;
                """, chat_id)

        else:
            rows = await self.pool.fetch(
                """
                SELECT COALESCE(c.model_type, 'universal') AS abs_model,
                    SUM(COALESCE((c.tokens_usage->>'input_tokens')::bigint, 0))  AS in_tok,
                    SUM(COALESCE((c.tokens_usage->>'output_tokens')::bigint, 0)) AS out_tok
                FROM chats c
                WHERE c.is_from_bot = TRUE
                AND c.tokens_usage IS NOT NULL
                AND c.created_at >= date_trunc('day', (now() AT TIME ZONE 'Europe/Moscow'))
                                    AT TIME ZONE 'Europe/Moscow'
                GROUP BY 1;
                """)

        tariffs = await self.get_tariffs()
        total = 0.0
        for r in rows:
            t = tariffs.get(r["abs_model"]) or tariffs.get("universal") or {}
            in_rate = float(t.get("input_per_1m", 0) or 0)
            out_rate = float(t.get("output_per_1m", 0) or 0)
            total += float((r["in_tok"] or 0) / 1_000_000) * in_rate
            total += float((r["out_tok"] or 0) / 1_000_000) * out_rate
        return round(total, 2)

    async def get_tariffs(self) -> dict:
        """Карта abstract_model_name -> {input_per_1m, output_per_1m, model_name}."""
        rows = await self.pool.fetch(
            "SELECT model_name, abstract_model_name, input_per_1m, output_per_1m FROM model_tariffs;")
        out = {}
        for r in rows:
            entry = {
                "model_name": r["model_name"],
                "input_per_1m": float(r["input_per_1m"] or 0),
                "output_per_1m": float(r["output_per_1m"] or 0),
            }
            if r["abstract_model_name"]:
                out[r["abstract_model_name"]] = entry
            if r["model_name"]:
                out[r["model_name"]] = entry
        return out

    async def add_message(
        self,
        user_id: int,
        message: str,
        chat_id: str,
        is_from_bot: bool,
        message_id: str | None = None,
        attached_files: list[dict] | None = None,
        action: str = None,
        selected_tools: list[str] | None = None,
        context_size: str | None = None,
        tool_trace: str | None = None,
        model_type: str | None = None,
        tokens_usage: dict | None = None,
        gateway_target: str | None = None,
        input_format: str | None = None,
        type_query: str | None = None,
    ):
        if user_id is None or chat_id is None:
            logging.warning("Пропущено сообщение: отсутствует user_id или chat_id")
            return None

        tokens_usage_json = json.dumps(tokens_usage) if tokens_usage is not None else None

        now_utc = datetime.now(timezone.utc)
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO chats (
                            user_id, chat_id, message_text, is_from_bot,
                            tool_name, selected_tools, context_size, message_id, created_at, tool_trace, model_type, tokens_usage, gateway_target, input_format, type_query
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13, $14, $15);
                    """,
                    user_id, chat_id, message, is_from_bot,
                    action, selected_tools, context_size, message_id, now_utc, tool_trace, model_type, tokens_usage_json, gateway_target, (input_format or 'text'),
                    ('model' if str(type_query or '').lower() == 'model' else 'agent'))

                    if attached_files:
                        source = "agent" if is_from_bot else "user"
                        await conn.executemany("""
                            INSERT INTO chat_files (message_id, chat_id, user_id, file_name, file_ext, source, file_path, file_kind, image_key, file_size, thumbhash, width, height)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13);
                        """, [
                            (
                                message_id, chat_id, user_id,
                                f["name"], f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else None,
                                source, f.get("path"), f.get("kind"), f.get("image_key"),
                                _file_size(f), _thumbhash(f), _pixels(f, "width"), _pixels(f, "height"),
                            )
                            for f in attached_files
                        ])
        except asyncpg.UniqueViolationError:
            logging.error("Сообщение не может быть добавлено (дубликат message_id)")

    async def set_message_reaction(
        self,
        chat_id: str,
        message_id: str | None = None,
        db_id: int | str | None = None,
        reaction: str | None = None,
    ) -> dict | None:
        if not chat_id:
            return None
        mid_str = str(message_id or "").strip()
        db_id_str = str(db_id if db_id is not None else "").strip()
        if not mid_str and not db_id_str:
            return None
        norm_reaction = str(reaction).strip().lower() if reaction else None
        if norm_reaction not in ("like", "dislike"):
            norm_reaction = None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE chats
                SET reaction = $1
                WHERE id = (
                    SELECT id FROM chats
                    WHERE chat_id = $2
                      AND is_from_bot = TRUE
                      AND (
                          ($3 <> '' AND (message_id = $3 OR id::text = $3))
                          OR ($4 <> '' AND (id::text = $4 OR message_id = $4))
                      )
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                )
                RETURNING id, chat_id, message_id, reaction;
                """,
                norm_reaction,
                str(chat_id),
                mid_str,
                db_id_str,
            )
            return dict(row) if row else None

    async def append_reaction(self, message_id: str, reaction: dict) -> dict | None:
        """Добавить реакцию к сообщению бота (JSONB-массив reactions).
        Возвращает {message_text, is_from_bot, reactions} по последней строке этого message_id
        или None, если сообщение не найдено."""
        import json as _json
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE chats
                SET reactions = COALESCE(reactions, '[]'::jsonb) || $2::jsonb
                WHERE id = (
                    SELECT id FROM chats
                    WHERE message_id = $1 AND is_from_bot = TRUE
                    ORDER BY created_at DESC LIMIT 1
                )
                RETURNING message_text, is_from_bot, reactions;
                """,
                message_id, _json.dumps([reaction]),
            )
            if not row:
                return None
            return {
                "message_text": row["message_text"],
                "is_from_bot": row["is_from_bot"],
                "reactions": row["reactions"],
            }

    async def get_bot_message_text(self, message_id: str) -> str | None:
        """Текст ответа бота по message_id (только is_from_bot)."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT message_text FROM chats
                WHERE message_id = $1 AND is_from_bot = TRUE
                ORDER BY created_at DESC LIMIT 1
                """,
                message_id,
            )

    async def get_all_user_messages(self, username: str, limit: int = 50, offset: int = 0):
        """
        Возвращает ВСЕ сообщения пользователя во ВСЕХ чатах
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        c.id, 
                        c.user_id,
                        u.username,
                        c.chat_id, 
                        c.message_text as message, 
                        c.is_from_bot, 
                        c.created_at as timestamp
                    FROM chats c
                    LEFT JOIN users u ON c.user_id = u.id
                    WHERE c.chat_id IN (
                        SELECT DISTINCT chat_id 
                        FROM chats 
                        WHERE user_id = (SELECT id FROM users WHERE username = $1)
                    )
                    ORDER BY c.created_at ASC
                    LIMIT $2 OFFSET $3
                """, username, limit, offset)

                messages = []
                for row in rows:
                    if row["user_id"] == -1:
                        sender_name = "AI Assistant"
                    elif row["username"]:
                        sender_name = row["username"]
                    else:
                        sender_name = f"User {row['user_id']}"

                    messages.append({
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "username": sender_name,
                        "chat_id": row["chat_id"],
                        "message": row["message"],
                        "is_from_bot": row["is_from_bot"],
                        "timestamp": row["timestamp"].isoformat()
                    })
                return messages
        except Exception as e:
            logging.error(f"Ошибка при получении всех сообщений пользователя {username}: {e}")
            raise

    async def search_user_messages(self, username: str, query: str, limit: int = 30):
        """
        Живой поиск по сообщениям пользователя во всех его (не удалённых) чатах.
        Возвращает найденные сообщения с id, message_id, chat_id, заголовком чата
        (renamed_title / auto_title / last_message), фрагментом (snippet),
        флагом is_from_bot и временем.
        """
        try:
            q = (query or "").strip()
            if len(q) < 2:
                return []
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    WITH target_user AS (
                        SELECT id AS user_id FROM users WHERE username = $1
                    ),
                    user_chats AS (
                        SELECT DISTINCT c.chat_id
                        FROM chats c
                        JOIN target_user tu ON tu.user_id = c.user_id
                    ),
                    matches AS (
                        SELECT
                            c.id,
                            c.message_id,
                            c.chat_id,
                            c.message_text,
                            c.is_from_bot,
                            c.created_at
                        FROM chats c
                        JOIN user_chats uc ON uc.chat_id = c.chat_id
                        LEFT JOIN chat_sidebar_meta m
                             ON m.chat_id = c.chat_id AND m.user_id = (SELECT user_id FROM target_user)
                        WHERE COALESCE(m.is_deleted, FALSE) = FALSE
                          AND (c.user_id = (SELECT user_id FROM target_user) OR c.user_id = -1)
                          AND c.message_text ILIKE $2 ESCAPE '\'
                        ORDER BY c.created_at DESC, c.id DESC
                        LIMIT $3
                    )
                    SELECT
                        mt.id,
                        mt.message_id,
                        mt.chat_id,
                        mt.message_text,
                        mt.is_from_bot,
                        mt.created_at,
                        meta.renamed_title,
                        meta.auto_title,
                        (
                            SELECT c2.message_text
                            FROM chats c2
                            JOIN target_user tu2 ON tu2.user_id = c2.user_id
                            WHERE c2.chat_id = mt.chat_id
                            ORDER BY c2.created_at DESC, c2.id DESC
                            LIMIT 1
                        ) AS last_message
                    FROM matches mt
                    LEFT JOIN chat_sidebar_meta meta
                           ON meta.chat_id = mt.chat_id
                          AND meta.user_id = (SELECT user_id FROM target_user)
                    ORDER BY mt.created_at DESC, mt.id DESC
                """, username, pattern, limit)

                results = []
                for row in rows:
                    results.append({
                        "id": row["id"],
                        "message_id": row["message_id"] or str(row["id"]),
                        "chat_id": str(row["chat_id"]),
                        "renamed_title": row["renamed_title"],
                        "auto_title": row["auto_title"],
                        "last_message": row["last_message"],
                        "snippet": row["message_text"] or "",
                        "is_from_bot": bool(row["is_from_bot"]),
                        "timestamp": row["created_at"].isoformat() if row["created_at"] else "",
                    })
                return results
        except Exception as e:
            logging.error(f"Ошибка при поиске по сообщениям пользователя {username}: {e}")
            raise

    async def ensure_sidebar_meta_for_user(self, username: str):
        """Создаёт дефолтные метаданные сайдбара для всех чатов пользователя."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, renamed_title)
                SELECT DISTINCT c.chat_id, c.user_id, FALSE, FALSE, NULL
                FROM chats c
                WHERE c.user_id = (SELECT id FROM users WHERE username = $1)
                ON CONFLICT (chat_id, user_id) DO NOTHING
            """, username)

    async def mark_chat_deleted(self, chat_id: str, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, renamed_title)
                VALUES ($1, $2, TRUE, FALSE, NULL)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP
            """, chat_id, user_id)
            await conn.execute("""
                UPDATE user_settings
                SET active_chat_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $1 AND active_chat_id = $2
            """, user_id, str(chat_id))

    async def set_chat_auto_title(self, chat_id: str, user_id: int, auto_title: str):
        """Записывает автогенерированный заголовок чата (только если ещё не задан).

        Не затирает ручной renamed_title и не перезаписывает уже существующий
        auto_title (заголовок генерится ровно один раз, по первому обмену).
        Возвращает итоговый auto_title (записанный или уже бывший) либо None.
        """
        if not auto_title or user_id is None or chat_id is None:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, auto_title)
                VALUES ($1, $2, FALSE, FALSE, $3)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET auto_title = COALESCE(chat_sidebar_meta.auto_title, EXCLUDED.auto_title),
                              updated_at = CURRENT_TIMESTAMP
                RETURNING auto_title
            """, chat_id, user_id, auto_title)
            return row["auto_title"] if row else None

    async def rename_chat(self, chat_id: str, user_id: int, new_title: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, renamed_title)
                VALUES ($1, $2, FALSE, FALSE, $3)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET renamed_title = $3, updated_at = CURRENT_TIMESTAMP
            """, chat_id, user_id, new_title)

    async def set_chat_favorite(self, chat_id: str, user_id: int, is_favorite: bool):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, renamed_title)
                VALUES ($1, $2, FALSE, $3, NULL)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET is_favorite = $3, updated_at = CURRENT_TIMESTAMP
            """, chat_id, user_id, is_favorite)

    async def get_user_chats_summary(self, username: str):
        """Возвращает список чатов пользователя с их последними сообщениями и метаданными сайдбара."""
        try:
            await self.ensure_sidebar_meta_for_user(username)
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    WITH target_user AS (
                        SELECT id AS user_id FROM users WHERE username = $1
                    ),
                    user_chats AS (
                        SELECT DISTINCT c.chat_id, c.user_id
                        FROM chats c
                        JOIN target_user tu ON tu.user_id = c.user_id
                    )
                    SELECT DISTINCT ON (c.chat_id)
                        c.chat_id AS id,
                        c.message_text AS last_message,
                        c.created_at AS timestamp,
                        COALESCE(m.is_deleted, FALSE) AS is_deleted,
                        COALESCE(m.is_favorite, FALSE) AS is_favorite,
                        m.renamed_title AS renamed_title,
                        (
                            SELECT am.auto_title
                            FROM chat_sidebar_meta am
                            WHERE am.chat_id = c.chat_id
                              AND am.auto_title IS NOT NULL
                            ORDER BY am.updated_at DESC
                            LIMIT 1
                        ) AS auto_title
                    FROM chats c
                    JOIN user_chats uc ON c.chat_id = uc.chat_id AND c.user_id = uc.user_id
                    LEFT JOIN chat_sidebar_meta m ON m.chat_id = uc.chat_id AND m.user_id = uc.user_id
                    WHERE COALESCE(m.is_deleted, FALSE) = FALSE
                    ORDER BY c.chat_id, c.created_at DESC
                """, username)

                return [
                    {
                        "id": row["id"],
                        "last_message": row["last_message"],
                        "timestamp": row["timestamp"].isoformat(),
                        "is_deleted": row["is_deleted"],
                        "is_favorite": row["is_favorite"],
                        "renamed_title": row["renamed_title"],
                        "auto_title": row["auto_title"]
                    }
                    for row in rows
                ]
        except Exception as e:
            logging.error(f"Ошибка при получении списка чатов пользователя {username}: {e}")
            raise

    async def get_user_chats_summary_by_user_id(
        self, user_id: int, chat_id: str
    ) -> list[dict]:
        """Возвращает список сессий пользователя для лички или конкретной группы Telegram."""
        try:
            target_chat_id = str(chat_id)

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, last_message, timestamp, renamed_title, auto_title
                    FROM (
                        SELECT DISTINCT ON (c.chat_id)
                            c.chat_id      AS id,
                            c.message_text AS last_message,
                            c.created_at   AS timestamp,
                            m.renamed_title AS renamed_title,
                            (
                                SELECT am.auto_title
                                FROM chat_sidebar_meta am
                                WHERE am.chat_id = c.chat_id
                                  AND am.auto_title IS NOT NULL
                                ORDER BY am.updated_at DESC
                                LIMIT 1
                            ) AS auto_title
                        FROM chats c
                        LEFT JOIN chat_sidebar_meta m
                            ON m.chat_id = c.chat_id
                        AND m.user_id = $1
                        WHERE COALESCE(m.is_deleted, FALSE) = FALSE
                        AND (
                            -- Группа: последнее сообщение этого чата от любого участника
                            ($2::text LIKE '-%' AND c.chat_id = $2::text)
                            OR
                            -- Личка: все личные чаты пользователя (только его сообщения)
                            ($2::text NOT LIKE '-%'
                                AND c.user_id = $1
                                AND c.chat_id ~ '^[0-9]+$')
                        )
                        ORDER BY c.chat_id, c.created_at DESC, c.id DESC
                    ) latest_messages
                    ORDER BY timestamp DESC
                    """,
                    user_id,
                    target_chat_id,
                )

                out = []
                for row in rows:
                    title = (
                        row["renamed_title"]
                        or row["auto_title"]
                        or row["last_message"]
                        or "Новый чат"
                    )

                    if len(title) > 40:
                        title = title[:37] + "..."

                    ts = int(row["timestamp"].timestamp()) if row["timestamp"] else 0

                    out.append({
                        "id": str(row["id"]),
                        "title": title,
                        "ts": ts,
                    })

                return out

        except Exception as e:
            logging.error(
                f"get_user_chats_summary_by_user_id fail user={user_id}, chat={chat_id}: {e}"
            )
            return []

    async def get_chat_messages(
        self,
        chat_id: str,
        username: str,
        limit: int = 50,
        offset: int = 0,
        around_id: str | None = None,
        radius: int = 15,
        before_id: int | None = None,
        after_id: int | None = None,
    ):
        """
        Возвращает страницу сообщений в чате для пользователя (и сообщения бота),
        вместе со списком прикреплённых файлов на каждое сообщение.

        Поддерживает:
          1) around_id + radius (по умолчанию ±15 сообщений вокруг найденного сообщения)
          2) before_id (дозагрузка более старых сообщений вверх от курсора id)
          3) after_id (дозагрузка более новых сообщений вниз от курсора id)
          4) стандартную пагинацию с конца (limit + offset)
        """
        try:
            async with self.pool.acquire() as conn:
                total_count = await conn.fetchval("""
                    SELECT COUNT(*)
                    FROM chats c
                    WHERE c.chat_id = $1
                        AND (
                            c.user_id = (SELECT id FROM users WHERE username = $2)
                            OR c.user_id = -1
                        )
                """, chat_id, username)

                base_select = """
                    SELECT 
                        c.id, 
                        c.user_id,
                        u.username as username,
                        c.chat_id, 
                        c.message_text, 
                        c.is_from_bot, 
                        c.message_id,
                        c.created_at,
                        c.reaction AS reaction,
                        COALESCE(
                            (
                                SELECT json_agg(
                                    json_build_object('name', f.file_name, 'ext', f.file_ext,
                                    'source', f.source, 'kind', f.file_kind, 'image_key', f.image_key,
                                    'size', f.file_size, 'thumbhash', f.thumbhash,
                                    'width', f.width, 'height', f.height)
                                    ORDER BY f.id ASC
                                )
                                FROM chat_files f
                                WHERE f.message_id = c.message_id
                            ),
                            '[]'::json
                        ) AS files,
                        COALESCE(
                            (
                                SELECT json_agg(
                                    json_build_object(
                                        'bot_message_id', p.bot_message_id,
                                        'poll_index', p.poll_index,
                                        'selected_index', p.selected_index,
                                        'selected_value', p.selected_value,
                                        'is_custom', p.is_custom,
                                        'is_skipped', p.is_skipped,
                                        'user_message_id', p.user_message_id
                                    )
                                    ORDER BY p.poll_index ASC
                                )
                                FROM chat_poll_answers p
                                WHERE p.chat_id = c.chat_id
                                  AND (p.bot_message_id = c.message_id OR p.bot_message_id = c.id::text)
                            ),
                            '[]'::json
                        ) AS poll_answers,
                        -- задачник: карточки задач, привязанные к ответу агента (tasks_db.TASKS_JSON_SQL)
                        COALESCE((
                            SELECT json_agg(json_build_object(
                                'task_id', t.task_id, 'label', t.label, 'kind', t.kind, 'status', t.status,
                                'stages', t.stages, 'progress', t.progress, 'elapsed_ms', t.elapsed_ms,
                                'exit_code', t.exit_code, 'error', t.error, 'stderr_tail', t.stderr_tail,
                                'stdout_tail', t.stdout_tail, 'files', t.files, 'workspace_id', t.workspace_id,
                                'created_at', t.created_at, 'finished_at', t.finished_at
                            ) ORDER BY t.created_at)
                            FROM agent_tasks t
                            WHERE t.chat_id = c.chat_id AND t.message_id = c.message_id
                        ), '[]'::json) AS tasks
                    FROM chats c
                    LEFT JOIN users u ON c.user_id = u.id
                    WHERE c.chat_id = $1 
                        AND (
                            c.user_id = (SELECT id FROM users WHERE username = $2)
                            OR c.user_id = -1
                        )
                """

                target_db_id = None
                target_msg_id = None
                rows = None
                mode = "default"

                if around_id:
                    target_str = str(around_id).strip()
                    target_row = await conn.fetchrow("""
                        SELECT c.id, c.message_id
                        FROM chats c
                        WHERE c.chat_id = $1
                            AND (
                                c.user_id = (SELECT id FROM users WHERE username = $2)
                                OR c.user_id = -1
                            )
                            AND (
                                c.message_id = $3
                                OR ($3 ~ '^[0-9]+$' AND c.id = $3::bigint)
                            )
                        ORDER BY c.id DESC
                        LIMIT 1
                    """, chat_id, username, target_str)
                    if target_row:
                        mode = "around"
                        target_db_id = int(target_row["id"])
                        target_msg_id = target_row["message_id"] or str(target_db_id)
                        rad = max(1, min(int(radius or 15), 100))
                        rows = await conn.fetch(f"""
                            SELECT * FROM (
                                (
                                    {base_select}
                                      AND c.id < $3
                                    ORDER BY c.id DESC
                                    LIMIT $4
                                )
                                UNION ALL
                                (
                                    {base_select}
                                      AND c.id >= $3
                                    ORDER BY c.id ASC
                                    LIMIT $5
                                )
                            ) sub
                            ORDER BY sub.created_at ASC, sub.id ASC
                        """, chat_id, username, target_db_id, rad, rad + 1)

                if rows is None and before_id is not None:
                    mode = "before"
                    rows = await conn.fetch(f"""
                        SELECT * FROM (
                            {base_select}
                              AND c.id < $3
                            ORDER BY c.id DESC
                            LIMIT $4
                        ) sub
                        ORDER BY sub.created_at ASC, sub.id ASC
                    """, chat_id, username, int(before_id), limit)

                if rows is None and after_id is not None:
                    mode = "after"
                    rows = await conn.fetch(f"""
                        SELECT * FROM (
                            {base_select}
                              AND c.id > $3
                            ORDER BY c.id ASC
                            LIMIT $4
                        ) sub
                        ORDER BY sub.created_at ASC, sub.id ASC
                    """, chat_id, username, int(after_id), limit)

                if rows is None:
                    rows = await conn.fetch(f"""
                        SELECT * FROM (
                            {base_select}
                            ORDER BY c.created_at DESC, c.id DESC
                            LIMIT $3 OFFSET $4
                        ) sub
                        ORDER BY sub.created_at ASC, sub.id ASC
                    """, chat_id, username, limit, offset)

                messages = []
                for row in rows:
                    files = row["files"]
                    if isinstance(files, str):
                        files = json.loads(files)
                    poll_answers = row["poll_answers"]
                    if isinstance(poll_answers, str):
                        poll_answers = json.loads(poll_answers)
                    tasks = row["tasks"]
                    if isinstance(tasks, str):
                        tasks = json.loads(tasks)

                    messages.append({
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "chat_id": row["chat_id"],
                        "message": row["message_text"],
                        "is_from_bot": row["is_from_bot"],
                        "message_id": row["message_id"],
                        "created_at": row["created_at"].isoformat(),
                        "reaction": row["reaction"],
                        "files": files or [],
                        "poll_answers": poll_answers or [],
                        "tasks": tasks or [],
                    })

                if mode in ("around", "before", "after"):
                    if messages:
                        oldest_id = int(messages[0]["id"])
                        newest_id = int(messages[-1]["id"])
                        has_more = bool(await conn.fetchval("""
                            SELECT EXISTS(
                                SELECT 1 FROM chats c
                                WHERE c.chat_id = $1
                                  AND (c.user_id = (SELECT id FROM users WHERE username = $2) OR c.user_id = -1)
                                  AND c.id < $3
                            )
                        """, chat_id, username, oldest_id))
                        has_newer = bool(await conn.fetchval("""
                            SELECT EXISTS(
                                SELECT 1 FROM chats c
                                WHERE c.chat_id = $1
                                  AND (c.user_id = (SELECT id FROM users WHERE username = $2) OR c.user_id = -1)
                                  AND c.id > $3
                            )
                        """, chat_id, username, newest_id))
                    else:
                        has_more = False
                        has_newer = False
                else:
                    has_more = (offset + len(messages)) < (total_count or 0)
                    has_newer = offset > 0

                return {
                    "messages": messages,
                    "total_count": total_count or 0,
                    "has_more": has_more,
                    "has_newer": has_newer,
                    "target_id": target_db_id,
                    "target_message_id": target_msg_id,
                }
        except Exception as e:
            logging.error(f"Ошибка при получении сообщений чата {chat_id}: {e}")
            raise

    async def get_user_messages(self, user_id: int, limit: int = 50, offset: int = 0):
        """
        Получение всех сообщений пользователя (по всем чатам)
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        id,
                        user_id,
                        chat_id,
                        message_text as message,
                        is_from_bot,
                        created_at as timestamp
                    FROM chats
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                """, user_id, limit, offset)

                return [dict(row) for row in rows]

        except Exception as e:
            logging.error(f"Ошибка получения сообщений пользователя {user_id}: {e}")
            return []

    async def create_new_chat(self) -> int:
        async with self.pool.acquire() as conn:
            chat_id = await conn.fetchval("""
                SELECT COALESCE(MAX(id), 0) + 1 FROM chats
            """)
            return chat_id

    async def get_stuck_messages(self, older_than_seconds: int):
        return await self.pool.fetch("""
            SELECT id, chat_id, user_id
            FROM chats
            WHERE is_from_bot = FALSE
              AND status = 'PROCESSING'
              AND created_at < now() - interval '%s seconds'
              AND created_at >= '2025-12-15'
        """ % older_than_seconds)

    async def mark_message_failed(self, message_id: int):
        await self.pool.execute("""
            UPDATE chats
            SET status = 'FAILED'
            WHERE id = $1
        """, message_id)

    async def set_chat_type(self, chat_id: str, user_id: int, type_agent: str):
        """Привязывает тип агента к чату."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_types_agent (chat_id, user_id, type_agent)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET type_agent = $3
            """, chat_id, user_id, type_agent)

    async def get_chat_type(self, chat_id: str, user_id: int) -> str | None:
        """Возвращает тип агента для чата или None."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT type_agent FROM chat_types_agent WHERE chat_id = $1 AND user_id = $2
            """, chat_id, user_id)

    async def get_overlay_stats(self, top_n: int = 3):
        """Агрегаты для оверлея онлайна за текущие сутки (UTC).
        Топ-N пользователей по числу отправленных сообщений (не бот).
        Гейтвей подмешивает сюда online/peak из Redis."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT
                        c.user_id,
                        COALESCE(u.username, 'User ' || c.user_id::text) AS username,
                        COUNT(*) AS msg_count
                    FROM chats c
                    LEFT JOIN users u ON c.user_id = u.id
                    WHERE c.is_from_bot = FALSE
                      AND c.user_id <> -1
                      AND c.gateway_target IS DISTINCT FROM 'telegram'
                      AND c.created_at >= date_trunc('day', now())
                    GROUP BY c.user_id, u.username
                    ORDER BY msg_count DESC
                    LIMIT $1
                """, top_n)
                total = await conn.fetchval("""
                    SELECT COUNT(*) FROM chats
                    WHERE is_from_bot = FALSE
                      AND user_id <> -1
                      AND gateway_target IS DISTINCT FROM 'telegram'
                      AND created_at >= date_trunc('day', now())
                """)
                return {
                    "top_users": [
                        {"username": r["username"], "messages": int(r["msg_count"])}
                        for r in rows
                    ],
                    "messages_today": int(total or 0),
                }
        except Exception as e:
            logging.error(f"Ошибка в get_overlay_stats: {e}")
            return {"top_users": [], "messages_today": 0}

    async def find_chat_by_type(self, user_id: int, type_agent: str) -> int | None:
        """Находит существующий чат пользователя с заданным типом агента."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT chat_id FROM chat_types_agent WHERE user_id = $1 AND type_agent = $2
                ORDER BY chat_id DESC LIMIT 1
            """, user_id, type_agent)

    @staticmethod
    def _normalize_setting_model(model: str | None) -> str:
        raw = str(model or "").strip().lower()
        mapping = {
            "coder": "gpt",
            "gpt": "gpt",
            "designer": "claude",
            "claude": "claude",
            "universal": "glm",
            "glm": "glm",
            "gemini": "gemini",
            "gigachat": "gigachat",
            "giga": "gigachat",
            "deepseek": "deepseek",
            "ds": "deepseek",
        }
        return mapping.get(raw, "gpt")

    async def upsert_user_settings(
        self,
        user_id: int,
        selected_tools: list | None = None,
        selected_model: str | None = None,
        active_chat_id: str | None = None,
        has_active_chat: bool = False,
    ) -> dict:
        uid = int(user_id)
        norm_tools = None
        if selected_tools is not None:
            norm_tools = [str(t).strip() for t in selected_tools if str(t).strip()]
        norm_model = self._normalize_setting_model(selected_model) if selected_model is not None else None
        norm_chat = (str(active_chat_id).strip() or None) if active_chat_id is not None else None

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT selected_tools, selected_model, active_chat_id FROM user_settings WHERE user_id = $1",
                uid,
            )
            cur_tools = []
            cur_model = "gpt"
            cur_chat = None
            if row:
                raw_t = row["selected_tools"]
                if isinstance(raw_t, str):
                    try:
                        cur_tools = json.loads(raw_t)
                    except Exception:
                        cur_tools = []
                elif isinstance(raw_t, list):
                    cur_tools = raw_t
                cur_model = self._normalize_setting_model(row["selected_model"])
                cur_chat = row["active_chat_id"]

            final_tools = norm_tools if norm_tools is not None else cur_tools
            final_model = norm_model if norm_model is not None else cur_model
            final_chat = norm_chat if has_active_chat else cur_chat

            saved = await conn.fetchrow(
                """
                INSERT INTO user_settings (user_id, selected_tools, selected_model, active_chat_id, updated_at)
                VALUES ($1, $2::jsonb, $3, $4, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET
                    selected_tools = EXCLUDED.selected_tools,
                    selected_model = EXCLUDED.selected_model,
                    active_chat_id = EXCLUDED.active_chat_id,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING user_id, selected_tools, selected_model, active_chat_id
                """,
                uid,
                json.dumps(final_tools, ensure_ascii=False),
                final_model,
                final_chat,
            )
            res_tools = saved["selected_tools"]
            if isinstance(res_tools, str):
                try:
                    res_tools = json.loads(res_tools)
                except Exception:
                    res_tools = []
            return {
                "user_id": int(saved["user_id"]),
                "selected_tools": res_tools if isinstance(res_tools, list) else [],
                "selected_model": self._normalize_setting_model(saved["selected_model"]),
                "active_chat_id": saved["active_chat_id"],
            }

    async def get_user_settings(self, user_id: int) -> dict:
        uid = int(user_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, selected_tools, selected_model, active_chat_id FROM user_settings WHERE user_id = $1",
                uid,
            )
            if not row:
                return {
                    "user_id": uid,
                    "selected_tools": [],
                    "selected_model": "gpt",
                    "active_chat_id": None,
                }
            tools = row["selected_tools"]
            if isinstance(tools, str):
                try:
                    tools = json.loads(tools)
                except Exception:
                    tools = []
            model = self._normalize_setting_model(row["selected_model"])
            active_chat_id = str(row["active_chat_id"]).strip() if row["active_chat_id"] else None

            if active_chat_id:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM chats c
                        LEFT JOIN chat_sidebar_meta m
                          ON m.chat_id = c.chat_id AND m.user_id = c.user_id
                        WHERE c.user_id = $1
                          AND c.chat_id = $2
                          AND COALESCE(m.is_deleted, FALSE) = FALSE
                    )
                    """,
                    uid,
                    active_chat_id,
                )
                if not exists:
                    active_chat_id = None
                    await conn.execute(
                        "UPDATE user_settings SET active_chat_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_id = $1",
                        uid,
                    )

            return {
                "user_id": uid,
                "selected_tools": tools if isinstance(tools, list) else [],
                "selected_model": model,
                "active_chat_id": active_chat_id,
            }

    async def _fetch_clean_selected_skill_ids(self, conn, uid: int, max_selected: int = 5) -> list[int]:
        await conn.execute(
            """
            DELETE FROM agent_skill_selected ss
            WHERE ss.user_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM agent_skills s
                  WHERE s.id = ss.skill_id AND s.is_deleted = FALSE
              )
            """,
            uid,
        )
        rows = await conn.fetch(
            """
            SELECT ss.skill_id
            FROM agent_skill_selected ss
            JOIN agent_skills s ON s.id = ss.skill_id
            WHERE ss.user_id = $1
              AND s.is_deleted = FALSE
            ORDER BY ss.created_at ASC
            LIMIT $2
            """,
            uid,
            int(max_selected),
        )
        return [int(r["skill_id"]) for r in rows]

    async def get_selected_skill_ids(self, user_id: int, conn=None) -> list[int]:
        uid = int(user_id)
        if conn is not None:
            return await self._fetch_clean_selected_skill_ids(conn, uid)
        async with self.pool.acquire() as c:
            return await self._fetch_clean_selected_skill_ids(c, uid)

    async def apply_skill_selection(
        self,
        user_id: int,
        skill_id: int,
        selected: bool | None = None,
        selected_ids_hint: list[int] | None = None,
        max_selected: int = 5,
    ) -> dict:
        uid = int(user_id)
        sid = int(skill_id)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    DELETE FROM agent_skill_selected ss
                    WHERE ss.user_id = $1
                      AND NOT EXISTS (
                          SELECT 1 FROM agent_skills s
                          WHERE s.id = ss.skill_id AND s.is_deleted = FALSE
                      )
                    """,
                    uid,
                )

                skill_exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skills WHERE id = $1 AND is_deleted = FALSE",
                    sid,
                )
                if not skill_exists:
                    await conn.execute(
                        "DELETE FROM agent_skill_selected WHERE skill_id = $1 AND user_id = $2",
                        sid,
                        uid,
                    )
                    selected_ids = await self._fetch_clean_selected_skill_ids(conn, uid, max_selected)
                    return {
                        "ok": False,
                        "reason": "not_found",
                        "skill_id": sid,
                        "selected": False,
                        "selected_ids": selected_ids,
                        "count": len(selected_ids),
                        "max_selected": max_selected,
                    }

                exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skill_selected WHERE skill_id = $1 AND user_id = $2",
                    sid,
                    uid,
                )
                target = (not bool(exists)) if selected is None else bool(selected)

                if not target:
                    await conn.execute(
                        "DELETE FROM agent_skill_selected WHERE skill_id = $1 AND user_id = $2",
                        sid,
                        uid,
                    )
                    is_selected = False
                else:
                    if not exists:
                        cnt = await conn.fetchval(
                            """
                            SELECT COUNT(*)::int
                            FROM agent_skill_selected ss
                            JOIN agent_skills s ON s.id = ss.skill_id
                            WHERE ss.user_id = $1
                              AND s.is_deleted = FALSE
                            """,
                            uid,
                        )
                        if int(cnt or 0) >= int(max_selected):
                            selected_ids = await self._fetch_clean_selected_skill_ids(conn, uid, max_selected)
                            return {
                                "ok": False,
                                "reason": "limit",
                                "skill_id": sid,
                                "selected": False,
                                "selected_ids": selected_ids,
                                "count": len(selected_ids),
                                "max_selected": max_selected,
                            }
                    await conn.execute(
                        """
                        INSERT INTO agent_skill_selected (skill_id, user_id)
                        VALUES ($1, $2)
                        ON CONFLICT (skill_id, user_id)
                        DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                        """,
                        sid,
                        uid,
                    )
                    is_selected = True

                if isinstance(selected_ids_hint, list):
                    clean_hint = []
                    for x in selected_ids_hint:
                        try:
                            xi = int(x)
                            if xi > 0 and xi not in clean_hint:
                                clean_hint.append(xi)
                        except (TypeError, ValueError):
                            pass
                    if len(clean_hint) == 0 and not is_selected:
                        await conn.execute(
                            "DELETE FROM agent_skill_selected WHERE user_id = $1",
                            uid,
                        )

                selected_ids = await self._fetch_clean_selected_skill_ids(conn, uid, max_selected)
                return {
                    "ok": True,
                    "skill_id": sid,
                    "selected": is_selected,
                    "selected_ids": selected_ids,
                    "count": len(selected_ids),
                    "max_selected": max_selected,
                }



    async def get_liked_skill_ids(self, user_id: int, conn=None) -> list[int]:
        uid = int(user_id)
        if conn is not None:
            return await self._fetch_clean_liked_skill_ids(conn, uid)
        async with self.pool.acquire() as c:
            return await self._fetch_clean_liked_skill_ids(c, uid)

    async def _fetch_clean_liked_skill_ids(self, conn, uid: int) -> list[int]:
        await conn.execute(
            """
            DELETE FROM agent_skill_likes sl
            WHERE sl.user_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM agent_skills s
                  WHERE s.id = sl.skill_id AND s.is_deleted = FALSE
              )
            """,
            uid,
        )
        rows = await conn.fetch(
            """
            SELECT sl.skill_id
            FROM agent_skill_likes sl
            JOIN agent_skills s ON s.id = sl.skill_id
            WHERE sl.user_id = $1
              AND s.is_deleted = FALSE
            ORDER BY sl.created_at ASC
            """,
            uid,
        )
        return [int(r["skill_id"]) for r in rows]

    async def apply_skill_like(self, user_id: int, skill_id: int, liked: bool | None = None) -> dict:
        uid = int(user_id)
        sid = int(skill_id)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    DELETE FROM agent_skill_likes sl
                    WHERE sl.user_id = $1
                      AND NOT EXISTS (
                          SELECT 1 FROM agent_skills s
                          WHERE s.id = sl.skill_id AND s.is_deleted = FALSE
                      )
                    """,
                    uid,
                )

                skill_exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skills WHERE id = $1 AND is_deleted = FALSE",
                    sid,
                )
                if not skill_exists:
                    await conn.execute(
                        "DELETE FROM agent_skill_likes WHERE skill_id = $1 AND user_id = $2",
                        sid, uid,
                    )
                    liked_ids = await self._fetch_clean_liked_skill_ids(conn, uid)
                    return {
                        "ok": False,
                        "reason": "not_found",
                        "skill_id": sid,
                        "liked": False,
                        "liked_ids": liked_ids,
                        "likes_count": 0,
                    }

                exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skill_likes WHERE skill_id = $1 AND user_id = $2",
                    sid, uid,
                )
                target = (not bool(exists)) if liked is None else bool(liked)

                if not target:
                    await conn.execute(
                        "DELETE FROM agent_skill_likes WHERE skill_id = $1 AND user_id = $2",
                        sid, uid,
                    )
                    is_liked = False
                else:
                    if not exists:
                        await conn.execute(
                            """
                            INSERT INTO agent_skill_likes (skill_id, user_id)
                            VALUES ($1, $2)
                            ON CONFLICT (skill_id, user_id) DO NOTHING
                            """,
                            sid, uid,
                        )
                    is_liked = True

                likes_count = await conn.fetchval(
                    """
                    UPDATE agent_skills 
                    SET likes_count = (SELECT COUNT(*)::int FROM agent_skill_likes WHERE skill_id = $1),
                        updated_at = NOW()
                    WHERE id = $1
                    RETURNING likes_count
                    """,
                    sid
                )

                liked_ids = await self._fetch_clean_liked_skill_ids(conn, uid)
                return {
                    "ok": True,
                    "skill_id": sid,
                    "liked": is_liked,
                    "liked_ids": liked_ids,
                    "likes_count": likes_count,
                }
