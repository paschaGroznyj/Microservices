import threading
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
import uvicorn
import json
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from contextlib import asynccontextmanager
# from agent_pm.agent import KnowledgeAgent
from Microservices.agent_service.app.agent_pm.async_agent_ import KnowledgeAgent
from langchain.schema import HumanMessage, SystemMessage
from agent_pm.summary_period import extract_json
import asyncio
from httpx import AsyncClient
# from agent_pm.pm_parser.arxiv_parser import ARXIVParser
from agent_pm.core.config import KAFKA_BROKER, PORT_PM_AGENT, PORT_PARSERS_SERVICE, PORT_GATEWAY_PORT
from agent_pm.search_bot.searcher import searcher_main
from agent_pm.core.db import AgentDB

logging.basicConfig(level=logging.INFO)

db = AgentDB()

# Глобальные переменные для producer/consumer
producer: AIOKafkaProducer | None = None
consumer: AIOKafkaConsumer | None = None
consumer_alive = False
agent: KnowledgeAgent | None = None

async def get_producer() -> AIOKafkaProducer:
    global producer
    if producer is None:
        producer = AIOKafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            key_serializer=lambda k: str(k).encode("utf-8"),
            value_serializer=lambda m: json.dumps(m).encode("utf-8"),
        )
        await producer.start()
    return producer

async def handle_message(payload: dict, prod: AIOKafkaProducer):
    """Обработка одного сообщения — запускается конкурентно."""
    start = time.perf_counter()

    user_id = payload.get("user_id")
    chat_id = payload.get("chat_id")
    text = payload.get("message")
    is_from_bot = payload.get("is_from_bot", False)
    type_agent = payload.get("type_agent")
    request_id = payload.get("request_id")

    if is_from_bot or user_id == -1:
        return
    if type_agent != "pm":
        return
    if not text or chat_id is None:
        logging.warning(f"Некорректное сообщение: {payload}")
        return

    try:
        response = await agent.process_query(user_input=text, request_id=request_id, user_id=user_id, database=db)
        # loop = asyncio.get_running_loop()
        # response = await loop.run_in_executor(
        #     None, agent.process_query, text, request_id
        # )
        response_text = response["message"]
        # from datetime import datetime
        # now = datetime.now()
        # formatted_time = now.strftime("%H:%M:%S")
        # response_text = f"Идет проведение нагрузочного тестирования! Cпасибо за понимание. Текущее время: **{formatted_time}**"

        bot_message = {
            "user_id": -1,
            "chat_id": chat_id,
            "message": response_text.strip(),
            "is_from_bot": True,
            "type_agent": "pm",
        }

        await prod.send_and_wait("chat-messages", value=bot_message)
        logging.info(f"Ответ в чат {chat_id}: {response_text[:50]}...")
        logging.info(f"Время обработки: {time.perf_counter() - start:.4f}s")

    except Exception as e:
        logging.error(f"Ошибка обработки сообщения: {e}")


async def kafka_consumer():
    global consumer_alive

    consumer = AIOKafkaConsumer(
        "chat-messages",
        bootstrap_servers=[KAFKA_BROKER],
        group_id="llm-bot-group",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        session_timeout_ms=60000,  # Ждем 60 сек перед тем как убить клиента
        heartbeat_interval_ms=10000,  # Шлем пульс каждые 10 сек
        max_poll_interval_ms=300000,  # Даем 5 минут на обработку одного сообщения
        max_poll_records=1,
    )

    await consumer.start()
    consumer_alive = True
    logging.info("LLM-бот запущен и ждёт сообщения...")

    prod = await get_producer()

    tasks: set[asyncio.Task] = set()

    try:
        async for message in consumer:
            # Каждое сообщение — отдельная задача, не ждём завершения
            task = asyncio.create_task(handle_message(message.value, prod))

            # Держим ссылки на таски, чтобы GC их не собрал
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    except asyncio.CancelledError:
        logging.info("Kafka consumer остановлен, ждём завершения активных задач...")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        consumer_alive = False
        await consumer.stop()

async def shutdown():
    global producer
    if producer:
        await producer.stop()

async def kafka_moment():
    try:
        await kafka_consumer()
    finally:
        await shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    await db.init_db_pool()
    task = asyncio.create_task(kafka_consumer())
    agent = KnowledgeAgent(database=db, skip_startup_autoprocess=False)
    yield

    # Graceful shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await shutdown()
    await db.close()

app = FastAPI(lifespan=lifespan)

class ParsingMessage(BaseModel):
    status: str

@app.post("/make_reviews")
async def make_reviews(parsing_status: ParsingMessage):
    if parsing_status.status == "success":
        logging.info("Получена команда на запуск генерации ревью. Запускаем в фоне...")

        try:
            # Создадим фоновую задачу, не дожидаемся ее выполнения, сразу передаем ответ обратно
            # в parsers_service. Типа выстрелил и забыл
            task = asyncio.create_task(agent.make_reviews())

            def handle_task_exception(t):
                if t.exception():
                    logging.error(f"Фоновая задача генерации ревью упала с ошибкой: {t.exception()}")

            task.add_done_callback(handle_task_exception)

            # Отправим запрос сразу в сервис парсинга
            return {
                "status": "accepted",
                "message": "Задача поставлена в очередь на выполнение"
            }
        except Exception as e:
            logging.error(f"Ошибка в агенте: {e}")
            raise HTTPException(status_code=500, detail=f"Agent failed: {str(e)}")

    return {"status": "ignored", "message": "Статус не success"}



SERVICES = {
    "parsers": {
         "url": f"http://parsers_service:{PORT_PARSERS_SERVICE}",
         },
    "gateway": {
        "url": f"http://api_gateway:{PORT_GATEWAY_PORT}"
    }
}

@app.post("/api/translate")
async def translate(request: Request):
    data = await request.json()
    article_id = int(data["article_id"])

    # Проверяем есть ли уже перевод в БД
    row = await db.get_article_translation(article_id)

    if row and row["translate"]:
        # Перевод уже есть — отдаём из БД
        return {"translated_abstract": row["translate"], "cached": True}

    if not row or not row["abstract"]:
        raise HTTPException(status_code=404, detail="Article not found")

    # Перевода нет — отправляем в LLM
    translated = translator(
        title=row.get("title", ""),
        abstract=row["abstract"]
    )
    logging.info(translated)
    # Сохраняем перевод в БД
    await db.save_translation(article_id, translated)

    return {"translated_abstract": translated, "cached": False}


@app.post("/api/search")
async def searching(request: Request):
    data = await request.json()
    logging.info(data)

    request_id = await db.insert_query(username=data["user_name"], query=data["query"])
    existing_tags, existing_keywords = await asyncio.gather(
        db.get_tags(),
        db.get_keywords()
    )

    intent = parse_user_intent(data["query"], existing_tags, existing_keywords)
    logging.info(f"INTENT = {intent}")
    # Пользователь просто поздоровался
    if not intent["is_search"]:
        await db.update_status(request_id=request_id, llm_answer=intent["chat_response"])
        return {"type": "agent", "message": intent["chat_response"]}

    new_tags = list(set(intent["categories"]) - set(existing_tags))
    new_keywords = list(set(intent["keywords"]) - set(existing_keywords))
    if new_tags:
        await db.insert_tags(new_tags)
    if new_keywords:
        await db.insert_keywords(new_keywords)
    logging.info(f"Новые теги: {new_tags}, новые keywords: {new_keywords}, search_now: {intent['search_now']}")

    # Иначе — идём в парсер с готовыми keywords
    post_body = {
        "user_name": data["user_name"],
        "query": data["query"],
        "params": {"keywords": intent["keywords"], "categories": intent["categories"]},
        "request_id": request_id,
        "search_now": intent["search_now"]
    }
    logging.info(post_body)
    try:
        async with httpx.AsyncClient() as client:
            # 1. Парсим статьи
            response = await client.post(
                f"{SERVICES['parsers']['url']}/search/articles",
                json=post_body
            )
            if response.status_code == 200:
                parser_data = response.json()
                parser_data['type'] = 'parser'

                # 2. Считаем эмбеддинги и ищем релевантные — фоново
                asyncio.create_task(
                    run_search_and_notify(
                        user_name=data["user_name"],
                        request_id=int(request_id),
                    )
                )
                # 3. Сразу отдаём все статьи фронту
                return parser_data
            else:
                error_msg = "Не удалось выполнить поиск. Попробуйте позже."

                try:
                    error_detail = response.json().get("detail", "")
                    if response.status_code == 429:
                        error_msg = "Слишком частые запросы к arxiv. Подождите минуту и попробуйте снова.😉"
                    elif response.status_code == 502:
                        error_msg = "Сервис поиска временно недоступен.😶‍"
                    elif error_detail:
                        error_msg = f"Ошибка поиска"
                except:
                    pass

                logging.warning(f"Parser returned error {response.status_code}: {error_msg}")

                return {
                    "type": "arxiv_error",
                    "message": error_msg
                }

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Parsers service unreachable: {str(e)}")

async def run_search_and_notify(user_name: str, request_id: int):
    """Фоновая задача — эмбеддинги + поиск похожих → пуш на gateway"""
    try:
        searcher_body = {
            "user_name": user_name,
            "request_id": request_id,
            "match_threshold": 0.8
        }
        result = await searcher_main(searcher_body, db=db)
        relevant_ids = [i["article_id"] for i in result["articles"]]

        # Пушим на gateway чтобы он передал фронту через SSE
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SERVICES['gateway']['url']}/internal/sse-push",
                json={
                    "username": user_name,
                    "data": {
                        "type": "embeddings_ready",
                        "request_id": request_id,
                        "article_ids": relevant_ids,
                        "count": len(relevant_ids)
                    }
                }
            )
    except Exception as e:
        logging.error(f"Background search failed: {e}")

@app.get("/health")
async def health():
    status = "ok" if consumer_alive else "error"
    return {"service": "llm-bot", "status": status}

class SearchIntent(BaseModel):
    is_search: bool = Field(
        description="True если пользователь хочет найти статьи, False если просто общается"
    )
    keywords: list[str] = Field(
        default=[],
        description="Ключевые слова для поиска на arXiv, на английском"
    )
    categories: list[str] = Field(
        default=[],
        description="Категории arXiv если применимо: cs.AI, cs.LG, stat.ML и т.д."
    )
    search_now: Optional[bool] = Field(
        default=True,
        description="Ответ модели, нужно ли запускать парсер или будем искать статьи в БД"
    )
    chat_response: Optional[str] = Field(
        default=None,
        description="Ответ модели если is_search=False (приветствие, вопрос не по теме и т.д.)"
    )

def translator(title: str, abstract: str) -> str:
    system_prompt = f"""
Ты — научный переводчик. Переводишь тексты академических статей с английского на русский.

Правила:
- Сохраняй научный стиль и точность формулировок
- Термины и названия методов оставляй на английском в скобках: «векторное представление (embedding)»
- Названия инструментов, библиотек, моделей не переводи: LSTM, arXiv, Python, LangChain
- Не добавляй пояснений, вступлений и комментариев — только перевод
- Не используй markdown

Название статьи: {title}
"""

    response = agent.gigachat.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=abstract),
    ])

    result = (response.content or "").strip()

    if not result:
        logging.warning("Переводчик вернул пустой ответ")
        return abstract  # fallback — возвращаем оригинал

    return result

def parse_user_intent(user_query: str, existing_tags: list[str], keywords: list[str]) -> dict:
    tags_str = ", ".join(existing_tags) if existing_tags else "список пуст"
    keywords = ', '.join(keywords) if keywords else 'база пуста'
    logging.info(f"Теги и ключевые слова из БД {tags_str}, keywords: {keywords}")

    system_prompt = f"""
    Ты анализируешь запрос пользователя и определяешь его намерение.
    Верни только JSON без markdown.

    Существующие теги в базе:
    {tags_str}

    Существующие ключевые слова в базе:
    {keywords}

    Если пользователь хочет найти статьи:
    1. Извлеки ключевые слова из запроса (на английском)
    2. Сравни их с существующими тегами и ключевыми словами
    3. Если все ключевые слова есть в бд — search_now = false (статьи уже есть в БД)
    4. Если предложенного тобой тега или ключевого слова нет в БД — search_now = true (нужно парсить arXiv)
    5. Если пользователь пишет о свежих статьях - всегда возвращай всегда search_now = true независимо от тегов

    Верни:
    {{
      "is_search": true,
      "keywords": ["process mining", "conformance checking"],
      "categories": ["cs.AI", "cs.LG"],
      "search_now": true,
      "chat_response": null
    }}

    Если пользователь просто общается — верни:
    {{
      "is_search": false,
      "keywords": [],
      "categories": [],
      "search_now": false,
      "chat_response": "Привет! Я помогаю искать научные статьи. Спросите меня о теме."
    }}

    Ключевые слова всегда на английском.
    Категории arXiv — точные коды: cs.AI, cs.AR, cs.CE, cs.CL, cs.CR, cs.CV, cs.CY, cs.DB, cs.DC, cs.DL, cs.DS, cs.ET, cs.FL, cs.GR, cs.GT, cs.HC, cs.IR, cs.IT, cs.LG, cs.LO, cs.MA, cs.MM, cs.NE, cs.NI, cs.OS, cs.PL, cs.RO, cs.SC, cs.SE, cs.SI
    Если тема явно относится к категории — включи её в categories.
    keywords — свободные слова для поиска по заголовку и аннотации.
    """

    response = agent.gigachat.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ])

    try:
        data = json.loads(extract_json(response.content))
    except Exception:
        logging.info("Не удалось распарсить JSON — AGENT SERVICE")
        data = {
            "is_search": False,
            "keywords": [],
            "categories": [],
            "search_now": False,
            "chat_response": "Технические шоколадки. Повторим запрос?😉",
        }

    return SearchIntent(
        is_search=data.get("is_search", False),
        keywords=data.get("keywords", []),
        categories=data.get("categories", []),
        search_now=data.get("search_now", True),
        chat_response=data.get("chat_response"),
    ).model_dump()

if __name__ == "__main__":
    # threading.Thread(target=kafka_consumer, daemon=True).start() # Поднятие консьюмера
    uvicorn.run(app, host="0.0.0.0", port=int(PORT_PM_AGENT), log_level="info")
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PM_AGENT), log_level="info")
