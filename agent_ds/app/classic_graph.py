import asyncio
import logging

from requests import session
from sympy.physics.optics import quarter_wave_retarder

import gigachat.context
from db.app_db import DataBaseMessages
from typing import List, Dict, Any, Optional
from db.graph_db import GraphStore
from prompts_graph import QUERY_SYSTEM_PROMPT, BATCH_SYSTEM_PROMPT
from langchain.schema import SystemMessage, HumanMessage
from core.model import get_model
from core.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
import json
from neo4j import AsyncGraphDatabase
import re

giga = get_model()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def normalize_relation(rel: str) -> str:
    rel = rel.upper().strip()

    mapping = {
        "DEVELOP_BY": "DEVELOPED_BY",
        "DEVELOPED": "DEVELOPED_BY",
        "DEVELOPER_OF": "DEVELOPED_BY",

        "CREATE": "CREATED_BY",
        "CREATED": "CREATED_BY",

        "ACQUIRED": "ACQUIRED_BY",
    }

    return mapping.get(rel, rel)

async def insert_graph_data(graph: GraphStore, data: List[Dict[str, Any]]):

    async with graph.driver.session() as session:

        stats = []
        for item in data:
            try:
                if not isinstance(item, dict):
                    continue

                document_id = item.get("document_id")
                if not document_id:
                    continue

                chunk_id = item.get("chunk_id")
                if not chunk_id:
                    continue

                chunk_index = item.get("chunk_index")

                try:
                    await session.execute_write(
                        graph.merge_document,
                        document_id
                    )
                except Exception as e:
                    stats.append(f"Ошибка при вставке {e}")
                    continue

                try:
                    await session.execute_write(
                        graph.merge_chunk,
                        chunk_id,
                        chunk_index,
                        document_id
                    )
                except Exception as e:
                    stats.append(f"Ошибка при вставке {e}")
                    continue

                entities = item.get("entities", [])
                if not isinstance(entities, list):
                    entities = []

                # entities
                for ent in entities:

                    name = ent.get("name")
                    ent_type = ent.get("type")

                    if not name:
                        continue

                    if not ent_type:
                        ent_type = "UNKNOWN"

                    await session.execute_write(
                        graph.merge_entity,
                        name,
                        ent_type
                    )

                    await session.execute_write(
                        graph.link_entity_chunk,
                        name,
                        chunk_id
                    )

                # relations
                entity_names = {
                    e.get("name")
                    for e in entities
                    if e.get("name")
                }# Чтобы не проскакивали сторонние source

                relations = item.get("relations", [])
                for rel in relations:

                    source = rel.get("source")
                    target = rel.get("target")

                    if not source or not target:
                        continue

                    if source not in entity_names: continue
                    if target not in entity_names: continue

                    normalized_rel = normalize_relation(rel["type"])

                    await session.execute_write(
                        graph.merge_relation,
                        rel["source"],
                        rel["target"],
                        normalized_rel
                    )
            except Exception as e:
                stats.append(e)
        print(stats)


async def extract_entities_and_relations_batch(
        chunks: List[Dict[str, any]],
        batch_size: int = 3,
        model=None,
        document_id: Optional[int] = None
) -> List[Dict]:
    results = []

    # Индекс для быстрого доступа к соседям
    chunks_by_index = {c["chunk_index"]: c for c in chunks}
    chunk_meta = {
        c["id"]: {"chunk_index": c["chunk_index"], "document_id": c["document_id"]}
        for c in chunks
    }

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]

        user_content = "Process these text chunks:\n\n"
        for chunk in batch:
            idx = chunk["chunk_index"]

            # Берём соседей для контекста
            prev_chunk = chunks_by_index.get(idx - 1)
            next_chunk = chunks_by_index.get(idx + 1)

            text = chunk["content"].replace('"""', '"')

            # Добавляем контекст соседей серым текстом
            context_before = f"[CONTEXT: ...{prev_chunk['content'][-150:]}]\n" if prev_chunk else ""
            context_after = f"\n[CONTEXT: {next_chunk['content'][:150]}...]" if next_chunk else ""

            user_content += (
                f"### CHUNK {chunk['id']}:\n"
                f"{context_before}"
                f"{text}"
                f"{context_after}\n\n"
            )

        user_content += "\nExtract entities and relations ONLY from the main chunk text, not from [CONTEXT] blocks. Return JSON for each chunk_id."

        try:
            response = model.invoke([
                SystemMessage(content=BATCH_SYSTEM_PROMPT),
                HumanMessage(content=user_content)
            ])
            logger.info(response.response_metadata)
            metadata = dict(response.response_metadata)

            token_usage = dict(metadata.get("token_usage", {}))
            cached = token_usage.get("precached_prompt_tokens", 0)
            prompt = token_usage.get("prompt_tokens", 0)
            total = token_usage.get("total_tokens", 0)

            logger.info(
                f"doc={document_id} batch={i // batch_size + 1} | "
                f"prompt={prompt} cached={cached} total={total} | "
                f"cache_ratio={cached / (prompt + cached) * 100:.0f}%" if (prompt + cached) > 0 else "cache_ratio=0%"
            )
            parsed = parse_json_response(response.content)

            if parsed and "results" in parsed:
                for item in parsed["results"]:
                    chunk_id = item.get("chunk_id")
                    meta = chunk_meta.get(chunk_id, {})
                    results.append({
                        "document_id": meta.get("document_id"),
                        "chunk_id": chunk_id,
                        "chunk_index": meta.get("chunk_index"),
                        "entities": item.get("entities", []),
                        "relations": item.get("relations", [])
                    })
            else:
                logger.warning(f"Unexpected format for batch {i // batch_size + 1}")

        except Exception as e:
            logger.error(f"❌ Error batch {i // batch_size + 1}: {e}")
            for chunk in batch:
                results.append({
                    "document_id": document_id,
                    "chunk_id": chunk["id"],
                    "chunk_index": chunk["chunk_index"],
                    "entities": [],
                    "relations": [],
                    "error": str(e)
                })

    return results


def parse_json_response(text: str) -> Optional[Dict]:
    """
    Извлекает и парсит JSON из ответа модели.
    Убирает markdown-обёртки, если они есть.
    """
    try:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        cleaned = cleaned.strip()

        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None


async def generate_entities_and_relations(db: DataBaseMessages, graph: GraphStore):
    # await db.init_db_pool()
    rows = await db.select_chunks_graph()
    chunks_in_graph = await graph.get_all_chunks()
    docs_chunks = {}

    counts_chunks = [i for i in range(50, 300, 50)]

    for row in rows:
        row = dict(row)
        if row["id"] in chunks_in_graph:
            continue

        docs_chunks.setdefault(row["document_id"], []).append({
            "id": row["id"],
            "content": row["content"],
            "chunk_index": row["chunk_index"],
            "document_id": row["document_id"]
        })

    all_results = []

    gigachat.context.session_id_cvar.set("graph-extract")
    logger.info("Session ID установлен: graph-extract")
    for document_id, chunks in docs_chunks.items():
        print(f"📄 Processing document {document_id} ({len(chunks)} chunks)...")

        results = await extract_entities_and_relations_batch(
            chunks=chunks,
            batch_size=10,
            model=giga
        )
        all_results.extend(results)

    return all_results

def match_entities_and_relations_llm(query: str):
    response = giga.invoke([
        SystemMessage(content=QUERY_SYSTEM_PROMPT),
        HumanMessage(content=query)
    ]).content
    return response


async def search_in_graph(db: DataBaseMessages, graph: GraphStore, query: str):
    response = match_entities_and_relations_llm(query=query)
    json_answer = json.loads(response)
    logger.info(f"Parsed query: {json_answer}")

    entity_chunks = set()
    relation_chunks = set()
    rel_type_chunks = set()

    # 1. Поиск по сущностям
    for entity in json_answer.get("entities", []):
        ids = await graph.get_chunks_by_entity(entity["name"])
        entity_chunks.update(ids)

    for rel in json_answer.get("relations", []):
        source = rel.get("source", "").strip()
        target = rel.get("target", "").strip()
        rel_type = rel.get("type", "").strip()

        # 2. Поиск по паре сущностей (оба конца известны)
        if source and target:
            ids = await graph.get_chunks_by_relation(source, target)
            relation_chunks.update(ids)

        # 3. Поиск по сущность + тип связи (один конец пустой)
        elif rel_type and (source or target):
            ids = await graph.get_chunks_by_relation_type(source, target, rel_type)
            rel_type_chunks.update(ids)

    # Приоритет: точные триплеты > тип связи > просто сущности
    # Если точные совпадения есть — не разбавляем общими
    if relation_chunks:
        final_chunks = list(relation_chunks | rel_type_chunks)
    elif rel_type_chunks:
        final_chunks = list(rel_type_chunks)
    else:
        final_chunks = list(entity_chunks)

    logger.info(
        f"Chunks found — entity: {len(entity_chunks)}, "
        f"relation: {len(relation_chunks)}, "
        f"rel_type: {len(rel_type_chunks)}, "
        f"final: {len(final_chunks)}"
    )

    chunks_data = await db.select_text_chunks(final_chunks)
    return chunks_data

async def main():
    db = DataBaseMessages()
    graph = GraphStore(
        NEO4J_URI,
        NEO4J_USER,
        NEO4J_PASSWORD
    )
    await db.init_db_pool()
    # all_results = await generate_entities_and_relations(db, graph) # Для формирования Graph
    # for i in all_results:
    #     logger.info(i)



    # await insert_graph_data(graph, all_results)


    # query = "А гигачат у нас это чья модель?"
    # await search_in_graph(query=query, graph=graph, db=db)

    # result = await graph.get_all_chunks()
    # print(result)


    # entities = await graph.get_all_entities()
    #
    # chunks = await graph.get_chunks_by_entity("Cursor")

    # for e in chunks:
    #     print(e)

    await graph.close()

if __name__ == "__main__":
    asyncio.run(main())