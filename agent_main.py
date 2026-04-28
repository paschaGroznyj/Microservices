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
#                 article_id=item["article_id"],
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
#             article_id = item["article_id"]
#             title = (item.get("title") or "").strip()
#             abstract = (item.get("abstract") or "").strip()

#             if not abstract:
#                 await db.save_article_processing_result(
#                     user_name=user_name,
#                     request_id=request_id,
#                     article_id=article_id,
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
#                 article_id=article_id,
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

from agent_service.app.agent_pm.core.db import AgentDB
from agent_service.app.agent_pm.embeddings import EmbeddingProcessor


async def main(post_body: dict):
    db = AgentDB()
    processor = EmbeddingProcessor()

    await db.init_db_pool()

    try:
        user_name = post_body["user_name"]
        request_id = post_body["request_id"]
        match_threshold = float(post_body.get("match_threshold", 0.65))
        match_count = int(post_body.get("match_count", 20))

        request_data = await db.get_request(user_name, request_id)
        if not request_data:
            raise ValueError(f"Request not found: request_id={request_id}, user_name={user_name}")

        articles = await db.get_request_articles_for_embedding(user_name, request_id)
        if not articles:
            return {
                "user_name": user_name,
                "request_id": request_id,
                "user_query": request_data["user_query"],
                "count": 0,
                "articles": [],
            }

        for article in articles:
            source_text = processor.build_article_text(
                title=article.get("title", ""),
                abstract=article.get("abstract", ""),
                category=article.get("category"),
            )

            if not source_text.strip():
                continue

            embedding = processor.get_embedding(source_text)

            await db.upsert_article_embedding(
                article_id=article["article_id"],
                embedding=embedding,
                embedding_model=processor.model_name,
                source_text=source_text,
            )

        query_embedding = processor.get_embedding(request_data["user_query"])

        matched_articles = await db.search_similar_articles_for_request(
            user_name=user_name,
            request_id=request_id,
            query_embedding=query_embedding,
            embedding_model=processor.model_name,
            match_threshold=match_threshold,
            match_count=match_count,
        )

        return {
            "user_name": user_name,
            "request_id": request_id,
            "user_query": request_data["user_query"],
            "match_threshold": match_threshold,
            "match_count": match_count,
            "count": len(matched_articles),
            "articles": matched_articles,
        }

    finally:
        await db.close()