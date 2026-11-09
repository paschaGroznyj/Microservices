"""
sandbox_tasks.py — фоновые задачи песочницы: POST /submit, GET /status/{id},
GET /workspace/{wid}/tasks, POST /tasks/{id}/cancel.

Жизненный цикл задачи уходит в Kafka topic "task-events":
    submitted -> started -> progress* -> finished (completed | failed | cancelled)
Каждое событие несёт seq (растёт в рамках задачи). Consumer в chats_service
применяет событие, только если его seq больше сохранённого, поэтому порядок
доставки не важен. Событие самодостаточно: полный снимок задачи.

Протокол прогресса — строки stdout скрипта (печатать с flush=True):
    ##STAGE <название этапа>      начат этап, предыдущий активный закрывается
    ##PROGRESS <0-100> [текст]    процент и короткий текст
Если скрипт ничего не печатает, раз в HEARTBEAT_SEC уходит progress-событие
с elapsed_ms, так что время выполнения на фронте идёт всегда.

app.py вызывает bind(...) и подключает router. Этот модуль app.py не импортирует.
"""
import asyncio
import json
import logging
import os
import re
import signal
import time
from typing import Optional

from aiokafka import AIOKafkaProducer
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger("sandbox.tasks")
router = APIRouter()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")
TASK_EVENTS_TOPIC = "task-events"

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 3600
MAX_CODE_SIZE = 500_000

TASK_KINDS = ("script", "media")
TERMINAL = ("completed", "failed", "cancelled")
MAX_STAGES = 12
MAX_STAGE_TITLE = 80
MAX_PROGRESS_TEXT = 120
PROGRESS_MIN_INTERVAL = 2.0   # не чаще раза в 2 с для ##PROGRESS
HEARTBEAT_SEC = 10.0          # если скрипт молчит
TASK_TTL_SECONDS = 6 * 3600   # завершённые задачи держим в памяти 6 ч
CANCEL_GRACE_SEC = 5.0        # SIGTERM -> через 5 с SIGKILL
STDERR_TAIL = 2000
STDOUT_TAIL = 2000

STAGE_RE = re.compile(r"^##STAGE\s+(.+?)\s*$")
PROGRESS_RE = re.compile(r"^##PROGRESS\s+(\d{1,3}(?:\.\d+)?)\s*%?\s*(.*?)\s*$")

PUBLIC_KEYS = (
    "task_id", "chat_id", "workspace_id", "label", "kind", "status", "progress",
    "notify_agent", "owner", "created_at", "started_at", "finished_at",
    "exit_code", "error", "stderr_tail", "stdout_tail", "files",
)

_tasks: dict[str, dict] = {}
_bg: set = set()
_producer: Optional[AIOKafkaProducer] = None
_hooks: dict = {}


def bind(*, get_workspace_path, touch_workspace, run_code, after_run):
    """Зависимости из app.py.

    run_code(code, timeout, run_dir, stdin, task_id, ws_path, persist, run_id,
             on_line=..., on_start=...) -> dict   (синхронная, зовётся в потоке)
    after_run(workspace_id, changed_files) -> awaitable
    """
    _hooks.update(
        get_workspace_path=get_workspace_path,
        touch_workspace=touch_workspace,
        run_code=run_code,
        after_run=after_run,
    )


class SubmitRequest(BaseModel):
    code: str = Field(..., description="Python код для исполнения")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    workspace_id: str = Field(..., description="ID persistent workspace")
    task_id: str = Field(..., description="Уникальный ID задачи (генерирует агент)")
    chat_id: str = Field(..., description="ID чата, к которому привязана задача")
    label: str = Field(default="", description="Человекочитаемое имя задачи")
    kind: str = Field(default="script", description="script | media — стиль карточки на фронте")
    stages: list[str] = Field(default_factory=list, description="Плановые этапы")
    notify_agent: bool = Field(default=True, description="Будить агента после завершения")
    owner: dict = Field(default_factory=dict, description="user_id/agent_model/selected_tools/... владельца")
    stdin: str = Field(default="")
    changed_files: str | list[str] = Field("", description="Что поменяется в workspace для live-редактора")


# ── Этапы и прогресс ───────────────────────────────────────────────

def parse_marker(line: str):
    """('stage', title) | ('progress', {'percent', 'text'}) | (None, None)."""
    s = (line or "").strip()
    if not s.startswith("##"):
        return None, None
    m = STAGE_RE.match(s)
    if m:
        return "stage", m.group(1)[:MAX_STAGE_TITLE]
    m = PROGRESS_RE.match(s)
    if m:
        pct = max(0, min(100, int(round(float(m.group(1))))))
        return "progress", {"percent": pct, "text": m.group(2)[:MAX_PROGRESS_TEXT]}
    return None, None


def strip_markers(text: str) -> str:
    return "\n".join(
        ln for ln in (text or "").splitlines()
        if not ln.lstrip().startswith(("##STAGE", "##PROGRESS"))
    )


def init_stages(titles) -> list[dict]:
    out = []
    for t in (titles or [])[:MAX_STAGES]:
        t = str(t).strip()[:MAX_STAGE_TITLE]
        if t:
            out.append({"title": t, "status": "pending", "started_at": None, "finished_at": None})
    return out


def apply_stage(task: dict, title: str) -> None:
    """Начать этап title: активный закрыть, пропущенные плановые пометить skipped."""
    title = (title or "").strip()[:MAX_STAGE_TITLE]
    if not title:
        return
    now = time.time()
    stages = task["stages"]
    target = next((s for s in stages if s["title"] == title and s["status"] in ("pending", "active")), None)
    if target is not None and target["status"] == "active":
        return
    for s in stages:
        if s["status"] == "active":
            s["status"], s["finished_at"] = "done", now
    if target is None:
        if len(stages) >= MAX_STAGES:
            task["progress"] = {**(task.get("progress") or {}), "text": title[:MAX_PROGRESS_TEXT]}
            return
        target = {"title": title, "status": "pending", "started_at": None, "finished_at": None}
        stages.append(target)
    else:
        for s in stages:
            if s is target:
                break
            if s["status"] == "pending":
                s["status"] = "skipped"
    target["status"], target["started_at"] = "active", now


def close_stages(task: dict, status: str) -> None:
    now = time.time()
    for s in task["stages"]:
        if s["status"] == "active":
            s["status"] = {"completed": "done", "failed": "failed"}.get(status, "cancelled")
            s["finished_at"] = now
        elif s["status"] == "pending" and status == "completed":
            s["status"], s["finished_at"] = "done", now


def _tail(text, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def public(task: dict) -> dict:
    started, finished = task.get("started_at"), task.get("finished_at")
    elapsed = int(((finished or time.time()) - started) * 1000) if started else 0
    out = {k: task.get(k) for k in PUBLIC_KEYS}
    out["stages"] = [dict(s) for s in task.get("stages") or []]
    out["elapsed_ms"] = elapsed
    return out


# ── Kafka ──────────────────────────────────────────────────────────

async def start_producer() -> Optional[AIOKafkaProducer]:
    global _producer
    if _producer is not None or not KAFKA_BROKER:
        return _producer
    prod = AIOKafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        key_serializer=lambda k: str(k).encode("utf-8"),
        value_serializer=lambda m: json.dumps(m, ensure_ascii=False).encode("utf-8"),
    )
    try:
        await asyncio.wait_for(prod.start(), timeout=15)
        _producer = prod
        log.info("[tasks] Kafka producer запущен, broker=%s", KAFKA_BROKER)
    except Exception as e:
        log.error("[tasks] Kafka producer не запустился: %s", e)
    return _producer


def build_event(task: dict, name: str) -> dict:
    """Снимок задачи + seq. Вызывается только из event loop."""
    task["seq"] = task.get("seq", 0) + 1
    task["_last_emit"] = time.monotonic()
    task["_dirty"] = False
    ev = public(task)
    ev.update(event=name, seq=task["seq"], ts=time.time())
    return ev


async def _send(ev: dict) -> None:
    try:
        prod = _producer or await start_producer()
        if prod is None:
            log.warning("[tasks] Kafka недоступна, событие %s task=%s потеряно", ev["event"], ev["task_id"])
            return
        await prod.send_and_wait(TASK_EVENTS_TOPIC, value=ev, key=ev["task_id"])
    except Exception as e:
        log.error("[tasks] не отправил %s task=%s: %s", ev.get("event"), ev.get("task_id"), e)


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)


def emit_soon(task: dict, name: str) -> None:
    _spawn(_send(build_event(task, name)))


async def emit(task: dict, name: str) -> None:
    await _send(build_event(task, name))


# ── Исполнение ─────────────────────────────────────────────────────

def _kill(proc, sig) -> None:
    try:
        os.killpg(proc.pid, sig)
    except Exception:
        try:
            proc.send_signal(sig)
        except Exception:
            pass


async def _force_kill_later(task: dict) -> None:
    await asyncio.sleep(CANCEL_GRACE_SEC)
    proc = task.get("_proc")
    if proc is not None and proc.poll() is None:
        _kill(proc, signal.SIGKILL)


async def _heartbeat(task: dict) -> None:
    while task["status"] == "running":
        await asyncio.sleep(1.0)
        if task["status"] != "running":
            break
        since = time.monotonic() - task.get("_last_emit", 0)
        if (task.get("_dirty") and since >= PROGRESS_MIN_INTERVAL) or since >= HEARTBEAT_SEC:
            emit_soon(task, "progress")


async def _finish(task: dict, status: str, error: str | None = None) -> None:
    task["status"] = status
    task["finished_at"] = time.time()
    if error:
        task["error"] = error[:2000]
    close_stages(task, status)
    await emit(task, "finished")
    log.info("[tasks] task=%s %s за %d мс", task["task_id"], status, public(task)["elapsed_ms"])


async def _run(req: SubmitRequest) -> None:
    task = _tasks[req.task_id]
    loop = asyncio.get_running_loop()
    if task.get("cancel_requested"):
        await _finish(task, "cancelled", "Отменена до запуска")
        return
    ws_path = _hooks["get_workspace_path"](req.workspace_id)
    if ws_path is None:
        await _finish(task, "failed", "Workspace удалён до запуска задачи")
        return

    task["status"], task["started_at"] = "running", time.time()
    _hooks["touch_workspace"](req.workspace_id)
    await emit(task, "started")
    hb = asyncio.create_task(_heartbeat(task))

    def on_line(line: str) -> None:          # поток чтения stdout
        kind, val = parse_marker(line)
        if kind == "stage":
            loop.call_soon_threadsafe(_on_stage, task, val)
        elif kind == "progress":
            task["progress"] = val
            task["_dirty"] = True

    def on_start(proc) -> None:
        task["_proc"] = proc
        if task.get("cancel_requested"):
            _kill(proc, signal.SIGTERM)

    try:
        result = await asyncio.to_thread(
            _hooks["run_code"], req.code, req.timeout, ws_path, req.stdin, req.task_id,
            ws_path, True, req.task_id, on_line=on_line, on_start=on_start,
        )
    except Exception as e:
        log.exception("[tasks] task=%s упала в раннере", req.task_id)
        result = {"ok": False, "error": f"Внутренняя ошибка песочницы: {type(e).__name__}: {e}"}
    finally:
        task.pop("_proc", None)
        hb.cancel()

    if task.get("cancel_requested"):
        status = "cancelled"
    else:
        status = "completed" if result.get("ok") else "failed"
    error = result.get("error")
    if status == "failed" and not error:
        ec = result.get("exit_code")
        error = f"Скрипт завершился с кодом {ec}" if ec is not None else "Неизвестная ошибка"
    if status == "cancelled":
        error = "Отменена по запросу"

    files = []
    for f in result.get("files") or []:
        name = getattr(f, "filename", None) or (f.get("filename") if isinstance(f, dict) else None)
        size = getattr(f, "size_bytes", None) if not isinstance(f, dict) else f.get("size_bytes")
        if name:
            files.append({"filename": name, "size_bytes": size or 0})
    task.update(
        exit_code=result.get("exit_code"),
        stderr_tail=_tail(result.get("stderr"), STDERR_TAIL),
        stdout_tail=_tail(strip_markers(result.get("stdout")), STDOUT_TAIL),
        files=files[:50],
    )
    try:
        await _hooks["after_run"](req.workspace_id, req.changed_files)
    except Exception as e:
        log.warning("[tasks] after_run task=%s: %s", req.task_id, e)
    await _finish(task, status, error)


def _on_stage(task: dict, title: str) -> None:
    if task["status"] != "running":
        return
    apply_stage(task, title)
    emit_soon(task, "progress")


def purge_expired(now: float | None = None) -> int:
    now = now or time.time()
    old = [tid for tid, t in _tasks.items()
           if t["status"] in TERMINAL and (t.get("finished_at") or now) < now - TASK_TTL_SECONDS]
    for tid in old:
        _tasks.pop(tid, None)
    return len(old)


# ── Эндпоинты ──────────────────────────────────────────────────────

@router.post("/submit")
async def submit_task(req: SubmitRequest):
    """Асинхронный запуск: сразу возвращает task_id, ход задачи идёт в Kafka."""
    if len(req.code.encode("utf-8")) > MAX_CODE_SIZE:
        raise HTTPException(status_code=413, detail=f"Code exceeds {MAX_CODE_SIZE} bytes")
    if _hooks["get_workspace_path"](req.workspace_id) is None:
        raise HTTPException(status_code=404, detail=f"Workspace {req.workspace_id} не найден")
    if req.task_id in _tasks:
        raise HTTPException(status_code=409, detail=f"Задача {req.task_id} уже есть")
    purge_expired()

    kind = req.kind if req.kind in TASK_KINDS else "script"
    task = {
        "task_id": req.task_id, "chat_id": req.chat_id, "workspace_id": req.workspace_id,
        "label": (req.label or "Задача")[:200], "kind": kind, "status": "pending",
        "progress": {"percent": None, "text": ""}, "stages": init_stages(req.stages),
        "notify_agent": bool(req.notify_agent), "owner": dict(req.owner or {}),
        "created_at": time.time(), "started_at": None, "finished_at": None,
        "exit_code": None, "error": None, "stderr_tail": "", "stdout_tail": "",
        "files": [], "seq": 0,
    }
    _tasks[req.task_id] = task
    emit_soon(task, "submitted")
    _spawn(_run(req))
    log.info("[tasks] submit task=%s ws=%s kind=%s label='%s' chat=%s",
             req.task_id, req.workspace_id, kind, task["label"], req.chat_id)
    return {
        "task_id": req.task_id, "status": "pending", "workspace_id": req.workspace_id,
        "kind": kind, "message": f"Задача '{task['label']}' поставлена в очередь",
    }


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Задача {task_id} не найдена")
    out = public(task)
    # совместимость со старым ответом
    out.update(duration_ms=out["elapsed_ms"], completed_at=task.get("finished_at"),
               stdout=task.get("stdout_tail") or "", stderr=task.get("stderr_tail") or "")
    return out


@router.get("/workspace/{workspace_id}/tasks")
async def get_workspace_tasks(workspace_id: str):
    items = [
        {"task_id": tid, "status": t["status"], "label": t.get("label", ""), "kind": t.get("kind"),
         "started_at": t.get("started_at"), "completed_at": t.get("finished_at")}
        for tid, t in _tasks.items() if t.get("workspace_id") == workspace_id
    ]
    return {
        "workspace_id": workspace_id, "tasks": items, "total": len(items),
        "completed": sum(1 for t in items if t["status"] in TERMINAL),
        "all_done": bool(items) and all(t["status"] in TERMINAL for t in items),
    }


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Задача {task_id} не найдена")
    if task["status"] in TERMINAL:
        return {"task_id": task_id, "status": task["status"], "message": "Задача уже завершена"}
    task["cancel_requested"] = True
    proc = task.get("_proc")
    if proc is not None:
        _kill(proc, signal.SIGTERM)
        _spawn(_force_kill_later(task))
    return {"task_id": task_id, "status": "cancelling", "message": "Отмена отправлена"}


def forget_workspace(workspace_id: str) -> None:
    """Workspace удалён: живые задачи убить, завершённые забыть."""
    for tid, t in list(_tasks.items()):
        if t.get("workspace_id") != workspace_id:
            continue
        if t["status"] in TERMINAL:
            _tasks.pop(tid, None)
        else:
            t["cancel_requested"] = True
            if t.get("_proc") is not None:
                _kill(t["_proc"], signal.SIGKILL)


async def shutdown() -> None:
    global _producer
    for t in _tasks.values():
        if t.get("_proc") is not None:
            _kill(t["_proc"], signal.SIGKILL)
    if _producer is not None:
        try:
            await _producer.stop()
        finally:
            _producer = None
