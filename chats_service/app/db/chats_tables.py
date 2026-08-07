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
                chat_id INTEGER NOT NULL,
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
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                type_agent TEXT NOT NULL DEFAULT 'pm',
                PRIMARY KEY (chat_id, user_id)
            );

            -- Метаданные для рендера левого сайдбара
            CREATE TABLE IF NOT EXISTS chat_sidebar_meta (
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
                renamed_title TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            );

            -- Миграция для id чатов
            ALTER TABLE chat_types_agent ALTER COLUMN chat_id TYPE BIGINT USING chat_id::BIGINT;
            ALTER TABLE chat_types_agent ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;

            ALTER TABLE chats ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;
            ALTER TABLE chats ALTER COLUMN chat_id TYPE BIGINT USING chat_id::BIGINT;

            ALTER TABLE chats ADD COLUMN IF NOT EXISTS selected_tools TEXT[];
            ALTER TABLE chats ADD COLUMN IF NOT EXISTS context_size TEXT;

        """)

        await conn.execute("""
            ALTER TABLE chats ADD COLUMN IF NOT EXISTS message_id TEXT;
            CREATE INDEX IF NOT EXISTS ix_chats_message_id ON chats(message_id);

            CREATE TABLE IF NOT EXISTS chat_files (
                id SERIAL PRIMARY KEY,
                message_id TEXT NOT NULL,
                chat_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                file_name TEXT NOT NULL,
                file_ext TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_chat_files_message_id ON chat_files(message_id);
            CREATE INDEX IF NOT EXISTS ix_chat_files_chat_id ON chat_files(chat_id);

            ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS source TEXT;
            ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS file_path TEXT;
            CREATE INDEX IF NOT EXISTS ix_chat_files_source ON chat_files(source);

            ALTER TABLE chats ADD COLUMN IF NOT EXISTS tool_trace TEXT;
        """)
    async def add_message(
        self,
        user_id: int,
        message: str,
        chat_id: int,
        is_from_bot: bool,
        message_id: str | None = None,
        attached_files: list[dict] | None = None,
        action: str = None,
        selected_tools: list[str] | None = None,
        context_size: str | None = None,
        tool_trace: str | None = None
    ):
        if user_id is None or chat_id is None:
            logging.warning("Пропущено сообщение: отсутствует user_id или chat_id")
            return None

        now_utc = datetime.now(timezone.utc)
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("""
                        INSERT INTO chats (
                            user_id, chat_id, message_text, is_from_bot,
                            tool_name, selected_tools, context_size, message_id, created_at, tool_trace
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);
                    """,
                    user_id, chat_id, message, is_from_bot,
                    action, selected_tools, context_size, message_id, now_utc, tool_trace)

                    if attached_files:
                        source = "agent" if is_from_bot else "user"
                        await conn.executemany("""
                            INSERT INTO chat_files (message_id, chat_id, user_id, file_name, file_ext, source, file_path)
                            VALUES ($1, $2, $3, $4, $5, $6, $7);
                        """, [
                            (
                                message_id, chat_id, user_id,
                                f["name"], f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else None,
                                source, f.get("path"),
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

    async def mark_chat_deleted(self, chat_id: int, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, renamed_title)
                VALUES ($1, $2, TRUE, FALSE, NULL)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET is_deleted = TRUE, updated_at = CURRENT_TIMESTAMP
            """, chat_id, user_id)

    async def rename_chat(self, chat_id: int, user_id: int, new_title: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_sidebar_meta (chat_id, user_id, is_deleted, is_favorite, renamed_title)
                VALUES ($1, $2, FALSE, FALSE, $3)
                ON CONFLICT (chat_id, user_id)
                DO UPDATE SET renamed_title = $3, updated_at = CURRENT_TIMESTAMP
            """, chat_id, user_id, new_title)

    async def set_chat_favorite(self, chat_id: int, user_id: int, is_favorite: bool):
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
                        m.renamed_title AS renamed_title
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
                        "renamed_title": row["renamed_title"]
                    }
                    for row in rows
                ]
        except Exception as e:
            logging.error(f"Ошибка при получении списка чатов пользователя {username}: {e}")
            raise

    async def get_chat_messages(self, chat_id: int, username: str, limit: int = 50, offset: int = 0):
        """
        Возвращает сообщения в чате для пользователя (и сообщения бота),
        вместе со списком прикреплённых файлов на каждое сообщение.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        c.id, 
                        c.user_id,
                        u.username as username,
                        c.chat_id, 
                        c.message_text, 
                        c.is_from_bot, 
                        c.message_id,
                        c.created_at,
                        COALESCE(
                            (
                                SELECT json_agg(
                                    json_build_object('name', f.file_name, 'ext', f.file_ext,
                                    'source', f.source)
                                    ORDER BY f.id ASC
                                )
                                FROM chat_files f
                                WHERE f.message_id = c.message_id
                            ),
                            '[]'::json
                        ) AS files
                    FROM chats c
                    LEFT JOIN users u ON c.user_id = u.id
                    WHERE c.chat_id = $1 
                        AND (
                            c.user_id = (SELECT id FROM users WHERE username = $2)
                            OR c.user_id = -1
                        )
                    ORDER BY c.created_at ASC
                    LIMIT $3 OFFSET $4
                """, chat_id, username, limit, offset)

                messages = []
                for row in rows:
                    files = row["files"]
                    if isinstance(files, str):
                        files = json.loads(files)

                    messages.append({
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "chat_id": row["chat_id"],
                        "message": row["message_text"],
                        "is_from_bot": row["is_from_bot"],
                        "message_id": row["message_id"],
                        "created_at": row["created_at"].isoformat(),
                        "files": files or [],
                    })
                return messages
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

    async def set_chat_type(self, chat_id: int, user_id: int, type_agent: str):
        """Привязывает тип агента к чату."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO chat_types_agent (chat_id, user_id, type_agent)
                VALUES ($1, $2, $3)
                ON CONFLICT (chat_id, user_id) DO UPDATE SET type_agent = $3
            """, chat_id, user_id, type_agent)

    async def get_chat_type(self, chat_id: int, user_id: int) -> str | None:
        """Возвращает тип агента для чата или None."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT type_agent FROM chat_types_agent WHERE chat_id = $1 AND user_id = $2
            """, chat_id, user_id)

    async def find_chat_by_type(self, user_id: int, type_agent: str) -> int | None:
        """Находит существующий чат пользователя с заданным типом агента."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT chat_id FROM chat_types_agent WHERE user_id = $1 AND type_agent = $2
                ORDER BY chat_id DESC LIMIT 1
            """, user_id, type_agent)   