"""
task_consumer.py — задачник в контейнере chats-consumer (отдельный поток рядом с endpoint_consumer).

  agent_sandbox --task-events--> upsert в agent_tasks (по seq) --> гейтвей /internal/task_event (task_update)
  finished + автотриггер: чат попадает в очередь побудки (self.pending), её разбирает tick():
     1. в БД берём готовые задачи чата без triggered_at (completed|failed, notify_agent);
        задачи одного запроса агента ждут друг друга (пачка), но не дольше TASK_BATCH_WAIT_S;
     2. атомарно занимаем agent:running:{chat_id} (SET NX EX, тот же формат, что run_status
        гейтвея) — занят пользователем или другой побудкой -> ждём следующего тика;
     3. гейтвей /internal/task_trigger + chat-messages от user_id=-2 (agent_service/app/task_trigger.py)
        со всеми готовыми задачами одним сообщением;
     4. triggered_at ставим только после подтверждённой отправки в Kafka; при ошибке флаг снимаем
        и пробуем снова на следующем тике.
  Redis недоступен -> не будим, ждём. Рестарт консьюмера -> sweep по БД подбирает
  незатриггеренные задачи моложе TASK_TRIGGER_MAX_AGE_S (и каждые SWEEP_S секунд).
  Сам служебный вход endpoint_consumer в БД не пишет.

Автотриггер: env TASK_AUTOTRIGGER (1 по умолчанию). На лету без рестарта:
  redis-cli SET tasks:autotrigger 0   — выключить;  SET ... 1 — включить;  DEL — снова по env.
Отменённые задачи агента не будят: отмену просил сам пользователь. Задачи, завершённые при
выключенном автотриггере, получают notify_agent=false и потом не всплывают.
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime

import requests

from app.db.tasks_db import FINISHED, TasksDB, jval

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
MAX_AGE_S = int(os.getenv("TASK_TRIGGER_MAX_AGE_S", "3600"))   # старше — не будим (рестарт, зависание)
BATCH_WAIT_S = int(os.getenv("TASK_BATCH_WAIT_S", "600"))      # сколько готовые ждут задачи своего запроса
SWEEP_S = int(os.getenv("TASK_TRIGGER_SWEEP_S", "60"))
RUN_TTL = int(os.getenv("AGENT_RUN_TTL", "3600"))              # как в run_status гейтвея
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


def _num_ts(v):
    if isinstance(v, datetime):
        return v.timestamp()
    return v


def row_task(row: dict) -> dict:
    """Строка agent_tasks -> снимок задачи для агента (как public_task у события)."""
    t = {k: row.get(k) for k in PUBLIC_FIELDS}
    for k, d in (("stages", []), ("progress", {}), ("files", [])):
        t[k] = jval(t.get(k), d)
    for k in ("created_at", "started_at", "finished_at"):
        t[k] = _num_ts(t.get(k))
    t["message_id"] = row.get("message_id")
    return t


def row_owner(row: dict) -> dict:
    owner = dict(jval(row.get("owner"), {}) or {})
    if owner.get("user_id") is None and row.get("user_id") is not None:
        owner["user_id"] = row["user_id"]
    return owner


def build_trigger_payload(rows: list, request_id: str, chat_id: str, owner: dict) -> dict:
    """Одна побудка на все готовые задачи. task — первая (совместимость), tasks — все."""
    tasks = []
    for r in rows:
        t = row_task(r)
        t["owner"] = {"user_id": owner.get("user_id")}
        tasks.append(t)
    labels = ", ".join(str(t.get("label") or t["task_id"]) for t in tasks)
    first = tasks[0]["task_id"]
    return {
        "user_id": TASK_TRIGGER_USER_ID, "tb_name": "", "username": TASK_TRIGGER_USERNAME,
        "chat_id": str(chat_id), "message": f"[task_trigger] {labels}",
        "type_agent": "pm", "is_from_bot": False, "request_id": request_id,
        "selected_tools": owner.get("selected_tools") or [],
        "context_size": owner.get("context_size") or "medium",
        "message_id": f"task-{first}" if len(tasks) == 1 else f"tasks-{request_id}",
        "has_files": False, "files": [],
        "agent_model": owner.get("agent_model"),
        "gateway_target": str(owner.get("gateway_target") or "main").lower(),
        "input_format": "text", "timezone_name": owner.get("timezone_name"),
        "task_trigger": True, "owner_user_id": owner.get("user_id"),
        "task": tasks[0], "tasks": tasks,
    }


def ready_groups(rows: list, open_requests: set, now: float, batch_wait_s: float) -> tuple:
    """Готовые к побудке строки и есть ли ещё ждущие пачки.
    Группа = задачи одного request_id (без request_id — сама по себе). Группа ждёт, пока в её
    запросе идут другие задачи, но не дольше batch_wait_s от первой завершённой."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(r.get("request_id") or f"task:{r['task_id']}", []).append(r)
    ready, waiting = [], False
    for key, grp in groups.items():
        oldest = min((_num_ts(r.get("finished_at")) or now) for r in grp)
        if key in open_requests and now - oldest < batch_wait_s:
            waiting = True
            continue
        ready.extend(grp)
    ready.sort(key=lambda r: (_num_ts(r.get("finished_at")) or 0))
    return ready, waiting


async def _to_thread(fn, *args):
    return await asyncio.to_thread(fn, *args)


class TaskConsumer:
    def __init__(self, tasks_db, redis_client=None, producer=None, http_post=requests.post,
                 redis_factory=None, run_blocking=_to_thread, max_age_s=MAX_AGE_S,
                 batch_wait_s=BATCH_WAIT_S, sweep_s=SWEEP_S, poll_s=2.0, clock=time.time):
        self.tasks = tasks_db
        self.redis = redis_client
        self.redis_factory = redis_factory
        self.producer = producer
        self.http_post = http_post
        self.run_blocking = run_blocking
        self.max_age_s, self.batch_wait_s = max_age_s, batch_wait_s
        self.sweep_s, self.poll_s, self.clock = sweep_s, poll_s, clock
        self.pending: set = set()
        self._last_sweep = None
        self._last_tick = None
        self._redis_warned = False

    # ---------- события ----------
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
        if ev.get("status") in FINISHED and gateway_of(ev) in GATEWAY_URLS:
            enabled = autotrigger_enabled(self.redis)
            if enabled:
                # и отмена тоже: она могла быть последней в пачке, которую ждут готовые задачи
                self.pending.add(str(ev["chat_id"]))
                res["trigger"] = should_trigger(ev, True)
            elif should_trigger(ev, True):
                await self.tasks.disable_notify(ev["task_id"])
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

    # ---------- Redis ----------
    def _redis(self):
        if self.redis is None and self.redis_factory is not None:
            try:
                rc = self.redis_factory()
                rc.ping()
                self.redis = rc
                log.info("[tasks] Redis снова доступен")
            except Exception:
                self.redis = None
        return self.redis

    def _redis_down(self, e=None) -> str:
        if not self._redis_warned:
            log.warning(f"[tasks] Redis недоступен, побудка отложена до восстановления: {e}")
            self._redis_warned = True
        self.redis = None if self.redis_factory is not None else self.redis
        return "redis_down"

    @staticmethod
    def _key(chat_id) -> str:
        return f"agent:running:{chat_id}"

    def _release(self, rc, chat_id, request_id):
        """Снять только свой флаг: между get и delete окно в миллисекунды, Lua ради него не нужен."""
        try:
            v = rc.get(self._key(chat_id))
            if v and request_id in str(v):
                rc.delete(self._key(chat_id))
        except Exception as e:
            log.warning(f"[tasks] не снят флаг agent:running chat={chat_id}: {e}")

    # ---------- побудка ----------
    async def dispatch_chat(self, chat_id: str) -> str:
        """sent | nothing | waiting | busy | redis_down | send_failed | disabled"""
        chat_id = str(chat_id)
        rc = self._redis()
        if rc is None:
            return self._redis_down()
        try:
            if rc.exists(self._key(chat_id)):
                return "busy"
        except Exception as e:
            return self._redis_down(e)
        self._redis_warned = False
        if not autotrigger_enabled(rc):
            return "disabled"

        rows, open_requests = await self.tasks.chat_state(chat_id, self.max_age_s, tuple(GATEWAY_URLS))
        if not rows:
            return "nothing"
        ready, waiting = ready_groups(rows, open_requests, self.clock(), self.batch_wait_s)
        if not ready:
            return "waiting"
        owner = row_owner(ready[-1])
        # один владелец на побудку: в общих чатах чужие задачи уйдут следующей пачкой
        ready = [r for r in ready if row_owner(r).get("user_id") == owner.get("user_id")]
        if owner.get("user_id") is None:
            log.warning(f"[tasks] нет владельца задач chat={chat_id}, побудка невозможна")
            await self.tasks.disable_notify_many([r["task_id"] for r in ready])
            return "nothing"

        request_id = str(uuid.uuid4())
        lock = json.dumps({"request_id": request_id, "model": owner.get("agent_model"),
                           "started_at": self.clock(), "run_ttl": RUN_TTL, "user_message": None,
                           "user_message_id": None, "source": "task_trigger"})
        try:
            if not rc.set(self._key(chat_id), lock, nx=True, ex=RUN_TTL):
                return "busy"   # пользователь или другая побудка успели первыми
        except Exception as e:
            return self._redis_down(e)

        ids = [r["task_id"] for r in ready]
        gw = str(owner.get("gateway_target") or "main").lower()
        payload = build_trigger_payload(ready, request_id, chat_id, owner)
        self._post(gw, "/internal/task_trigger",
                   {"chat_id": chat_id, "request_id": request_id, "task_id": ids[0],
                    "label": ready[0].get("label"), "task_ids": ids,
                    "labels": [r.get("label") for r in ready]})
        try:
            await self.run_blocking(self._send, payload)
        except Exception as e:
            log.error(f"[tasks] побудка не отправлена chat={chat_id} tasks={ids}: {e}")
            self._release(rc, chat_id, request_id)
            return "send_failed"
        try:
            await self.tasks.mark_triggered_many(ids)
        except Exception as e:  # агент уже разбужен; после его ответа sweep может разбудить повторно
            log.error(f"[tasks] triggered_at не записан tasks={ids}: {e}")
        log.info(f"[tasks] агент разбужен chat={chat_id} tasks={ids} req={request_id}"
                 + (" (ещё есть ждущие пачки)" if waiting else ""))
        return "sent"

    def _send(self, payload: dict):
        fut = self.producer.send(CHAT_MESSAGES_TOPIC, key=TASK_TRIGGER_USER_ID, value=payload)
        if fut is not None and hasattr(fut, "get"):
            fut.get(timeout=10)   # подтверждение брокера, иначе triggered_at не ставим
        self.producer.flush()

    async def sweep(self):
        if not autotrigger_enabled(self.redis):
            return
        try:
            chats = await self.tasks.pending_chats(self.max_age_s, tuple(GATEWAY_URLS))
        except Exception as e:
            log.error(f"[tasks] sweep: {e}")
            return
        if chats:
            log.info(f"[tasks] sweep: чаты с неотправленными результатами задач: {len(chats)}")
        self.pending.update(str(c) for c in chats)

    async def tick(self) -> dict:
        now = self.clock()
        if self._last_tick is not None and now - self._last_tick < self.poll_s:
            return {}
        self._last_tick = now
        if self._last_sweep is None or now - self._last_sweep >= self.sweep_s:
            self._last_sweep = now
            await self.sweep()
        out = {}
        for chat_id in list(self.pending):
            try:
                st = await self.dispatch_chat(chat_id)
            except Exception as e:
                log.error(f"[tasks] dispatch chat={chat_id}: {e}")
                st = "error"
            out[chat_id] = st
            if st in ("nothing", "disabled"):
                self.pending.discard(chat_id)
        return out


async def _run():
    from kafka import KafkaConsumer, KafkaProducer
    import redis as redis_sync
    from app.core.config import KAFKA_BROKER
    from app.db.chats_tables import DataBaseChats

    db = DataBaseChats()
    await db.init_db_pool()
    tasks = TasksDB(db.pool)
    try:
        await tasks.ensure_schema()
    except Exception as e:
        log.error(f"[tasks] ensure_schema agent_tasks: {e}")

    def redis_factory():
        return redis_sync.from_url(REDIS_URL, decode_responses=True)

    try:
        rc = redis_factory()
        rc.ping()
    except Exception as e:
        log.error(f"[tasks] Redis недоступен, побудка ждёт его восстановления: {e}")
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
    tc = TaskConsumer(tasks, rc, producer, redis_factory=redis_factory)
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
        await tc.tick()


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
