import asyncio
import asyncpg
import logging
import os
from .config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


class DataBase:
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
                    max_size=10,
                    max_queries=50000,
                    max_inactive_connection_lifetime=300,
                    command_timeout=60,
                    timeout=60,
                    statement_cache_size=100,
                )

                async with self.pool.acquire() as conn:
                    await self.init_schema(conn)

                logging.info("DB pool initialized")
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

    async def init_schema(self, conn):
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS search_requests (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                user_query TEXT NOT NULL,
                params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                date_start DATE,
                date_end DATE,
                keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                limit_value INTEGER NOT NULL DEFAULT 20,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS articles (
                id BIGSERIAL PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'arxiv',
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                date_sub DATE,
                url_article TEXT,
                pdf_url TEXT,
                authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                abstract TEXT,
                category TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (source, external_id)
            );

            CREATE TABLE IF NOT EXISTS user_articles (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, article_id)
            );

            CREATE TABLE IF NOT EXISTS request_articles (
                id BIGSERIAL PRIMARY KEY,
                request_id BIGINT NOT NULL REFERENCES search_requests(id) ON DELETE CASCADE,
                article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (request_id, article_id)
            );
            
            CREATE TABLE IF NOT EXISTS favorites (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE (user_id, article_id)
            );
        
            CREATE INDEX IF NOT EXISTS ix_research_users_user_name
                ON users(username);

            CREATE INDEX IF NOT EXISTS ix_search_requests_user_id
                ON search_requests(user_id);

            CREATE INDEX IF NOT EXISTS ix_search_requests_created_at
                ON search_requests(created_at DESC);

            CREATE INDEX IF NOT EXISTS ix_articles_external_id
                ON articles(external_id);

            CREATE INDEX IF NOT EXISTS ix_articles_date_sub
                ON articles(date_sub DESC);

            CREATE INDEX IF NOT EXISTS ix_user_articles_user_id
                ON user_articles(user_id);

            CREATE INDEX IF NOT EXISTS ix_user_articles_article_id
                ON user_articles(article_id);

            CREATE INDEX IF NOT EXISTS ix_request_articles_request_id
                ON request_articles(request_id);

            CREATE INDEX IF NOT EXISTS ix_request_articles_article_id
                ON request_articles(article_id);
            
            CREATE INDEX IF NOT EXISTS idx_favorites_user_id 
                ON favorites(user_id);
            
            ALTER TABLE search_requests ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'pending';
            ALTER TABLE search_requests ADD COLUMN IF NOT EXISTS error_message TEXT;
        """)