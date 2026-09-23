import asyncio
import asyncpg
from app.core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
import logging
from datetime import datetime, timezone
import json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id TEXT NOT NULL,
                message_text TEXT,
                is_from_bot BOOLEAN,
                tool_name TEXT,
                tool_trace TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Миграция: если таблица уже существовала со старой колонкой username
            DO $$ 
            BEGIN 
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='chats' AND column_name='username'
                ) THEN
                    ALTER TABLE chats RENAME COLUMN username TO user_id;
                END IF;
            END $$;

            ALTER TABLE chats ADD COLUMN IF NOT EXISTS tool_name TEXT;
            DROP INDEX IF EXISTS ix_chats_username;
            CREATE INDEX IF NOT EXISTS ix_chats_user_id ON chats(user_id);
            CREATE INDEX IF NOT EXISTS ix_chats_chat_id ON chats(chat_id);

            -- Таблица типов чатов (один чат — один тип агента)
            CREATE TABLE IF NOT EXISTS chat_types_agent (
                chat_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                type_agent TEXT NOT NULL DEFAULT 'pm',
                PRIMARY KEY (chat_id, user_id)
            );

            -- Метаданные для рендера левого сайдбара
            CREATE TABLE IF NOT EXISTS chat_sidebar_meta (
                chat_id TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
                renamed_title TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            );

            -- Автогенерируемый заголовок чата (по первому обмену human+ai).
            -- Приоритет отображения: renamed_title (ручной) > auto_title > last_message.
            ALTER TABLE chat_sidebar_meta ADD COLUMN IF NOT EXISTS auto_title TEXT;

            -- Миграция для id чатов
            ALTER TABLE chat_types_agent ALTER COLUMN chat_id TYPE BIGINT USING chat_id::BIGINT;
            ALTER TABLE chat_types_agent ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;

            ALTER TABLE chats ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;

            -- === Миграция chat_id -> TEXT (UUID v4) ===
            -- Числовой 6-значный chat_id давал коллизии; переходим на строковый UUID.
            -- Старые числовые id становятся строками "719729". Идемпотентно.
            DO $migr$
            BEGIN
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name='chats' AND column_name='chat_id') <> 'text' THEN
                    ALTER TABLE chats ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
                END IF;
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name='chat_types_agent' AND column_name='chat_id') <> 'text' THEN
                    ALTER TABLE chat_types_agent ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
                END IF;
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name='chat_sidebar_meta' AND column_name='chat_id') <> 'text' THEN
                    ALTER TABLE chat_sidebar_meta ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
                END IF;
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name='chat_files' AND column_name='chat_id') = 'bigint' THEN
                    ALTER TABLE chat_files ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
                END IF;
                IF (SELECT data_type FROM information_schema.columns
                    WHERE table_name='generated_images' AND column_name='chat_id') = 'bigint' THEN
                    ALTER TABLE generated_images ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
                END IF;
            END $migr$;

            ALTER TABLE chats ADD COLUMN IF NOT EXISTS selected_tools TEXT[];
            ALTER TABLE chats ADD COLUMN IF NOT EXISTS context_size TEXT;

            ALTER TABLE chats ADD COLUMN IF NOT EXISTS model_type VARCHAR(100);
            ALTER TABLE chats ADD COLUMN IF NOT EXISTS tokens_usage JSONB;
            ALTER TABLE chats ADD COLUMN IF NOT EXISTS gateway_target TEXT;

        """)

        await conn.execute("""
            ALTER TABLE chats ADD COLUMN IF NOT EXISTS message_id TEXT;
            CREATE INDEX IF NOT EXISTS ix_chats_message_id ON chats(message_id);

            CREATE TABLE IF NOT EXISTS chat_files (
                id SERIAL PRIMARY KEY,
                message_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                file_name TEXT NOT NULL,
                file_ext TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_chat_files_message_id ON chat_files(message_id);
            CREATE INDEX IF NOT EXISTS ix_chat_files_chat_id ON chat_files(chat_id);

            ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS source TEXT;
            ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS file_path TEXT;
            ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS file_kind TEXT;
            ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS image_key TEXT;
            CREATE INDEX IF NOT EXISTS ix_chat_files_source ON chat_files(source);

            ALTER TABLE chats ADD COLUMN IF NOT EXISTS tool_trace TEXT;

            -- Сгенерированные изображения (generate_image): бинарник + метаданные.
            -- Источник истины: БД. На диск ничего постоянного не сохраняем.
            CREATE TABLE IF NOT EXISTS generated_images (
                id SERIAL PRIMARY KEY,
                image_key UUID NOT NULL UNIQUE,
                chat_id TEXT,
                user_id BIGINT,
                message_id TEXT,
                file_name TEXT NOT NULL,
                mime_type TEXT,
                prompt TEXT NOT NULL,
                model TEXT,
                aspect_ratio TEXT,
                temperature REAL,
                image_data BYTEA NOT NULL,
                byte_size INTEGER,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );

            -- Миграции для уже существующей таблицы generated_images
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            ALTER TABLE generated_images ADD COLUMN IF NOT EXISTS image_key UUID;
            UPDATE generated_images SET image_key = gen_random_uuid() WHERE image_key IS NULL;
            ALTER TABLE generated_images ALTER COLUMN image_key SET NOT NULL;
            ALTER TABLE generated_images DROP COLUMN IF EXISTS file_path;

            CREATE UNIQUE INDEX IF NOT EXISTS ux_generated_images_image_key ON generated_images(image_key);
            CREATE INDEX IF NOT EXISTS ix_generated_images_chat_id ON generated_images(chat_id);
            CREATE INDEX IF NOT EXISTS ix_generated_images_message_id ON generated_images(message_id);
            CREATE INDEX IF NOT EXISTS ix_generated_images_created_at ON generated_images(created_at DESC);

            -- === ГРУППОВЫЕ ЧАТЫ: реестр участников обычного PM-чата ===
            -- Чат становится групповым, как только в нём есть хотя бы один активный
            -- участник (removed_at IS NULL). Привязка по chat_id-строке (UUID),
            -- т.к. chats.id — это id сообщения, а не карточки чата.
            CREATE TABLE IF NOT EXISTS chats_group (
                id          BIGSERIAL PRIMARY KEY,
                chat_id     TEXT   NOT NULL,
                user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                is_admin    BOOLEAN NOT NULL DEFAULT FALSE,
                added_by    BIGINT,
                added_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                removed_at  TIMESTAMPTZ,
                UNIQUE (chat_id, user_id)
            );
            CREATE INDEX IF NOT EXISTS ix_chats_group_chat ON chats_group(chat_id);
            CREATE INDEX IF NOT EXISTS ix_chats_group_user ON chats_group(user_id);
            CREATE INDEX IF NOT EXISTS ix_chats_group_active ON chats_group(chat_id) WHERE removed_at IS NULL;
        """)
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
                            tool_name, selected_tools, context_size, message_id, created_at, tool_trace, model_type, tokens_usage, gateway_target
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13);
                    """,
                    user_id, chat_id, message, is_from_bot,
                    action, selected_tools, context_size, message_id, now_utc, tool_trace, model_type, tokens_usage_json, gateway_target)

                    if attached_files:
                        source = "agent" if is_from_bot else "user"
                        await conn.executemany("""
                            INSERT INTO chat_files (message_id, chat_id, user_id, file_name, file_ext, source, file_path, file_kind, image_key)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);
                        """, [
                            (
                                message_id, chat_id, user_id,
                                f["name"], f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else None,
                                source, f.get("path"), f.get("kind"), f.get("image_key"),
                            )
                            for f in attached_files
                        ])
        except asyncpg.UniqueViolationError:
            logging.error("Сообщение не может быть добавлено (дубликат message_id)")

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
        Возвращает по одному лучшему совпадению на чат: chat_id, заголовок для
        сайдбара (renamed_title или превью), фрагмент найденного сообщения (snippet),
        флаг is_from_bot и время. ILIKE по message_text, группировка по chat_id.
        """
        try:
            q = (query or "").strip()
            if len(q) < 2:
                return []
            # экранируем спецсимволы LIKE, чтобы % и _ искались буквально
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
                        SELECT DISTINCT ON (c.chat_id)
                            c.chat_id, c.message_text, c.is_from_bot, c.created_at
                        FROM chats c
                        JOIN user_chats uc ON uc.chat_id = c.chat_id
                        LEFT JOIN chat_sidebar_meta m
                             ON m.chat_id = c.chat_id AND m.user_id = (SELECT user_id FROM target_user)
                        WHERE COALESCE(m.is_deleted, FALSE) = FALSE
                        AND c.message_text ILIKE $2 ESCAPE '\'
                        ORDER BY c.chat_id, c.created_at DESC
                    )
                    SELECT
                        mt.chat_id,
                        mt.message_text,
                        mt.is_from_bot,
                        mt.created_at,
                        meta.renamed_title,
                        (
                            SELECT c2.message_text
                            FROM chats c2
                            JOIN target_user tu2 ON tu2.user_id = c2.user_id
                            WHERE c2.chat_id = mt.chat_id
                            ORDER BY c2.created_at DESC
                            LIMIT 1
                        ) AS last_message
                    FROM matches mt
                    LEFT JOIN chat_sidebar_meta meta
                           ON meta.chat_id = mt.chat_id
                          AND meta.user_id = (SELECT user_id FROM target_user)
                    ORDER BY mt.created_at DESC
                    LIMIT $3
                """, username, pattern, limit)

                results = []
                for row in rows:
                    results.append({
                        "chat_id": row["chat_id"],
                        "renamed_title": row["renamed_title"],
                        "last_message": row["last_message"],
                        "snippet": row["message_text"],
                        "is_from_bot": row["is_from_bot"],
                        "timestamp": row["created_at"].isoformat(),
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
                    -- chat_id, где юзер — автор сообщений (личные + его собственные).
                    -- ВАЖНО: если чат групповой, а юзер там уже удалён
                    -- (нет активной записи в chats_group) — чат НЕ показываем,
                    -- даже если он раньше писал в него.
                    own_chats AS (
                        SELECT DISTINCT c.chat_id
                        FROM chats c
                        JOIN target_user tu ON tu.user_id = c.user_id
                        WHERE NOT EXISTS (
                            -- чат является групповым (есть активные участники)
                            SELECT 1 FROM chats_group gx
                            WHERE gx.chat_id = c.chat_id AND gx.removed_at IS NULL
                        )
                        OR EXISTS (
                            -- ... либо юзер сам активный участник этого группового
                            SELECT 1 FROM chats_group gy
                            WHERE gy.chat_id = c.chat_id
                              AND gy.user_id = tu.user_id
                              AND gy.removed_at IS NULL
                        )
                    ),
                    -- chat_id групповых чатов, куда юзер приглашён (активный участник)
                    group_chats AS (
                        SELECT DISTINCT g.chat_id
                        FROM chats_group g
                        JOIN target_user tu ON tu.user_id = g.user_id
                        WHERE g.removed_at IS NULL
                    ),
                    all_chat_ids AS (
                        SELECT chat_id FROM own_chats
                        UNION
                        SELECT chat_id FROM group_chats
                    )
                    SELECT DISTINCT ON (c.chat_id)
                        c.chat_id AS id,
                        c.message_text AS last_message,
                        c.created_at AS timestamp,
                        COALESCE(m.is_deleted, FALSE) AS is_deleted,
                        COALESCE(m.is_favorite, FALSE) AS is_favorite,
                        m.renamed_title AS renamed_title,
                        m.auto_title AS auto_title,
                        EXISTS(
                            SELECT 1 FROM chats_group g2
                            WHERE g2.chat_id = c.chat_id AND g2.removed_at IS NULL
                        ) AS is_group
                    FROM chats c
                    JOIN all_chat_ids uc ON c.chat_id = uc.chat_id
                    LEFT JOIN chat_sidebar_meta m
                        ON m.chat_id = c.chat_id
                       AND m.user_id = (SELECT user_id FROM target_user)
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
                        "auto_title": row["auto_title"],
                        "is_group": row["is_group"]
                    }
                    for row in rows
                ]
        except Exception as e:
            logging.error(f"Ошибка при получении списка чатов пользователя {username}: {e}")
            raise

    async def get_chat_messages(self, chat_id: str, username: str, limit: int = 50, offset: int = 0):
        """
        Возвращает страницу элементов ленты чата: сообщения + (для групповых
        чатов) системные события "добавлен/удалён участник" из chats_group.

        Все элементы имеют поле "kind":
            - "message"      — обычное сообщение (юзера или бота)
            - "group_event"  — плашка о добавлении/удалении участника
                               (поля actor, target, action=add|remove)

        Пагинация "с конца" по общему потоку (created_at DESC), затем разворот
        страницы в ASC для рендера. События вписаны в поток хронологически,
        поэтому не "выпадают" между страницами.

        Для ГРУППОВОГО чата фильтр по автору снят: участник видит сообщения
        ВСЕХ участников (и бота). Для личного — только свои + бот (-1).
        """
        try:
            async with self.pool.acquire() as conn:
                # Групповой? (есть активные участники)
                is_group = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM chats_group
                        WHERE chat_id = $1 AND removed_at IS NULL
                    )
                """, chat_id)

                # Условие видимости сообщений:
                #  - групповой чат -> все сообщения этого chat_id
                #  - личный        -> только свои (по username) + бот (-1)
                if is_group:
                    msg_visibility = "TRUE"
                    q_args_common = [chat_id]
                else:
                    msg_visibility = (
                        "(c.user_id = (SELECT id FROM users WHERE username = $2) "
                        "OR c.user_id = -1)"
                    )
                    q_args_common = [chat_id, username]

                # total_count: сообщения + group-события (только для групповых)
                if is_group:
                    total_count = await conn.fetchval(f"""
                        SELECT
                            (SELECT COUNT(*) FROM chats c
                                WHERE c.chat_id = $1 AND {msg_visibility})
                          + (SELECT COUNT(*) FROM (
                                SELECT added_at AS ts FROM chats_group
                                    WHERE chat_id = $1 AND added_at IS NOT NULL
                                          AND NOT (is_admin = TRUE)
                                          AND (added_by IS NULL OR added_by <> user_id)
                                UNION ALL
                                SELECT removed_at AS ts FROM chats_group
                                    WHERE chat_id = $1 AND removed_at IS NOT NULL
                            ) ev)
                    """, *q_args_common)
                else:
                    total_count = await conn.fetchval(f"""
                        SELECT COUNT(*) FROM chats c
                        WHERE c.chat_id = $1 AND {msg_visibility}
                    """, *q_args_common)

                # Единый поток: сообщения + события (UNION ALL), пагинация по ts DESC.
                # Плейсхолдеры limit/offset идут ПОСЛЕ common-аргументов.
                lim_ph = f"${len(q_args_common) + 1}"
                off_ph = f"${len(q_args_common) + 2}"

                if is_group:
                    union_sql = f"""
                        SELECT * FROM (
                            -- === СООБЩЕНИЯ ===
                            SELECT
                                'message'::text        AS kind,
                                c.id                   AS id,
                                c.user_id              AS user_id,
                                u.username             AS username,
                                c.chat_id              AS chat_id,
                                c.message_text         AS message_text,
                                c.is_from_bot          AS is_from_bot,
                                c.message_id           AS message_id,
                                c.created_at           AS ts,
                                NULL::text             AS ev_action,
                                NULL::text             AS ev_actor,
                                NULL::text             AS ev_target,
                                COALESCE(
                                    (SELECT json_agg(
                                        json_build_object('name', f.file_name, 'ext', f.file_ext,
                                        'source', f.source, 'kind', f.file_kind, 'image_key', f.image_key)
                                        ORDER BY f.id ASC)
                                     FROM chat_files f WHERE f.message_id = c.message_id),
                                    '[]'::json
                                ) AS files
                            FROM chats c
                            LEFT JOIN users u ON c.user_id = u.id
                            WHERE c.chat_id = $1 AND {msg_visibility}

                            UNION ALL

                            -- === СОБЫТИЯ ДОБАВЛЕНИЯ ===
                            SELECT
                                'group_event'::text,
                                g.id,
                                g.user_id,
                                tu.username,
                                g.chat_id,
                                NULL::text,
                                FALSE,
                                NULL::text,
                                g.added_at,
                                'add'::text,
                                au.username,
                                tu.username,
                                '[]'::json
                            FROM chats_group g
                            LEFT JOIN users tu ON g.user_id = tu.id
                            LEFT JOIN users au ON g.added_by = au.id
                            WHERE g.chat_id = $1 AND g.added_at IS NOT NULL
                                  AND NOT (g.is_admin = TRUE)
                                  AND (g.added_by IS NULL OR g.added_by <> g.user_id)

                            UNION ALL

                            -- === СОБЫТИЯ УДАЛЕНИЯ ===
                            SELECT
                                'group_event'::text,
                                g.id,
                                g.user_id,
                                tu.username,
                                g.chat_id,
                                NULL::text,
                                FALSE,
                                NULL::text,
                                g.removed_at,
                                'remove'::text,
                                au.username,
                                tu.username,
                                '[]'::json
                            FROM chats_group g
                            LEFT JOIN users tu ON g.user_id = tu.id
                            LEFT JOIN users au ON g.added_by = au.id
                            WHERE g.chat_id = $1 AND g.removed_at IS NOT NULL
                        ) feed
                        ORDER BY feed.ts DESC, feed.id DESC
                        LIMIT {lim_ph} OFFSET {off_ph}
                    """
                    rows = await conn.fetch(union_sql, *q_args_common, limit, offset)
                else:
                    rows = await conn.fetch(f"""
                        SELECT * FROM (
                            SELECT
                                'message'::text        AS kind,
                                c.id                   AS id,
                                c.user_id              AS user_id,
                                u.username             AS username,
                                c.chat_id              AS chat_id,
                                c.message_text         AS message_text,
                                c.is_from_bot          AS is_from_bot,
                                c.message_id           AS message_id,
                                c.created_at           AS ts,
                                NULL::text             AS ev_action,
                                NULL::text             AS ev_actor,
                                NULL::text             AS ev_target,
                                COALESCE(
                                    (SELECT json_agg(
                                        json_build_object('name', f.file_name, 'ext', f.file_ext,
                                        'source', f.source, 'kind', f.file_kind, 'image_key', f.image_key)
                                        ORDER BY f.id ASC)
                                     FROM chat_files f WHERE f.message_id = c.message_id),
                                    '[]'::json
                                ) AS files
                            FROM chats c
                            LEFT JOIN users u ON c.user_id = u.id
                            WHERE c.chat_id = $1 AND {msg_visibility}
                            ORDER BY c.created_at DESC, c.id DESC
                            LIMIT {lim_ph} OFFSET {off_ph}
                        ) feed
                        ORDER BY feed.ts ASC, feed.id ASC
                    """, *q_args_common, limit, offset)

                # Для групповых после LIMIT/OFFSET по DESC разворачиваем в ASC вручную
                rows = list(rows)
                if is_group:
                    rows.sort(key=lambda r: (r["ts"], r["id"]))

                items = []
                for row in rows:
                    if row["kind"] == "group_event":
                        items.append({
                            "kind": "group_event",
                            "id": row["id"],
                            "chat_id": row["chat_id"],
                            "action": row["ev_action"],
                            "actor": row["ev_actor"] or "Кто-то",
                            "target": row["ev_target"] or "",
                            "created_at": row["ts"].isoformat() if row["ts"] else None,
                        })
                        continue

                    files = row["files"]
                    if isinstance(files, str):
                        files = json.loads(files)
                    items.append({
                        "kind": "message",
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "username": row["username"],
                        "chat_id": row["chat_id"],
                        "message": row["message_text"],
                        "is_from_bot": row["is_from_bot"],
                        "message_id": row["message_id"],
                        "created_at": row["ts"].isoformat() if row["ts"] else None,
                        "files": files or [],
                    })

                # Кто смотрит (для разграничения свой/чужой в групповом чате).
                viewer_user_id = await conn.fetchval(
                    "SELECT id FROM users WHERE username = $1", username
                )

                has_more = (offset + len(items)) < (total_count or 0)
                return {
                    "messages": items,
                    "total_count": total_count or 0,
                    "has_more": has_more,
                    "is_group": bool(is_group),
                    "viewer_user_id": viewer_user_id,
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
                SELECT COALESCE(MAX(chat_id), 0) + 1 FROM chats
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
                      AND c.created_at >= date_trunc('day', now())
                    GROUP BY c.user_id, u.username
                    ORDER BY msg_count DESC
                    LIMIT $1
                """, top_n)
                total = await conn.fetchval("""
                    SELECT COUNT(*) FROM chats
                    WHERE is_from_bot = FALSE
                      AND user_id <> -1
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

    async def get_user_id_by_username(self, username: str):
        """id по username или None."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT id FROM users WHERE username = $1", username)

    # ===================== ГРУППОВЫЕ ЧАТЫ =====================

    async def is_group_chat(self, chat_id: str) -> bool:
        """Чат групповой, если есть хотя бы один активный участник."""
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM chats_group
                    WHERE chat_id = $1 AND removed_at IS NULL
                )
            """, str(chat_id))
            return bool(val)

    async def get_group_members(self, chat_id: str):
        """Список активных участников группового чата с username и датами."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT g.user_id, u.username, g.is_admin, g.added_by, g.added_at
                FROM chats_group g
                LEFT JOIN users u ON u.id = g.user_id
                WHERE g.chat_id = $1 AND g.removed_at IS NULL
                ORDER BY g.is_admin DESC, g.added_at ASC
            """, str(chat_id))
            return [
                {
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "is_admin": r["is_admin"],
                    "added_by": r["added_by"],
                    "added_at": r["added_at"].isoformat() if r["added_at"] else None,
                }
                for r in rows
            ]

    async def get_group_admin_id(self, chat_id: str):
        """user_id админа группы (кто создал), или None."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT user_id FROM chats_group
                WHERE chat_id = $1 AND is_admin = TRUE AND removed_at IS NULL
                LIMIT 1
            """, str(chat_id))

    async def add_group_member(self, chat_id: str, user_id: int, added_by: int, is_admin: bool = False):
        """Добавляет участника. Если был удалён — реактивирует (removed_at=NULL)."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chats_group (chat_id, user_id, is_admin, added_by, added_at, removed_at)
                VALUES ($1, $2, $3, $4, CURRENT_TIMESTAMP, NULL)
                ON CONFLICT (chat_id, user_id) DO UPDATE
                    SET removed_at = NULL,
                        added_by = EXCLUDED.added_by,
                        added_at = CURRENT_TIMESTAMP,
                        is_admin = chats_group.is_admin OR EXCLUDED.is_admin
            """, str(chat_id), int(user_id), bool(is_admin), int(added_by))

    async def ensure_group_admin(self, chat_id: str, admin_user_id: int):
        """Гарантирует, что у чата есть админ. Вызывается при первом инвайте.
        Создатель чата — тот, кто первым пригласил; его пишем с is_admin=TRUE."""
        admin = await self.get_group_admin_id(chat_id)
        if admin is None:
            await self.add_group_member(chat_id, admin_user_id, admin_user_id, is_admin=True)
            return admin_user_id
        return admin

    async def remove_group_member(self, chat_id: str, user_id: int):
        """Софт-удаление участника (removed_at=now)."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE chats_group SET removed_at = CURRENT_TIMESTAMP
                WHERE chat_id = $1 AND user_id = $2 AND removed_at IS NULL
            """, str(chat_id), int(user_id))

    async def is_group_member(self, chat_id: str, user_id: int) -> bool:
        """Проверяет, активный ли участник чата."""
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM chats_group
                    WHERE chat_id = $1 AND user_id = $2 AND removed_at IS NULL
                )
            """, str(chat_id), int(user_id))
            return bool(val)

    async def can_post_to_chat(self, chat_id: str, user_id: int) -> bool:
        """
        Может ли пользователь писать в чат.

        Правила:
          - Групповой чат (есть активные участники): писать может ТОЛЬКО
            активный участник (removed_at IS NULL). Удалённый — нет.
          - Личный чат: писать может владелец (автор существующих сообщений)
            ИЛИ если в чате ещё нет ни одного сообщения (новый чат).
        """
        async with self.pool.acquire() as conn:
            is_group = await conn.fetchval("""
                SELECT EXISTS(
                    SELECT 1 FROM chats_group
                    WHERE chat_id = $1 AND removed_at IS NULL
                )
            """, str(chat_id))

            if is_group:
                return bool(await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM chats_group
                        WHERE chat_id = $1 AND user_id = $2 AND removed_at IS NULL
                    )
                """, str(chat_id), int(user_id)))

            # Личный чат: есть ли вообще сообщения от кого-то, кроме бота?
            owner_id = await conn.fetchval("""
                SELECT user_id FROM chats
                WHERE chat_id = $1 AND user_id <> -1
                ORDER BY created_at ASC
                LIMIT 1
            """, str(chat_id))
            if owner_id is None:
                return True  # новый/пустой чат — можно писать
            return int(owner_id) == int(user_id)

    async def search_users(self, query: str, exclude_chat_id: str = None, limit: int = 20):
        """Поиск юзеров по username. Пустой query — самые свежие зареги.
        Если задан exclude_chat_id — в ответе помечает уже добавленных (already_member)."""
        q = (query or "").strip()
        async with self.pool.acquire() as conn:
            if q:
                rows = await conn.fetch("""
                    SELECT id AS user_id, username, created_at
                    FROM users
                    WHERE username ILIKE $1
                    ORDER BY username ASC
                    LIMIT $2
                """, f"%{q}%", int(limit))
            else:
                rows = await conn.fetch("""
                    SELECT id AS user_id, username, created_at
                    FROM users
                    ORDER BY created_at DESC
                    LIMIT $1
                """, int(limit))

            members = set()
            if exclude_chat_id:
                mrows = await conn.fetch("""
                    SELECT user_id FROM chats_group
                    WHERE chat_id = $1 AND removed_at IS NULL
                """, str(exclude_chat_id))
                members = {r["user_id"] for r in mrows}

            return [
                {
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "already_member": r["user_id"] in members,
                }
                for r in rows
            ]

    async def get_group_chat_ids_for_user(self, user_id: int):
        """chat_id групповых чатов, где юзер — активный участник."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT chat_id FROM chats_group
                WHERE user_id = $1 AND removed_at IS NULL
            """, int(user_id))
            return [r["chat_id"] for r in rows]   