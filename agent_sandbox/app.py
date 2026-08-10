"""
agent_sandbox/app.py  —  v3: Async Submit + Kafka + Persistent Workspace
════════════════════════════════════════════════════════════════════════
Изменения от v2:
  • POST /submit — асинхронный запуск (не блокирует, возвращает task_id)
  • GET /status/{task_id} — статус задачи
  • GET /workspace/{wid}/tasks — статусы всех задач в workspace
  • Kafka producer — шлёт события в topic "task-events" после завершения
  • Workspace persistence — файлы НЕ удаляются после /submit (только после /execute)
  • TTL-очистка воркспейсов по таймеру

Эндпоинты:
  POST /execute           — синхронный (как раньше), изолированный run_dir
  POST /submit            — асинхронный, persistent workspace, Kafka-нотификация
  GET  /status/{task_id}  — статус задачи
  GET  /workspace/{wid}/tasks — все задачи workspace
  POST /workspace/create  — создать workspace
  GET  /workspace/{wid}/files — список файлов
  GET  /workspace/{wid}/file/{path} — скачать файл
  DELETE /workspace/{wid} — удалить workspace
  POST /workspace/cleanup — принудительная очистка просроченных
  GET  /health            — healthcheck
"""

import asyncio
import base64
import json
import logging
import os
import resource
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional
from aiokafka import AIOKafkaProducer

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sandbox")

app = FastAPI(title="Agent Code Sandbox v3")

# ── Конфиг ─────────────────────────────────────────────────────────
MAX_CODE_SIZE = 100_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 3600
MAX_OUTPUT_SIZE = 20_000
WORKDIR = "/tmp/exec"
WORKSPACES_DIR = "/tmp/exec/workspaces"
MAX_MEMORY_MB = 3072
MAX_CPU_SECONDS = 200

OUTPUT_DIR_NAME = "output"
MAX_FILE_SIZE_FOR_RETURN = 50 * 1024 * 1024
MAX_FILES_RETURNED = 5

WORKSPACE_TTL_SECONDS = 3600  # 1 час
WORKSPACE_MAX_COUNT = 50
WORKSPACE_CLEANUP_INTERVAL = 3600

KAFKA_BROKER = os.getenv("KAFKA_BROKER")
KAFKA_TASK_EVENTS_TOPIC = "task-events"

# ── Хранилище задач (in-memory) ────────────────────────────────────
# task_id → {status, result, error, workspace_id, chat_id, label, ...}
_tasks: dict[str, dict] = {}


# ── Kafka Producer ─────────────────────────────────────────────────

_kafka_producer = None

async def get_kafka_producer():
    global _kafka_producer
    if _kafka_producer is None:
        try:
            _kafka_producer = AIOKafkaProducer(
                bootstrap_servers=[KAFKA_BROKER],
                value_serializer=lambda m: json.dumps(m).encode("utf-8"),
            )
            await _kafka_producer.start()
            log.info("Kafka producer запущен, broker=%s", KAFKA_BROKER)
        except Exception as e:
            log.error("Не удалось запустить Kafka producer: %s", e)
            _kafka_producer = None
    return _kafka_producer


async def send_task_event(event: dict):
    """Отправляет событие завершения задачи в Kafka."""
    try:
        prod = await get_kafka_producer()
        if prod is not None:
            await prod.send_and_wait(KAFKA_TASK_EVENTS_TOPIC, value=event)
            log.info("Kafka event отправлен: task=%s status=%s", event.get("task_id"), event.get("status"))
        else:
            log.warning("Kafka producer недоступен, событие не отправлено: %s", event)
    except Exception as e:
        log.error("Ошибка отправки Kafka event: %s", e)


# ── Модели ─────────────────────────────────────────────────────────

class OutputFile(BaseModel):
    filename: str
    size_bytes: int
    content_base64: str


class ExecuteRequest(BaseModel):
    code: str = Field(..., description="Python код для исполнения")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    stdin: str = ""
    workspace_id: str | None = None


class ExecuteResponse(BaseModel):
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None
    files: list[OutputFile] = Field(default_factory=list)


class SubmitRequest(BaseModel):
    code: str = Field(..., description="Python код для исполнения")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    workspace_id: str = Field(..., description="ID persistent workspace")
    task_id: str = Field(..., description="Уникальный ID задачи (генерирует агент)")
    chat_id: int = Field(..., description="ID чата для нотификации")
    label: str = Field(default="", description="Человекочитаемое имя задачи")
    stdin: str = Field(default="")


class SubmitResponse(BaseModel):
    task_id: str
    status: str = "pending"
    workspace_id: str
    message: str = "Задача поставлена в очередь"


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str  # pending | running | completed | failed
    label: str = ""
    workspace_id: str = ""
    duration_ms: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    files: list[dict] = Field(default_factory=list)  # [{filename, size_bytes}]
    started_at: float | None = None
    completed_at: float | None = None


# ── Утилиты ────────────────────────────────────────────────────────

def _limit_resources():
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (30 * 1024 * 1024, 30 * 1024 * 1024))
    except Exception as e:
        os.write(2, f"[sandbox] resource limit warning: {e}\n".encode())


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT_SIZE:
        return text[:MAX_OUTPUT_SIZE] + "\n...(truncated)...", True
    return text, False


def _collect_output_files(output_dir: str) -> tuple[list[OutputFile], list[str]]:
    files = []
    warnings = []

    if not os.path.isdir(output_dir):
        return files, warnings

    entries = sorted(os.listdir(output_dir))
    if len(entries) > MAX_FILES_RETURNED:
        warnings.append(f"В output/ найдено {len(entries)} файлов, возвращаю первые {MAX_FILES_RETURNED}")
        entries = entries[:MAX_FILES_RETURNED]

    for name in entries:
        fpath = os.path.join(output_dir, name)
        if not os.path.isfile(fpath):
            continue
        try:
            size = os.path.getsize(fpath)
            if size > MAX_FILE_SIZE_FOR_RETURN:
                warnings.append(f"Файл {name} ({size} байт) превышает лимит, пропущен")
                continue
            with open(fpath, "rb") as f:
                content = f.read()
            files.append(OutputFile(
                filename=name,
                size_bytes=size,
                content_base64=base64.b64encode(content).decode("ascii"),
            ))
        except OSError as e:
            warnings.append(f"Не удалось прочитать {name}: {e}")

    return files, warnings


def _list_workspace_files(workspace_id: str) -> list[dict]:
    """Возвращает список файлов в workspace (для GET endpoints)."""
    ws_path = os.path.join(WORKSPACES_DIR, workspace_id)
    result = []

    if not os.path.isdir(ws_path):
        return result

    for root, dirs, files in os.walk(ws_path):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, ws_path)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                size = 0
            result.append({
                "filename": rel_path,
                "size_bytes": size,
            })

    return result


def _get_workspace_path(workspace_id: str) -> str | None:
    ws_path = os.path.join(WORKSPACES_DIR, workspace_id)
    if os.path.isdir(ws_path):
        return ws_path
    return None


def _run_code_sync(code: str, timeout: int, run_dir: str, stdin: str = "", task_id: str = None,
    ws_path: str = None) -> dict:
    """
    Синхронный запуск кода в subprocess.
    Используется и в /execute, и в /submit (через run_in_executor).
    """
    run_dir = Path(run_dir)  # нормализуем в Path один раз

    if task_id:
        task_dir = run_dir / ".tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # output/ в task_dir — симлинк на общий workspace output/
        workspace_output = run_dir / OUTPUT_DIR_NAME
        workspace_output.mkdir(parents=True, exist_ok=True)
        task_output_link = task_dir / OUTPUT_DIR_NAME
        if not task_output_link.exists():
            task_output_link.symlink_to(workspace_output)

        script_path = task_dir / "script.py"
        cwd = task_dir
    else:
        if ws_path:
            workspace_output = Path(ws_path) / "output"
        else:
            workspace_output = run_dir / OUTPUT_DIR_NAME
        script_path = run_dir / "script.py"
        cwd = run_dir
        workspace_output.mkdir(parents=True, exist_ok=True)


    try:
        script_path.write_text(code, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"Не удалось подготовить исполнение: {e}"}

    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["python3", "script.py"],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            preexec_fn=_limit_resources,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PLAYWRIGHT_BROWSERS_PATH": "/opt/playwright-browsers",
                "MPLCONFIGDIR": "/tmp",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "HOME": "/tmp",
                "MAIL_HOST": os.getenv("MAIL_HOST", ""),
                "MAIL_PORT": os.getenv("MAIL_PORT", ""),
                "MAIL_LOGIN": os.getenv("MAIL_LOGIN", ""),
                "MAIL_PASSWORD": os.getenv("MAIL_PASSWORD", ""),
                "MAIL_SMTP_PORT": os.getenv("MAIL_SMTP_PORT", ""),
                "GITVERSE_TOKEN": os.getenv("GITVERSE_TOKEN", ""),
            },
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        stdout, out_trunc = _truncate(proc.stdout)
        stderr, err_trunc = _truncate(proc.stderr)

        missing_module_hint = None
        if proc.returncode != 0 and "ModuleNotFoundError" in stderr:
            missing_module_hint = (
                "Похоже, код использует библиотеку, которой нет в sandbox-окружении. "
                "Доступные зависимости фиксированы администратором в requirements-sandbox.txt."
            )

        files, warnings = _collect_output_files(str(workspace_output))
        if warnings:
            stderr = (stderr + "\n[sandbox warnings] " + "; ".join(warnings)).strip()

        return {
            "ok": proc.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "truncated": out_trunc or err_trunc,
            "duration_ms": duration_ms,
            "files": files,
            "error": missing_module_hint,
        }

    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": False,
            "error": f"Превышен таймаут исполнения ({timeout}с). Код прерван.",
            "duration_ms": duration_ms,
        }

    except MemoryError:
        return {"ok": False, "error": "Превышен лимит памяти при подготовке процесса"}

    except OSError as e:
        return {"ok": False, "error": f"Системная ошибка при исполнении: {e}"}

    except Exception as e:
        return {"ok": False, "error": f"Внутренняя ошибка сервиса: {type(e).__name__}: {e}"}


# ── Workspace management ───────────────────────────────────────────

def _create_workspace_dir() -> str:
    workspace_id = uuid.uuid4().hex[:12]
    ws_path = os.path.join(WORKSPACES_DIR, workspace_id)
    os.makedirs(ws_path, exist_ok=True)
    os.makedirs(os.path.join(ws_path, OUTPUT_DIR_NAME), exist_ok=True)
    # Записываем время создания для TTL
    with open(os.path.join(ws_path, ".created_at"), "w") as f:
        f.write(str(time.time()))
    log.info("[workspace] Создан %s → %s", workspace_id, ws_path)
    return workspace_id


def _touch_workspace(workspace_id: str):
    """Обновляет время последней активности workspace."""
    ws_path = os.path.join(WORKSPACES_DIR, workspace_id)
    if os.path.isdir(ws_path):
        with open(os.path.join(ws_path, ".last_active"), "w") as f:
            f.write(str(time.time()))


def _cleanup_expired_workspaces():
    """Удаляет workspace с истёкшим TTL."""
    if not os.path.isdir(WORKSPACES_DIR):
        return 0

    now = time.time()
    removed = 0

    for entry in os.listdir(WORKSPACES_DIR):
        ws_path = os.path.join(WORKSPACES_DIR, entry)
        if not os.path.isdir(ws_path):
            continue

        last_active_file = os.path.join(ws_path, ".last_active")
        created_file = os.path.join(ws_path, ".created_at")

        try:
            if os.path.exists(last_active_file):
                ts = float(open(last_active_file).read().strip())
            elif os.path.exists(created_file):
                ts = float(open(created_file).read().strip())
            else:
                ts = now

            if now - ts > WORKSPACE_TTL_SECONDS:
                shutil.rmtree(ws_path, ignore_errors=True)
                removed += 1
                log.info("[workspace] TTL-удаление %s", entry)
        except (ValueError, OSError):
            pass

    return removed


async def _workspace_cleanup_loop():
    """Фоновый таск: периодически чистит просроченные workspace."""
    while True:
        await asyncio.sleep(WORKSPACE_CLEANUP_INTERVAL)
        try:
            removed = await asyncio.to_thread(_cleanup_expired_workspaces)
            if removed:
                log.info("[workspace] Очищено %d просроченных workspace", removed)
        except Exception as e:
            log.error("[workspace] Ошибка очистки: %s", e)


# ── /execute (синхронный, изолированный — как раньше) ──────────────

@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    if len(req.code.encode("utf-8")) > MAX_CODE_SIZE:
        raise HTTPException(status_code=413, detail=f"Code exceeds {MAX_CODE_SIZE} bytes")

    run_id = uuid.uuid4().hex
    run_dir = os.path.join(WORKDIR, run_id)
    ws_path = None
    if req.workspace_id:
        ws_path = _get_workspace_path(req.workspace_id)
        if ws_path is None:
            raise HTTPException(status_code=404, detail=f"Workspace {req.workspace_id} не найден")

    result = await asyncio.to_thread(
        _run_code_sync, req.code, req.timeout, run_dir, req.stdin, ws_path
    )

    # Удаляем временный run_dir (изоляция — как раньше)
    try:
        shutil.rmtree(run_dir, ignore_errors=True)
    except OSError as e:
        log.warning(f"Не удалось удалить {run_dir}: {e}")

    return ExecuteResponse(
        ok=result.get("ok", False),
        stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""),
        exit_code=result.get("exit_code"),
        truncated=result.get("truncated", False),
        duration_ms=result.get("duration_ms", 0),
        files=result.get("files", []),
        error=result.get("error"),
    )


# ── /submit (асинхронный, persistent workspace, Kafka) ─────────────

@app.post("/submit", response_model=SubmitResponse)
async def submit_task(req: SubmitRequest):
    """
    Асинхронный запуск кода.
    Не блокирует — сразу возвращает task_id.
    После завершения шлёт Kafka event в topic "task-events".
    Файлы сохраняются в persistent workspace (НЕ удаляются).
    """
    if len(req.code.encode("utf-8")) > MAX_CODE_SIZE:
        raise HTTPException(status_code=413, detail=f"Code exceeds {MAX_CODE_SIZE} bytes")

    # Проверяем workspace
    ws_path = _get_workspace_path(req.workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail=f"Workspace {req.workspace_id} не найден")

    # Регистрируем задачу
    _tasks[req.task_id] = {
        "status": "pending",
        "label": req.label,
        "workspace_id": req.workspace_id,
        "chat_id": req.chat_id,
        "started_at": None,
        "completed_at": None,
        "result": None,
    }

    # Запускаем в фоне
    asyncio.create_task(_run_task_background(req))

    log.info(
        "[submit] task=%s workspace=%s label='%s' chat_id=%s",
        req.task_id, req.workspace_id, req.label, req.chat_id,
    )

    return SubmitResponse(
        task_id=req.task_id,
        status="pending",
        workspace_id=req.workspace_id,
        message=f"Задача '{req.label}' поставлена в очередь",
    )


async def _run_task_background(req: SubmitRequest):
    """Фоновое исполнение задачи с Kafka-нотификацией."""

    _tasks[req.task_id]["status"] = "running"
    _tasks[req.task_id]["started_at"] = time.time()

    ws_path = _get_workspace_path(req.workspace_id)
    run_dir = ws_path  # код выполняется прямо в workspace — файлы в output/ сохраняются

    # Обновляем активность workspace
    _touch_workspace(req.workspace_id)

    # Запуск в executor (не блокирует event loop)
    result = await asyncio.to_thread(
        _run_code_sync, req.code, req.timeout, run_dir, req.stdin, req.task_id
    )

    _tasks[req.task_id]["completed_at"] = time.time()
    _tasks[req.task_id]["result"] = result

    # Файлы в output/ НЕ удаляем — persistent workspace
    _touch_workspace(req.workspace_id)

    # Готовим file list (без base64 — только метаданные для Kafka)
    file_list = []
    if result.get("files"):
        file_list = [
            {"filename": f.filename, "size_bytes": f.size_bytes}
            for f in result["files"]
        ]

    status = "completed" if result.get("ok") else "failed"

    _tasks[req.task_id]["status"] = status

    # Kafka event
    event = {
        "task_id": req.task_id,
        "chat_id": req.chat_id,
        "workspace_id": req.workspace_id,
        "label": req.label,
        "status": status,
        "ok": result.get("ok", False),
        "duration_ms": result.get("duration_ms", 0),
        "error": result.get("error"),
        "files": file_list,
        "stdout_preview": result.get("stdout", "")[:500],  # первые 500 символов
    }

    await send_task_event(event)

    log.info(
        "[submit] task=%s completed ok=%s duration=%dms files=%d",
        req.task_id, result.get("ok"), result.get("duration_ms", 0), len(file_list),
    )


# ── /status/{task_id} ──────────────────────────────────────────────

@app.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Задача {task_id} не найдена")

    result = task.get("result") or {}
    file_list = []
    if result.get("files"):
        file_list = [
            {"filename": f.filename, "size_bytes": f.size_bytes}
            for f in result["files"]
        ]

    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        label=task.get("label", ""),
        workspace_id=task.get("workspace_id", ""),
        duration_ms=result.get("duration_ms", 0),
        stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""),
        error=result.get("error"),
        files=file_list,
        started_at=task.get("started_at"),
        completed_at=task.get("completed_at"),
    )


# ── /workspace/{wid}/tasks — все задачи workspace ──────────────────

@app.get("/workspace/{workspace_id}/tasks")
async def get_workspace_tasks(workspace_id: str):
    tasks_in_ws = [
        {
            "task_id": tid,
            "status": t["status"],
            "label": t.get("label", ""),
            "started_at": t.get("started_at"),
            "completed_at": t.get("completed_at"),
        }
        for tid, t in _tasks.items()
        if t.get("workspace_id") == workspace_id
    ]
    return {
        "workspace_id": workspace_id,
        "tasks": tasks_in_ws,
        "total": len(tasks_in_ws),
        "completed": sum(1 for t in tasks_in_ws if t["status"] in ("completed", "failed")),
        "all_done": all(t["status"] in ("completed", "failed") for t in tasks_in_ws) if tasks_in_ws else False,
    }


# ── Workspace CRUD endpoints ───────────────────────────────────────

@app.post("/workspace/create")
async def create_workspace():
    # Проверяем лимит
    existing = 0
    if os.path.isdir(WORKSPACES_DIR):
        existing = len([d for d in os.listdir(WORKSPACES_DIR) if os.path.isdir(os.path.join(WORKSPACES_DIR, d))])
    if existing >= WORKSPACE_MAX_COUNT:
        # Пытаемся очистить просроченные
        removed = _cleanup_expired_workspaces()
        if existing - removed >= WORKSPACE_MAX_COUNT:
            raise HTTPException(status_code=429, detail=f"Достигнут лимит workspace ({WORKSPACE_MAX_COUNT})")

    workspace_id = _create_workspace_dir()
    return {
        "workspace_id": workspace_id,
        "created_at": time.time(),
        "path": os.path.join(WORKSPACES_DIR, workspace_id),
    }


@app.get("/workspace/{workspace_id}/files")
async def list_workspace_files_endpoint(workspace_id: str):
    files = _list_workspace_files(workspace_id)
    total = sum(f["size_bytes"] for f in files)
    return {
        "workspace_id": workspace_id,
        "files": files,
        "total_size_bytes": total,
        "file_count": len(files),
    }


@app.get("/workspace/{workspace_id}/file/{filepath:path}")
async def download_workspace_file_endpoint(workspace_id: str, filepath: str):
    from fastapi import Response
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")

    target = os.path.join(ws_path, filepath)
    # Защита от path traversal
    if not os.path.realpath(target).startswith(os.path.realpath(ws_path)):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail=f"Файл {filepath} не найден")

    with open(target, "rb") as f:
        content = f.read()

    from urllib.parse import quote
    filename = os.path.basename(filepath)

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.delete("/workspace/{workspace_id}")
async def delete_workspace_endpoint(workspace_id: str):
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")

    shutil.rmtree(ws_path, ignore_errors=True)

    # Чистим задачи этого workspace
    to_remove = [tid for tid, t in _tasks.items() if t.get("workspace_id") == workspace_id]
    for tid in to_remove:
        del _tasks[tid]

    return {"status": "deleted", "workspace_id": workspace_id}


@app.post("/workspace/cleanup")
async def cleanup_workspaces_endpoint():
    removed = _cleanup_expired_workspaces()
    remaining = 0
    if os.path.isdir(WORKSPACES_DIR):
        remaining = len([d for d in os.listdir(WORKSPACES_DIR) if os.path.isdir(os.path.join(WORKSPACES_DIR, d))])
    return {"removed": removed, "remaining": remaining}


# ── Health ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "v3"}


# ── Lifespan: Kafka producer + cleanup loop ────────────────────────

@app.on_event("startup")
async def startup():
    os.makedirs(WORKDIR, exist_ok=True)
    os.makedirs(WORKSPACES_DIR, exist_ok=True)
    # Kafka producer (ленивый — запустится при первом /submit)
    asyncio.create_task(get_kafka_producer())
    # Фоновая очистка workspace
    asyncio.create_task(_workspace_cleanup_loop())
    log.info("Sandbox v3 запущен. WORKDIR=%s KAFKA=%s", WORKDIR, KAFKA_BROKER)


@app.on_event("shutdown")
async def shutdown():
    global _kafka_producer
    if _kafka_producer:
        await _kafka_producer.stop()
    log.info("Sandbox v3 остановлен")
