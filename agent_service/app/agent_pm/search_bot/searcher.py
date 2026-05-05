from agent_pm.core.db import AgentDB
from agent_pm.embeddings import EmbeddingProcessor
from agent_pm.core.agent_provider import get_embedd_model
import logging

logging.basicConfig(level=logging.INFO)

BATCH_SIZE = 32


class ArticleSearcher:
    def __init__(self, db, embedding_model_name: str = "Embeddings-2"):
        self.db = db
        self.embeddings = get_embedd_model()
        self.processor = EmbeddingProcessor(embedding_model_name)
        self.embedding_model_name = embedding_model_name
        self.MAX_CHARS = 800

    def _truncate(self, text: str) -> str:
        """Обрезаем по границе предложения чтобы не резать посередине"""
        if len(text) <= self.MAX_CHARS:
            return text

        # Обрезаем и ищем последнюю точку
        truncated = text[:self.MAX_CHARS]
        last_dot = truncated.rfind(". ")
        if last_dot > self.MAX_CHARS // 2:
            return truncated[:last_dot + 1]

        return truncated

    # ── Эмбеддинги ───────────────────────────────────────────
    async def _embed_articles(self, articles: list[dict]):
        """Считаем и сохраняем эмбеддинги для списка статей батчами"""
        batch_texts = []
        batch_ids = []

        for article in articles:
            source_text = self.processor.build_article_text(
                title=article.get("title", ""),
                abstract=article.get("abstract", ""),
                category=article.get("category"),
            )
            if not source_text.strip():
                continue

            batch_texts.append(self._truncate(source_text))
            batch_ids.append(article["article_id"])

            if len(batch_texts) == BATCH_SIZE:
                await self._flush_batch(batch_ids, batch_texts)
                batch_texts = []
                batch_ids = []

        if batch_texts:
            await self._flush_batch(batch_ids, batch_texts)

    async def _flush_batch(self, ids: list[int], texts: list[str]):
        # Если батч не прошёл — пробуем по одной статье
        try:
            vectors = await self.embeddings.aembed_documents(texts)
        except Exception as e:
            if "413" in str(e) or "Tokens limit" in str(e):
                logging.warning(f"Батч не прошёл по токенам, пробуем по одной: {e}")
                vectors = []
                for text in texts:
                    try:
                        vec = await self.embeddings.aembed_documents([text[:600]])
                        vectors.append(vec[0])
                    except Exception as inner:
                        logging.error(f"Не удалось заэмбеддить даже короткий текст: {inner}")
                        vectors.append([0.0] * 1024)  # нулевой вектор как fallback
            else:
                raise

        articles_data = [
            {"article_id": aid, "embedding": vec, "source_text": txt}
            for aid, vec, txt in zip(ids, vectors, texts)
        ]
        await self.db.upsert_article_embeddings_batch(
            articles_data,
            embedding_model=self.embedding_model_name,
        )

    # ── Поиск ────────────────────────────────────────────────
    async def search(
        self,
        user_name: str,
        request_id: int,
        match_threshold: float = 0.65,
        match_count: int = 20,
    ) -> dict:

        request_data = await self.db.get_request(user_name, request_id)
        if not request_data:
            raise ValueError(f"Request not found: request_id={request_id}, user_name={user_name}")

        articles = await self.db.get_request_articles_for_embedding(user_name, request_id)
        if articles:
            await self._embed_articles(articles)

        query_embedding = await self.embeddings.aembed_query(request_data["user_query"][:self.MAX_LENGTH])

        matched = await self.db.search_similar_articles_for_request(
            user_name=user_name,
            request_id=request_id,
            query_embedding=query_embedding,
            embedding_model=self.embedding_model_name,
            match_threshold=match_threshold,
            match_count=match_count,
        )

        return {
            "user_name": user_name,
            "request_id": request_id,
            "user_query": request_data["user_query"],
            "match_threshold": match_threshold,
            "match_count": match_count,
            "count": len(matched),
            "articles": matched,
        }


# ── Точка входа ──────────────────────────────────────────────
async def searcher_main(post_body: dict, db: AgentDB):
    searcher = ArticleSearcher(db)
    return await searcher.search(
        user_name=post_body["user_name"],
        request_id=post_body["request_id"],
        match_threshold=float(post_body.get("match_threshold", 0.65)),
        match_count=int(post_body.get("match_count", 20)))




# if __name__ == "__main__":
#     import asyncio
#     data = {"user_name": "oppo", "request_id": 296, "match_threshold": 0.80}
#     result = asyncio.run(searcher_main(data))
#     print(result)