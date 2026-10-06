# db_consumer.py
import json
import logging
import asyncio
from kafka import KafkaConsumer
from app.db.chats_tables import DataBaseChats
from app.core.config import KAFKA_BROKER
from typing import List
import os
import base64
import requests  # Для отправки сообщений обратно в основной сервис
import time

# Обратный путь для Telegram: ответ агента (gateway_target=="telegram") шлём
# не на гейтвей, а прямо телеграм-боту (host-режим) через docker gateway IP.
TG_BOT_PUSH_URL = os.getenv("TG_BOT_PUSH_URL", "http://172.28.0.1:8010/push_message")

# Redis — обратный путь мобильного клиента (gateway_target="mobile").
# Телефон за NAT не принимает входящие, поэтому ответ агента кладём в
# Redis-список mobile:reply:{chat_id}, а мобилка забирает его через
# GET /mobile/poll (long-poll, BLPOP) на chats_service.
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
MOBILE_REPLY_KEY = "mobile:reply:{chat_id}"
MOBILE_REPLY_TTL = 300  # сек

# agent_service отдаёт бинарники не-image файлов (голос, docx, pptx) по HTTP
# эндпоинту /web/file (с диска или восстанавливает из OBS). Контейнеры
# изолированы, поэтому читать файл с диска агента нельзя — только докачка
# по этому URL. Внутри compose-сети catchPM адрес — http://agent_service:9001.
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://agent_service:9001")

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
        self.redis = None  # redis.asyncio.Redis — обратный путь мобилки
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

        try:
            import redis.asyncio as aioredis
            self.redis = aioredis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_timeout=10,
                socket_connect_timeout=5,
                socket_keepalive=True,
                health_check_interval=30,
                retry_on_timeout=True,
            )
            await self.redis.ping()
            logging.info(f"[mobile] consumer Redis подключён: {REDIS_URL}")
        except Exception as e:
            self.redis = None
            logging.error(f"[mobile] consumer Redis недоступен ({REDIS_URL}): {e}")

        self.consumer = KafkaConsumer(
            'chat-messages',
            bootstrap_servers=[KAFKA_BROKER],
            group_id='db-consumer-group',
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='latest',
            enable_auto_commit=True,
            max_partition_fetch_bytes=10485760
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
            attached_files = payload.get("attached_files") or payload.get("files")
            tool_trace = payload.get("tool_trace")
            gateway_target = (payload.get("gateway_target") or "main").lower()
            input_format = payload.get("input_format") or "text"
            
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
                gateway_target=gateway_target,
                input_format=input_format,
            )
            logger.info(f"Сообщение сохранено в БД: chat={chat_id}, user={user_id}")
            elapsed = time.perf_counter() - start
            # logging.info(f"Message Service. Время чтобы вытащить из кафки и положить в БД = {elapsed:.4f}s")
            # Если сообщение от бота, отправляем обратно в основной сервис для WebSocket
            if is_from_bot and gateway_target == "telegram":
                # Ответ агента для Telegram: пушим напрямую боту (host-режим).
                await self.send_to_telegram(chat_id, message, attached_files, message_id=message_id)
                return
            if is_from_bot and gateway_target == "mobile":
                # Ответ агента для мобильного клиента: телефон за NAT, поэтому
                # не пушим ему, а кладём в Redis-список — мобилка заберёт
                # long-poll'ом через GET /mobile/poll.
                mobile_actions = payload.get("mobile_actions") or []
                await self.push_to_mobile(chat_id, message, attached_files, message_id=message_id, actions=mobile_actions)
                return

            if is_from_bot:
                type_agent = payload.get("type_agent", "pm")
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

    async def _hydrate_tg_files(self, files, chat_id=None):
        """Для Telegram дочитываем бинарник картинок из БД (Вариант B).

        Агент СПЕЦИАЛЬНО НЕ кладёт base64 сгенерированных картинок в
        Kafka-payload (бьёт лимит 1 МБ, MessageSizeTooLargeError) — он
        передаёт только image_key. Здесь, непосредственно перед
        отправкой боту, тянем бинарник из generated_images и b64-кодируем.
        Kafka остаётся лёгкой, БД messages не раздувается.
        """
        if not files:
            return files
        MAX_TG = 20 * 1024 * 1024
        for f in files:
            try:
                if f.get("file_b64"):
                    continue  # уже есть бинарник (файлы с диска агента)
                img_key = f.get("image_key")
                if f.get("kind") != "image" or not img_key:
                    # Не картинка из generated_images — это дисковый файл агента
                    # (голос generate_audio → .ogg, документы и т.п.). Контейнеры
                    # ИЗОЛИРОВАНЫ: у консьюмера НЕТ файловой системы агента, читать
                    # f["path"] с диска бессмысленно (os.path.isfile всегда False).
                    # Единственный способ достать байты — HTTP-эндпоинт агента
                    # /web/file?path=...&chat_id=... (отдаёт с диска агента или
                    # восстанавливает из OBS). Это тот же механизм, что у телеги
                    # (_send_agent_file по file_url). Мобилке за NAT нужны байты
                    # прямо в JSON, поэтому докачиваем здесь и кладём base64.
                    file_url = f.get("file_url")
                    fpath = f.get("path")
                    pull_url = None
                    if file_url:
                        pull_url = file_url
                    elif fpath:
                        from urllib.parse import quote
                        pull_url = f"/web/file?path={quote(fpath)}"
                        if chat_id:
                            pull_url += f"&chat_id={quote(str(chat_id))}"
                    if pull_url:
                        if pull_url.startswith("/"):
                            pull_url = AGENT_SERVICE_URL.rstrip("/") + pull_url
                        try:
                            resp = await asyncio.to_thread(
                                requests.get, pull_url, timeout=120
                            )
                            if resp.status_code == 200:
                                raw = resp.content
                                if len(raw) <= MAX_TG:
                                    f["file_b64"] = base64.b64encode(raw).decode("ascii")
                                    f.pop("path", None)
                                    f.pop("file_url", None)
                                    logging.info(
                                        f"[mobile] файл {f.get('name')} = {len(raw)} байт "
                                        f"докачан с /web/file → file_b64 (kind={f.get('kind')})"
                                    )
                                else:
                                    logging.warning(
                                        f"[mobile] файл {f.get('name')} = {len(raw)} байт > 20MB, пропускаю"
                                    )
                            else:
                                logging.warning(
                                    f"[mobile] /web/file -> HTTP {resp.status_code} для {pull_url}"
                                )
                        except Exception as e:
                            logging.error(f"[mobile] не удалось докачать файл по {pull_url}: {e}")
                    continue
                async with self.db.pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT image_data FROM generated_images "
                        "WHERE image_key::text = $1 LIMIT 1",
                        str(img_key).strip(),
                    )
                if row and row["image_data"]:
                    size = len(row["image_data"])
                    if size <= MAX_TG:
                        f["file_b64"] = base64.b64encode(row["image_data"]).decode("ascii")
                        logging.info(
                            f"[telegram] картинка из БД image_key={img_key} = {size} байт "
                            f"→ file_b64 подставлен (consumer, Kafka лёгкая)"
                        )
                    else:
                        logging.warning(
                            f"[telegram] картинка image_key={img_key} = {size} байт > 20MB, пропускаю"
                        )
                else:
                    logging.warning(
                        f"[telegram] картинка image_key={img_key} не найдена в generated_images"
                    )
            except Exception as e:
                logging.error(f"[telegram] не удалось дочитать картинку из БД: {e}")
        return files

    async def push_to_mobile(self, chat_id: str, text: str, files=None, message_id=None, actions=None):
        """Ответ агента → Redis-список для мобильного клиента (AI_tracker).

        Телефон за NAT: сервер не инициирует соединение к нему, поэтому ответ
        кладётся в mobile:reply:{chat_id}, а мобилка забирает его long-poll'ом
        через GET /mobile/poll (BLPOP).

        Голосовой ответ (generate_audio → .ogg) и картинки приезжают так же,
        как для Telegram: бинарник в generated_images по image_key, здесь
        догидрируем file_b64 (переиспользуем _hydrate_tg_files), чтобы мобилка
        получила самодостаточный JSON и проиграла голос «на ухо» без доп.
        запросов.
        """
        if self.redis is None:
            logging.error(f"[mobile] Redis offline — ответ chat={chat_id} потерян")
            return
        try:
            start = time.perf_counter()
            files = await self._hydrate_tg_files(files, chat_id=chat_id)
            # Для мобилки чистим серверный path (телефон его не прочитает) и
            # оставляем самодостаточные поля: name, kind, ext, file_b64.
            mobile_files = None
            if files:
                mobile_files = []
                for f in files:
                    nm = f.get("name") or ""
                    ext = f.get("ext")
                    if not ext and "." in nm:
                        ext = nm.rsplit(".", 1)[-1].lower()
                    mobile_files.append({
                        "name": nm,
                        "kind": f.get("kind"),
                        "ext": ext,
                        "file_b64": f.get("file_b64"),
                    })
            reply = {
                "text": text or "",
                "files": mobile_files,
                "actions": actions or [],
                "message_id": message_id,
                "ts": int(time.time()),
            }
            key = MOBILE_REPLY_KEY.format(chat_id=str(chat_id))
            await self.redis.rpush(key, json.dumps(reply))
            await self.redis.expire(key, MOBILE_REPLY_TTL)
            elapsed = time.perf_counter() - start
            logging.info(
                f"[mobile] ответ агента положен в Redis key={key} "
                f"(files={len(files) if files else 0}) за {elapsed:.4f}s"
            )
        except Exception as e:
            logger.error(f"[mobile] ошибка push в Redis chat={chat_id}: {e}")

    async def send_to_telegram(self, chat_id: str, text: str, files=None, message_id=None):
        """Ответ агента → телеграм-бот (host-режим, эндпоинт /push_message).

        Бот сам нарежет длинный текст и починит HTML-теги. Для картинок
        (сгенерированных агентом) бинарник лежит в БД (Вариант B):
        дочитываем file_b64 по image_key ЗДЕСЬ, чтобы не гнать base64
        через Kafka (лимит 1 МБ).
        """
        try:
            start = time.perf_counter()
            files = await self._hydrate_tg_files(files)
            payload = {
                "chat_id": str(chat_id),
                "text": text or "",
                "files": files or None,
                # бот отредактирует свой плейсхолдер «Думаю...» вместо нового сообщения
                "edit_thinking": True,
                # agent message_id — для связки TG-реакций с записью в БД
                "agent_message_id": message_id,
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