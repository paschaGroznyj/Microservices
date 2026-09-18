"""
Kafka-консюмер ОБЩЕГО / группового чата.

Отдельный consumer group ('group-db-consumer-group') на топике
'group-chat-messages'. Читает события, спокойно пишет в БД
(group_messages) и транслирует результат обратно на гейтвеи
(/internal/group_message): main -> WebSocket, sigma -> SSE.

События (event):
  new_message   -> INSERT + broadcast сохранённого сообщения
  edit_message  -> уже в БД (сделал endpoint), просто broadcast
  delete_message-> broadcast
  pin_message   -> broadcast
"""
import json
import logging
import asyncio
import time

from kafka import KafkaConsumer
import requests

from app.db.chats_tables import DataBaseChats
from app.db.group_chat_db import GroupChatDB
from app.core.config import KAFKA_BROKER

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROUP_TOPIC = "group-chat-messages"


class GroupDBConsumer:
    def __init__(self):
        # собственный пул (консюмер — отдельный процесс/таск)
        self.db_chats = DataBaseChats()
        self.group_db = None
        self.consumer = None
        self.gateway_urls = {
            "main": "http://api_gateway:7000",
            "sigma": "http://api_gateway_sigma:7010",
        }

    async def init(self):
        await self.db_chats.init_db_pool()
        self.group_db = GroupChatDB(self.db_chats.pool)
        await self.group_db.init_schema()
        self.consumer = KafkaConsumer(
            GROUP_TOPIC,
            bootstrap_servers=[KAFKA_BROKER],
            group_id="group-db-consumer-group",
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        logger.info("[group-chat] консюмер инициализирован")

    async def process(self, payload: dict):
        event = payload.get("event", "new_message")
        room_uid = payload.get("room_uid", "global")
        gateway_target = payload.get("gateway_target", "both")
        try:
            if event == "new_message":
                start = time.perf_counter()
                saved = await self.group_db.add_message(
                    room_uid=room_uid,
                    user_id=payload.get("user_id"),
                    username=payload.get("username"),
                    message_text=payload.get("message"),
                    client_msg_id=payload.get("client_msg_id"),
                )
                logger.info(f"[group-chat] INSERT room={room_uid} "
                            f"({time.perf_counter()-start:.4f}s)")
                if saved:
                    await self._broadcast(room_uid, {
                        "event": "new_message", "message": saved,
                    }, gateway_target)
            elif event in ("edit_message", "delete_message", "pin_message"):
                # Фичи уже применены в БД эндпоинтом, консюмер только транслирует
                await self._broadcast(room_uid, payload, gateway_target)
            else:
                logger.warning(f"[group-chat] неизвестный event: {event}")
        except Exception as e:
            logger.error(f"[group-chat] process error: {e}")

    async def _broadcast(self, room_uid: str, event: dict, gateway_target: str):
        """Шлёт событие на гейтвеи. gateway_target: main | sigma | both."""
        targets = []
        if gateway_target == "both":
            targets = ["main", "sigma"]
        elif gateway_target in self.gateway_urls:
            # сообщение видят ВСЕ в общем чате — шлём на оба гейтвея всегда,
            # gateway_target из send говорит лишь откуда пришло (для логов)
            targets = ["main", "sigma"]
        else:
            targets = ["main", "sigma"]
        body = {"room_uid": room_uid, **event}
        for tgt in targets:
            url = self.gateway_urls[tgt]
            try:
                requests.post(f"{url}/internal/group_message", json=body, timeout=5)
            except Exception as e:
                logger.error(f"[group-chat] broadcast -> {tgt} error: {e}")

    async def consume(self):
        logger.info("[group-chat] GroupDBConsumer запущен")
        for kafka_message in self.consumer:
            try:
                await self.process(kafka_message.value)
            except Exception as e:
                logger.error(f"[group-chat] Kafka error: {e}")

    async def close(self):
        if self.consumer:
            self.consumer.close()
        if self.db_chats.pool:
            await self.db_chats.pool.close()


async def main():
    consumer = GroupDBConsumer()
    await consumer.init()
    try:
        await consumer.consume()
    except KeyboardInterrupt:
        logger.info("[group-chat] остановка")
    finally:
        await consumer.close()


if __name__ == "__main__":
    asyncio.run(main())
