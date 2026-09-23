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
            if gateway_target not in self.gateway_urls:
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
            else:
                # === USER-СООБЩЕНИЕ В ГРУППОВОМ ЧАТЕ ===
                # Кросс-гейтвейный фан-аут: сообщение одного участника должно
                # прилететь всем остальным в реальном времени, независимо от того,
                # на какой морде (main/sigma) каждый сидит. Раньше пуш был только
                # для is_from_bot, поэтому чужие user-сообщения появлялись лишь
                # после F5. Отправителя исключать не нужно — он получит своё
                # сообщение и отфильтрует по message_id (у него оно уже отрисовано
                # оптимистично, дедуп на фронте).
                try:
                    if user_id and user_id != -1 and await self.db.is_group_chat(chat_id):
                        members = await self.db.get_group_members(chat_id)
                        event_payload = {
                            "type": "group_new_message",
                            "chat_id": chat_id,
                            "message": {
                                "kind": "message",
                                "user_id": user_id,
                                "username": payload.get("username"),
                                "message": message,
                                "message_id": message_id,
                                "is_from_bot": False,
                                "files": _sanitize_files_for_ws(attached_files),
                            },
                        }
                        await self.fanout_group_message(chat_id, members, event_payload)
                except Exception as e:
                    logger.error(f"Групповой фан-аут user-сообщения не удался chat={chat_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def fanout_group_message(self, chat_id: str, members: list, event: dict):
        """Рассылает событие группового чата на ОБА гейтвея.
        Каждый гейтвей сам решает как доставить своим клиентам:
        main → WS broadcast по chat_id, sigma → push_user_event по username."""
        usernames = [m["username"] for m in members if m.get("username")]
        for gw_name, gw_url in self.gateway_urls.items():
            try:
                requests.post(
                    f"{gw_url}/internal/group_new_message",
                    json={
                        "chat_id": chat_id,
                        "usernames": usernames,
                        "event": event,
                    },
                    timeout=5,
                )
            except Exception as e:
                logger.warning(f"[group] fanout {gw_name} fail chat={chat_id}: {e}")

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