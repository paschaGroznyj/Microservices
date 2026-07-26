import threading
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, APIRouter
import uvicorn
import json
import logging
import base64
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from contextlib import asynccontextmanager
# from agent_pm.async_agent import KnowledgeAgent
from agent_pm.langchain_agent import KnowledgeAgent, GATEWAY_URL
from langchain.schema import HumanMessage, SystemMessage
from agent_pm.summary_period import extract_json
import asyncio
from httpx import AsyncClient
# from agent_pm.pm_parser.arxiv_parser import ARXIVParser
from agent_pm.core.config import (KAFKA_BROKER, PORT_PM_AGENT, PORT_PARSERS_SERVICE, PORT_GATEWAY_PORT, 
OUROBOROS_API_URL, OUROBOROS_API_PASSWORD, OUROBOROS_WS, OUROBOROS_RESPONSE_TIMEOUT)
from agent_pm.search_bot.searcher import searcher_main
from agent_pm.core.db import AgentDB
from agent_pm.core.file_parsers import parse_file
from agent_pm.scheduler_digest import start_digest_scheduler, start_interval_digest_scheduler
import websockets
from agent_pm.core.botocore_client import database
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

async def handle_ouroboros_message(payload: dict, prod: AIOKafkaProducer):
    """Перенаправление сообщения на Ouroboros API + трансляция промежуточных мыслей."""
    start = time.perf_counter()

    chat_id = payload.get("chat_id")
    text = payload.get("message")
    request_id = payload.get("request_id")
    tb_name = payload.get("tb_name")
    username = payload.get("username")

    if not text or chat_id is None:
        logging.warning(f"Некорректное сообщение для Ouroboros: {payload}")
        return

    message = f"[CatchPM, ТабельНомер: {tb_name}, Пользователь: {username}]\n" + text

    async def send_status(status_text: str):
        """Best-effort отправка статуса через WS-бродкаст гейтвея."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as status_client:
                await status_client.post(
                    f"{GATEWAY_URL}/internal/bot_status",
                    json={"request_id": request_id, "status": status_text},
                )
        except Exception:
            pass  # статус — best-effort, не блокируем основной поток

    await send_status("Ouroboros думает...")

    try:
        # 1. Отправляем команду
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{OUROBOROS_API_URL}/api/command",
                json={"cmd": message},
                headers={"x-ouroboros-password": OUROBOROS_API_PASSWORD},
            )

        if resp.status_code != 200:
            error_msg = {
                "user_id": -1,
                "chat_id": chat_id,
                "message": f"❌ Ouroboros API вернул {resp.status_code}",
                "is_from_bot": True,
                "type_agent": "ouroboros",
                "action": "error"
            }
            await prod.send_and_wait("chat-messages", value=error_msg)
            return

        logging.info(f"Ouroboros принял запрос для чата {chat_id}, слушаем WS-ответ")

        # 2. Слушаем WS: транслируем промежуточные мысли, ждём финальный ответ
        final_content = await _listen_ouroboros_with_progress(send_status)

        if final_content is None:
            timeout_msg = {
                "user_id": -1,
                "chat_id": chat_id,
                "message": "⏱️ Ouroboros не ответил вовремя",
                "is_from_bot": True,
                "type_agent": "ouroboros",
                "action": "error"
            }
            await prod.send_and_wait("chat-messages", value=timeout_msg)
            return

        bot_message = {
            "user_id": -1,
            "chat_id": chat_id,
            "message": final_content.strip(),
            "is_from_bot": True,
            "type_agent": "ouroboros",
            "action": "answer"
        }
        await prod.send_and_wait("chat-messages", value=bot_message)

        logging.info(f"Ouroboros ответ в чат {chat_id}: {final_content[:50]}...")
        logging.info(f"Время обработки: {time.perf_counter() - start:.4f}s")

    except Exception as e:
        logging.error(f"Ошибка обработки сообщения Ouroboros: {e}")
        error_message = {
            "user_id": -1,
            "chat_id": chat_id,
            "message": f"❌ Ошибка связи с Ouroboros: {str(e)}",
            "is_from_bot": True,
            "type_agent": "ouroboros",
            "action": "error"
        }
        await prod.send_and_wait("chat-messages", value=error_message)


async def _listen_ouroboros_with_progress(send_status) -> str | None:
    """Подключиться к WS Ouroboros, транслировать промежуточные мысли через send_status,
    вернуть финальный ответ ассистента."""
    try:
        async with websockets.connect(
            OUROBOROS_WS,
            additional_headers={"x-ouroboros-password": OUROBOROS_API_PASSWORD},
        ) as ws:
            # Пропускаем первое сообщение (возможно, state/buffer)
            try:
                await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError:
                pass

            while True:
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=OUROBOROS_RESPONSE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    logging.warning("Ouroboros response timeout (%ds)", OUROBOROS_RESPONSE_TIMEOUT)
                    return None

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue

                if msg.get("type") == "chat" and msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if not content:
                        continue

                    if msg.get("is_progress", False):
                        # Промежуточная мысль — шлём как статус, не как сообщение в Kafka
                        await send_status(content)
                        continue

                    # Финальный ответ
                    return content
    except Exception as e:
        logging.error("WS connection to Ouroboros failed: %s", e)
        return None


async def handle_message(payload: dict, prod: AIOKafkaProducer):
    """Обработка одного сообщения — запускается конкурентно."""
    start = time.perf_counter()

    user_id = payload.get("user_id")
    chat_id = payload.get("chat_id")
    text = payload.get("message")
    is_from_bot = payload.get("is_from_bot", False)
    type_agent = payload.get("type_agent")
    request_id = payload.get("request_id")
    tb_name = payload.get("tb_name")
    username = payload.get("username")

    message = f"[CatchPM, ТабельНомер: {tb_name}, Пользователь: {username}]\n" + text
    # message = f"[CatchPM, ТабельНомер: {tb_name}, Пользователь: Иванов Иван]\n" + text

    if is_from_bot or user_id == -1:
        return
    if not text or chat_id is None:
        logging.warning(f"Некорректное сообщение: {payload}")
        return
    print("ВНУТРИ АГЕНТА", payload)
    # Маршрутизация: ouroboros → Ouroboros API, pm → KnowledgeAgent
    if type_agent == "ouroboros":
        await handle_ouroboros_message(payload, prod)
        return

    if type_agent != "pm":
        return

    try:
        response = await agent.process_query(user_input=message, request_id=request_id, user_id=user_id, chat_id=chat_id, database=db)
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
            "action": response['action']
        }

        await prod.send_and_wait("chat-messages", value=bot_message)
        logging.info(f"Ответ в чат {chat_id}: {response_text[:50]}...")
        logging.info(f"Время обработки: {time.perf_counter() - start:.4f}s")

    except Exception as e:
        logging.error(f"Ошибка обработки сообщения: {e}")

async def _process_service_message(message_id: int, enriched_text: str):
    """Обрабатывает сообщение сервиса в фоне, пишет ответ в service_chats."""
    try:
        await _db.update_service_status(message_id)
 
        result = await _agent.process_query(
            user_input=enriched_text,
            request_id=None,
            user_id=None,           # нет пользователя — это сервис
            chat_id=None,
            database=_db,
            source="service",
        )
        reply = result.get("message", "Агент не вернул ответ.")
        status = "done"
 
    except Exception as e:
        log.error("Ошибка обработки service message id=%s: %s", message_id, e, exc_info=True)
        reply  = f"Ошибка агента: {e}"
        status = "error"
 
    async with _db.pool.acquire() as conn:
        await conn.execute(_SQL_UPDATE_DONE, reply, status, message_id)
 
    log.info("service_chats id=%s → status=%s | %.60s", message_id, status, reply)


async def kafka_consumer():
    global consumer_alive

    consumer = AIOKafkaConsumer(
        "chat-messages",
        bootstrap_servers=[KAFKA_BROKER],
        group_id="llm-bot-group",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        session_timeout_ms=30000,  # Ждем 60 сек перед тем как убить клиента
        heartbeat_interval_ms=5000,  # Шлем пульс каждые 10 сек
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
    global agent, digest_scheduler

    await db.init_db_pool()
    await db.init_readonly_pool() # Для теста

    # from agent_pm.core.botocore_client import OBSWorker
    # obs = OBSWorker()
    # await obs.upload_all_md()

    kafka_task = asyncio.create_task(kafka_consumer())

    agent = KnowledgeAgent(database=db, skip_startup_autoprocess=False)

    connect_service_routers(agent, db)
    # await agent.make_reviews()
    # # Получаем сам объект scheduler, а не таск
    # digest_scheduler = await start_interval_digest_scheduler()

    yield

    # Graceful shutdown
    logging.info("Shutting down...")

    # # 1. Останавливаем планировщик правильно
    # if digest_scheduler and digest_scheduler.running:
    #     digest_scheduler.shutdown(wait=False)
    #     logging.info("Digest scheduler stopped.")

    # 2. Kafka
    kafka_task.cancel()
    await asyncio.gather(kafka_task, return_exceptions=True)

    await shutdown()
    await db.close()

app = FastAPI(lifespan=lifespan)

tg_router = APIRouter(prefix="/api/tg", tags=["telegram"])
service_router = APIRouter(prefix="/service", tags=["service"])



"""
agent_service/api/tg_endpoint.py
─────────────────────────────────
Эндпоинт POST /api/tg/message для приёма сообщений из tg_bot контейнера.
Подключается к app.py через app.include_router(tg_router).
"""


class TgMessageRequest(BaseModel):
    message:   str
    user_id:   int
    username:  str
    user_tag: str
    chat_id:   int
    chat_type: str = "private"   # "private" | "group"
    file_b64: Optional[str] = None   # Base64 строка с содержимым файла
    file_name: Optional[str] = None  # Имя файла (например, "document.pdf")


class TgMessageResponse(BaseModel):
    reply: str

class ParsingMessage(BaseModel):
    status: str

class ServiceMessageRequest(BaseModel):
    service_name: str           # "parsers_service"
    service_port: Optional[int] = None   # 8003
    message: str                # текст команды для агента
 
 
class ServiceMessageResponse(BaseModel):
    status: str                 # "accepted"
    message_id: int             # ID записи в service_chats

_agent = None
_db    = None


def connect_service_routers(agent, db):
    global _agent, _db
    _agent = agent
    _db    = db


@tg_router.post("/message", response_model=TgMessageResponse)
async def handle_tg_message(req: TgMessageRequest) -> TgMessageResponse:
    """
    Принимает сообщение из Telegram-бота, прогоняет через PM Agent,
    возвращает текстовый ответ.
    """
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    try:
        # Сразу дергаем ID пользователя из БД:
        user_id_database = await _db.ensure_user_exists(username=req.user_tag, source="telegram")
    except Exception as e:
        logging.warning("Не удалось автоматически зарегистрировать TG юзера %s: %s", req.username, e)
        raise HTTPException(status_code=500, detail="Failed to resolve user in database")

    # Обогащаем текст мета-информацией (как делал ouroboros-мост)
    enriched = (
        f"[TG | user_id={req.user_id} | name={req.username} | chat={req.chat_type}] | chat_id={req.chat_id} | tag={req.user_tag}\n"
        f"{req.message}"
    )
    
    # Если передан файл в base64 — декодируем и парсим
    if req.file_b64 and req.file_name:
        logging.info(f"ПОЛУЧЕН ФАЙЛ ->")
        try:
            # 1. Декодируем base64 в байты
            file_bytes = base64.b64decode(req.file_b64)
            
            # 2. Выносим тяжелый синхронный парсинг в separate thread (Task/Executor)
            extracted_text = await asyncio.to_thread(parse_file, req.file_name, file_bytes)
            
            # 3. Формируем контекст для агента
            file_prompt = f"\n\n📎 [Прикрепленный файл: {req.file_name}]\nСодержимое файла:\n```\n{extracted_text[:30000]}\n```"
            enriched += file_prompt
            logging.info(f"СОДЕРЖИМОЕ ФАЙЛА: {extracted_text[:300]}")
        except Exception as e:
            logging.error("Ошибка при декодировании/парсинге файла %s: %s", req.file_name, e)
            enriched += f"\n\n[Не удалось прочитать прикрепленный файл {req.file_name}: {e}]"

    logging.info(
        "TG message | user_id=%s name=%s chat_id=%s type=%s | %.60s",
        req.user_id, req.username, req.chat_id, req.chat_type, req.message,
    )
    
    try:
        result = await _agent.process_query(
            user_input=enriched,
            request_id=None,       # статусы через Kafka не нужны
            user_id=user_id_database,
            chat_id=req.chat_id,          # чат в БД не создаём
            database=_db,
            source="telegram"
        )
        reply = result.get("message", "Агент не вернул ответ.")
    except Exception as e:
        logging.error("process_query failed for TG: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    return TgMessageResponse(reply=reply)
    # return TgMessageResponse(reply="NOTHING")

@service_router.post("/message", response_model=ServiceMessageResponse, status_code=202)
async def handle_service_message(req: ServiceMessageRequest):
    """
    Принимает команду от внешнего сервиса, сохраняет в service_chats,
    запускает обработку агентом в фоне и сразу возвращает 202 Accepted.
 
    Пример тела от parsers_service:
        {
          "service_name": "parsers_service",
          "service_port": 8003,
          "message": "Я сервис парсинга на порту 8003, подготовил для тебя 42 новые статьи.
                      Посмотри papers_metadata и отбери релевантные (is_relevant IS NULL).
                      Для каждой проставь is_relevant=true/false и relevance_score."
        }
    """
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
 
    # 1. Записываем входящее сообщение в service_chats
    row = await _db.add_service_message(req.service_name, req.service_port, req.message)
    message_id = row["id"]
 
    log.info(
        "service_chats: новое сообщение id=%s от %s:%s | %.80s",
        message_id, req.service_name, req.service_port, req.message,
    )
 
    # 2. Обогащаем текст для агента
    enriched = (
        f"[SERVICE | from={req.service_name} port={req.service_port} "
        f"msg_id={message_id}]\n{req.message}"
    )
 
    # 3. Fire-and-forget — сразу возвращаем 202
    task = asyncio.create_task(_process_service_message(message_id, enriched))
 
    def _on_done(t: asyncio.Task):
        if t.exception():
            log.error("_process_service_message упал: %s", t.exception())
 
    task.add_done_callback(_on_done)
 
    return ServiceMessageResponse(status="accepted", message_id=message_id)


routers = [tg_router, service_router]

for rout in routers:
    app.include_router(rout)


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

    info = await db.insert_query(username=data["user_name"], query=data["query"])
    request_id = info["request_id"]
    count_search = info["count_search"]
    token = info["token"]
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
        "search_now": intent["search_now"],
        "count_search": count_search,
        "token": token,
    }
    logging.info(post_body)
    try:
        async with httpx.AsyncClient() as client:
            # 1. Парсим статьи
            response = await client.post(
                f"{SERVICES['parsers']['url']}/search/articles",
                json=post_body,
                timeout=30
            )
            if response.status_code == 200:
                parser_data = response.json()
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
    logging.info("СОЗДАНИЕ ЭМБЕДДИНГОВ")
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

@app.post("/make_style_report")
async def make_style_report(request: Request):
    data =  await request.json()
    style = data["style"]
    text = data["text"]
    return await make_report(text=text, style=style)


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

STYLE_PROMPTS = {
    "pushkin":    "Пиши возвышенно и лирично, в духе Пушкина. Используй поэтичные обороты, обращения «душа моя», «друг мой». Без архаизмов, но с элегантностью.",
    "esenin":     "Пиши как Есенин: тепло, немного тоскливо, с образами деревни, природы, берёз. Простой язык, душевность, лёгкая меланхолия.",
    "chekhov":    "Пиши кратко и с мягкой иронией, как Чехов. Никакого пафоса. Чуть грустно, чуть смешно, по-человечески.",
    "dostoevsky": "Пиши надрывно и философски. В стиле Достоевского",
    "tolstoy":    "Пиши обстоятельно и с моралью. В стиле Льва Толстого",
    "gogol":      "Пиши с абсурдом и народным юмором в духе Гоголя. Немного чертовщины, яркие образы, живой язык.",
    "soviet":     "Пиши в стиле советского плаката: чётко, пафосно, «товарищ», коллективизм, уверенность в светлом будущем.",
    "babushka":   "Пиши ласково и по-домашнему: «касатик», «голубчик», пословицы, забота. Как письмо от бабушки из деревни.",
    "normal":     "Пиши естественно и дружелюбно.",
    "formal":     "Пиши официально и по делу, без лишних эмоций.",
    "pirate":     "Пиши как пират: «йо-хо-хо», морские метафоры, дерзко и весело.",
    "strict":     "Пиши строго и требовательно, как недовольный клиент или проверяющий.",
    "gen_z":      "Пиши на молодёжном сленге: кринж, краш, вайб, имба. Коротко, с эмодзи.",
}

async def make_report(text: str, style: str):
    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["normal"])
    system_prompt = f"""Ты помощник, который переформулирует пользовательские отзывы.

    Стиль переформулирования: {style}:{style_instruction}

    Правила:
    - Не увеличивай объём текста, опирайся только на исходный отзыв
    - Сохраняй смысл, меняй только подачу под стиль
    - Определи категорию отзыва в одно слово, например: Идея, Баг, Проблема, Респект или другие

    Ответь СТРОГО в формате JSON, без markdown, без пояснений:
    {{
    "category": "твоя категория", 
    "text": "переформулированный отзыв"
    }}"""

    response = agent.gigachat.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=text),
    ])
    logging.info(response.content)
    try:
        data = json.loads(response.content)
    except Exception as e:
        logging.info(f"Не удалось распарсить JSON — AGENT SERVICE, {e}")
        data = {
            "category": "",
            "text": text,
        }

    return data

if __name__ == "__main__":
    # threading.Thread(target=kafka_consumer, daemon=True).start() # Поднятие консьюмера
    uvicorn.run("app:app", host="0.0.0.0", port=int(PORT_PM_AGENT), log_level="info", reload=False)
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PM_AGENT), log_level="info")

