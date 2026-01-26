import asyncio
import asyncpg
from core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
import logging
from typing import List, Dict, Any
print(DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class DataBaseMessages:
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
                    await DataBaseMessages.__init_db(conn)


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
                CREATE TABLE IF NOT EXISTS telegram_messages (
                chat_id BIGINT NOT NULL,          -- ID чата в Telegram
                message_id BIGINT NOT NULL,       -- ID сообщения в Telegram в конкретном чате
                date TIMESTAMPTZ NOT NULL,        -- ISO 8601 с часовым поясом
                text TEXT, -- Текст сообщения
                is_reply BOOLEAN NOT NULL DEFAULT FALSE, -- Тег пересылки
                reply_to_msg_id BIGINT, -- ID сообщения пересылки
            
                -- Отправитель
                sender_id BIGINT, 
                sender_username TEXT,
                sender_first_name TEXT,
                sender_last_name TEXT,
            
                -- Пересылка
                fwd_chat_title TEXT,
                fwd_from_id BIGINT,               -- channel_id источника
                fwd_channel_post BIGINT,          -- ID поста в источнике
                fwd_url TEXT,                     -- https://t.me/c/
                fwd_date TIMESTAMPTZ,
            
                -- Ссылки из текста
                source_links TEXT[],              -- Парсим ссылки из текста
            
                -- Служебное
                created_at TIMESTAMPTZ DEFAULT NOW(),
                
                -- Первичный ключ: уникальность по чату + сообщению
                PRIMARY KEY (chat_id, message_id)
            );
            
            -- Индексы для быстрого поиска
            CREATE INDEX IF NOT EXISTS idx_telegram_messages_chat_id ON telegram_messages(chat_id);
            CREATE INDEX IF NOT EXISTS idx_telegram_messages_date ON telegram_messages(date);
            CREATE INDEX IF NOT EXISTS idx_telegram_messages_sender_id ON telegram_messages(sender_id);
            """
                           )

    async def add_messages(self, messages: List[Dict[str, Any]]):
        if not messages:
            return 0

        records = [] # Список для кортежей
        for msg in messages:
            sender = msg.get("sender") or {}
            fwd = msg.get("forwarded_from") or {}
            main_date = msg["date"]
            fwd_date = fwd.get("date")

            records.append((
                msg["chat_id"],
                msg["message_id"],
                main_date,
                msg.get("text"),
                msg.get("is_reply", False),
                msg.get("reply_to_msg_id"),
                sender.get("id"),
                sender.get("username"),
                sender.get("first_name"),
                sender.get("last_name"),
                fwd.get("chat_title"),
                fwd.get("from_id"),
                fwd.get("channel_post"),
                fwd.get("from_url"),
                fwd_date,
                msg.get("source_links", [])
            ))

        try:
            async with self.pool.acquire() as conn:
                # Массовая вставка
                result = await conn.executemany("""
                       INSERT INTO telegram_messages (
                           chat_id, message_id, date, text, is_reply, reply_to_msg_id,
                           sender_id, sender_username, sender_first_name, sender_last_name,
                           fwd_chat_title, fwd_from_id, fwd_channel_post, fwd_url, fwd_date,
                           source_links
                       ) VALUES (
                           $1, $2, $3, $4, $5, $6,
                           $7, $8, $9, $10,
                           $11, $12, $13, $14, $15,
                           $16
                       )
                       ON CONFLICT (chat_id, message_id) DO NOTHING;
                   """, records)


        except Exception as e:
            logging.error(f"Ошибка при вставке сообщений: {e}")
