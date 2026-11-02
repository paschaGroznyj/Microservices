import asyncio
import asyncpg
import logging
import os
import json
from datetime import date, datetime, timezone
from typing import List, Dict, Any
from pathlib import Path
import random

from .config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME, FERNET_KEY, AGENT_POOL_PASSWORD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

EMB_DIM = "1024"
MAX_COUNT_JOURNAL = 9 # Максимальное 

from cryptography.fernet import Fernet

fernet = Fernet(FERNET_KEY)

def _coerce_tags(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        # "tag1, tag2" -> ["tag1", "tag2"]
        return [t.strip() for t in val.split(",") if t.strip()]
    return list(val)


SCHEMA_PATH = Path(__file__).parent / "sql_scripts"

def load_sql_schema(folder_path: Path) -> str:
    """Считывает SQL скрипты из папки."""
    if not folder_path.is_dir():
        raise FileNotFoundError(f"Директория со скриптами не найдена: {folder_path.resolve()}")

    sql_parts = []
    sql_files = sorted(folder_path.glob("*.sql"))

    if not sql_files:
        raise FileNotFoundError(f"В папке {folder_path.resolve()} не найдено ни одного .sql файла")
        return ''

    for file_path in sql_files:
        content = file_path.read_text(encoding="utf-8").strip()
        if content:
            sql_parts.append(f"-- File: {file_path.name}\n{content}")

    # Объединяем скрипты через перевод строки
    return "\n\n".join(sql_parts)


class AgentDB:
    def __init__(self):
        self.pool = None
        self.readonly_pool = None  # отдельный пул для LLM-запросов
        self.base_agent_pool = None

    async def init_db_pool(self):
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
                    min_size=2,
                    max_size=3,
                    max_queries=500,
                    max_inactive_connection_lifetime=300,
                    command_timeout=60,
                    timeout=60,
                    statement_cache_size=100,
                )

                async with self.pool.acquire() as conn:
                    await self.init_schema(conn)

                logging.info("Agent DB pool initialized")
                return

            except Exception as e:
                logging.warning(f"DB connection attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                else:
                    raise

    async def init_agent_admin_pool(self):
        self.readonly_pool = await asyncpg.create_pool(
            user="knowledge_manager",
            password=AGENT_POOL_PASSWORD,
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            min_size=1, max_size=2,
            command_timeout=10,  # жёсткий таймаут на запрос
        )
    
    async def init_agent_base_pool(self):
        self.base_agent_pool = await asyncpg.create_pool(
            user="my_journal",
            password=AGENT_POOL_PASSWORD,
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
            min_size=1, max_size=2,
            command_timeout=10,  # жёсткий таймаут на запрос
        )

    async def close(self):
        if self.pool:
            await self.pool.close()

    @staticmethod
    def _vector_to_str(vector: list[float]) -> str:
        return "[" + ",".join(map(str, vector)) + "]"

    async def init_schema(self, conn: asyncpg.Connection):

        FULL_sql = load_sql_schema(SCHEMA_PATH)

        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

        await conn.execute(FULL_sql)

        await conn.execute("""
            ALTER TABLE papers_metadata
                ADD COLUMN IF NOT EXISTS review BOOLEAN,
                ADD COLUMN IF NOT EXISTS text_review TEXT;
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_article_embeddings_article_id
                ON article_embeddings(article_id);

            CREATE INDEX IF NOT EXISTS ix_article_embeddings_model
                ON article_embeddings(embedding_model);
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_article_embeddings_embedding_cosine
                ON article_embeddings
                USING hnsw (embedding vector_cosine_ops);
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS article_translations (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                request_id BIGINT NOT NULL REFERENCES search_requests(id) ON DELETE CASCADE,
                translated_text TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, article_id, request_id)
            );
        """)

        await conn.execute("""
            ALTER TABLE request_articles
                ADD COLUMN IF NOT EXISTS is_relevant BOOLEAN,
                ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP DEFAULT NOW();
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS ix_article_translations_user_id
                ON article_translations(user_id);

            CREATE INDEX IF NOT EXISTS ix_article_translations_article_id
                ON article_translations(article_id);

            CREATE INDEX IF NOT EXISTS ix_article_translations_request_id
                ON article_translations(request_id);

            CREATE INDEX IF NOT EXISTS ix_request_articles_is_relevant
                ON request_articles(is_relevant);
        """)


    # async def get_agent_journal(self, user_id: int | None = None) -> str:
    #     async with self.pool.acquire() as conn:
    #         rows = await conn.fetch("""
    #             SELECT id, entry_type, content, context, created_at, updated_at
    #         FROM (
    #             -- personal identity конкретно этого пользователя
    #             SELECT id, entry_type, content, context, created_at, updated_at,
    #                    1 AS priority, updated_at AS sort_key
    #             FROM agent_journal
    #             WHERE entry_type = 'identity'
    #               AND user_id = $1

    #             UNION ALL

    #             -- по 2 последних от каждого НЕ-identity типа для этого юзера
    #             -- (или общих, если user_id IS NULL в записи)
    #             SELECT id, entry_type, content, context, created_at, updated_at,
    #                    2 AS priority, updated_at AS sort_key
    #             FROM (
    #                 SELECT id, entry_type, content, context, created_at, updated_at,
    #                        ROW_NUMBER() OVER (
    #                            PARTITION BY entry_type
    #                            ORDER BY updated_at DESC
    #                        ) AS rn
    #                 FROM agent_journal
    #                 WHERE id != 1
    #                   AND entry_type != 'identity'
    #                   AND (user_id = $1 OR user_id IS NULL)
    #             ) ranked
    #             WHERE rn <= 2
    #         ) combined
    #         GROUP BY id, entry_type, content, context,
    #                 created_at, updated_at, priority, sort_key
    #         ORDER BY priority ASC, sort_key DESC
    #         LIMIT 15;
    #         """, user_id)

    #         count = await conn.fetchval(
    #             "SELECT count(id) FROM agent_journal WHERE user_id = $1 OR user_id IS NULL;",
    #             user_id,
    #         )

    #     if not rows:
    #         return "Журнал агента пуст."

    #     formatted_entries = []
    #     head_str = ''
    #     if count > MAX_COUNT_JOURNAL:
    #         head_str = f"Записей в журнале уже больше порога = {MAX_COUNT_JOURNAL}. Пора бы делать summary и дроппать лишние строки\n\n"

    #     for row in rows:
    #         created_str = row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else "—"
    #         updated_str = row["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if row["updated_at"] else "—"
    #         context_data = row["context"]
    #         ctx_str = json.dumps(context_data, ensure_ascii=False) if isinstance(context_data, dict) else (str(context_data) if context_data else "без контекста")
    #         entry = (
    #             f"• [{created_str}] [{updated_str}] ID: {row['id']} | Тип: [{row['entry_type']}]\n"
    #             f"  Содержание: {row['content']}\n"
    #             f"  Контекст: {ctx_str}"
    #         )
    #         formatted_entries.append(entry)

    #     return head_str + "\n\n".join(formatted_entries)

    async def deactivate_loop_task(self, chat_id: str) -> bool:
        """САМОЦИКЛ: погасить активную установку петли (стоп-кнопка).

        Таблица agent_loop_tasks живёт в ОБЩЕЙ БД (создаёт chats_service). Агент
        ходит в тот же Postgres своим пулом. После деактивации consumer
        при следующем реентри увидит отсутствие активной задачи и не продолжит.
        """
        if not chat_id or not self.pool:
            return False
        try:
            res = await self.pool.execute(
                "UPDATE agent_loop_tasks SET is_active=FALSE, updated_at=now() "
                "WHERE chat_id=$1 AND is_active;", str(chat_id))
            return "UPDATE" in res
        except Exception as e:
            import logging as _lg
            _lg.error(f"[self-loop] deactivate_loop_task chat={chat_id}: {e}")
            return False

    async def get_agent_journal(self, chat_id: str | None = None) -> str:
        async with self.pool.acquire() as conn:
            # В основной контекст тянем ТОЛЬКО активные (не архивные) записи.
            identity_row = await conn.fetchrow("""
                SELECT id, entry_type, content, context, created_at, updated_at
                FROM agent_journal
                WHERE entry_type = 'identity' AND chat_id = $1
                  AND is_archived = FALSE
                LIMIT 1
            """, chat_id)

            other_rows = await conn.fetch("""
                SELECT id, entry_type, content, context, created_at, updated_at
                FROM (
                    SELECT id, entry_type, content, context, created_at, updated_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY entry_type
                            ORDER BY updated_at DESC
                        ) AS rn
                    FROM agent_journal
                    WHERE entry_type != 'identity'
                    AND chat_id = $1
                    AND is_archived = FALSE
                ) ranked
                ORDER BY updated_at DESC
                LIMIT 10
            """, chat_id)

            count = await conn.fetchval(
                "SELECT count(id) FROM agent_journal WHERE chat_id = $1 AND is_archived = FALSE;",
                chat_id,
            )

            # Архивные записи: тянем компактно только id + краткое summary,
            # чтобы модель на лету могла разархивировать нужную по id.
            archived_rows = await conn.fetch("""
                SELECT id, entry_type, archive_summary, updated_at
                FROM agent_journal
                WHERE chat_id = $1 AND is_archived = TRUE
                ORDER BY updated_at DESC
                LIMIT 50
            """, chat_id)

        if not identity_row and not other_rows and not archived_rows:
            return "Журнал агента пуст."

        def _format_entry(row) -> str:
            created_str = row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else "—"
            updated_str = row["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if row["updated_at"] else "—"
            context_data = row["context"]
            ctx_str = (
                json.dumps(context_data, ensure_ascii=False) if isinstance(context_data, dict)
                else (str(context_data) if context_data else "без контекста")
            )
            return (
                f"• [{created_str}] [{updated_str}] ID: {row['id']} | Тип: [{row['entry_type']}]\n"
                f"  Содержание: {row['content']}\n"
                f"  Контекст: {ctx_str}"
            )

        parts = []

        if count and count > MAX_COUNT_JOURNAL:
            parts.append(
                f"Записей в журнале уже больше порога = {MAX_COUNT_JOURNAL}. "
                f"Пора бы делать summary и дроппать лишние строки\n"
            )

        if identity_row:
            parts.append("── ЛИЧНОСТЬ ──\n" + _format_entry(identity_row))

        if other_rows:
            others_formatted = "\n\n".join(_format_entry(r) for r in other_rows)
            parts.append(("── ОСТАЛЬНЫЕ ЗАПИСИ ──\n" if identity_row else "") + others_formatted)

        # Компактный реестр архива: id + одно предложение о содержимом.
        # В основной контекст сами тексты не тянутся, но модель видит, что
        # лежит в архиве, и может разархивировать нужную запись по id.
        if archived_rows:
            arch_lines = []
            for r in archived_rows:
                summ = (r["archive_summary"] or "").strip() or "(без описания)"
                arch_lines.append(f"  ID: {r['id']} | [{r['entry_type']}] — {summ}")
            parts.append(
                "── В АРХИВЕ (свёрнуто, в контекст не тянется; разархивировать по id) ──\n"
                + "\n".join(arch_lines)
            )

        return "\n\n".join(parts)
            

    async def get_user_id_by_name(self, user_name: str) -> int | None:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT id
                FROM users
                WHERE user_name = $1
            """, user_name)

    async def get_request(self, user_name: str, request_id: int) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    sr.id,
                    sr.user_id,
                    ru.username,
                    sr.user_query,
                    sr.params_json,
                    sr.date_start,
                    sr.date_end,
                    sr.limit_value,
                    sr.created_at
                FROM search_requests sr
                JOIN users ru ON ru.id = sr.user_id
                WHERE sr.id = $1
                  AND ru.username = $2
            """, request_id, user_name)

            return dict(row) if row else None

    async def get_request_articles_for_embedding(self, user_name: str, request_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.abstract,
                    a.date_sub,
                    a.category
                FROM request_articles ra
                JOIN articles a ON a.id = ra.article_id
                JOIN search_requests sr ON sr.id = ra.request_id
                JOIN users ru ON ru.id = sr.user_id
                LEFT JOIN article_embeddings embed ON a.id = embed.article_id
                WHERE ra.request_id = $1
                  AND ru.username = $2
                  AND embed.embedding is NULL
                ORDER BY a.date_sub DESC NULLS LAST, a.id DESC
            """, request_id, user_name)

            return [dict(row) for row in rows]
        
    async def upsert_article_embedding(
        self,
        article_id: int,
        embedding: list[float],
        embedding_model: str,
        source_text: str,
    ) -> int:
        
        embedding_str = self._vector_to_str(embedding)

        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO article_embeddings (
                    article_id,
                    embedding,
                    embedding_model,
                    source_text,
                    updated_at
                )
                VALUES ($1, $2::vector, $3, $4, NOW())
                ON CONFLICT (article_id, embedding_model)
                DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    source_text = EXCLUDED.source_text,
                    updated_at = NOW()
                RETURNING id
            """, article_id, embedding_str, embedding_model, source_text)

    async def upsert_article_embeddings_batch(
            self,
            articles_data: list[dict],  # [{article_id, embedding, source_text}]
            embedding_model: str,
    ) -> list[int]:

        embedding_records = [
            (
                item["article_id"],
                self._vector_to_str(item["embedding"]),
                embedding_model,
                item["source_text"],
            )
            for item in articles_data
        ]

        async with self.pool.acquire() as conn:
            result = await conn.fetch("""
                                      INSERT INTO article_embeddings (article_id, embedding, embedding_model,
                                                                      source_text, updated_at)
                                      SELECT *
                                      FROM UNNEST(
                                                   $1::int[],
                                                   $2::vector[],
                                                   $3::text[],
                                                   $4::text[]
                                           ) AS t(article_id, embedding, embedding_model, source_text),
                                           NOW() AS updated_at
                                      ON CONFLICT (article_id, embedding_model)
                                          DO UPDATE SET embedding   = EXCLUDED.embedding,
                                                        source_text = EXCLUDED.source_text,
                                                        updated_at  = NOW()
                                      RETURNING id
                                      """,
                                      [r[0] for r in embedding_records],  # article_ids
                                      [r[1] for r in embedding_records],  # embeddings
                                      [embedding_model] * len(embedding_records),
                                      [r[3] for r in embedding_records],  # source_texts
                                      )
            return [row["id"] for row in result]

    async def search_similar_articles_for_request(
        self,
        user_name: str,
        request_id: int,
        query_embedding: list[float],
        embedding_model: str,
        match_threshold: float = 0.65,
        match_count: int = 20, # Пробуем без лимита
    ) -> list[dict]:
        
        query_embedding_str = self._vector_to_str(query_embedding)
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.abstract,
                    a.date_sub,
                    a.url_article,
                    a.pdf_url,
                    a.category,
                    1 - (ae.embedding <=> $3) AS similarity
                FROM request_articles ra
                JOIN search_requests sr ON sr.id = ra.request_id
                JOIN users ru ON ru.id = sr.user_id
                JOIN articles a ON a.id = ra.article_id
                JOIN article_embeddings ae ON ae.article_id = a.id
                WHERE sr.id = $1
                  AND ru.username = $2
                  AND ae.embedding_model = $4
                  AND 1 - (ae.embedding <=> $3::vector) >= $5
                ORDER BY ae.embedding <=> $3
            """, request_id, user_name, query_embedding_str, embedding_model, match_threshold)

            return [dict(row) for row in rows]

    async def get_request_articles(self, user_name: str, request_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.abstract,
                    a.date_sub,
                    a.url_article,
                    a.pdf_url,
                    a.category,
                    ra.is_relevant,
                    ra.processed_at
                FROM request_articles ra
                JOIN articles a ON a.id = ra.article_id
                JOIN search_requests sr ON sr.id = ra.request_id
                JOIN users ru ON ru.id = sr.user_id
                WHERE ra.request_id = $1
                  AND ru.username = $2
                ORDER BY a.date_sub DESC NULLS LAST, a.id DESC
            """, request_id, user_name)

            return [dict(row) for row in rows]

    async def get_request_articles_for_llm(self, user_name: str, request_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.abstract,
                    sr.user_query
                FROM request_articles ra
                JOIN articles a ON a.id = ra.article_id
                JOIN search_requests sr ON sr.id = ra.request_id
                JOIN users ru ON ru.id = sr.user_id
                WHERE ra.request_id = $1
                  AND ru.username = $2
                ORDER BY a.date_sub DESC NULLS LAST, a.id DESC
            """, request_id, user_name)

            return [dict(row) for row in rows]

    async def upsert_translation(
        self,
        user_id: int,
        article_id: int,
        request_id: int,
        translated_text: str,
    ) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO article_translations (
                    user_id,
                    article_id,
                    request_id,
                    translated_text,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (user_id, article_id, request_id)
                DO UPDATE SET
                    translated_text = EXCLUDED.translated_text,
                    updated_at = NOW()
                RETURNING id
            """, user_id, article_id, request_id, translated_text)

    async def get_translation(
        self,
        user_id: int,
        article_id: int,
        request_id: int,
    ) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    id,
                    user_id,
                    article_id,
                    request_id,
                    translated_text,
                    created_at,
                    updated_at
                FROM article_translations
                WHERE user_id = $1
                  AND article_id = $2
                  AND request_id = $3
            """, user_id, article_id, request_id)

            return dict(row) if row else None

    async def update_article_relevance(
        self,
        request_id: int,
        article_id: int,
        is_relevant: bool,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE request_articles
                SET
                    is_relevant = $3,
                    processed_at = NOW()
                WHERE request_id = $1
                  AND article_id = $2
            """, request_id, article_id, is_relevant)

    async def save_article_processing_result(
        self,
        user_name: str,
        request_id: int,
        article_id: int,
        translated_text: str,
        is_relevant: bool,
    ) -> dict:
        request_data = await self.get_request(user_name, request_id)
        if not request_data:
            raise ValueError("Request not found")

        user_id = request_data["user_id"]

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                translation_id = await conn.fetchval("""
                    INSERT INTO article_translations (
                        user_id,
                        article_id,
                        request_id,
                        translated_text,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (user_id, article_id, request_id)
                    DO UPDATE SET
                        translated_text = EXCLUDED.translated_text,
                        updated_at = NOW()
                    RETURNING id
                """, user_id, article_id, request_id, translated_text)

                await conn.execute("""
                    UPDATE request_articles
                    SET
                        is_relevant = $3,
                        processed_at = NOW()
                    WHERE request_id = $1
                      AND article_id = $2
                """, request_id, article_id, is_relevant)

        return {
            "translation_id": translation_id,
            "request_id": request_id,
            "article_id": article_id,
            "is_relevant": is_relevant,
        }

    async def get_request_articles_for_front(self, user_name: str, request_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.abstract,
                    a.date_sub,
                    a.url_article,
                    a.pdf_url,
                    a.category,
                    ra.is_relevant,
                    ra.processed_at,
                    at.translated_text
                FROM request_articles ra
                JOIN articles a ON a.id = ra.article_id
                JOIN search_requests sr ON sr.id = ra.request_id
                JOIN users ru ON ru.id = sr.user_id
                LEFT JOIN article_translations at
                    ON at.article_id = a.id
                   AND at.user_id = ru.id
                   AND at.request_id = sr.id
                WHERE sr.id = $1
                  AND ru.username = $2
                ORDER BY ra.is_relevant DESC NULLS LAST,
                         a.date_sub DESC NULLS LAST,
                         a.id DESC
            """, request_id, user_name)

            return [dict(row) for row in rows]

    async def get_unprocessed_request_articles(self, user_name: str, request_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.abstract,
                    sr.user_query
                FROM request_articles ra
                JOIN articles a ON a.id = ra.article_id
                JOIN search_requests sr ON sr.id = ra.request_id
                JOIN users ru ON ru.id = sr.user_id
                WHERE sr.id = $1
                  AND ru.username = $2
                  AND ra.processed_at IS NULL
                ORDER BY a.date_sub DESC NULLS LAST, a.id DESC
            """, request_id, user_name)

            return [dict(row) for row in rows]

    def _decrypt_token(self, encrypted: str) -> str:
        return fernet.decrypt(encrypted.encode()).decode()

    async def insert_query(self, username: str, query: str) -> dict:
        async with self.pool.acquire() as conn:
            request_id =  await conn.fetchval("""
                    INSERT INTO search_requests (user_id, user_query, status)
                    SELECT id, $2, $3
                    FROM users
                    WHERE username = $1
                    RETURNING id
                """, username, query, 'pending')
            search_requests = await conn.fetchval("""
                                                  SELECT COUNT(s_r.id)
                                                  FROM search_requests s_r
                                                           JOIN users u ON u.id = s_r.user_id
                                                  WHERE u.username = $1
                                                    AND s_r.user_query != 'default'
                                                  """, username)
            token = await conn.fetchval(
                    "SELECT scholar_token FROM users WHERE username = $1", username
                )
            if token:
                token = await asyncio.to_thread(self._decrypt_token, token)
            return {"request_id": request_id, "count_search": search_requests, "token": token}
            

    async def update_status(self, request_id: int, llm_answer: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
            UPDATE search_requests 
            SET status = 'rejected', 
                error_message = $1 
            WHERE id = $2
        """, llm_answer, request_id)

    async def get_article_translation(self, article_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("""
                                       SELECT abstract, translate, title
                                       FROM articles
                                       WHERE id = $1
                                       """, article_id)

    async def save_translation(self, article_id: int, translated_text: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                               UPDATE articles
                               SET translate = $1
                               WHERE id = $2
                               """, translated_text, article_id)

    async def get_reviews(self, user_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetch("""
            SELECT 
                a.id AS article_id, 
                a.pdf_url, 
                a.title,
                a.date_sub, 
                a_r.review_text
            FROM favorites f
            JOIN articles a ON f.article_id = a.id
            LEFT JOIN article_reviews a_r 
                ON a_r.user_id = f.user_id AND a_r.article_id = f.article_id
            WHERE f.user_id = $1
        """, user_id)

    async def save_review(self, user_id: int, article_id: int, review_text: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO article_reviews (user_id, article_id, review_text)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, article_id) 
                DO UPDATE SET 
                    review_text = EXCLUDED.review_text,
                    generated_at = NOW()
            """, user_id, article_id, review_text)

    # Работа с динамическим промптом
    async def get_tags(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT tag_name FROM tags ORDER BY generated_at DESC")
            return [row["tag_name"] for row in rows]

    async def insert_tags(self, tags: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany("""
                                   INSERT INTO tags (tag_name)
                                   VALUES ($1)
                                   ON CONFLICT (tag_name) DO NOTHING
                                   """, [(tag,) for tag in tags])

    # ── Ключевые слова ────────────────────────────────────────
    async def get_keywords(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT keyword FROM keywords ORDER BY generated_at DESC")
            return [row["keyword"] for row in rows]

    async def insert_keywords(self, keywords: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.executemany("""
                                   INSERT INTO keywords (keyword)
                                   VALUES ($1)
                                   ON CONFLICT (keyword) DO NOTHING
                                   """, [(kw,) for kw in keywords])

    async def upsert_metadata(self, records: list[dict]) -> None:
        """Принимает список записей (бывший JSON-массив) и кладёт в БД"""
        async with self.pool.acquire() as conn:
            await conn.executemany("""
                INSERT INTO papers_metadata (
                    external_id, source, title, date_sub, url_article,
                    pdf_url, authors, tags, abstract, file_article,
                    category, relevance_score, relevance_reasoning,
                    is_relevant, pdf_downloaded
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10,
                    $11, $12, $13, $14, $15
                )
                ON CONFLICT (external_id) DO UPDATE SET
                    source              = EXCLUDED.source,
                    title               = EXCLUDED.title,
                    date_sub            = EXCLUDED.date_sub,
                    url_article         = EXCLUDED.url_article,
                    pdf_url             = EXCLUDED.pdf_url,
                    authors             = EXCLUDED.authors,
                    tags                = EXCLUDED.tags,
                    abstract            = EXCLUDED.abstract,
                    file_article        = EXCLUDED.file_article,
                    category            = EXCLUDED.category,
                    relevance_score     = EXCLUDED.relevance_score,
                    relevance_reasoning = EXCLUDED.relevance_reasoning,
                    is_relevant         = EXCLUDED.is_relevant,
                    pdf_downloaded      = EXCLUDED.pdf_downloaded
            """, [
                (
                    r["external_id"],
                    r.get("source"),
                    r.get("title"),
                    date.fromisoformat(r["date_sub"]) if r.get("date_sub") else None,  # <-- fix         # asyncpg сам разберёт строку "2025-10-01" как DATE
                    r.get("url_article"),
                    r.get("pdf_url"),
                    json.dumps(r.get("authors", {})),  # JSONB
                    _coerce_tags(r.get("tags")),                  # TEXT[]
                    r.get("abstract"),
                    r.get("file_article"),
                    r.get("category"),
                    r.get("relevance_score"),
                    r.get("relevance_reasoning"),
                    r.get("is_relevant"),
                    r.get("pdf_downloaded"),
                )
                for r in records
            ])

    async def get_metadata_as_json(self) -> list[dict]:
        """Возвращает все строки в виде списка словарей — замена старого download_metadata"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    external_id, source, title,
                    date_sub::text AS date_sub,
                    url_article, pdf_url,
                    authors, tags, abstract,
                    file_article, category,
                    relevance_score, relevance_reasoning,
                    is_relevant, pdf_downloaded
                FROM papers_metadata
            """)
        return [dict(r) for r in rows]
    
    async def get_metadata_as_json_with_pdf(self) -> list[dict]:
        """Возвращает все строки в виде списка словарей — замена старого download_metadata"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    external_id, source, title,
                    date_sub::text AS date_sub,
                    url_article, pdf_url,
                    authors, tags, abstract,
                    file_article, category,
                    relevance_score, relevance_reasoning,
                    is_relevant, pdf_downloaded
                FROM papers_metadata WHERE pdf_downloaded = True
            """)
        return [dict(r) for r in rows]
    
    async def update_pdf_downloaded_status(
        self, external_id: str, downloaded: bool = True
    ) -> None:
        """Обновляет флаг pdf_downloaded для конкретной статьи по ее external_id"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE papers_metadata
                SET pdf_downloaded = $1
                WHERE external_id = $2
            """,
                downloaded,
                external_id,
            )

    async def get_chat_history(self, chat_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Получить последние N сообщений из чата для восстановления контекста.
        Возвращает список словарей с полями: message_text, is_from_bot, created_at
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, message_text, is_from_bot, created_at, tool_trace
                FROM chats
                WHERE chat_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET 1
            """, chat_id, limit)
            
            # Возвращаем в хронологическом порядке (от старых к новым)
            return [dict(row) for row in reversed(rows)]
            

    async def ensure_user_exists(self, username: str, password: str = None, source: str = "telegram"):
        DUMMY_PASSWORD_HASH = "EXTERNALLY_AUTHENTICATED_NO_LOGIN_POSSIBLE"
        
        try:
            async with self.pool.acquire() as conn:
                user_id = await conn.fetchval("""
                    INSERT INTO users (username, password_hash, source)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (username) DO NOTHING
                    RETURNING id;
                """, username, DUMMY_PASSWORD_HASH, source)

                if user_id:
                    logging.info(f"Создан новый пользователь {username} (source={source}) с id={user_id}")
                    return user_id
                
                existing_id = await conn.fetchval(
                    "SELECT id FROM users WHERE username = $1", username
                )
                return existing_id

        except Exception as e:
            logging.error(f"Ошибка при работе с пользователем {username}: {e}")
            raise

    async def add_message(self, user_id: int, message: str, chat_id: str, is_from_bot: bool, action: str = None):
        """Добавить сообщение пользователя из ТГ"""
        if user_id is None or chat_id is None:
            logging.warning("Пропущено сообщение: отсутствует user_id или chat_id")
            return None
        
        # Генерируем точное время создания со значением UTC
        now_utc = datetime.now(timezone.utc)
        
        try:
            async with self.pool.acquire() as conn:
               await conn.fetchval("""
                    INSERT INTO chats (user_id, chat_id, message_text, is_from_bot, tool_name, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6);
                """,
                user_id, chat_id, message, is_from_bot, action, now_utc)
        except asyncpg.UniqueViolationError:
            logging.error(f"Сообщение не может быть добавлено")

    async def get_bot_message_text(self, message_id: str):
        """Текст ответа бота по message_id (только is_from_bot)."""
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval(
                    """
                    SELECT message_text FROM chats
                    WHERE message_id = $1 AND is_from_bot = TRUE
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    message_id,
                )
        except Exception as e:
            logging.warning(f"get_bot_message_text fail: {e}")
            return None

    async def append_reaction(self, message_id: str, reaction: dict):
        """Добавить реакцию в JSONB-массив reactions сообщения бота."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE chats
                    SET reactions = COALESCE(reactions, '[]'::jsonb) || $2::jsonb
                    WHERE id = (
                        SELECT id FROM chats
                        WHERE message_id = $1 AND is_from_bot = TRUE
                        ORDER BY created_at DESC LIMIT 1
                    )
                    """,
                    message_id, json.dumps([reaction]),
                )
        except Exception as e:
            logging.warning(f"append_reaction fail: {e}")

# ============================== ОБЩЕНИЕ МЕЖДУ СЕРВИСАМИ ==============================

    async def add_service_message(service_name, service_port, message):
        _SQL_INSERT = """
            INSERT INTO service_chats (service_name, service_port, message_text, status)
            VALUES ($1, $2, $3, 'pending')
            RETURNING id
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    _SQL_INSERT,
                    service_name,
                    service_port,
                    message,
                )
                return row
        except asyncpg.UniqueViolationError:
            logging.error(f"Сообщение не может быть добавлено")
    
    async def update_service_status(message_id):
        _SQL_UPDATE_STATUS = """
            UPDATE service_chats SET status = $1, updated_at = NOW() WHERE id = $2
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(_SQL_UPDATE_STATUS, "processing", message_id)
        except asyncpg.UniqueViolationError:
            logging.error(f"Сообщение не может быть добавлено")

        
    async def update_service_message(reply, status, message_id):
        _SQL_UPDATE_DONE = """
            UPDATE service_chats
            SET reply_text = $1, status = $2, updated_at = NOW()
            WHERE id = $3
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(_SQL_UPDATE_DONE, reply, status, message_id)
        except asyncpg.UniqueViolationError:
            logging.error(f"Сообщение не может быть добавлено")

# ==================== РАБОТА С СЕССИЯМИ ==========================

    # ── session_summaries ──────────────────────────────────────────────────────────

    async def session_start(self, chat_id: str) -> dict:
        """
        Создаёт новую запись сессии.
        Генерит 6-значный session_id, фиксирует текущий MAX(id) из chats как start_msg_id.
        Возвращает {"session_id": ..., "start_msg_id": ...}
        """
        _SQL_MAX_MSG = "SELECT COALESCE(MAX(id), 0) FROM chats WHERE chat_id = $1"
        _SQL_INSERT  = """
            INSERT INTO session_summaries (session_id, chat_id, start_msg_id)
            VALUES ($1, $2, $3)
            RETURNING id, session_id, start_msg_id
        """
        session_id = random.randint(100_000, 999_999)

        try:
            async with self.pool.acquire() as conn:
                # Гарантируем уникальность session_id
                while True:
                    exists = await conn.fetchval(
                        "SELECT 1 FROM session_summaries WHERE session_id = $1", session_id
                    )
                    if not exists:
                        break
                    session_id = random.randint(100_000, 999_999)

                start_msg_id = await conn.fetchval(_SQL_MAX_MSG, chat_id)
                row = await conn.fetchrow(_SQL_INSERT, session_id, chat_id, start_msg_id)

            logging.info("Session start: session_id=%s chat_id=%s start_msg_id=%s",
                        session_id, chat_id, start_msg_id)
            return {"session_id": row["session_id"], "start_msg_id": row["start_msg_id"]}

        except Exception as e:
            logging.error("session_start failed: %s", e)
            raise


    async def session_end(self, chat_id: str, session_id: int, llm) -> dict:
        """
        Завершает сессию:
        1. Берёт start_msg_id из записи сессии
        2. Берёт текущий MAX(id) из chats как end_msg_id
        3. Тянет все сообщения из диапазона [start_msg_id, end_msg_id]
        4. Передаёт текст напрямую в LLM (не через агента) для генерации саммари
        5. Обновляет запись: end_msg_id + summary_text
        Возвращает {"session_id": ..., "summary_text": ...}
        """
        _SQL_GET_START = """
            SELECT start_msg_id FROM session_summaries
            WHERE session_id = $1 AND chat_id = $2
        """
        _SQL_MESSAGES = """
            SELECT id, is_from_bot, message_text
            FROM chats
            WHERE chat_id = $1 AND id BETWEEN $2 AND $3
            ORDER BY id ASC
        """
        _SQL_UPDATE = """
            UPDATE session_summaries
            SET end_msg_id   = $1,
                summary_text = $2,
                updated_at   = NOW()
            WHERE session_id = $3 AND chat_id = $4
            RETURNING id
        """

        try:
            async with self.pool.acquire() as conn:
                start_msg_id = await conn.fetchval(_SQL_GET_START, session_id, chat_id)
                if start_msg_id is None:
                    raise ValueError(f"Сессия session_id={session_id} chat_id={chat_id} не найдена")

                end_msg_id = await conn.fetchval(
                    "SELECT COALESCE(MAX(id), 0) FROM chats WHERE chat_id = $1", chat_id
                )

                messages = await conn.fetch(_SQL_MESSAGES, chat_id, start_msg_id, end_msg_id)

            if not messages:
                summary = "Сессия завершена, сообщений в диапазоне не найдено."
            else:
                # Формируем диалог для LLM
                dialog_lines = []
                for msg in messages:
                    role = "Агент" if msg["is_from_bot"] else "Пользователь"
                    text = (msg["message_text"] or "").strip()
                    if text:
                        dialog_lines.append(f"{role}: {text}")

                dialog_text = "\n".join(dialog_lines)

                # Прямой вызов LLM — без агентного цикла
                from langchain.schema import SystemMessage, HumanMessage
                response = llm.invoke([
                    SystemMessage(content=(
                        "Ты — ассистент. Прочитай диалог между пользователем и агентом. "
                        "Составь краткое саммари сессии (3–7 предложений): "
                        "о чём говорили, какие задачи решались, что было сделано, "
                        "что осталось незавершённым. Пиши на русском, без лишних вступлений."
                    )),
                    HumanMessage(content=f"Диалог:\n{dialog_text}"),
                ])
                summary = response.content.strip()

            async with self.pool.acquire() as conn:
                await conn.fetchrow(_SQL_UPDATE, end_msg_id, summary, session_id, chat_id)

            logging.info("Session end: session_id=%s chat_id=%s msgs=[%s..%s]",
                        session_id, chat_id, start_msg_id, end_msg_id)
            return {"session_id": session_id, "summary_text": summary}

        except Exception as e:
            logging.error("session_end failed: %s", e)
            raise


    async def get_last_session_summary(self, chat_id: str) -> dict | None:
        """
        Возвращает последнюю завершённую сессию для чата.
        Подставляется в messages при каждом новом запросе.
        None если сессий ещё нет или summary не сформировано.
        """
        _SQL = """
            SELECT session_id, summary_text
            FROM session_summaries
            WHERE chat_id = $1
            ORDER BY updated_at DESC
            LIMIT 1
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(_SQL, chat_id)
                if row is None:
                    return None
                return {"session_id": row["session_id"], "summary_text": row["summary_text"]}
        except Exception as e:
            logging.error("get_last_session_summary failed: %s", e)
            return None

    # async def add_tokens(self, user_id: int, message: str, chat_id: int, is_from_bot: bool, action: str = None):
    #     """Добавить сообщение пользователя из ТГ"""
    #     if user_id is None or chat_id is None:
    #         logging.warning("Пропущено сообщение: отсутствует user_id или chat_id")
    #         return None
        
    #     # Генерируем точное время создания со значением UTC
    #     now_utc = datetime.now(timezone.utc)
        
    #     try:
    #         async with self.pool.acquire() as conn:
    #            await conn.fetchval("""
    #                 INSERT INTO chats (user_id, chat_id, message_text, is_from_bot, tool_name, created_at)
    #                 VALUES ($1, $2, $3, $4, $5, $6);
    #             """,
    #             user_id, chat_id, message, is_from_bot, action, now_utc)
    #     except asyncpg.UniqueViolationError:
    #         logging.error(f"Сообщение не может быть добавлено")

    # ── code_library: сохранение ──────────────────────────────────

    async def save_to_code_library(
        self,
        title: str,
        description: str,
        code_type: str,
        tags: list[str],
        author: str,
        path: str,
        run_id: str | None = None,
    ) -> int:
        """
        Документирует переиспользуемый скрипт в таблицу code_library.
        Динамически тянет схему таблицы перед вставкой.
        Возвращает id созданной записи.
        """
        async with self.pool.acquire() as conn:
            # Динамически проверяем схему (без get_db_schema)
            columns = await conn.fetch("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'code_library'
                ORDER BY ordinal_position
            """)
            column_names = {row["column_name"] for row in columns}

        if "title" not in column_names:
            raise RuntimeError(
                "Таблица code_library не найдена или не содержит ожидаемых колонок. "
                "Убедитесь что DDL применён."
            )

        async with self.pool.acquire() as conn:
            record_id = await conn.fetchval("""
                INSERT INTO code_library (
                    title,
                    description,
                    code_type,
                    tags,
                    author,
                    path,
                    run_id,
                    created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
                RETURNING id
            """,
                title,
                description,
                code_type,
                tags,          # asyncpg передаёт list[str] → TEXT[]
                author,
                path,
                run_id,
            )

        logging.info(
            "code_library: сохранён скрипт id=%s title=%r author=%s",
            record_id, title, author
        )
        return record_id


# ── code_library: поиск ──────────────────────────────────────
    async def _exact_path_search(
        self,
        path: str,
        code_type: str | None = None,
        tags: list[str] | None = None,
        author: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Точный поиск по полю path (точное совпадение или ILIKE без %)."""
        conditions = ["path = $1"]
        params: list = [path]
        idx = 2

        if code_type is not None:
            conditions.append(f"code_type = ${idx}")
            params.append(code_type); idx += 1
        if tags:
            conditions.append(f"tags @> ${idx}::text[]")
            params.append(tags); idx += 1
        if author is not None:
            conditions.append(f"author = ${idx}")
            params.append(author); idx += 1

        params.append(limit)
        limit_placeholder = f"${idx}"
        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT id, title, description, code_type, tags, author, path,
                run_id, use_count, last_used_at, created_at,
                1.0 AS score
            FROM code_library
            WHERE {where_clause}
            ORDER BY use_count DESC, created_at DESC
            LIMIT {limit_placeholder}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [self._row_to_dict(r, search_type="exact_path") for r in rows]

    async def _like_search(
        self,
        query: str,
        code_type: str | None = None,
        tags: list[str] | None = None,
        author: str | None = None,
        limit: int = 5,
        path: str | None = None,
    ) -> list[dict]:
        conditions = ["(title ILIKE $1 OR description ILIKE $1 OR path ILIKE $1)"]
        params: list = [f"%{query}%"]
        idx = 2

        if path is not None:
            conditions.append(f"path = ${idx}")
            params.append(path); idx += 1
        if code_type is not None:
            conditions.append(f"code_type = ${idx}")
            params.append(code_type); idx += 1
        if tags:
            conditions.append(f"tags @> ${idx}::text[]")
            params.append(tags); idx += 1
        if author is not None:
            conditions.append(f"author = ${idx}")
            params.append(author); idx += 1

        params.append(limit)
        limit_placeholder = f"${idx}"
        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT id, title, description, code_type, tags, author, path,
                run_id, use_count, last_used_at, created_at,
                similarity(title, $1) AS score
            FROM code_library
            WHERE {where_clause}
            ORDER BY score DESC, use_count DESC
            LIMIT {limit_placeholder}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [self._row_to_dict(r, search_type="like") for r in rows]
    
    async def _fts_search(
        self,
        query: str,
        code_type: str | None = None,
        tags: list[str] | None = None,
        author: str | None = None,
        limit: int = 5,
        path: str | None = None,
    ) -> list[dict]:
        """
        BM25-подобный поиск по code_library через websearch_to_tsquery ('russian').
        Дополнительные фильтры: code_type, tags (GIN @>), author, path.
        """
        limit = max(1, min(limit, 50))

        conditions = ["tsv @@ q.tsq"]
        params: list = [query]
        idx = 2

        if path is not None:
            conditions.append(f"path = ${idx}")
            params.append(path)
            idx += 1

        if code_type is not None:
            conditions.append(f"code_type = ${idx}")
            params.append(code_type)
            idx += 1

        if tags:
            conditions.append(f"tags @> ${idx}::text[]")
            params.append(tags)
            idx += 1

        if author is not None:
            conditions.append(f"author = ${idx}")
            params.append(author)
            idx += 1

        params.append(limit)
        limit_placeholder = f"${idx}"
        where_clause = " AND ".join(conditions)

        sql = f"""
            WITH q AS (
                SELECT websearch_to_tsquery('russian', $1) AS tsq
            )
            SELECT
                cl.id, cl.title, cl.description, cl.code_type,
                cl.tags, cl.author, cl.path, cl.run_id,
                cl.use_count, cl.last_used_at, cl.created_at,
                ts_rank_cd(cl.tsv, q.tsq, 32) AS score
            FROM code_library cl, q
            WHERE {where_clause}
            ORDER BY score DESC, use_count DESC
            LIMIT {limit_placeholder}
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [self._row_to_dict(r, search_type="bm25") for r in rows]

    @staticmethod
    def _row_to_dict(r, search_type: str) -> dict:
        return {
            "id":           r["id"],
            "title":        r["title"],
            "description":  r["description"],
            "code_type":    r["code_type"],
            "tags":         list(r["tags"] or []),
            "author":       r["author"],
            "path":         r["path"],
            "run_id":       r["run_id"],
            "use_count":    r["use_count"],
            "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] else None,
            "created_at":   r["created_at"].isoformat() if r["created_at"] else None,
            "score":        float(r["score"] or 0),
            "search_type":  search_type,
        }

    async def search_code_library(
        self,
        query: str,
        code_type: str | None = None,
        tags: list[str] | None = None,
        author: str | None = None,
        limit: int = 5,
        path: str | None = None,
    ) -> list[dict]:
        limit = int(limit) if not isinstance(limit, int) else limit
        
        # 1. Если передан точный путь к файлу — сначала ищем строго по нему
        if path:
            rows = await self._exact_path_search(path, code_type, tags, author, limit)
            if rows:
                return rows

        # 2. Основной полнотекстовый поиск (FTS)
        rows = await self._fts_search(query, code_type, tags, author, limit, path=path)
        
        # 3. Фолбэк на LIKE поиск, если FTS ничего не нашел
        if not rows:
            rows = await self._like_search(query, code_type, tags, author, limit, path=path)
            
        return rows


# ── code_library: инкремент use_count ────────────────────────

    async def touch_code_library(self, library_id: int) -> None:
        """
        Обновляет last_used_at и инкрементирует use_count.
        Вызывается из тулзы edit_and_run после каждого использования скрипта.
        """
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE code_library
                SET
                    use_count    = use_count + 1,
                    last_used_at = NOW()
                WHERE id = $1
            """, library_id)

        logging.info("code_library: touch id=%s", library_id)

    # Суммаризация сообщений, которые были отброшены фильтром на токены
    async def get_rolling_summary(self, chat_id: str) -> str | None:
        row = await self.pool.fetchrow(
            "SELECT summary_text FROM chat_rolling_summary WHERE chat_id = $1", chat_id
        )
        return row["summary_text"] if row else None

    async def upsert_rolling_summary(self, chat_id: str, summary_text: str, covers_messages: int) -> None:
        await self.pool.execute("""
            INSERT INTO chat_rolling_summary (chat_id, summary_text, covers_messages, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (chat_id) DO UPDATE
            SET summary_text = $2, covers_messages = $3, updated_at = NOW()
        """, chat_id, summary_text, covers_messages)

    async def get_rolling_summary(self, chat_id: str) -> dict | None:
        row = await self.pool.fetchrow(
            "SELECT summary_text, covered_up_to_id FROM chat_rolling_summary WHERE chat_id = $1", chat_id
        )
        return dict(row) if row else None


    async def create_skill(
        self,
        theme: str,
        body: str,
        published_by_user_id: int | None,
        published_by_name: str | None,
        tools: list[str] | None = None,
        model_used: str | None = None,
    ) -> dict:
        tools = tools or []
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO agent_skills (theme, body, published_by_user_id, published_by_name, tools, model_used)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, theme, body, published_by_user_id, published_by_name, tools, model_used, likes_count, COALESCE(uses_count, 0) AS uses_count, created_at
                """,
                theme.strip(), body.strip(), published_by_user_id, published_by_name, tools, model_used,
            )
            return dict(row)

    async def list_skills(
        self,
        query: str = "",
        sort_by: str = "new",
        tool: str | None = None,
        limit: int = 100,
        include_body: bool = False,
        filter_by: str = "all",
        user_id: int = 0,
    ) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        uid = int(user_id or 0)
        if sort_by == "popular":
            order_sql = "COALESCE(agent_skills.uses_count, 0) DESC, agent_skills.likes_count DESC, agent_skills.created_at DESC"
        elif sort_by in ("liked", "likes"):
            order_sql = "agent_skills.likes_count DESC, COALESCE(agent_skills.uses_count, 0) DESC, agent_skills.created_at DESC"
        else:
            order_sql = "agent_skills.created_at DESC"
        where = ["agent_skills.is_deleted = FALSE"]
        args = []
        idx = 1

        q = (query or "").strip()
        if q:
            where.append(
                f"(to_tsvector('russian', coalesce(theme,'') || ' ' || coalesce(body,'')) "
                f"@@ plainto_tsquery('russian', ${idx}) "
                f"OR theme ILIKE ${idx+1} OR body ILIKE ${idx+1})"
            )
            args.append(q)
            args.append(f"%{q}%")
            idx += 2

        if tool:
            where.append(f"${idx} = ANY(tools)")
            args.append(tool.strip())
            idx += 1

        liked_col = "FALSE AS liked_by_me"
        if uid > 0:
            uid_idx = idx
            args.append(uid)
            idx += 1
            liked_col = f"EXISTS (SELECT 1 FROM agent_skill_likes sl WHERE sl.skill_id = agent_skills.id AND sl.user_id = ${uid_idx}) AS liked_by_me"
            if filter_by == "liked":
                where.append(f"EXISTS (SELECT 1 FROM agent_skill_likes sl WHERE sl.skill_id = agent_skills.id AND sl.user_id = ${uid_idx})")
            elif filter_by == "selected":
                where.append(f"EXISTS (SELECT 1 FROM user_selected_skills uss WHERE uss.skill_id = agent_skills.id AND uss.user_id = ${uid_idx})")
        elif filter_by in ("liked", "selected"):
            return []

        args.append(limit)
        where_j = [w.replace('theme', 'agent_skills.theme').replace('body', 'agent_skills.body').replace('tools', 'agent_skills.tools') for w in where]
        body_col = ", agent_skills.body" if include_body else ""
        sql = f"""
            SELECT agent_skills.id, agent_skills.theme{body_col},
                   agent_skills.published_by_user_id,
                   agent_skills.tools, agent_skills.model_used,
                   agent_skills.likes_count,
                   COALESCE(agent_skills.uses_count, 0) AS uses_count,
                   {liked_col},
                   agent_skills.created_at
            FROM agent_skills
            WHERE {' AND '.join(where_j)}
            ORDER BY {order_sql}
            LIMIT ${idx}
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]

    async def get_skill_by_id(self, skill_id: int, user_id: int = 0) -> dict | None:
        uid = int(user_id or 0)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT agent_skills.id, agent_skills.theme, agent_skills.body,
                       agent_skills.published_by_user_id,
                       COALESCE(agent_skills.published_by_name, u.username) AS published_by_name,
                       agent_skills.tools, agent_skills.model_used,
                       agent_skills.likes_count,
                       COALESCE(agent_skills.uses_count, 0) AS uses_count,
                       CASE WHEN $2::bigint > 0 THEN EXISTS (
                           SELECT 1 FROM agent_skill_likes sl
                           WHERE sl.skill_id = agent_skills.id AND sl.user_id = $2::bigint
                       ) ELSE FALSE END AS liked_by_me,
                       agent_skills.created_at, agent_skills.updated_at
                FROM agent_skills
                LEFT JOIN users u ON u.id = agent_skills.published_by_user_id
                WHERE agent_skills.id = $1 AND agent_skills.is_deleted = FALSE
                """,
                skill_id,
                uid,
            )
            return dict(row) if row else None

    async def increment_skills_uses(self, skill_ids: list[int]) -> None:
        ids = [int(x) for x in (skill_ids or []) if int(x or 0) > 0]
        if not ids:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE agent_skills
                SET uses_count = COALESCE(uses_count, 0) + 1
                WHERE id = ANY($1::bigint[])
                """,
                ids,
            )

    async def update_skill(
        self,
        skill_id: int,
        user_id: int,
        theme: str,
        body: str,
        tools: list[str] | None = None,
    ) -> dict | None:
        tools = tools or []
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE agent_skills
                SET theme = $3,
                    body = $4,
                    tools = $5,
                    updated_at = NOW()
                WHERE id = $1
                  AND published_by_user_id = $2
                  AND is_deleted = FALSE
                RETURNING id, theme, body, published_by_user_id, published_by_name, tools, model_used, likes_count, created_at
                """,
                skill_id,
                user_id,
                theme.strip(),
                body.strip(),
                tools,
            )
            return dict(row) if row else None

    async def replace_skill_body(
        self,
        skill_id: int,
        user_id: int,
        old_str: str,
        new_str: str,
    ) -> dict:
        """Точечная правка тела скилла через find & replace.

        Заменяет РОВНО одно вхождение old_str на new_str в body скилла,
        которым владеет user_id. Возвращает dict со статусом:
          {"status": "ok", "skill": {...}}          — заменено
          {"status": "not_found"}                    — скилл не найден / не автор
          {"status": "no_match"}                     — old_str не встречается
          {"status": "ambiguous", "count": N}        — old_str встречается >1 раза
        Правка выполняется в транзакции: сначала читаем актуальный body
        FOR UPDATE, проверяем уникальность вхождения, затем пишем.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT body FROM agent_skills "
                    "WHERE id = $1 AND published_by_user_id = $2 "
                    "AND is_deleted = FALSE FOR UPDATE",
                    skill_id,
                    user_id,
                )
                if not row:
                    return {"status": "not_found"}
                body = row["body"] or ""
                cnt = body.count(old_str)
                if cnt == 0:
                    return {"status": "no_match"}
                if cnt > 1:
                    return {"status": "ambiguous", "count": cnt}
                new_body = body.replace(old_str, new_str, 1)
                updated = await conn.fetchrow(
                    "UPDATE agent_skills SET body = $3, updated_at = NOW() "
                    "WHERE id = $1 AND published_by_user_id = $2 "
                    "AND is_deleted = FALSE "
                    "RETURNING id, theme, body, published_by_user_id, "
                    "published_by_name, tools, model_used, likes_count, created_at",
                    skill_id,
                    user_id,
                    new_body,
                )
                return {"status": "ok", "skill": dict(updated)}

    async def delete_skill(self, skill_id: int) -> bool:
        # Soft-delete: помечаем is_deleted=TRUE (как chats.is_deleted),
        # физически строку не удаляем — лайки и история публикаций сохраняются.
        # Дополнительно снимаем выбор скилла у всех пользователей,
        # чтобы удалённый скилл не учитывался в selected-контексте.
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "UPDATE agent_skills SET is_deleted = TRUE, updated_at = NOW() "
                    "WHERE id = $1 AND is_deleted = FALSE RETURNING id",
                    skill_id,
                )
                if not row:
                    return False
                await conn.execute(
                    "DELETE FROM agent_skill_selected WHERE skill_id = $1",
                    skill_id,
                )
                return True

    async def _fetch_selected_skill_ids_conn(self, conn, user_id: int) -> list[int]:
        await conn.execute(
            """
            DELETE FROM agent_skill_selected ss
            WHERE ss.user_id = $1
              AND NOT EXISTS (
                  SELECT 1 FROM agent_skills s
                  WHERE s.id = ss.skill_id AND s.is_deleted = FALSE
              )
            """,
            user_id,
        )
        rows = await conn.fetch(
            """
            SELECT ss.skill_id
            FROM agent_skill_selected ss
            JOIN agent_skills s ON s.id = ss.skill_id
            WHERE ss.user_id = $1
              AND s.is_deleted = FALSE
            ORDER BY ss.created_at ASC
            """,
            user_id,
        )
        return [int(r["skill_id"]) for r in rows]

    async def get_selected_skill_ids(self, user_id: int, conn=None) -> list[int]:
        if conn is not None:
            return await self._fetch_selected_skill_ids_conn(conn, user_id)
        async with self.pool.acquire() as c:
            return await self._fetch_selected_skill_ids_conn(c, user_id)

    async def get_selected_skills_for_user(self, user_id: int) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT s.id, s.theme, s.body, s.tools, ss.created_at
                FROM agent_skill_selected ss
                JOIN agent_skills s ON s.id = ss.skill_id
                WHERE ss.user_id = $1
                  AND s.is_deleted = FALSE
                ORDER BY ss.created_at ASC
                LIMIT 5
                """,
                user_id,
            )
            return [dict(r) for r in rows]

    async def toggle_skill_selected(self, skill_id: int, user_id: int, max_selected: int = 5) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                skill_exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skills WHERE id = $1 AND is_deleted = FALSE",
                    skill_id,
                )
                if not skill_exists:
                    return {"ok": False, "reason": "not_found"}

                exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skill_selected WHERE skill_id = $1 AND user_id = $2",
                    skill_id, user_id,
                )

                if exists:
                    await conn.execute(
                        "DELETE FROM agent_skill_selected WHERE skill_id = $1 AND user_id = $2",
                        skill_id, user_id,
                    )
                    selected = False
                else:
                    count_selected = await conn.fetchval(
                        """
                        SELECT COUNT(*)::int
                        FROM agent_skill_selected ss
                        JOIN agent_skills s ON s.id = ss.skill_id
                        WHERE ss.user_id = $1
                          AND s.is_deleted = FALSE
                        """,
                        user_id,
                    )
                    if int(count_selected or 0) >= int(max_selected):
                        selected_ids = await self.get_selected_skill_ids(user_id, conn=conn)
                        return {
                            "ok": False,
                            "reason": "limit",
                            "max_selected": max_selected,
                            "selected_ids": selected_ids,
                            "count": int(count_selected or 0),
                        }
                    await conn.execute(
                        """
                        INSERT INTO agent_skill_selected (skill_id, user_id)
                        VALUES ($1, $2)
                        ON CONFLICT (skill_id, user_id)
                        DO UPDATE SET updated_at = NOW()
                        """,
                        skill_id, user_id,
                    )
                    selected = True

                selected_ids = await self.get_selected_skill_ids(user_id, conn=conn)
                return {
                    "ok": True,
                    "skill_id": skill_id,
                    "selected": selected,
                    "selected_ids": selected_ids,
                    "count": len(selected_ids),
                    "max_selected": max_selected,
                }

    
    async def get_liked_skill_ids(self, user_id: int) -> list[int]:
        uid = int(user_id)
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sl.skill_id
                FROM agent_skill_likes sl
                JOIN agent_skills s ON s.id = sl.skill_id
                WHERE sl.user_id = $1
                  AND s.is_deleted = FALSE
                """,
                uid,
            )
            return [int(r["skill_id"]) for r in rows]

    async def toggle_skill_like(self, skill_id: int, user_id: int) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                exists = await conn.fetchval(
                    "SELECT 1 FROM agent_skill_likes WHERE skill_id = $1 AND user_id = $2",
                    skill_id, user_id,
                )
                if exists:
                    await conn.execute(
                        "DELETE FROM agent_skill_likes WHERE skill_id = $1 AND user_id = $2",
                        skill_id, user_id,
                    )
                    await conn.execute(
                        "UPDATE agent_skills SET likes_count = GREATEST(0, likes_count - 1), updated_at = NOW() WHERE id = $1",
                        skill_id,
                    )
                    liked = False
                else:
                    await conn.execute(
                        "INSERT INTO agent_skill_likes (skill_id, user_id) VALUES ($1, $2)",
                        skill_id, user_id,
                    )
                    await conn.execute(
                        "UPDATE agent_skills SET likes_count = likes_count + 1, updated_at = NOW() WHERE id = $1",
                        skill_id,
                    )
                    liked = True

                likes_count = await conn.fetchval("SELECT likes_count FROM agent_skills WHERE id = $1", skill_id)
                return {"skill_id": skill_id, "liked": liked, "likes_count": likes_count}
    async def upsert_rolling_summary(self, chat_id: str, summary_text: str, covered_up_to_id: int) -> None:
        await self.pool.execute("""
            INSERT INTO chat_rolling_summary (chat_id, summary_text, covered_up_to_id, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (chat_id) DO UPDATE
            SET summary_text = $2, covered_up_to_id = $3, updated_at = NOW()
        """, chat_id, summary_text, covered_up_to_id)

    # ─── Telegram-триггеры (агент ставит сам через тулу) ───
    async def create_tg_trigger(self, chat_id: str, added_by_user_id,
                                kind: str, time_msk: str,
                                title=None, days_of_week=None,
                                trigger_date=None, prompt=None,
                                addressed: bool = False) -> dict:
        """Создать запись в telegram_triggers (та же БД, что у chats_service).
        days_of_week — список 1..7 (пн..вс) или None. trigger_date — date для once.
        Сканер chats_service подхватит запись автоматически (proactive_enabled должен быть TRUE)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO telegram_triggers
                    (chat_id, added_by_user_id, kind, title, time_msk,
                     days_of_week, trigger_date, prompt, addressed)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                RETURNING id, chat_id, kind, title, time_msk,
                          days_of_week, trigger_date, prompt, addressed,
                          is_active, created_at
            """, chat_id, added_by_user_id, kind, title, time_msk,
                 days_of_week, trigger_date, prompt, addressed)
        return dict(row)

    async def register_editor_workspace(self, user_id, workspace_id: str,
                                        chat_id=None, title=None) -> dict:
        """Закрепить воркспейс песочницы за юзером в реестре editor_workspaces
        (та же БД, что у chats_service). Читает редактор для дерева проекта."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO editor_workspaces (user_id, workspace_id, chat_id, title)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, workspace_id) DO UPDATE SET
                    chat_id    = COALESCE(EXCLUDED.chat_id, editor_workspaces.chat_id),
                    title      = COALESCE(EXCLUDED.title, editor_workspaces.title),
                    is_active  = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, user_id, workspace_id, chat_id, title, is_active, created_at
            """, int(user_id), str(workspace_id), chat_id, title)
        return dict(row) if row else None

    async def list_editor_workspaces(self, user_id, chat_id=None) -> list[dict]:
        async with self.pool.acquire() as conn:
            if chat_id:
                rows = await conn.fetch("""
                    SELECT id, workspace_id, chat_id, title, created_at
                    FROM editor_workspaces
                    WHERE user_id = $1 AND is_active = TRUE AND chat_id = $2
                    ORDER BY updated_at DESC
                """, int(user_id), str(chat_id))
            else:
                rows = await conn.fetch("""
                    SELECT id, workspace_id, chat_id, title, created_at
                    FROM editor_workspaces
                    WHERE user_id = $1 AND is_active = TRUE
                    ORDER BY updated_at DESC
                """, int(user_id))
        return [dict(r) for r in rows]

    async def list_tg_triggers(self, chat_id: str) -> list[dict]:
        """Активные триггеры чата — чтобы агент мог посмотреть что уже стоит."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, kind, title, time_msk, days_of_week,
                       trigger_date, prompt, addressed, is_active, last_fired_at
                FROM telegram_triggers
                WHERE chat_id = $1 AND is_active = TRUE
                ORDER BY kind, time_msk
            """, chat_id)
        return [dict(r) for r in rows]

    async def deactivate_tg_trigger(self, trigger_id: int, chat_id: str) -> bool:
        """Мягкое удаление триггера (is_active=FALSE), только в своём чате."""
        async with self.pool.acquire() as conn:
            res = await conn.execute("""
                UPDATE telegram_triggers SET is_active = FALSE,
                       updated_at = CURRENT_TIMESTAMP
                WHERE id = $1 AND chat_id = $2
            """, trigger_id, chat_id)
        return res.endswith("1")

