import asyncio
import asyncpg
import logging
import os

from .config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

EMB_DIM = "1024"

class AgentDB:
    def __init__(self):
        self.pool = None

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

    async def close(self):
        if self.pool:
            await self.pool.close()

    @staticmethod
    def _vector_to_str(vector: list[float]) -> str:
        return "[" + ",".join(map(str, vector)) + "]"

    async def init_schema(self, conn: asyncpg.Connection):
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS article_embeddings (
                id BIGSERIAL PRIMARY KEY,
                article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                embedding vector(1024) NOT NULL,
                embedding_model TEXT NOT NULL,
                source_text TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (article_id, embedding_model)
            );
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
                ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP;
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

    async def insert_query(self, username: str, query: str) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("""
                    INSERT INTO search_requests (user_id, user_query, status)
                    SELECT id, $2, $3
                    FROM users
                    WHERE username = $1
                    RETURNING id
                """, username, query, 'pending')

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

