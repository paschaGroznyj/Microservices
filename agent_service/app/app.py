import threading
import time
from fastapi import FastAPI, HTTPException
import uvicorn
import json
import logging
from pydantic import BaseModel
from kafka import KafkaConsumer, KafkaProducer
from agent_pm.agent import KnowledgeAgent
import asyncio
# from agent_pm.pm_parser.arxiv_parser import ARXIVParser
from agent_pm.core.config import KAFKA_BROKER, PORT_PM_AGENT

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

                # Пропускаем сообщения от бота (защита от петли)
                if is_from_bot or user_id == -1:
                    continue

                if not text or chat_id is None:
                    logging.warning(f"Некорректное сообщение: {payload}")
                    continue

                try:
                    response = agent.process_query(text)
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
                        "is_from_bot": True
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


@app.get("/health")
async def health():
    status = "ok" if consumer_alive else "error"
    return {"service": "llm-bot", "status": status}


if __name__ == "__main__":
    threading.Thread(target=kafka_consumer, daemon=True).start() # Поднятие консьюмера
    uvicorn.run(app, host="0.0.0.0", port=int(PORT_PM_AGENT), log_level="info")
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PM_AGENT), log_level="info")
