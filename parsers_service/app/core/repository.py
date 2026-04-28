import json
import logging
from dataclasses import asdict
from datetime import datetime, date

def to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    raise ValueError(f"Unsupported date value: {value!r}")

class ResearchRepository:
    def __init__(self, db):
        self.db = db

    async def get_or_create_user(self, user_name: str) -> int:
        async with self.db.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO research_users (user_name)
                VALUES ($1)
                ON CONFLICT (user_name)
                DO UPDATE SET user_name = EXCLUDED.user_name
                RETURNING id
            """, user_name)

    async def get_user_id_by_name(self, user_name: str) -> int:
        async with self.db.pool.acquire() as conn:
            return await conn.fetchval("""
                                          SELECT id
                                          FROM users
                                          WHERE username = $1
                                          """, user_name)
    async def create_search_request(
        self,
        user_id: int,
        user_query: str,
        params,
    ) -> int:
        params_dict = asdict(params)
        params_json = json.dumps(params_dict, ensure_ascii=False)
        keywords_json = json.dumps(params.keywords or [], ensure_ascii=False)

        async with self.db.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO search_requests (
                    user_id,
                    user_query,
                    params_json,
                    date_start,
                    date_end,
                    keywords_json,
                    limit_value
                )
                VALUES (
                    $1,
                    $2,
                    $3::jsonb,
                    $4,
                    $5,
                    $6::jsonb,
                    $7
                )
                RETURNING id
            """,
                user_id,
                user_query,
                params_json,
                to_date(params.date_start),
                to_date(params.date_end),
                keywords_json,
                params.limit,
            )

    async def upsert_article(self, article) -> int:
        authors_json = json.dumps(article.authors or [], ensure_ascii=False)
        tags_json = json.dumps(article.tags or [], ensure_ascii=False)

        async with self.db.pool.acquire() as conn:
            return await conn.fetchval("""
                INSERT INTO articles (
                    source,
                    external_id,
                    title,
                    date_sub,
                    url_article,
                    pdf_url,
                    authors_json,
                    tags_json,
                    abstract,
                    category,
                    updated_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    $6,
                    $7::jsonb,
                    $8::jsonb,
                    $9,
                    $10,
                    NOW()
                )
                ON CONFLICT (source, external_id)
                DO UPDATE SET
                    title = EXCLUDED.title,
                    date_sub = EXCLUDED.date_sub,
                    url_article = EXCLUDED.url_article,
                    pdf_url = EXCLUDED.pdf_url,
                    authors_json = EXCLUDED.authors_json,
                    tags_json = EXCLUDED.tags_json,
                    abstract = EXCLUDED.abstract,
                    category = EXCLUDED.category,
                    updated_at = NOW()
                RETURNING id
            """,
                article.source,
                article.external_id,
                article.title,
                to_date(article.date_sub),
                article.url_article,
                article.pdf_url,
                authors_json,
                tags_json,
                article.abstract,
                article.category,
            )

    async def link_user_article(self, user_id: int, article_id: int):
        async with self.db.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_articles (user_id, article_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, article_id)
                DO NOTHING
            """, user_id, article_id)

    async def link_request_article(self, request_id: int, article_id: int):
        async with self.db.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO request_articles (request_id, article_id)
                VALUES ($1, $2)
                ON CONFLICT (request_id, article_id)
                DO NOTHING
            """, request_id, article_id)

    async def save_search_results(
        self,
        user_name: str,
        user_query: str,
        params,
        articles: list,
    ) -> dict:
        user_id = await self.get_user_id_by_name(user_name)
        print(user_id)
        request_id = await self.create_search_request(
            user_id=user_id,
            user_query=user_query,
            params=params,
        )

        saved_articles = []

        for article in articles:
            try:
                article_id = await self.upsert_article(article)
                await self.link_user_article(user_id, article_id)
                await self.link_request_article(request_id, article_id)

                saved_articles.append({
                    "article_id": article_id,
                    "external_id": article.external_id,
                    "title": article.title,
                    "authors": article.authors,
                    "tags": article.tags,
                    "date": article.date_sub,
                    "link": article.url_article,
                    "abstract": article.abstract,
                    "source": article.source,
                })
            except Exception as e:
                logging.exception(f"Failed to save article {article.external_id}: {e}")

        return {
            "request_id": request_id,
            "user_name": user_name,
            "user_query": user_query,
            "count": len(saved_articles),
            "articles": saved_articles,
        }

    async def get_user_requests(self, user_name: str, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    sr.id,
                    ru.username,
                    sr.user_query,
                    sr.params_json,
                    sr.date_start,
                    sr.date_end,
                    sr.keywords_json,
                    sr.limit_value,
                    sr.created_at
                FROM search_requests sr
                JOIN users ru ON ru.id = sr.user_id
                WHERE ru.username = $1
                ORDER BY sr.created_at DESC
                LIMIT $2 OFFSET $3
            """, user_name, limit, offset)

            return [dict(row) for row in rows]

    async def get_request_articles(self, request_id: int, limit: int = 100, offset: int = 0) -> list[dict]:
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.source,
                    a.external_id,
                    a.title,
                    a.date_sub,
                    a.url_article,
                    a.pdf_url,
                    a.authors_json,
                    a.tags_json,
                    a.abstract,
                    a.category,
                    ra.created_at AS linked_at
                FROM request_articles ra
                JOIN articles a ON a.id = ra.article_id
                WHERE ra.request_id = $1
                ORDER BY a.date_sub DESC NULLS LAST, a.id DESC
                LIMIT $2 OFFSET $3
            """, request_id, limit, offset)

            return [dict(row) for row in rows]

    async def get_user_articles(self, user_name: str, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    ru.username,
                    a.source,
                    a.external_id,
                    a.title,
                    a.date_sub,
                    a.url_article,
                    a.pdf_url,
                    a.authors_json,
                    a.tags_json,
                    a.abstract,
                    a.category,
                    ua.created_at AS saved_at
                FROM user_articles ua
                JOIN users ru ON ru.id = ua.user_id
                JOIN articles a ON a.id = ua.article_id
                WHERE ru.username = $1
                ORDER BY ua.created_at DESC
                LIMIT $2 OFFSET $3
            """, user_name, limit, offset)

            return [dict(row) for row in rows]