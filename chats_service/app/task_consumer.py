"""
task_consumer.py — задачник в контейнере chats-consumer (отдельный поток рядом с endpoint_consumer).

  agent_sandbox --task-events--> upsert в agent_tasks (по seq) --> гейтвей /internal/task_event (task_update)
  finished (completed|failed) + notify_agent + автотриггер:
     ждём, пока агент в чате свободен (Redis agent:running:{chat_id})
     --> гейтвей /internal/task_trigger (request_id, флаг «агент в работе»)
     --> chat-messages от user_id=-2 с task_trigger=True (agent_service/app/task_trigger.py)
  Сам служебный вход endpoint_consumer в БД не пишет.

Автотриггер: env TASK_AUTOTRIGGER (1 по умолчанию). На лету без рестарта:
  redis-cli SET tasks:autotrigger 0   — выключить;  SET ... 1 — включить;  DEL — снова по env.
Отменённые задачи агента не будят: отмену просил сам пользователь.
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid

import requests

from app.db.tasks_db import TasksDB

log = logging.getLogger("task_consumer")

TASK_EVENTS_TOPIC = "task-events"
CHAT_MESSAGES_TOPIC = "chat-messages"
TASK_TRIGGER_USER_ID = -2
TASK_TRIGGER_USERNAME = "task-trigger"
AUTOTRIGGER_KEY = "tasks:autotrigger"
TRIGGER_STATUSES = ("completed", "failed")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
GATEWAY_URLS = {
    "main": os.getenv("TASK_GW_MAIN_URL", "http://api_gateway:7000"),
    "sigma": os.getenv("TASK_GW_SIGMA_URL", "http://api_gateway_sigma:7010"),
}
WAIT_IDLE_S = int(os.getenv("TASK_TRIGGER_WAIT_S", "900"))
PUBLIC_FIELDS = (
    "task_id", "label", "kind", "status", "stages", "progress", "elapsed_ms", "exit_code",
    "error", "stderr_tail", "stdout_tail", "files", "workspace_id", "created_at",
    "started_at", "finished_at", "seq",
)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def autotrigger_enabled(redis_client=None) -> bool:
    if redis_client is not None:
        try:
            v = redis_client.get(AUTOTRIGGER_KEY)
        except Exception:
            v = None
        if v is not None:
            return _truthy(v)
    return _truthy(os.getenv("TASK_AUTOTRIGGER", "1"))


def gateway_of(ev: dict) -> str:
    return str((ev.get("owner") or {}).get("gateway_target") or "main").lower()


def public_task(ev: dict, message_id=None) -> dict:
    """Снимок для фронта: без owner (права, модель, request_id наружу не нужны)."""
    t = {k: ev.get(k) for k in PUBLIC_FIELDS}
    t["message_id"] = message_id
    return t


def should_trigger(ev: dict, enabled: bool) -> bool:
    return (enabled and ev.get("status") in TRIGGER_STATUSES
            and bool(ev.get("notify_agent", True)) and gateway_of(ev) in GATEWAY_URLS)


def build_trigger_payload(ev: dict, request_id: str) -> dict:
    owner = ev.get("owner") or {}
    task = public_task(ev)
    task["owner"] = {"user_id": owner.get("user_id")}
    return {
        "user_id": TASK_TRIGGER_USER_ID, "tb_name": "", "username": TASK_TRIGGER_USERNAME,
        "chat_id": str(ev["chat_id"]), "message": f"[task_trigger] {ev.get('label') or ev['task_id']}",
        "type_agent": "pm", "is_from_bot": False, "request_id": request_id,
        "selected_tools": owner.get("selected_tools") or [],
        "context_size": owner.get("context_size") or "medium",
        "message_id": f"task-{ev['task_id']}", "has_files": False, "files": [],
        "agent_model": owner.get("agent_model"), "gateway_target": gateway_of(ev),
        "input_format": "text", "timezone_name": owner.get("timezone_name"),
        "task_trigger": True, "owner_user_id": owner.get("user_id"), "task": task,
    }


def _spawn_thread(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True, name="task-trigger").start()


class TaskConsumer:
    def __init__(self, tasks_db, redis_client=None, producer=None, http_post=requests.post,
                 spawn=_spawn_thread, wait_s=WAIT_IDLE_S, poll_s=2.0, sleep=time.sleep):
        self.tasks = tasks_db
        self.redis = redis_client
        self.producer = producer
        self.http_post = http_post
        self.spawn = spawn
        self.wait_s, self.poll_s, self.sleep = wait_s, poll_s, sleep

    async def handle_event(self, ev) -> dict:
        res = {"stored": False, "pushed": False, "trigger": False}
        if not isinstance(ev, dict) or not ev.get("task_id") or not ev.get("chat_id"):
            return res
        mid = await self.tasks.upsert_event(ev)
        if mid is False:  # устаревшее событие (seq не новее сохранённого)
            return res
        res["stored"] = True
        res["pushed"] = self._post(gateway_of(ev), "/internal/task_event",
                                   {"chat_id": str(ev["chat_id"]), "task": public_task(ev, mid)})
        if should_trigger(ev, autotrigger_enabled(self.redis)) and await self.tasks.mark_triggered(ev["task_id"]):
            self.spawn(self.trigger, ev)
            res["trigger"] = True
        return res

    def _post(self, gateway: str, path: str, body: dict) -> bool:
        base = GATEWAY_URLS.get(gateway)
        if not base:  # telegram/max/mobile: карточек нет
            return False
        try:
            r = self.http_post(f"{base}{path}", json=body, timeout=5)
            return getattr(r, "status_code", 200) < 400
        except Exception as e:
            log.warning(f"[tasks] {path} -> {gateway}: {e}")
            return False

    def wait_idle(self, chat_id: str) -> bool:
        if self.redis is None:
            return True
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                busy = self.redis.exists(f"agent:running:{chat_id}")
            except Exception:
                return True
            if not busy:
                return True
            if time.monotonic() >= deadline:
                return False
            self.sleep(self.poll_s)

    def trigger(self, ev: dict) -> bool:
        chat_id = str(ev["chat_id"])
        if not self.wait_idle(chat_id):
            log.warning(f"[tasks] агент занят дольше {self.wait_s}с, триггер пропущен task={ev['task_id']}")
            return False
        request_id = str(uuid.uuid4())
        self._post(gateway_of(ev), "/internal/task_trigger",
                   {"chat_id": chat_id, "request_id": request_id,
                    "task_id": ev["task_id"], "label": ev.get("label")})
        self.producer.send(CHAT_MESSAGES_TOPIC, key=TASK_TRIGGER_USER_ID,
                           value=build_trigger_payload(ev, request_id))
        self.producer.flush()
        log.info(f"[tasks] агент разбужен chat={chat_id} task={ev['task_id']} req={request_id}")
        return True


async def _run():
    from kafka import KafkaConsumer, KafkaProducer
    import redis as redis_sync
    from app.core.config import KAFKA_BROKER
    from app.db.chats_tables import DataBaseChats

    db = DataBaseChats()
    await db.init_db_pool()
    try:
        rc = redis_sync.from_url(REDIS_URL, decode_responses=True)
        rc.ping()
    except Exception as e:
        log.error(f"[tasks] Redis недоступен, ожидание агента и флаг автотриггера отключены: {e}")
        rc = None
    producer = KafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False, default=str).encode("utf-8"),
        key_serializer=lambda k: str(k).encode("utf-8"),
    )
    consumer = KafkaConsumer(
        TASK_EVENTS_TOPIC, bootstrap_servers=[KAFKA_BROKER], group_id="chats-task-consumer",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest", enable_auto_commit=True,
    )
    tc = TaskConsumer(TasksDB(db.pool), rc, producer)
    loop = asyncio.get_running_loop()
    log.info(f"[tasks] task consumer запущен, автотриггер={autotrigger_enabled(rc)}")
    while True:
        batch = await loop.run_in_executor(None, lambda: consumer.poll(timeout_ms=1000))
        for msgs in batch.values():
            for m in msgs:
                try:
                    await tc.handle_event(m.value)
                except Exception as e:
                    log.error(f"[tasks] событие не обработано: {e}")


def _run_forever():
    while True:
        try:
            asyncio.run(_run())
        except Exception as e:
            log.error(f"[tasks] task consumer упал, перезапуск через 5с: {e}")
            time.sleep(5)


def start_in_thread() -> threading.Thread:
    t = threading.Thread(target=_run_forever, daemon=True, name="task-consumer")
    t.start()
    return t
