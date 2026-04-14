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
    """
    Обрабатывает чанки батчами и возвращает плоский список результатов.

    Returns:
        [
            {
                "document_id": 303,
                "chunk_id": 1202,
                "chunk_index": 2,
                "entities": [...],
                "relations": [...]
            },
            ...
        ]
    """
    results = []

    chunk_meta = {c["id"]: {"chunk_index": c["chunk_index"], "document_id": c["document_id"]}
                  for c in chunks}

    # Разбиваем чанки на батчи
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]

        # Формируем пользовательский промпт с нумерованными чанками
        user_content = "Process these text chunks:\n\n"
        for chunk in batch:
            # Экранируем возможные конфликты в тексте
            text = chunk["content"].replace('"""', '"')
            user_content += f'### CHUNK {chunk["id"]}:\n{text}\n\n'

        user_content += "\nReturn JSON with results for EACH chunk_id."

        try:
            response = model.invoke([
                SystemMessage(content=BATCH_SYSTEM_PROMPT),
                HumanMessage(content=user_content)
            ])

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
                print(f"Unexpected response format for batch {i // batch_size + 1}")

        except Exception as e:
            print(f"❌ Error processing batch {i // batch_size + 1}: {e}")
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
    found_entity_names = []

    # 1. Поиск по сущностям
    for entity in json_answer.get("entities", []):
        found_entity_names.append(entity["name"])
        ids = await graph.get_chunks_by_entity(entity["name"])
        entity_chunks.update(ids)

    for rel in json_answer.get("relations", []):
        source = rel.get("source", "").strip()
        target = rel.get("target", "").strip()
        rel_type_raw = rel.get("type", "")

        if source:
            found_entity_names.append(source)
        if target:
            found_entity_names.append(target)

        if isinstance(rel_type_raw, str):
            rel_types = [rel_type_raw.strip()] if rel_type_raw.strip() else []
        elif isinstance(rel_type_raw, list):
            rel_types = [t.strip() for t in rel_type_raw if t.strip()]
        else:
            rel_types = []

        if source and target:
            ids = await graph.get_chunks_by_relation(source, target)
            relation_chunks.update(ids)
        elif source or target:
            for rel_type in rel_types:
                ids = await graph.get_chunks_by_relation_type(source, target, rel_type)
                rel_type_chunks.update(ids)

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

    # Получаем связи для всех найденных сущностей
    relations = []
    if found_entity_names:
        relations = await graph.get_relations_for_entities(found_entity_names)

    logger.info(f"Relations found: {len(relations)}")

    return {
        "chunks": chunks_data[:4],
        "relations": relations
    }


def build_answer_prompt(query: str, chunks: list, relations: list) -> str:
    chunks_text = "\n\n".join([
        f"[Чанк {c['chunk_id']}]: {c['content']}"
        for c in chunks
    ])

    relations_text = "\n".join([
        f"- {r['source']} --[{r['relation']}]--> {r['target']}"
        for r in relations
    ])

    return f"""Ответь на вопрос, используя предоставленные фрагменты текста и граф связей.

Граф связей между сущностями:
{relations_text}

Текстовые фрагменты:
{chunks_text}

Вопрос: {query}

Ответ:"""


# ─── Вспомогательные функции ───────────────────────────────────────────────────
def relations_to_text(relations: list[dict]) -> str:
    """Конвертирует список связей графа в человекочитаемый текст"""
    if not relations:
        return ""

    lines = [
        f"{r['source']} {r['relation'].lower().replace('_', ' ')} {r['target']}"
        for r in relations
    ]
    return "Известные связи между сущностями: " + "; ".join(lines) + "."


async def generate_answer_with_graph_context(query: str, graph: GraphStore, db) -> Dict:
    """Генерирует ответ с использованием Graph RAG и возвращает запись для Ragas"""

    # 1. Поиск в графе: получаем чанки + связи
    result = await search_in_graph(query=query, graph=graph, db=db)

    chunks = result.get("chunks", [])
    relations = result.get("relations", [])

    # 2. Формируем контекст: контент чанков + текстовое описание связей
    contexts = [c["content"] for c in chunks]

    if relations:
        relations_text = relations_to_text(relations)
        contexts.append(relations_text)

    # 3. Генерация ответа
    if not contexts:
        response = "В контексте нет информации для ответа."
    else:
        prompt = f"""Ответь на вопрос пользователя, используя только информацию из контекста ниже.
Если в контексте нет ответа — скажи об этом прямо.
Не выдумывай факты и не добавляй ссылки самостоятельно.

Контекст:
{' | '.join(contexts)}

Вопрос: {query}

Ответ:"""
        response = giga.invoke([SystemMessage(content=prompt)]).content

    # 4. Сбор данных в формате Ragas
    return {
        "user_input": query,
        "retrieved_contexts": contexts,  # List[str] — как требует Ragas
        "response": response,
        "reference": None,  # ← заполните вручную позже
    }


async def main():
    # Инициализация
    db = DataBaseMessages()
    await db.init_db_pool()

    graph = GraphStore(
        NEO4J_URI,
        NEO4J_USER,
        NEO4J_PASSWORD
    )

    # Тестовые запросы
    queries = [
        "С кем конкурирует дипсик?",
        "Как генеративный ИИ влияет на занятость сотрудников?",
        "Как математика связана с гуманитарными науками?",
        "С кем сотрудничает Palantir и кто его создал?",
        "Кто из сотрудников IBM публиковал исследования и в каких изданиях?",
        "Какие платежные системы партнёрятся с Agent Payments Protocol и с какими фреймворками он совместим?",
        "Какое китайское оборудование связано с передачей данных, и кто из известных личностей выступает против Китая?"
    ]

    results = []

    for i, q in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] Graph RAG: {q}")

        try:
            record = await generate_answer_with_graph_context(q, graph, db)
            results.append(record)

        except Exception as e:
            print(f"✗ Ошибка при обработке '{q}': {e}")
            # Добавляем пустой результат, чтобы не ломать датасет
            results.append({
                "user_input": q,
                "retrieved_contexts": [],
                "response": f"Ошибка обработки: {str(e)}",
                "reference": None,
            })

    # Сохранение в JSON
    output_file = f"ragas_dataset_graph.json"

    dataset = {
        "samples": results
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Graph RAG dataset сохранён: {output_file}")
    print(f"📊 Всего семплов: {len(results)}")
    print(f"🔧 Заполните поле 'reference' вручную для метрики ContextRecall")

    # Cleanup
    await graph.close()



if __name__ == "__main__":
    asyncio.run(main())