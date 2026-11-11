# sigma_consumer.py
"""
SIGMA Kafka-consumer — собственный потребитель топика 'chat-messages' в
ОТДЕЛЬНОЙ consumer-group ('sigma-gateway-group'). Kafka доставляет каждой
группе независимую копию, поэтому существующий 'db-consumer-group'
(chats_service) продолжает писать историю в БД, а sigma параллельно ловит
те же сообщения и отмечает готовность ответа бота в status_store.

Так остальные сервисы (agent_service, chats_service) вообще не знают о
существовании второго фронта — их трогать не нужно. Обратный HTTP-канал
(/internal/bot_message) для sigma не используется.

Асинхронная реализация на aiokafka: consumer живёт прямо в event loop
FastAPI, без отдельных потоков и executor'ов. Poll-цикл — обычная
asyncio-таска, офсеты коммитятся автоматически (enable_auto_commit).
"""
import json
import logging
import asyncio

from aiokafka import AIOKafkaConsumer

logger = logging.getLogger(__name__)


def _sanitize_files(attached_files):
    """Оставляем фронту только name/ext/kind/image_key/size (без служебного path)."""
    if not attached_files:
        return None
    out = []
    for f in attached_files:
        name = f.get("name")
        out.append({
            "name": name,
            "ext": name.rsplit(".", 1)[-1].lower() if name and "." in name else None,
            "kind": f.get("kind"),
            "image_key": f.get("image_key"),
            "size": f.get("size"),
            "thumbhash": f.get("thumbhash"),
            "width": f.get("width"),
            "height": f.get("height"),
        })
    return out


class SigmaConsumer:
    def __init__(self, bootstrap_servers: str, store, topic: str = "chat-messages",
                 group_id: str = "sigma-gateway-group"):
        self._bootstrap = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._store = store
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._run(), name="sigma-consumer")
        logger.info(f"[sigma-consumer] запущен, group_id={self._group_id}, topic={self._topic}")

    async def _run(self) -> None:
        try:
            async for msg in self._consumer:
                if self._stop.is_set():
                    break
                self._handle(msg.value)
        except asyncio.CancelledError:
            logger.debug("[sigma-consumer] цикл отменён")
            raise
        except Exception as e:
            logger.error(f"[sigma-consumer] ошибка в цикле: {e}")

    def _handle(self, payload: dict) -> None:
        try:
            if not payload.get("is_from_bot"):
                return  # интересует только финал бота
            chat_id = payload.get("chat_id")
            if chat_id is None:
                return

            attached = payload.get("attached_files") or payload.get("files")
            message = {
                "user_id": payload.get("user_id", -1),
                "chat_id": chat_id,
                "message": payload.get("message"),
                "message_id": payload.get("message_id"),
                "is_from_bot": True,
                "action": payload.get("action"),
                "files": _sanitize_files(attached),
                "tool_trace": payload.get("tool_trace"),
                "agent_model": payload.get("agent_model"),
            }
            self._store.mark_done(chat_id, message)
            try:
                import run_status
                coro = run_status.clear_running(chat_id, request_id=payload.get("request_id"))
                if asyncio.iscoroutine(coro):
                    asyncio.get_running_loop().create_task(coro)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[sigma-consumer] не удалось обработать сообщение: {e}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"[sigma-consumer] ожидание таски: {e}")
        if self._consumer is not None:
            try:
                await self._consumer.stop()
            except Exception as e:
                logger.debug(f"[sigma-consumer] close: {e}")
        logger.info("[sigma-consumer] остановлен")
