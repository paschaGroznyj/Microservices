from langchain_community.llms.tongyi import astream_generate_with_retry

from db.app_db import DataBaseMessages
import asyncio
import re
# from langchain.chat_models import GigaChat
from langchain_gigachat.embeddings import GigaChatEmbeddings
from langchain.schema import SystemMessage
from langchain.text_splitter import RecursiveCharacterTextSplitter
import nltk
from core.model import get_model
from nltk.corpus import stopwords

# Загружаем стоп-слова один раз при старте
try:
    RU_STOP_WORDS = set(stopwords.words('russian'))
except LookupError:
    nltk.download('stopwords')
    RU_STOP_WORDS = set(stopwords.words('russian'))


splitter = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=100
)

embeddings = GigaChatEmbeddings(
    credentials="somebody_once_told_me",
    auth_url="http://localhost:9004/api/v2/oauth",
    verify_ssl_certs=False,
    model="Embeddings-2"
)

giga = get_model()


def preprocess_query(query_text: str) -> str:
    words = query_text.split()
    filtered = [w for w in words if w.lower() not in RU_STOP_WORDS and len(w) > 2]

    if not filtered:
        return ""

    return " | ".join(filtered)

def clean_text(text: str):
    text = re.sub(r'https?://[^\s]+', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'[^\x00-\x7F\u0400-\u04FF\s.,!?;:-]', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)

    return text.strip()


async def pouring_into_chunks(db: DataBaseMessages):
    rows = await db.select_raw_data()
    records = []

    MIN_CHUNK_LEN = 101

    for row in rows:
        row = dict(row)

        raw_chunks = splitter.split_text(row["raw_text"])

        cleaned_chunks = [clean_text(c) for c in raw_chunks]

        valid_chunks = []
        for chunk in cleaned_chunks:
            chunk = chunk.strip()
            if not chunk:
                continue

            if len(chunk) < MIN_CHUNK_LEN:
                if valid_chunks:
                    valid_chunks[-1] = valid_chunks[-1] + " " + chunk
                continue

            valid_chunks.append(chunk)

        for i, chunk in enumerate(valid_chunks):
            if len(chunk.strip()) >= MIN_CHUNK_LEN:
                records.append((
                    row["id"],
                    i,
                    chunk.strip(),
                    None
                ))

    if records:
        await db.insert_chunks_data(records)

BATCH_SIZE = 32

async def create_embeddings(db: DataBaseMessages):
    rows = await db.select_chunks()

    batch_texts = []
    batch_ids = []

    for row in rows:
        row = dict(row)

        batch_texts.append(row["content"])
        batch_ids.append(row["id"])

        if len(batch_texts) == BATCH_SIZE:
            response = await embeddings._client.aembeddings(texts=batch_texts)
            for item in response.data:
                print(item.usage.prompt_tokens)  # токены конкретного чанка
                print(item.index)  # индекс в батче
                print(len(item.embedding))  # размерность вектора
            # vectors = await embeddings.aembed_documents(batch_texts)
            # await db.update_embeddings(batch_ids, vectors)
            print("Получен list[векторов]")
            batch_texts = []
            batch_ids = []

    # if batch_texts:
    #     vectors = await embeddings.aembed_documents(batch_texts)
    #     await db.update_embeddings(batch_ids, vectors)

async def search_in_embeddings(db: DataBaseMessages, query: str):
    vector = await embeddings.aembed_query(query)
    query = preprocess_query(query)  # Удаление стоп слов
    print(query)
    results = await db.search_hybrid(query_text=query, query_embedding=vector, limit=5)
    print(f"\nНайдено через гибрид: {len(results)}")
    for r in results:
        print(f"Score: {r['score']:.3f}, Preview: {r['content']}")
    return results
    # results = await db.search_vector(
    #     query_embedding=vector,
    #     limit=5,
    #     threshold=0.7
    # )
    # print(f"Найдено через вектора: {len(results)}")
    # for r in results:
    #     print(f"Score: {r['score']:.3f}, Preview: {r['content']}")

    # results = await db.search_bm25(
    #     query_text=query,
    #     limit=5
    # )
    # print(f"\nНайдено через BM25: {len(results)}")
    # for r in results:
    #     print(f"Score: {r['score']:.3f}, Preview: {r['content']}")

import json
from typing import Dict
from datetime import datetime

async def generate_answer_with_context(query: str, db) -> Dict:
    """Генерирует ответ и возвращает полную запись для Ragas"""

    # 1. Поиск контекста
    search_results = await search_in_embeddings(db, query)
    contexts = [c["content"] for c in search_results]

    # 2. Формирование промпта
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

    # 3. Сбор данных в формате Ragas
    return {
        "user_input": query,
        "retrieved_contexts": contexts,  # List[str] — как требует Ragas
        "response": response,
        "reference": None,  # ← заполните вручную позже, или оставьте null
    }


async def main():
    db = DataBaseMessages()
    await db.init_db_pool()

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
        print(f"[{i}/{len(queries)}] Обрабатываю: {q}")
        record = await generate_answer_with_context(q, db)
        results.append(record)
        print(f"✓ Ответ получен ({len(record['retrieved_contexts'])} контекстов)\n")

    # Сохранение в JSON
    output_file = f"ragas_dataset_hybrid.json"

    dataset = {
        "samples": results
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"\n💾 Dataset сохранён: {output_file}")
    print(f"📊 Всего семплов: {len(results)}")
    print(f"🔧 Откройте файл и заполните поле 'reference' вручную для ContextRecall")

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
