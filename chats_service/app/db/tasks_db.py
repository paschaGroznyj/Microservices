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
    }


UPSERT_SQL = """
INSERT INTO agent_tasks (task_id, chat_id, user_id, request_id, workspace_id, label, kind, status,
    stages, progress, elapsed_ms, exit_code, error, stderr_tail, stdout_tail, files,
    notify_agent, seq, created_at, started_at, finished_at, message_id)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,$12,$13,$14,$15,$16::jsonb,$17,$18,$19,$20,$21,$22)
ON CONFLICT (task_id) DO UPDATE SET
    status=EXCLUDED.status, stages=EXCLUDED.stages, progress=EXCLUDED.progress,
    elapsed_ms=EXCLUDED.elapsed_ms, exit_code=EXCLUDED.exit_code, error=EXCLUDED.error,
    stderr_tail=EXCLUDED.stderr_tail, stdout_tail=EXCLUDED.stdout_tail, files=EXCLUDED.files,
    seq=EXCLUDED.seq, started_at=COALESCE(EXCLUDED.started_at, agent_tasks.started_at),
    finished_at=EXCLUDED.finished_at,
    message_id=COALESCE(agent_tasks.message_id, EXCLUDED.message_id), updated_at=now()
WHERE agent_tasks.seq < EXCLUDED.seq
RETURNING message_id
"""


class TasksDB:
    def __init__(self, pool):
        self.pool = pool

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
                r["finished_at"], message_id,
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

    async def mark_triggered(self, task_id: str) -> bool:
        """Атомарно: True только у первого, кто будит агента по этой задаче."""
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE agent_tasks SET triggered_at=now() WHERE task_id=$1 AND triggered_at IS NULL",
                task_id)
        return res.endswith(" 1")
