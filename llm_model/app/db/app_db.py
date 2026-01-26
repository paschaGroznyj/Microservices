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

            except Exception as e:
                print("Пока не удалось подключиться")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                else:
                    raise

    async def fetch_messages_for_rag(self, chat_ids: List[int]) -> List[Dict[str, Any]]:
        """Получает все сообщения для RAG по списку chat_id."""
        query = """
            SELECT 
                chat_id,
                message_id,
                date,
                text,
                sender_username,
                sender_first_name,
                sender_last_name,
                fwd_chat_title,
                fwd_url
            FROM telegram_messages
            WHERE chat_id = ANY($1)
              AND text IS NOT NULL
              AND LENGTH(text) >= 256
            ORDER BY date;
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, chat_ids)
            return [dict(row) for row in rows]