# main.py
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import List, Dict, Any
from fastapi import FastAPI
import uvicorn
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from core.model import get_model

from core.config import KAFKA_BROKER, PORT_DS_AGENT
from db.app_db import DataBaseMessages
from db.graph_db import GraphStore
from classic_graph import search_in_graph
from classic_rag import search_in_embeddings
from core.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from langchain.schema import SystemMessage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Глобальные переменные
db: DataBaseMessages = None
graph: GraphStore = None
producer: AIOKafkaProducer = None
giga = get_model()


class MessageProcessor:
    """Обработчик сообщений с поддержкой разных стратегий"""

    def __init__(self, db, graph, producer):
        self.db = db
        self.graph = graph
        self.producer = producer
        self.strategies = {
            "rag": self._rag_search,
            "graph": self._graph_search,
            "hybrid": self._hybrid_search
        }

    async def process(self, payload: Dict[str, Any]) -> str:
        """Основной метод обработки"""
        try:
            text = payload.get("message", "")
            chat_id = payload.get("chat_id")
            strategy = payload.get("strategy", "graph")  # Можно выбирать стратегию



            #
            # # Выбираем стратегию поиска
            # if strategy in self.strategies:
            #     logger.info(f"Выбрали стратегию {strategy}")
            #     results = await self.strategies[strategy](text)
            # else:
            #     results = await self._rag_search(text)
            #
            # # Формируем ответ с контекстом
            # response, links = await self._generate_response(text, results)

            # Заглушка
            response = "Тут ответ модели"
            links = [
                "http://blog.kaggle.com/2018/01/22/reviewing-2017-and-previewing-2018/",
                "https://corp.mail.ru/ru/press/events/413/",
                "https://docs.google.com/document/d/1ajyJhXyt4q9ZsufXV1kZxDH_3Isg3MYAKsFqNytXrCw/edit",
                "https://meduza.io/feature/2018/01/22/orki-pobedivshie-tehnarey",
                "https://m.youtube.com/watch?v=XfRea9SvziU",
                "https://www.dataquest.io/blog/introduction-to-ensembles/"
            ]

            # Отправляем ответ
            await self._send_response(chat_id, response, links)

            return response

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return "Извините, произошла ошибка при обработке запроса."

    async def _rag_search(self, query: str) -> List[Dict]:
        """Классический RAG через эмбеддинги"""
        return await search_in_embeddings(self.db, query)

    async def _graph_search(self, query: str) -> List[Dict]:
        """Поиск через граф"""
        return await search_in_graph(self.db, self.graph, query)

    async def _hybrid_search(self, query: str) -> List[Dict]:
        """Гибридный поиск"""
        # Параллельный запуск обоих методов
        rag_task = self._rag_search(query)
        graph_task = self._graph_search(query)

        rag_results, graph_results = await asyncio.gather(rag_task, graph_task)
        _log_chunks("RAG", rag_results, id_field="chunk_id")
        _log_chunks("GRAPH", graph_results, id_field="id")
        # Объединяем и дедуплицируем результаты
        return _merge_results(rag_results, graph_results)

    async def _generate_response(self, query: str, results: List[Dict]) -> tuple[str, list[str]]:
        """Генерация ответа. Возвращает (текст, список_ссылок)"""

        top_results = results[:5]

        context_parts = []
        for i, r in enumerate(top_results, 1):
            context_parts.append(f"[{i}] {r['content']}")
        context = "\n\n".join(context_parts)

        # Собираем уникальные ссылки отдельно
        seen_links = set()
        unique_links = []
        for r in top_results:
            raw = r.get("links") or []
            if isinstance(raw, str):
                raw = raw.strip("{}").split(",")
            for link in raw:
                link = link.strip()
                if link and link not in seen_links:
                    seen_links.add(link)
                    unique_links.append(link)

        prompt = f"""Ответь на вопрос пользователя, используя только информацию из контекста ниже.
    Если в контексте нет ответа — скажи об этом прямо.
    Не выдумывай факты и не добавляй ссылки самостоятельно.

    Контекст:
    {context}

    Вопрос: {query}

    Ответ:"""

        logger.info(f"💬 Context:\n{context}")
        logger.info(f"🔗 Links: {unique_links}")

        response = await asyncio.to_thread(
            giga.invoke,
            [SystemMessage(content=prompt)]
        )

        # Возвращаем текст и ссылки раздельно
        return response.content, unique_links

    async def _send_response(self, chat_id: int, text: str, links: list[str]):
        """Отправка ответа в Kafka"""

        def url_to_label(url: str) -> str:
            try:
                from urllib.parse import urlparse
                host = urlparse(url).hostname or url
                return host.replace("www.", "")
            except Exception:
                return url

        bot_message = {
            "user_id": -1,
            "chat_id": chat_id,
            "message": text.strip(),
            "is_from_bot": True,
            "type_agent": "ds",
            # ссылки отдельным полем
            "sources": [
                {"label": url_to_label(link), "url": link}
                for link in links
            ]
        }

        await self.producer.send_and_wait("chat-messages", value=bot_message)
        logger.info(f"Sent response to chat {chat_id}, sources: {len(links)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    global db, graph, producer, processor

    # 1. Инициализация БД
    db = DataBaseMessages()
    await db.init_db_pool()
    logger.info("✅ PostgreSQL connected")

    # 2. Инициализация графа
    graph = GraphStore(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    logger.info("✅ Neo4j connected")

    # 3. Инициализация Kafka producer
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BROKER,
        value_serializer=lambda m: json.dumps(m).encode('utf-8')
    )
    await producer.start()
    logger.info("✅ Kafka producer started")

    # 4. Создаем процессор
    processor = MessageProcessor(db, graph, producer)

    # 5. Запускаем Kafka consumer в фоне
    consumer_task = asyncio.create_task(kafka_consumer_loop())

    yield

    # Cleanup
    consumer_task.cancel()
    await producer.stop()
    await db.close()
    await graph.close()
    logger.info("✅ All connections closed")


app = FastAPI(lifespan=lifespan)


async def kafka_consumer_loop():
    """Асинхронный Kafka consumer"""
    consumer = AIOKafkaConsumer(
        'chat-messages',
        bootstrap_servers=KAFKA_BROKER,
        group_id='llm-bot-group',
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='latest'
    )

    await consumer.start()
    logger.info("✅ Kafka consumer started")

    try:
        async for msg in consumer:
            start = asyncio.get_event_loop().time()

            payload = msg.value

            # Пропускаем сообщения от бота
            if payload.get("is_from_bot", False) or payload.get("user_id") == -1:
                continue
            logger.info(f"Сообщение {payload}")
            # Запускаем обработку в отдельной таске
            asyncio.create_task(processor.process(payload))

            elapsed = asyncio.get_event_loop().time() - start
            logger.info(f"Message received, processing started: {elapsed:.3f}s")

    except Exception as e:
        logger.error(f"Consumer error: {e}")
    finally:
        await consumer.stop()


@app.get("/health")
async def health():
    """Health check"""
    return {
        "service": "llm-bot",
        "status": "ok"
    }


@app.get("/search")
async def search(
        query: str,
        strategy: str = "hybrid"
):
    """Прямой поиск через API"""
    if strategy == "rag":
        results = await search_in_embeddings(db, query)
    elif strategy == "graph":
        results = await search_in_graph(db, graph, query)
    else:
        # Гибрид
        rag_task = search_in_embeddings(db, query)
        graph_task = search_in_graph(db, graph, query)
        rag_results, graph_results = await asyncio.gather(rag_task, graph_task)
        results = _merge_results(rag_results, graph_results)

    return {
        "query": query,
        "strategy": strategy,
        "results": results[:5]
    }


def _merge_results(rag_results: List[Dict], graph_results: List[Dict]) -> List[Dict]:
    """Объединение результатов с дедупликацией"""
    seen_ids = set()
    merged = []

    for r in graph_results:
        row = dict(r)
        if row["id"] not in seen_ids:
            seen_ids.add(row["id"])
            row["source"] = "graph"
            merged.append(row)

    for r in rag_results:
        row = dict(r)
        if row["chunk_id"] not in seen_ids:
            seen_ids.add(row["chunk_id"])
            row["source"] = "rag"
            merged.append(row)

    return merged


def _log_chunks(label: str, chunks: list, id_field: str = "id") -> None:
    if not chunks:
        logger.info(f"[{label}] пусто")
        return

    lines = [f"[{label}] найдено {len(chunks)} чанков:"]
    for i, chunk in enumerate(chunks, 1):
        # поддержка и dict и asyncpg.Record и neo4j.Record
        row = dict(chunk) if not isinstance(chunk, dict) else chunk

        chunk_id = row.get(id_field) or row.get("chunk_id") or row.get("id") or "?"
        content = row.get("content") or row.get("text") or ""
        preview = content[:100].replace("\n", " ").strip()
        score = row.get("score") or row.get("rrf_score") or ""
        score_str = f" | score: {score:.4f}" if isinstance(score, float) else ""

        lines.append(f"  {i}. id={chunk_id}{score_str} | {preview}...")

    logger.info("\n".join(lines))


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(PORT_DS_AGENT),
        log_level="info"
    )