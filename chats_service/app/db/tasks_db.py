"""agent_tasks: карточки фоновых задач песочницы (postgres/sql/02_chats/04_agent_tasks.sql)."""
import json
from datetime import datetime, timezone

TASK_COLUMNS = (
    "task_id", "chat_id", "user_id", "request_id", "message_id", "workspace_id", "label",
    "kind", "status", "stages", "progress", "elapsed_ms", "exit_code", "error",
    "stderr_tail", "stdout_tail", "files", "created_at", "started_at", "finished_at",
)
# JSON для фронта в get_chat_messages: те же поля, что в WS task_update.
TASKS_JSON_SQL = """
    COALESCE((
        SELECT json_agg(json_build_object(
            'task_id', t.task_id, 'label', t.label, 'kind', t.kind, 'status', t.status,
            'stages', t.stages, 'progress', t.progress, 'elapsed_ms', t.elapsed_ms,
            'exit_code', t.exit_code, 'error', t.error, 'stderr_tail', t.stderr_tail,
            'stdout_tail', t.stdout_tail, 'files', t.files, 'workspace_id', t.workspace_id,
            'created_at', t.created_at, 'finished_at', t.finished_at
        ) ORDER BY t.created_at)
        FROM agent_tasks t
        WHERE t.chat_id = c.chat_id AND t.message_id = c.message_id
    ), '[]'::json)
"""


def _ts(v):
    if v in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def event_row(ev: dict) -> dict:
    """Событие task-events -> значения колонок agent_tasks."""
    owner = ev.get("owner") or {}
    try:
        uid = int(owner.get("user_id")) if owner.get("user_id") is not None else None
    except (TypeError, ValueError):
        uid = None
    return {
        "task_id": ev["task_id"], "chat_id": str(ev.get("chat_id") or ""),
        "user_id": uid, "request_id": owner.get("request_id"),
        "workspace_id": ev.get("workspace_id"), "label": (ev.get("label") or "")[:200],
        "kind": ev.get("kind") if ev.get("kind") in ("script", "media") else "script",
        "status": ev.get("status") or "pending",
        "stages": json.dumps(ev.get("stages") or [], ensure_ascii=False),
        "progress": json.dumps(ev.get("progress") or {}, ensure_ascii=False),
        "elapsed_ms": int(ev.get("elapsed_ms") or 0),
        "exit_code": ev.get("exit_code"), "error": ev.get("error"),
        "stderr_tail": ev.get("stderr_tail") or None, "stdout_tail": ev.get("stdout_tail") or None,
        "files": json.dumps(ev.get("files") or [], ensure_ascii=False),
        "notify_agent": bool(ev.get("notify_agent", True)), "seq": int(ev.get("seq") or 0),
        "created_at": _ts(ev.get("created_at")) or datetime.now(timezone.utc),
        "started_at": _ts(ev.get("started_at")), "finished_at": _ts(ev.get("finished_at")),
        "owner": json.dumps(owner_snapshot(owner), ensure_ascii=False),
    }


# Что из owner события нужно, чтобы разбудить агента после рестарта консьюмера
# (событие из Kafka к тому времени уже прочитано, остаётся только строка в БД).
OWNER_KEYS = ("user_id", "gateway_target", "agent_model", "selected_tools", "context_size",
              "timezone_name", "request_id")


def owner_snapshot(owner: dict) -> dict:
    return {k: owner.get(k) for k in OWNER_KEYS if owner.get(k) is not None}


def jval(v, default):
    """jsonb без кодека asyncpg приходит строкой."""
    if v is None:
        return default
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return v


UPSERT_SQL = """
INSERT INTO agent_tasks (task_id, chat_id, user_id, request_id, workspace_id, label, kind, status,
    stages, progress, elapsed_ms, exit_code, error, stderr_tail, stdout_tail, files,
    notify_agent, seq, created_at, started_at, finished_at, message_id, owner)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,$12,$13,$14,$15,$16::jsonb,$17,$18,$19,$20,$21,$22,$23::jsonb)
ON CONFLICT (task_id) DO UPDATE SET
    status=EXCLUDED.status, stages=EXCLUDED.stages, progress=EXCLUDED.progress,
    elapsed_ms=EXCLUDED.elapsed_ms, exit_code=EXCLUDED.exit_code, error=EXCLUDED.error,
    stderr_tail=EXCLUDED.stderr_tail, stdout_tail=EXCLUDED.stdout_tail, files=EXCLUDED.files,
    seq=EXCLUDED.seq, started_at=COALESCE(EXCLUDED.started_at, agent_tasks.started_at),
    finished_at=EXCLUDED.finished_at,
    message_id=COALESCE(agent_tasks.message_id, EXCLUDED.message_id),
    owner=CASE WHEN EXCLUDED.owner = '{}'::jsonb THEN agent_tasks.owner ELSE EXCLUDED.owner END,
    updated_at=now()
WHERE agent_tasks.seq < EXCLUDED.seq
RETURNING message_id
"""


# Существующие базы: init-скрипты postgres там не выполняются, колонку и индекс
# досоздаёт сам консьюмер при старте (идемпотентно).
ENSURE_SCHEMA_SQL = (
    "ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS owner JSONB NOT NULL DEFAULT '{}'::jsonb",
    "CREATE INDEX IF NOT EXISTS agent_tasks_untriggered_idx ON agent_tasks (chat_id, finished_at) "
    "WHERE triggered_at IS NULL AND notify_agent",
)
FINISHED = ("completed", "failed", "cancelled")
NOTIFY = ("completed", "failed")
OPEN = ("pending", "running")

# Чаты, где есть завершённая задача, о которой агент ещё не знает.
# Только веб-гейтвеи: у telegram/max/mobile карточек и побудки нет.
PENDING_CHATS_SQL = """
SELECT DISTINCT chat_id FROM agent_tasks
WHERE triggered_at IS NULL AND notify_agent AND status = ANY($1::text[])
  AND finished_at > now() - make_interval(secs => $2)
  AND lower(COALESCE(owner->>'gateway_target', 'main')) = ANY($3::text[])
"""
CHAT_UNTRIGGERED_SQL = """
SELECT * FROM agent_tasks
WHERE chat_id = $1 AND triggered_at IS NULL AND notify_agent AND status = ANY($2::text[])
  AND finished_at > now() - make_interval(secs => $3)
  AND lower(COALESCE(owner->>'gateway_target', 'main')) = ANY($4::text[])
ORDER BY finished_at, created_at
"""
# Запросы агента в этом чате, где ещё идёт хоть одна задача: их готовые задачи ждут остальных,
# чтобы агент получил результаты одной пачкой. Зависшие дольше max_age не держат пачку.
CHAT_OPEN_REQUESTS_SQL = """
SELECT DISTINCT request_id FROM agent_tasks
WHERE chat_id = $1 AND request_id IS NOT NULL AND status = ANY($2::text[])
  AND created_at > now() - make_interval(secs => $3)
"""


class TasksDB:
    def __init__(self, pool):
        self.pool = pool

    async def ensure_schema(self) -> None:
        async with self.pool.acquire() as conn:
            for sql in ENSURE_SCHEMA_SQL:
                await conn.execute(sql)

    async def pending_chats(self, max_age_s: float, gateways) -> list:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(PENDING_CHATS_SQL, list(NOTIFY), float(max_age_s), list(gateways))
        return [r["chat_id"] for r in rows]

    async def chat_state(self, chat_id: str, max_age_s: float, gateways) -> tuple:
        """(незатриггеренные готовые задачи чата, request_id с ещё идущими задачами)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(CHAT_UNTRIGGERED_SQL, str(chat_id), list(NOTIFY),
                                    float(max_age_s), list(gateways))
            open_rows = await conn.fetch(CHAT_OPEN_REQUESTS_SQL, str(chat_id), list(OPEN), float(max_age_s))
        return [dict(r) for r in rows], {r["request_id"] for r in open_rows}

    async def mark_triggered_many(self, task_ids) -> list:
        """Отметить побудку после успешной отправки. Возвращает реально отмеченные."""
        if not task_ids:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "UPDATE agent_tasks SET triggered_at=now() "
                "WHERE task_id = ANY($1::text[]) AND triggered_at IS NULL RETURNING task_id",
                list(task_ids))
        return [r["task_id"] for r in rows]

    async def upsert_event(self, ev: dict, message_id: str | None = None):
        """Применить событие, если его seq новее. Возвращает message_id карточки
        (может быть None, пока ответ агента не сохранён) или False, если событие устарело."""
        r = event_row(ev)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                UPSERT_SQL, r["task_id"], r["chat_id"], r["user_id"], r["request_id"],
                r["workspace_id"], r["label"], r["kind"], r["status"], r["stages"], r["progress"],
                r["elapsed_ms"], r["exit_code"], r["error"], r["stderr_tail"], r["stdout_tail"],
                r["files"], r["notify_agent"], r["seq"], r["created_at"], r["started_at"],
                r["finished_at"], message_id, r["owner"],
            )
        return False if row is None else row["message_id"]

    async def link_request(self, request_id: str, message_id: str) -> list:
        """Ответ агента сохранён: карточки задач его запроса привязываем к нему."""
        if not request_id or not message_id:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "UPDATE agent_tasks SET message_id=$2, updated_at=now() "
                "WHERE request_id=$1 AND message_id IS NULL RETURNING task_id", request_id, message_id)
        return [r["task_id"] for r in rows]

    async def claim_for_user(self, chat_id: str, user_id, task_ids) -> list:
        """Ручной триггер: пользователь отправил подсказку «задачи выполнены».
        Атомарно отмечаем triggered_at и возвращаем строки (только свои задачи этого чата,
        готовые и ещё не отданные агенту) — повторная отправка вернёт пусто."""
        if not task_ids:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "UPDATE agent_tasks SET triggered_at=now(), updated_at=now() "
                "WHERE task_id = ANY($1::text[]) AND chat_id=$2 AND user_id=$3 "
                "AND triggered_at IS NULL AND status = ANY($4::text[]) RETURNING *",
                [str(t) for t in task_ids], str(chat_id), int(user_id), list(NOTIFY))
        return [dict(r) for r in rows]

    async def disable_notify_many(self, task_ids) -> None:
        if not task_ids:
            return
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE agent_tasks SET notify_agent=false, updated_at=now() "
                               "WHERE task_id = ANY($1::text[]) AND triggered_at IS NULL", list(task_ids))

    async def disable_notify(self, task_id: str) -> None:
        await self.disable_notify_many([task_id])

    async def mark_triggered(self, task_id: str) -> bool:
        return bool(await self.mark_triggered_many([task_id]))
