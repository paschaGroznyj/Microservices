import threading
import time
from fastapi import FastAPI, HTTPException
import uvicorn
import json
import logging
from pydantic import BaseModel
from kafka import KafkaConsumer, KafkaProducer
import asyncio
from core.config import KAFKA_BROKER, PORT_PLUG_AGENT

logging.basicConfig(level=logging.INFO)

from langchain.chat_models import GigaChat
from langchain.schema import SystemMessage

giga = GigaChat(
    credentials="any_value",  # Прокси не проверяет, можно любое значение
    auth_url="http://token_service:9004/api/v2/oauth",  # ← Ваш прокси!
    verify_ssl_certs=False,
    model="GigaChat-2-Max"
)



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
                    # response = agent.process_query(text)
                    # response_text = response["message"]

                    # Всё работает — токен кэшируется автоматически!
                    response_text = giga.invoke([SystemMessage(content=text)])
                    print(response_text.content)
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

@app.get("/health")
async def health():
    status = "ok" if consumer_alive else "error"
    return {"service": "llm-bot", "status": status}

if __name__ == "__main__":
    threading.Thread(target=kafka_consumer, daemon=True).start() # Поднятие консьюмера
    uvicorn.run(app, host="0.0.0.0", port=int(PORT_PLUG_AGENT), log_level="info")
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PLUG_AGENT), log_level="info")
