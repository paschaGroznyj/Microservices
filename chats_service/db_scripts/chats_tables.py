import asyncio
import asyncpg
from utils.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
import logging

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
                    min_size=1,
                    max_size=10
                )

                # Инициализация структуры базы данных
                async with self.pool.acquire() as conn:
                    await DataBaseChats.__init_db(conn)


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
                username INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_text TEXT,
                is_from_bot BOOLEAN,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_chats_username ON chats(username);
            CREATE INDEX IF NOT EXISTS ix_chats_chat_id ON chats(chat_id);
            """
                           )
    async def add_message(self, username: int, message: str, chat_id: int, is_from_bot: bool):
        if username is None or chat_id is None:
            logging.warning("Пропущено сообщение: отсутствует user_id или chat_id")
            return None
        try:
            async with self.pool.acquire() as conn:
               await conn.fetchval("""
                    INSERT INTO chats (username, chat_id, message_text, is_from_bot)
                    VALUES ($1, $2, $3, $4);
                """,
                username, chat_id, message, is_from_bot)
        except asyncpg.UniqueViolationError:
            logging.error(f"Сообщение не может быть добавлено")

    async def get_all_user_messages(self, username: str, limit: int = 50, offset: int = 0):
        """
        Возвращает ВСЕ сообщения пользователя во ВСЕХ чатах
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        c.id, 
                        c.username as user_id,
                        u.username,
                        c.chat_id, 
                        c.message_text as message, 
                        c.is_from_bot, 
                        c.created_at as timestamp
                    FROM chats c
                    LEFT JOIN users u ON c.username = u.id
                    WHERE c.chat_id IN (
                        SELECT DISTINCT chat_id 
                        FROM chats 
                        WHERE username = (SELECT id FROM users WHERE username = $1)
                    )
                    ORDER BY c.created_at ASC
                    LIMIT $2 OFFSET $3
                """, username, limit, offset)

                messages = []
                for row in rows:
                    # Определяем имя отправителя
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

    # async def get_chat_messages(self, chat_id: int, limit: int = 50, offset: int = 0):
    #     """
    #     Возвращает список всех сообщений в чате (и от юзера, и от бота).
    #     """
    #     try:
    #         async with self.pool.acquire() as conn:
    #             rows = await conn.fetch("""
    #                 SELECT id, username AS user_id, chat_id, message_text, is_from_bot, created_at
    #                 FROM chats
    #                 WHERE chat_id = $1
    #                 ORDER BY created_at ASC
    #                 LIMIT $2 OFFSET $3
    #             """, chat_id, limit, offset)
    #
    #             # Преобразуем в список словарей
    #             messages = []
    #             for row in rows:
    #                 messages.append({
    #                     "id": row["id"],
    #                     "user_id": row["user_id"],
    #                     "chat_id": row["chat_id"],
    #                     "message": row["message_text"],
    #                     "is_from_bot": row["is_from_bot"],
    #                     "created_at": row["created_at"].isoformat()
    #                 })
    #             return messages
    #     except Exception as e:
    #         logging.error(f"Ошибка при получении сообщений чата {chat_id}: {e}")
    #         raise

    async def get_chat_messages(self, chat_id: int, username: str, limit: int = 50, offset: int = 0):
        """
        Возвращает сообщения в чате для пользователя (и сообщения бота)
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT 
                        c.id, 
                        c.username as user_id,
                        u.username as username,
                        c.chat_id, 
                        c.message_text, 
                        c.is_from_bot, 
                        c.created_at
                    FROM chats c
                    LEFT JOIN users u ON c.username = u.id
                    WHERE c.chat_id = $1 
                        AND (
                            c.username = (SELECT id FROM users WHERE username = $2)
                            OR c.username = -1
                        )
                    ORDER BY c.created_at ASC
                    LIMIT $3 OFFSET $4
                """, chat_id, username, limit, offset)

                messages = []
                for row in rows:
                    messages.append({
                        "id": row["id"],
                        "user_id": row["user_id"],
                        "chat_id": row["chat_id"],
                        "message": row["message_text"],
                        "is_from_bot": row["is_from_bot"],
                        "created_at": row["created_at"].isoformat()
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
                    FROM chats_messages
                    WHERE user_id = $1
                    ORDER BY created_at DESC  -- Сначала новые
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
            SELECT id, chat_id, username
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


