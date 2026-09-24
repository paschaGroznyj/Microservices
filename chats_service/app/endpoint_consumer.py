# db_consumer.py
import json
import logging
import asyncio
from kafka import KafkaConsumer
from app.db.chats_tables import DataBaseChats
from app.core.config import KAFKA_BROKER
from typing import List
import os
import requests  # Для отправки сообщений обратно в основной сервис
import time

# Обратный путь для Telegram: ответ агента (gateway_target=="telegram") шлём
# не на гейтвей, а прямо телеграм-боту (host-режим) через docker gateway IP.
TG_BOT_PUSH_URL = os.getenv("TG_BOT_PUSH_URL", "http://172.18.0.1:8010/push_message")

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
            # kind пробрасываем на фронт: "image" — рисуем превью, иначе файл-чип.
            "kind": f.get("kind"),
            "image_key": f.get("image_key"),
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
            }
        }
        self.gateway_urls = {
            "main": "http://api_gateway:7000",
            "sigma": "http://api_gateway_sigma:7010",
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
            # Ботовое сообщение приходит с ключом attached_files (из agent_service),
            # а сообщение пользователя — с ключом files (метаданные из getFilesMeta()
            # на фронте, продюсер send_message кладёт их как chat.files). Раньше
            # user-файлы терялись: консюмер читал только attached_files → в chat_files
            # для юзера ничего не писалось, и чипы пропадали после F5. Берём files
            # как фолбэк, чтобы вложения пользователя тоже сохранялись.
            attached_files = payload.get("attached_files") or payload.get("files")
            tool_trace = payload.get("tool_trace")
            gateway_target = (payload.get("gateway_target") or "main").lower()
            # telegram — валидный таргет (обратный путь не на гейтвей, а боту).
            if gateway_target not in self.gateway_urls and gateway_target != "telegram":
                gateway_target = "main"
            logging.info(payload)
            logging.info(f"gateway_target={gateway_target}")
            
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
                tokens_usage=tokens_usage,
                gateway_target=gateway_target
            )
            logger.info(f"Сообщение сохранено в БД: chat={chat_id}, user={user_id}")
            elapsed = time.perf_counter() - start
            logging.info(f"Message Service. Время чтобы вытащить из кафки и положить в БД = {elapsed:.4f}s")
            # Если сообщение от бота, отправляем обратно в основной сервис для WebSocket
            if is_from_bot and gateway_target == "telegram":
                # Ответ агента для Telegram: пушим напрямую боту (host-режим).
                await self.send_to_telegram(chat_id, message, attached_files)
                return

            if is_from_bot:
                type_agent = payload.get("type_agent", "pm")
                logging.info(type_agent)
                gateway_url = self.gateway_urls[gateway_target]
                ws_payload = dict(payload)
                ws_payload["files"] = _sanitize_files_for_ws(attached_files) # Адаптивно, чтобы и для файлов пользователя
                ws_payload["gateway_target"] = gateway_target

                # Автогенерированный заголовок чата (agent_service кладёт auto_title
                # в payload только на первом обмене). Пишем в СУБД и пушим
                # событие title_update на фронт в реалтайме.
                auto_title = payload.get("auto_title")
                if auto_title:
                    try:
                        saved_title = await self.db.set_chat_auto_title(chat_id, user_id, auto_title)
                        if saved_title:
                            ws_payload["auto_title"] = saved_title
                    except Exception as e:
                        logger.error(f"Не удалось сохранить auto_title chat={chat_id}: {e}")

                await self.send_to_main_service(chat_id, ws_payload, gateway_url)

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def send_to_telegram(self, chat_id: str, text: str, files=None):
        """Ответ агента → телеграм-бот (host-режим, эндпоинт /push_message).

        Бот сам нарежет длинный текст и починит HTML-теги. Файлы агента
        (attached_files) пробрасываем как есть — бот решит, слать ли документом.
        """
        try:
            start = time.perf_counter()
            payload = {
                "chat_id": str(chat_id),
                "text": text or "",
                "files": files or None,
                # бот отредактирует свой плейсхолдер «Думаю...» вместо нового сообщения
                "edit_thinking": True,
            }
            response = requests.post(TG_BOT_PUSH_URL, json=payload, timeout=30)
            logger.info(f"Ответ агента отправлен в Telegram: {response.status_code}")
            elapsed = time.perf_counter() - start
            logging.info(f"Message Service. Время отправки ответа в Telegram = {elapsed:.4f}s")
        except Exception as e:
            logger.error(f"Ошибка отправки в Telegram: {e}")

    async def send_to_main_service(self, chat_id: str, message: dict, gateway_url: str):
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