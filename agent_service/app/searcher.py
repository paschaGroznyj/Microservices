# from agent_service.app.agent_pm.core.db import AgentDB


# async def main(post_body: dict):
#     db = AgentDB()
#     await db.init_db_pool()

#     try:
#         user_name = post_body["user_name"]
#         request_id = post_body["request_id"]

#         request_data = await db.get_request(user_name, request_id)
#         if not request_data:
#             raise ValueError("Request not found")

#         items = await db.get_request_articles(user_name, request_id)
#         if not items:
#             return {
#                 "user_name": user_name,
#                 "request_id": request_id,
#                 "count": 0,
#                 "articles": [],
#             }

#         for i, item in enumerate(items, start=1):
#             translated_text = f"Тестовый перевод #{i}: {item['title']}"
#             is_relevant = i % 2 == 1

#             await db.save_article_processing_result(
#                 user_name=user_name,
#                 request_id=request_id,
#                 articles_id=item["articles_id"],
#                 translated_text=translated_text,
#                 is_relevant=is_relevant,
#             )

#         result_items = await db.get_request_articles_for_front(user_name, request_id)

#         return {
#             "user_name": user_name,
#             "request_id": request_id,
#             "count": len(result_items),
#             "articles": result_items,
#         }

#     finally:
#         await db.close()
# from agent_service.app.agent_pm.core.db import AgentDB

# from agent_service.app.agent_pm.translate_relevant import LLMProcessor

# async def main(post_body: dict):
#     db = AgentDB()
#     llm = LLMProcessor()

#     await db.init_db_pool()

#     try:
#         user_name = post_body["user_name"]
#         request_id = post_body["request_id"]

#         request_data = await db.get_request(user_name, request_id)
#         if not request_data:
#             raise ValueError(f"Request not found: request_id={request_id}, user_name={user_name}")

#         items = await db.get_request_articles_for_llm(user_name, request_id)
#         if not items:
#             return {
#                 "user_name": user_name,
#                 "request_id": request_id,
#                 "count": 0,
#                 "articles": [],
#             }

#         for item in items:
#             articles_id = item["articles_id"]
#             title = (item.get("title") or "").strip()
#             abstract = (item.get("abstract") or "").strip()

#             if not abstract:
#                 await db.save_article_processing_result(
#                     user_name=user_name,
#                     request_id=request_id,
#                     articles_id=articles_id,
#                     translated_text="",
#                     is_relevant=False,
#                 )
#                 continue

#             result = llm.process_article(
#                 user_query=request_data["user_query"],
#                 title=title,
#                 abstract=abstract,
#             )

#             await db.save_article_processing_result(
#                 user_name=user_name,
#                 request_id=request_id,
#                 articles_id=articles_id,
#                 translated_text=result["translated_text"],
#                 is_relevant=result["is_relevant"],
#             )

#         articles = await db.get_request_articles_for_front(user_name, request_id)

#         return {
#             "user_name": user_name,
#             "request_id": request_id,
#             "user_query": request_data["user_query"],
#             "count": len(articles),
#             "articles": articles,
#         }

#     finally:
#         await db.close()

# from agent_pm.core.db import AgentDB
# from agent_pm.embeddings import EmbeddingProcessor
# from agent_pm.core.agent_provider import get_embedd_model
#
# db = AgentDB()
# embeddings = get_embedd_model()
# processor = EmbeddingProcessor("Embeddings-2")
# BATCH_SIZE = 32
#
# async def init_classes():
#     await db.init_db_pool()
#
# async def main(post_body: dict):
#     await init_classes()
#     try:
#         user_name = post_body["user_name"]
#         request_id = post_body["request_id"]
#         match_threshold = float(post_body.get("match_threshold", 0.65))
#         match_count = int(post_body.get("match_count", 20))
#
#         request_data = await db.get_request(user_name, request_id)
#         print("ЗАПРОСЫ ПОЛЬЗОВАТЕЛЯ", request_data)
#         if not request_data:
#             raise ValueError(f"Request not found: request_id={request_id}, user_name={user_name}")
#
#         articles = await db.get_request_articles_for_embedding(user_name, request_id)
#         print("СТАТЬИ", articles)
#         if articles:
#             # return {
#             #     "user_name": user_name,
#             #     "request_id": request_id,
#             #     "user_query": request_data["user_query"],
#             #     "count": 0,
#             #     "articles": [],
#             # }
#             batch_texts = []
#             articles_id = []
#
#             for article in articles:
#                 source_text = processor.build_article_text(
#                     title=article.get("title", ""),
#                     abstract=article.get("abstract", ""),
#                     category=article.get("category"),
#                 )
#                 if not source_text.strip():
#                     continue
#
#                 batch_texts.append(source_text)
#                 articles_id.append(article["article_id"])
#
#                 if len(batch_texts) == BATCH_SIZE:
#                     vectors = await embeddings.aembed_documents(batch_texts)
#                     articles_data = [
#                         {"article_id": aid, "embedding": vec, "source_text": txt}
#                         for aid, vec, txt in zip(articles_id, vectors, batch_texts)
#                     ]
#                     await db.upsert_article_embeddings_batch(articles_data, embedding_model="Embeddings-2")
#                     batch_texts = []
#                     articles_id = []
#
#
#             if batch_texts:
#                 vectors = await embeddings.aembed_documents(batch_texts)
#                 articles_data = [
#                     {"article_id": aid, "embedding": vec, "source_text": txt}
#                     for aid, vec, txt in zip(articles_id, vectors, batch_texts)
#                 ]
#                 await db.upsert_article_embeddings_batch(articles_data, embedding_model="Embeddings-2")
#
#
#         # query_embedding = await embeddings.aembed_query(request_data["user_query"])
#         query_embedding = await embeddings.aembed_query("Хочется найти что нибудь по llm моделькам")
#
#         matched_articles = await db.search_similar_articles_for_request(
#             user_name=user_name,
#             request_id=request_id,
#             query_embedding=query_embedding,
#             embedding_model=processor.model_name,
#             match_threshold=match_threshold,
#             match_count=match_count,
#         )
#         print(len(matched_articles))
#         return {
#             "user_name": user_name,
#             "request_id": request_id,
#             "user_query": request_data["user_query"],
#             "match_threshold": match_threshold,
#             "match_count": match_count,
#             "count": len(matched_articles),
#             "articles": matched_articles,
#         }
#
#     finally:
#         await db.close()
#
# import asyncio
#
# data = {"user_name": "oppo",
#         "request_id": 143,
#         "match_threshold": 0.80}
# asyncio.run(main(data))



from agent_pm.core.db import AgentDB
from agent_pm.embeddings import EmbeddingProcessor
from agent_pm.core.agent_provider import get_embedd_model

BATCH_SIZE = 32


class ArticleSearcher:
    def __init__(self, embedding_model_name: str = "Embeddings-2"):
        self.db = AgentDB()
        self.embeddings = get_embedd_model()
        self.processor = EmbeddingProcessor(embedding_model_name)
        self.embedding_model_name = embedding_model_name

    async def init(self):
        await self.db.init_db_pool()

    async def close(self):
        await self.db.close()

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

            batch_texts.append(source_text)
            batch_ids.append(article["article_id"])

            if len(batch_texts) == BATCH_SIZE:
                await self._flush_batch(batch_ids, batch_texts)
                batch_texts = []
                batch_ids = []

        if batch_texts:
            await self._flush_batch(batch_ids, batch_texts)

    async def _flush_batch(self, ids: list[int], texts: list[str]):
        vectors = await self.embeddings.aembed_documents(texts)
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

        query_embedding = await self.embeddings.aembed_query(self._truncate(request_data["user_query"]))

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
async def main(post_body: dict):
    searcher = ArticleSearcher()
    await searcher.init()
    try:
        return await searcher.search(
            user_name=post_body["user_name"],
            request_id=post_body["request_id"],
            match_threshold=float(post_body.get("match_threshold", 0.80)),
            match_count=int(post_body.get("match_count", 20)), # В БД отключена
        )
    finally:
        await searcher.close()


if __name__ == "__main__":
    import asyncio
    data = {"user_name": "oppo", "request_id": 166, "match_threshold": 0.75}
    result = asyncio.run(main(data))
    print(result)