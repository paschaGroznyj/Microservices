# db_consumer.py
import json
import logging
import asyncio
from kafka import KafkaConsumer
from app.db.chats_tables import DataBaseChats
from app.core.config import KAFKA_BROKER
from app.db.tasks_db import TasksDB
from typing import List
import os
import base64
import uuid
import httpx  # Для отправки сообщений обратно в основной сервис
import time

# Обратный путь для Telegram: ответ агента (gateway_target=="telegram") шлём
# не на гейтвей, а прямо телеграм-боту (host-режим) через docker gateway IP.
TG_BOT_PUSH_URL = os.getenv("TG_BOT_PUSH_URL", "http://172.28.0.1:8010/push_message")
# Обратный путь для MAX (аналог Telegram, host-режим бота).
MAX_BOT_PUSH_URL = os.getenv("MAX_BOT_PUSH_URL", "http://172.28.0.1:8011/push_message")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
# FlowMap: хронология пути запроса flow:{chat_id} (см. api_gateway/run_status.flow_push)
FLOW_TTL = int(os.getenv("FLOW_TTL", os.getenv("AGENT_RUN_TTL", "3600")))
FLOW_MAX = int(os.getenv("FLOW_MAX", "600"))
MOBILE_REPLY_KEY = "mobile:reply:{chat_id}"
MOBILE_REPLY_TTL = 300  # сек


AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://agent_service:9001")

# САМОЦИКЛ АГЕНТА: consumer делает реентри — POST обратно в /send_message того же
# chats_service (внутри docker-сети). Новый тик летит от user_id=-100 (служебный).
CHATS_SELF_URL = os.getenv("CHATS_SERVICE_SELF_URL", "http://chats_service:8001")
LOOP_SERVICE_USER_ID = -100
LOOP_SERVICE_USERNAME = "agent-self-loop"
LOOP_STOP_MARKER = "[STOP LOOP]"

STOP_LOOP_INSTRUCTION = f"\nTo stop and exit the self-loop, you need to write {LOOP_STOP_MARKER} at the beginning of your message."



def _clear_loop_flag(chat_id):
    """Снять стабильный Redis-флаг самоцикла agent:loop:{chat_id}.

    Нужен для случаев, когда петля гаснет НЕ по стоп-кнопке (её снимает
    gateway), а по решению consumer: бюджет исчерпан или установка больше
    не активна. Без этого флаг провисит до TTL и фронт после F5 покажет
    ложное «идёт работа». Синхронный redis, best-effort — не роняем петлю.
    """
    try:
        import redis as _redis_sync
        r = _redis_sync.from_url(REDIS_URL, decode_responses=True)
        r.delete(f"agent:loop:{chat_id}")
        r.delete(f"agent:running:{chat_id}")
    except Exception as e:
        logger.debug(f"[self-loop] clear_loop_flag failed chat={chat_id}: {e}")



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
            # размер в байтах (может быть None) — фронт показывает, сколько ждать
            "size": f.get("size"),
            # base64 ThumbHash: размытый фон под кольцом загрузки
            "thumbhash": f.get("thumbhash"),
            "width": f.get("width"),
            "height": f.get("height"),
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

    async def _flow_store(self, chat_id, request_id: str, flow: dict) -> None:
        """FlowMap: событие в хронологию Redis flow:{chat_id} (восстановление после F5).
        Формат как у гейтвея (run_status.flow_push) и агента. Ошибки глушим."""
        if chat_id is None or self.redis is None:
            return
        rec = {"ev": flow.get("ev"), "ts": int(time.time() * 1000), "rid": request_id}
        rec.update({k: v for k, v in flow.items() if k != "ev" and v is not None})
        key = f"flow:{chat_id}"
        try:
            n = await self.redis.rpush(key, json.dumps(rec, ensure_ascii=False))
            if isinstance(n, int) and n > FLOW_MAX:
                await self.redis.ltrim(key, -FLOW_MAX, -1)
            await self.redis.expire(key, FLOW_TTL)
        except Exception as e:
            logger.debug(f"[flow] redis не записал {flow.get('ev')}: {e}")

    async def _send_flow(self, payload: dict, ev: str, **data) -> None:
        """Служебное событие FlowMap (kind="flow") в веб-клиент, по аналогии с агентом.

        Только main (HTTP /internal/bot_stream, гейтвей резолвит чат по request_id)
        и sigma (Redis pub/sub stream:{request_id}). Без request_id и для
        Telegram/MAX/mobile ничего не шлём. Ошибки глушим: карта не должна
        мешать сохранению и доставке сообщения."""
        request_id = payload.get("request_id")
        target = (payload.get("gateway_target") or "main").lower()
        if not request_id or target not in self.gateway_urls:
            return
        flow = {"ev": ev}
        flow.update({k: v for k, v in data.items() if v is not None})
        await self._flow_store(payload.get("chat_id"), request_id, flow)
        frame = {"request_id": request_id, "chunk": "", "id": None, "kind": "flow", "flow": flow}
        try:
            if target == "sigma":
                if self.redis is not None:
                    await self.redis.publish(f"stream:{request_id}", json.dumps(frame, ensure_ascii=False))
                return
            url = f"{self.gateway_urls[target]}/internal/bot_stream"
            async with httpx.AsyncClient(timeout=1.0) as client:
                await client.post(url, json=frame)
        except Exception as e:
            logger.debug(f"[flow] {ev} не отправлен: {e}")

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
            # agent | model: у запроса — что выбрал пользователь, у ответа — чем реально отвечали
            # (agent_service принудительно ставит agent для TG/MAX/loop/задачника).
            type_query = "model" if str(payload.get("type_query") or "").lower() == "model" else "agent"
            poll_answer = payload.get("poll_answer")

            # ЗАДАЧНИК: служебный вход от task_consumer (user_id=-2) в историю не пишем.
            # В чате остаются карточка задачи (agent_tasks) и ответ агента.
            if not is_from_bot and (payload.get("task_trigger") or user_id == -2):
                return

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
                type_query=type_query,
            )
            if not is_from_bot and isinstance(poll_answer, dict):
                try:
                    saved_pa = await self.db.save_poll_answer(
                        chat_id=chat_id,
                        user_id=user_id,
                        user_message_id=message_id,
                        poll_answer=poll_answer,
                    )
                    if saved_pa:
                        logger.info(
                            f"Ответ опроса сохранён в БД: chat={chat_id}, bot_msg={saved_pa.get('bot_message_id')}, idx={saved_pa.get('poll_index')}"
                        )
                except Exception as pa_err:
                    logger.error(f"Ошибка сохранения poll_answer chat={chat_id}: {pa_err}")
            logger.info(f"Сообщение сохранено в БД: chat={chat_id}, user={user_id}")
            # FlowMap: подсветить запись в БД (до доставки ответа, чтобы успеть раньше done).
            await self._send_flow(payload, "db_saved", role="bot" if is_from_bot else "user")
            # ЗАДАЧНИК: ответ агента сохранён — карточки задач его запроса привязываем к нему
            # (после F5 они рисуются под этим сообщением, live фронт переносит их по task_ids).
            linked_task_ids = []
            if is_from_bot and payload.get("request_id") and message_id:
                try:
                    linked_task_ids = await TasksDB(self.db.pool).link_request(payload["request_id"], message_id)
                except Exception as e:
                    logger.error(f"[tasks] link_request chat={chat_id}: {e}")
            elapsed = time.perf_counter() - start
            # logging.info(f"Message Service. Время чтобы вытащить из кафки и положить в БД = {elapsed:.4f}s")
            # Если сообщение от бота, отправляем обратно в основной сервис для WebSocket
            if is_from_bot and gateway_target == "max":
                deliver_chat_id = payload.get("max_chat_id") or chat_id
                await self.send_to_max(deliver_chat_id, message, attached_files, message_id=message_id)
                return
            if is_from_bot and gateway_target == "telegram":
                # Ответ агента для Telegram: пушим напрямую боту (host-режим).
                # chat_id в payload = session_id (ключ памяти). Физический адрес
                # доставки — tg_chat_id (если прокинут из бота); фолбэк на chat_id
                # для обратной совместимости со старым контрактом.
                deliver_chat_id = payload.get("tg_chat_id") or chat_id
                await self.send_to_telegram(deliver_chat_id, message, attached_files, message_id=message_id)
                return
            if is_from_bot and gateway_target == "mobile":
                # Ответ агента для мобильного клиента: телефон за NAT, поэтому
                # не пушим ему, а кладём в Redis-список — мобилка заберёт
                # long-poll'ом через GET /mobile/poll.
                mobile_actions = payload.get("mobile_actions") or []
                await self.push_to_mobile(chat_id, message, attached_files, message_id=message_id, actions=mobile_actions)
                return

            if is_from_bot:
                gateway_url = self.gateway_urls[gateway_target]
                ws_payload = dict(payload)
                ws_payload["files"] = _sanitize_files_for_ws(attached_files) # Адаптивно, чтобы и для файлов пользователя
                ws_payload["gateway_target"] = gateway_target
                if linked_task_ids:
                    ws_payload["task_ids"] = linked_task_ids

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

                # Страховка: если гейтвей не успел снять agent:running:{chat_id}
                # (например, ошибка сокета или пустой ответ), снимаем флаг и закрываем flow здесь.
                if not payload.get("loop") and self.redis is not None and chat_id is not None:
                    try:
                        await self.redis.delete(f"agent:running:{chat_id}")
                        rid = payload.get("request_id")
                        if rid:
                            tail = await self.redis.lrange(f"flow:{chat_id}", -1, -1)
                            last = json.loads(tail[0].decode() if isinstance(tail[0], bytes) else tail[0]) if tail else {}
                            if not (isinstance(last, dict) and last.get("ev") == "done" and last.get("rid") == rid):
                                await self._flow_store(chat_id, rid, {"ev": "done"})
                    except Exception as _re:
                        logger.debug(f"[flow] clear running fallback failed: {_re}")

                # САМОЦИКЛ: после логирования ответа агента — пробуем реентри (новый тик).
                # Только для web (gateway main): telegram/mobile выше сделали return.
                if payload.get("loop"):
                    await self._maybe_reenter_loop(payload)

        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

    async def _maybe_reenter_loop(self, payload):
        """САМОЦИКЛ: решает, продолжать ли петлю, и дёргает новый тик.

        Цепочка: ответ агента (is_from_bot) уже залогирован. Здесь:
          1) проверка бюджета (ЗАГЛУШКА — логика позже);
          2) проверка is_active в agent_loop_tasks (килл-свитч/стоп-кнопка);
          3) POST /send_message с установкой от user_id=-100, loop_tick=True.
        Сообщение агента НЕ переносим — в message кладём ИМЕННО установку из БД.
        """
        chat_id = payload.get("chat_id")
        gateway_target = (payload.get("gateway_target") or "main").lower()
        if gateway_target != "main":
            return  # петля только для web-чата

        # 0) САМО-СТОП АГЕНТА: если агент в своём ответе явно попросил остановить
        # цикл маркером [ОСТАНОВИТЬ ЦИКЛ] — гасим петлю по его решению, чистим
        # флаги и дёргаем финальный тик с просьбой финализировать проделанную работу.
        agent_text = payload.get("message") or ""
        if LOOP_STOP_MARKER in agent_text.upper():
            await self.db.deactivate_loop_task(chat_id)
            _clear_loop_flag(chat_id)
            logger.info(f"[self-loop] агент запросил остановку цикла маркером, петля остановлена chat={chat_id}")
            await self._emit_loop_stop_message(payload, budget_data=None, agent_requested=True)
            return

        # 1) БЮДЖЕТ: три условия агрегирует chats_service (там пул + ключи Activity).
        # fail-open: если эндпоинт недоступен — петлю НЕ рубим, пропускаем тик.
        budget_ok = True
        budget_data = {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{CHATS_SELF_URL}/budget/check/{chat_id}")
            if resp.status_code == 200:
                budget_data = resp.json()
                budget_ok = bool(budget_data.get("allowed", True))
                if not budget_ok:
                    logger.info(
                        f"[self-loop] бюджет: reason={budget_data.get('reason')} "
                        f"estimate_loop={budget_data.get('estimate_loop')} "
                        f"estimate_dept={budget_data.get('estimate_dept')} "
                        f"actual={budget_data.get('actual')} threshold={budget_data.get('threshold')} chat={chat_id}"
                    )
        except Exception as e:
            logger.warning(f"[self-loop] проверка бюджета недоступна ({e}), пропускаем тик chat={chat_id}")
        if not budget_ok:
            # Петля гаснет по бюджету: деактивируем установку + чистим Redis-флаги,
            # после чего дёргаем ОДИН финальный тик (loop=False), чтобы агент
            # сам написал прощальное сообщение с причиной остановки.
            await self.db.deactivate_loop_task(chat_id)
            _clear_loop_flag(chat_id)
            logger.info(f"[self-loop] бюджет исчерпан, петля остановлена chat={chat_id}")
            await self._emit_loop_stop_message(payload, budget_data)
            return

        # 2) Активна ли установка (килл-свитч/стоп снимают is_active).
        task = await self.db.get_active_loop_task(chat_id)
        if not task:
            _clear_loop_flag(chat_id)
            logger.info(f"[self-loop] активной установки нет, петля не продолжается chat={chat_id}")
            return

        # 3) РЕЕНТРИ: POST /send_message с установкой. Настройки чата статичны — берём из payload.
        ms = int(time.time() * 1000)
        tick_payload = {
            "user_id": LOOP_SERVICE_USER_ID,
            "username": LOOP_SERVICE_USERNAME,
            "tb_name": None,
            "message": task["instruction"] + STOP_LOOP_INSTRUCTION,
            "chat_id": chat_id,
            "type_query": "agent",
            "request_id": f"loop-{ms}-{uuid.uuid4().hex[:8]}",
            "message_id": f"msg-{ms}-{uuid.uuid4().hex[:8]}",
            "selected_tools": payload.get("selected_tools") or [],
            "context_size": payload.get("context_size") or "medium",
            "agent_model": payload.get("agent_model"),
            "gateway_target": "main",
            "has_files": False,
            "files": [],
            # флаги петли: loop остаётся True (цикл продолжается), loop_tick=True (служебный),
            # loop_instruction дублируем для шапки агента. loop_instruction=УСТАНОВКУ НЕ ПЕРЕПИСЫВАЕМ.
            "loop": True,
            "loop_tick": True,
            "loop_instruction": task["instruction"],
        }
        url = f"{CHATS_SELF_URL}/send_message"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=tick_payload)
            logger.info(f"[self-loop] реентри chat={chat_id} -> {url} HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[self-loop] реентри не удался chat={chat_id}: {e}")

    async def _emit_loop_stop_message(self, payload, budget_data, agent_requested=False):
        """Отправить ОДИН финальный тик (loop=False) с причиной остановки.

        Петля уже погашена (deactivate + чистка флагов). Здесь мы подмешиваем
        в промпт причину (бюджет упёрся ИЛИ агент сам решил остановиться) и
        просим агента написать финальное сообщение. loop=False — этот тик НЕ
        породит новый реентри (см. _maybe_reenter_loop: вход только если payload.loop).
        """
        chat_id = payload.get("chat_id")

        if agent_requested:
            instruction = (
                "САМОЦИКЛ ОСТАНОВЛЕН ПО ТВОЕМУ РЕШЕНИЮ. "
                "Ты сам решил завершить цикл (запросил остановку маркером [ОСТАНОВИТЬ ЦИКЛ]). "
                "Финализируй работу: напиши пользователю ОДНО сообщение — коротко расскажи "
                "о проделанной за цикл работе и о том, что цикл завершён по твоему решению. "
                "Не вызывай инструменты, не продолжай работу — только подведи итог."
            )
        else:
            checks = (budget_data or {}).get("checks") or {}
            reason = (budget_data or {}).get("reason") or "budget"

            if checks.get("C_loop_limit") is False:
                cause = (
                    f"Средства в рамках текущей петли исчерпаны: оценка "
                    f"{(budget_data or {}).get('estimate_loop')} ₽ превысила лимит петли "
                    f"{(budget_data or {}).get('loop_hard_limit')} ₽."
                )
            elif checks.get("A_activity") is False or checks.get("B_estimate") is False:
                cause = (
                    f"Суточный бюджет отдела исчерпан: расход "
                    f"{(budget_data or {}).get('estimate_dept')} ₽ (факт Activity {(budget_data or {}).get('actual')} ₽) "
                    f"достиг порога {(budget_data or {}).get('threshold')} ₽."
                )
            else:
                cause = f"Бюджет исчерпан (reason={reason})."

            instruction = (
                "САМОЦИКЛ ОСТАНОВЛЕН ПО БЮДЖЕТУ. "
                f"Причина: {cause} "
                "Напиши пользователю ОДНО короткое сообщение: почему самоцикл был остановлен. "
                "Не вызывай инструменты, не продолжай работу — только сообщи об остановке."
            )

        ms = int(time.time() * 1000)
        tick_payload = {
            "user_id": LOOP_SERVICE_USER_ID,
            "username": LOOP_SERVICE_USERNAME,
            "tb_name": None,
            "message": instruction,
            "chat_id": chat_id,
            "type_query": "agent",
            "request_id": f"loopstop-{ms}-{uuid.uuid4().hex[:8]}",
            "message_id": f"msg-{ms}-{uuid.uuid4().hex[:8]}",
            "selected_tools": payload.get("selected_tools") or [],
            "context_size": payload.get("context_size") or "medium",
            "agent_model": payload.get("agent_model"),
            "gateway_target": "main",
            "has_files": False,
            "files": [],
            "loop": False,
            "loop_tick": True,
            "loop_instruction": instruction,
        }
        url = f"{CHATS_SELF_URL}/send_message"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=tick_payload)
            logger.info(f"[self-loop] финальный тик (причина остановки) chat={chat_id} -> HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"[self-loop] финальный тик не удался chat={chat_id}: {e}")

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
                            async with httpx.AsyncClient(timeout=120.0) as client:
                                resp = await client.get(pull_url)
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(TG_BOT_PUSH_URL, json=payload)
            logger.info(f"Ответ агента отправлен в Telegram: {response.status_code}")
            elapsed = time.perf_counter() - start
            logging.info(f"Message Service. Время отправки ответа в Telegram = {elapsed:.4f}s")
        except Exception as e:
            logger.error(f"Ошибка отправки в Telegram: {e}")

    async def send_to_max(self, chat_id: str, text: str, files=None, message_id=None):
        """Ответ агента → MAX-бот (host-режим, эндпоинт /push_message).

        Бот сам нарежет длинный текст (лимит MAX ~4000) и выполнит 2-шаговую
        загрузку вложений. Картинки агента лежат в generated_images по image_key
        (Вариант B): дочитываем file_b64 ЗДЕСЬ (переиспользуем _hydrate_tg_files),
        чтобы не гнать base64 через Kafka. Дисковые файлы (голос/видео/документы)
        догидрируются по file_url/path с agent_service.
        """
        try:
            start = time.perf_counter()
            files = await self._hydrate_tg_files(files, chat_id=chat_id)
            payload = {
                "chat_id": str(chat_id),
                "text": text or "",
                "files": files or None,
                "agent_message_id": message_id,
                "parse_mode": "markdown",
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(MAX_BOT_PUSH_URL, json=payload)
            logger.info(f"Ответ агента отправлен в MAX: {response.status_code}")
            elapsed = time.perf_counter() - start
            logging.info(f"Message Service. Время отправки ответа в MAX = {elapsed:.4f}s")
        except Exception as e:
            logger.error(f"Ошибка отправки в MAX: {e}")

    async def send_to_main_service(self, chat_id: str, message: dict, gateway_url: str):
        """Отправляем сообщение обратно в основной сервис для WebSocket"""
        try:
            start = time.perf_counter()
            # Внутренний endpoint в основном сервисе для получения сообщений от бота
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{gateway_url}/internal/bot_message",
                    json={
                        "chat_id": chat_id,
                        "message": message, # Сообщение из кафки, сам текст, флаг на бота, ссылки
                    },
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

    # Задачник: свой Kafka-консьюмер task-events в отдельном потоке того же контейнера.
    from app.task_consumer import start_in_thread
    start_in_thread()

    try:
        await consumer.consume()
    except KeyboardInterrupt:
        logger.info("Остановка потребителя...")
    finally:
        await consumer.close()


if __name__ == "__main__":
    asyncio.run(main())