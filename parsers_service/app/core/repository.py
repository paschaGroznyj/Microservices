import re
import json
import copy
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

    async def update_request_params(self, request_id: int, params):
        """
        Обновляет параметры существующего запроса (keywords, dates, limit)
        и меняет статус на 'processing'.
        Используется, когда Агент передал управление Парсеру.
        """
        params_dict = asdict(params)
        params_json = json.dumps(params_dict, ensure_ascii=False)
        keywords_json = json.dumps(params.keywords or [], ensure_ascii=False)

        async with self.db.pool.acquire() as conn:
            await conn.execute("""
                               UPDATE search_requests
                               SET params_json   = $2::jsonb,
                                   keywords_json = $3::jsonb,
                                   date_start    = $4,
                                   date_end      = $5,
                                   limit_value   = $6,
                                   status        = 'processing'
                               WHERE id = $1
                               """,
                               request_id,
                               params_json,
                               keywords_json,
                               to_date(params.date_start) if params.date_start else None,
                               to_date(params.date_end) if params.date_end else None,
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
        request_id: int = None,
    ) -> dict:
        user_id = await self.get_user_id_by_name(user_name)
        if request_id:
            await self.update_request_params(request_id, params)
        else:
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
                    "date_sub": article.date_sub,
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

    async def toggle_favorite(self, username: str, article_id: int, is_favorite: bool):
        async with self.db.pool.acquire() as conn:
            if is_favorite:
                # Добавляем в избранное
                # ON CONFLICT DO NOTHING предотвратит ошибку, если статья уже в избранном
                await conn.execute("""
                                    INSERT INTO favorites (user_id, article_id)
                                    SELECT id, $2
                                    FROM users
                                    WHERE username = $1
                                    ON CONFLICT (user_id, article_id) DO NOTHING;
                                   """, username, article_id)
            else:
                # Удаляем из избранного
                await conn.execute("""
                                   DELETE
                                   FROM favorites
                                   WHERE user_id = (SELECT id
                                    FROM users
                                    WHERE username = $1)
                                     AND article_id = $2;
                                   """, username, article_id)

    async def get_favorites(self, username: str):
        """Тянем список избранных статей для конкретного пользователя"""
        async with self.db.pool.acquire() as conn:
            return await conn.fetch("""
                                    SELECT a.id AS article_id,
                                        a.external_id,
                                        a.title,
                                        a.date_sub,
                                        a.url_article,
                                        a.authors_json,
                                        a.tags_json,
                                        a.abstract,
                                        a.category
                                    FROM favorites f
                                             JOIN users u ON f.user_id = u.id
                                             JOIN articles a ON f.article_id = a.id
                                    WHERE u.username = $1
                                    ORDER BY f.created_at DESC;""", username)
    
    async def get_favorites_with_reviews(self, username: str) -> list[dict]:
        """
        Возвращает избранные статьи пользователя + его review по каждой статье.
        Если review отсутствует, поле review будет None.
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    a.id AS article_id,
                    a.external_id,
                    a.title,
                    a.date_sub,
                    a.url_article,
                    a.pdf_url,
                    a.authors_json,
                    a.tags_json,
                    a.abstract,
                    a.category,
                    r.review_text,
                FROM favorites f
                JOIN articles a ON f.article_id = a.id
                LEFT JOIN arcticle_reviews r
                    ON r.article_id = a.id
                   AND r.user_id = u.id
                WHERE u.username = $1
                ORDER BY f.created_at DESC
            """, username)

    # async def get_articles_by_keywords(
    #         self,
    #         keywords: list[str],
    #         categories: list[str],
    #         user_name: str
    # ) -> list[dict]:
    #     async with self.db.pool.acquire() as conn:
    #         rows = await conn.fetch("""
    #                                 SELECT DISTINCT a.id           AS article_id,
    #                                                 a.title,
    #                                                 a.authors_json AS authors,
    #                                                 a.date_sub     AS date,
    #                                                 a.url_article  AS link,
    #                                                 a.tags_json    AS tags,
    #                                                 a.abstract,
    #                                                 a.category
    #                                 FROM articles a
    #                                 WHERE (
    #                                     -- По категориям arXiv — кастуем JSONB в TEXT
    #                                     --EXISTS (
    #                                       --  SELECT 1
    #                                        -- FROM unnest($2::text[]) AS cat
    #                                        -- WHERE a.tags_json::text ILIKE '%' || cat || '%'
    #                                    -- )
    #                                    -- OR
    #                                     -- По ключевым словам в заголовке и аннотации
    #                                     EXISTS (
    #                                         SELECT 1
    #                                         FROM unnest($1::text[]) AS kw
    #                                         WHERE a.title ILIKE '%' || kw || '%'
    #                                            OR a.abstract ILIKE '%' || kw || '%'
    #                                     )
    #                                 )
    #                                 ORDER BY a.date_sub DESC
    #                                 LIMIT 50;
    #         """, keywords)
    #
    #         return [dict(row) for row in rows]


    async def get_articles_by_keywords(
            self,
            keywords: list[list[str]],  # Теперь ожидаем список списков
            categories: list[str],
            user_name: str
    ) -> list[dict]:

        query_parts = []

        for group in keywords:
            clean_words = []
            for kw in group:
                cleaned = re.sub(r'[^a-zA-Z0-9]', '', kw).strip()
                if cleaned:
                    clean_words.append(cleaned)

            if clean_words:
                group_query = " & ".join(clean_words)
                query_parts.append(f"({group_query})")

        if not query_parts:
            return []

        # Результат: "(process & mining) | (task & mining)"
        final_ts_query = " | ".join(query_parts)

        logging.info(f"TS Query: {final_ts_query}")

        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch("""
                                    SELECT DISTINCT 
                                            a.id           AS article_id,
                                            a.source       AS source,            -- Добавлено
                                            a.external_id  AS external_id,       -- Добавлено
                                            a.title        AS title,
                                            a.date_sub     AS date_sub,          -- Унифицировано с первым запросом (было date)
                                            a.url_article  AS url_article,       -- Унифицировано (было link)
                                            a.pdf_url      AS pdf_url,           -- Добавлено
                                            a.authors_json AS authors_json,      -- Унифицировано (было authors)
                                            a.tags_json    AS tags_json,         -- Унифицировано (было tags)
                                            a.abstract     AS abstract,
                                            a.category     AS category
                                    FROM articles a
                                    WHERE to_tsvector('english', a.title) @@ to_tsquery('english', $1)
                                       OR to_tsvector('english', a.abstract) @@ to_tsquery('english', $1)
                                    ORDER BY a.date_sub DESC
                                    LIMIT 50;
                                    """, final_ts_query)

            return [dict(row) for row in rows]


# from db import DataBase
# import asyncio
#
# db = DataBase()
#
# research = ResearchRepository(db)
#
# async def main():
#     await db.init_db_pool()
#     result = await research.get_favorites('oppo')
#     for i in result:
#         print(i["article_id"])
#
# asyncio.run(main())