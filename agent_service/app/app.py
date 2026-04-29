import threading
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
import uvicorn
import json
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from kafka import KafkaConsumer, KafkaProducer
from agent_pm.agent import KnowledgeAgent
import asyncio
from httpx import AsyncClient
# from agent_pm.pm_parser.arxiv_parser import ARXIVParser
from agent_pm.core.config import KAFKA_BROKER, PORT_PM_AGENT, PORT_PARSERS_SERVICE, PORT_GATEWAY_PORT
from agent_pm.search_bot.searcher import searcher_main

logging.basicConfig(level=logging.INFO)

agent = KnowledgeAgent()

producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    api_version=(4,1,0),
    key_serializer=lambda k: str(k).encode('utf-8'),
    value_serializer=lambda m: json.dumps(m).encode('utf-8'))

consumer = KafkaConsumer(
    'chat-messages',
    bootstrap_servers=[KAFKA_BROKER],
    api_version=(4,1,0),
    group_id='llm-bot-group',  # ← обязательно отличается от DB consumer'а!
    value_deserializer=lambda m: json.loads(m.decode('utf-8')),
    auto_offset_reset='latest'
)

logging.info("LLM-бот запущен и ждёт сообщения...")

# Глобальный флаг
consumer_alive = False


def kafka_consumer():
    global consumer_alive
    consumer_alive = True
    while True:
        try:
            for message in consumer:
                start = time.perf_counter()

                payload = message.value
                user_id = payload.get("user_id")
                chat_id = payload.get("chat_id")
                text = payload.get("message")
                is_from_bot = payload.get("is_from_bot", False)
                type_agent = payload.get("type_agent")
                request_id = payload.get("request_id")
                # Пропускаем сообщения от бота (защита от петли)
                if is_from_bot or user_id == -1:
                    continue

                # Пропускаем сообщения не для этого агента
                if type_agent != "pm":
                    continue

                if not text or chat_id is None:
                    logging.warning(f"Некорректное сообщение: {payload}")
                    continue

                try:
                    response = agent.process_query(text, request_id)
                    response_text = response["message"]

                    # from datetime import datetime
                    # now = datetime.now()
                    # formatted_time = now.strftime("%H:%M:%S")
                    # response_text = f"Идет проведение нагрузочного тестирования! Cпасибо за понимание. Текущее время: **{formatted_time}**"
                    # Формируем сообщение от бота
                    bot_message = {
                        "user_id": -1,  # специальный ID для бота
                        "chat_id": chat_id,
                        "message": response_text.strip(),
                        "is_from_bot": True,
                        "type_agent": "pm"
                    }

                    # Отправляем в Kafka
                    producer.send("chat-messages", value=bot_message)
                    producer.flush()
                    logging.info(f"Отправлен ответ бота в чат {chat_id}: {response_text[:50]}...")
                    elapsed = time.perf_counter() - start
                    logging.info(f"LLM Service. Время чтобы вытащить из кафки и сгенерировать ответ = {elapsed:.4f}s")

                except Exception as e:
                    logging.error(f"Ошибка в LLM-боте: {e}")
            time.sleep(0.1)
        except Exception as e:
            consumer_alive = False
            logging.error(f"LLM отвалился")


app = FastAPI()

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

@app.post("/api/search")
async def searching(request: Request):
    data = await request.json()
    logging.info(data)
    intent = parse_user_intent(data["query"])

    # Пользователь просто поздоровался
    if not intent["is_search"]:
        return {"type": "chat", "message": intent["chat_response"]}

    # Иначе — идём в парсер с готовыми keywords
    post_body = {
        "user_name": data["user_name"],
        "query": data["query"],
        "params": {"keywords": intent["keywords"]}
    }
    try:
        async with httpx.AsyncClient() as client:
            # 1. Парсим статьи
            response = await client.post(
                f"{SERVICES['parsers']['url']}/search/articles",
                json=post_body
            )
            parser_data = response.json()
            request_id = parser_data["request_id"]

            # 2. Считаем эмбеддинги и ищем релевантные — фоново
            asyncio.create_task(
                run_search_and_notify(
                    user_name=data["user_name"],
                    request_id=int(request_id),
                )
            )

            # 3. Сразу отдаём все статьи фронту
            return parser_data

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
        result = await searcher_main(searcher_body)
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
    chat_response: Optional[str] = Field(
        default=None,
        description="Ответ модели если is_search=False (приветствие, вопрос не по теме и т.д.)"
    )


def parse_user_intent(user_query: str) -> dict:
#     system_prompt = """
# Ты анализируешь запрос пользователя и определяешь его намерение.
# Верни только JSON без markdown.
#
# Если пользователь хочет найти статьи — верни:
# {
#   "is_search": true,
#   "keywords": ["process mining", "conformance checking"],
#   "categories": ["cs.AI", "cs.LG"],
#   "chat_response": null
# }
#
# Если пользователь просто общается (привет, как дела, спасибо) — верни:
# {
#   "is_search": false,
#   "keywords": [],
#   "categories": [],
#   "chat_response": "Привет! Я помогаю искать научные статьи. Спросите меня о теме — и я найду релевантные публикации."
# }
#
# Ключевые слова всегда на английском.
# Категории только стандартные arXiv: cs.AI, cs.LG, cs.SE, cs.DB, cs.CR, stat.ML и т.д.
# """
#
#     response = self.model.invoke([
#         SystemMessage(content=system_prompt),
#         HumanMessage(content=user_query),
#     ])
#
#     data = self._extract_json(response.content)

    data = {
        "is_search": True,
        "keywords": [user_query],
        "categories": [],
        "chat_response": 'null',
    }
    return SearchIntent(
        is_search=data.get("is_search", False),
        keywords=data.get("keywords", []),
        categories=data.get("categories", []),
        chat_response=data.get("chat_response"),
    ).model_dump()

if __name__ == "__main__":
    threading.Thread(target=kafka_consumer, daemon=True).start() # Поднятие консьюмера
    uvicorn.run(app, host="0.0.0.0", port=int(PORT_PM_AGENT), log_level="info")
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PM_AGENT), log_level="info")
