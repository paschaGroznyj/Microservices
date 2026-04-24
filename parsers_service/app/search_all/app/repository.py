import json
from db import get_connection


class ResearchRepository:
    def create_run(self, user_query: str, topic: str) -> int:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO pipeline_runs (user_query, topic)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                    (user_query, topic),
                )
                return cur.fetchone()["id"]

    def upsert_article(self, article) -> int:
        authors_json = json.dumps(article.authors or {}, ensure_ascii=False)
        tags_json = json.dumps(article.tags or [], ensure_ascii=False)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO articles (
                        source, external_id, title, date_sub, url_article, pdf_url,
                        authors_json, tags_json, abstract, file_article, category, text_article
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                    ON CONFLICT (source, external_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        date_sub = EXCLUDED.date_sub,
                        url_article = EXCLUDED.url_article,
                        pdf_url = EXCLUDED.pdf_url,
                        authors_json = EXCLUDED.authors_json,
                        tags_json = EXCLUDED.tags_json,
                        abstract = EXCLUDED.abstract,
                        file_article = EXCLUDED.file_article,
                        category = EXCLUDED.category,
                        text_article = EXCLUDED.text_article
                    RETURNING id
                    """,
                    (
                        article.source,
                        article.external_id,
                        article.title,
                        article.date_sub or None,
                        article.url_article,
                        article.pdf_url,
                        authors_json,
                        tags_json,
                        article.abstract,
                        article.file_article,
                        article.category,
                        article.text_article,
                    ),
                )
                return cur.fetchone()["id"]

    def save_review(self, run_id: int, article_id: int, review_markdown: str, review_language: str = "ru") -> int:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reviews (run_id, article_id, review_markdown, review_language)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (run_id, article_id)
                    DO UPDATE SET
                        review_markdown = EXCLUDED.review_markdown,
                        review_language = EXCLUDED.review_language
                    RETURNING id
                    """,
                    (run_id, article_id, review_markdown, review_language),
                )
                return cur.fetchone()["id"]
