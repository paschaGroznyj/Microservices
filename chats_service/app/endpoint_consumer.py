# db_consumer.py
import json
import logging
import asyncio
from kafka import KafkaConsumer
from app.db.chats_tables import DataBaseChats
from app.core.config import KAFKA_BROKER
from typing import List
import requests  # Для отправки сообщений обратно в основной сервис
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _sanitize_files_for_ws(attached_files: list[dict] | None) -> list[dict] | None:
    """Убираем служебный path — фронту нужны только name/ext."""
    if not attached_files:
        return None
    return [
        {
            "name": f["name"],
            "ext": f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else None,
        }
        for f in attached_files
    ]

class DBConsumer:
    def __init__(self):
        self.db = DataBaseChats()
        self.consumer = None
        self.agents = {
            "pm": {
                "name": "Agent PM",
                "gateway": "http://api_gateway:7000"
            }
        }

    async def init(self):
        await self.db.init_db_pool()

        self.consumer = KafkaConsumer(
            'chat-messages',
            bootstrap_servers=[KAFKA_BROKER],
            group_id='db-consumer-group',
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='latest',
            enable_auto_commit=True
        )

    async def process_message(self, payload):
        try:
            start = time.perf_counter()
            user_id = payload.get("user_id")
            chat_id = payload.get("chat_id")
            message = payload.get("message")
            is_from_bot = payload.get("is_from_bot", False)
            action = payload.get("action")
            selected_tools = payload.get("selected_tools")
            context_size = payload.get("context_size")
            message_id = payload.get("message_id")
            attached_files = payload.get("attached_files")
            tool_trace = payload.get("tool_trace")
            logging.info(payload)
            
            model_type = None
            tokens_usage = None
            if is_from_bot:
                model_type = payload.get("agent_model")
                tokens_usage = payload.get("tokens_usage")
            # Сохраняем в БД
            await self.db.add_message(
                user_id=user_id,
                message=message[:20000],
                chat_id=chat_id,
                is_from_bot=is_from_bot,
                action=action,
                selected_tools=selected_tools or None,
                context_size=context_size,
                message_id=message_id,
                attached_files=attached_files,
                tool_trace=tool_trace,
                model_type=model_type,
                tokens_usage=tokens_usage
            )
            logger.info(f"Сообщение сохранено в БД: chat={chat_id}, user={user_id}")
            elapsed = time.perf_counter() - start
            logging.info(f"Message Service. Время чтобы вытащить из кафки и положить в БД = {elapsed:.4f}s")
            # Если сообщение от бота, отправляем обратно в основной сервис для WebSocket
            if is_from_bot:
                type_agent = payload.get("type_agent", "pm")
                logging.info(type_agent)
                gateway_url = self.agents[type_agent]["gateway"]
                ws_payload = dict(payload)
                ws_payload["files"] = _sanitize_files_for_ws(attached_files) # Адаптивно, чтобы и для файлов пользователя

                await self.send_to_main_service(chat_id, ws_payload, gateway_url)

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def send_to_main_service(self, chat_id: int, message: dict, gateway_url: str):
        """Отправляем сообщение обратно в основной сервис для WebSocket"""
        try:
            start = time.perf_counter()
            # Внутренний endpoint в основном сервисе для получения сообщений от бота
            response = requests.post(
                f"{gateway_url}/internal/bot_message",
                json={
                    "chat_id": chat_id,
                    "message": message, # Сообщение из кафки, сам текст, флаг на бота, ссылки
                }
            )
            logger.info(f"Сообщение бота отправлено в основной сервис: {response.status_code}")
            elapsed = time.perf_counter() - start
            logging.info(f"Message Service. Время чтобы, отправить сообщение в основной модуль сервиса = {elapsed:.4f}s")
        except Exception as e:
            logger.error(f"Ошибка отправки в основной сервис: {e}")

    async def consume(self):
        logger.info("DB Consumer запущен")

        for kafka_message in self.consumer:
            try:
                payload = kafka_message.value
                await self.process_message(payload)
            except Exception as e:
                logger.error(f"Ошибка обработки Kafka сообщения: {e}")

    async def close(self):
        if self.consumer:
            self.consumer.close()
        if self.db.pool:
            await self.db.pool.close()


async def main():
    consumer = DBConsumer()
    await consumer.init()

    try:
        await consumer.consume()
    except KeyboardInterrupt:
        logger.info("Остановка потребителя...")
    finally:
        await consumer.close()


if __name__ == "__main__":
    asyncio.run(main())