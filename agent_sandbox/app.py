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
  POST /workspace/{wid}/keep — закрепить workspace бессрочно (keep-маркер, иммунитет к TTL)
  DELETE /workspace/{wid}/keep — снять keep-маркер
  POST /workspace/cleanup — принудительная очистка просроченных
  GET  /health            — healthcheck
"""

import asyncio
import base64
import json
import logging
import os
import resource
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional
from aiokafka import AIOKafkaProducer

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import zipfile

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sandbox")

app = FastAPI(title="Agent Code Sandbox v3")

# ── Конфиг ─────────────────────────────────────────────────────────
MAX_CODE_SIZE = 500_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 3600
MAX_OUTPUT_SIZE = 20_000
WORKDIR = "/tmp/exec"
WORKSPACES_DIR = "/tmp/exec/workspaces"
MAX_MEMORY_MB = 8192
MAX_CPU_SECONDS = 3600

OUTPUT_DIR_NAME = "output"
MAX_FILE_SIZE_FOR_RETURN = 50 * 1024 * 1024
MAX_FILES_RETURNED = 50

WORKSPACE_TTL_SECONDS = 36000  # 10 часов
WORKSPACE_MAX_COUNT = 500
WORKSPACE_CLEANUP_INTERVAL = 3600

# Секретный маркер-закрепление: если в корне workspace лежит файл с этим именем,
# workspace НЕ удаляется НИКОГДА, пока файл физически лежит (полный иммунитет к TTL).
# Имя намеренно неочевидное и переопределяемо через env.
WORKSPACE_KEEP_MARKER = os.environ.get("WORKSPACE_KEEP_MARKER", ".ws_hold")

KAFKA_BROKER = os.getenv("KAFKA_BROKER")
KAFKA_TASK_EVENTS_TOPIC = "task-events"

# Лимиты приёма (defense in depth)
MAX_UPLOAD_SIZE = 100 * 1024 * 1024      # 100 MB на один файл
MAX_UPLOAD_FILES = 50                     # не больше 50 файлов за раз

MAX_FILE_SIZE_FOR_EDIT = 200_000
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
    code: str | None = Field(None, description="Python код для исполнения")
    commands: str | list[str] | None = Field(None, description="Команды. Строкой ('ls | grep py && echo ok') — выполняется через bash -lc: работают пайпы |, && || ;, редиректы, $(), glob. Список аргументов — прямой запуск без shell (back-compat).")
    is_shell: bool = Field(False, description="Флаг, указывающий, что нужно выполнить системные команды вместо Python-кода")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    stdin: str = ""
    workspace_id: str | None = None
    persist_script: bool = Field(True, description="Сохранять ли исполнённый Python-скрипт в workspace (output/.scripts/<run_id>.py) для переиспользования. Работает только при заданном workspace_id")


class ExecuteResponse(BaseModel):
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None
    files: list[OutputFile] = Field(default_factory=list)
    run_id: str | None = None
    saved_script: str | None = None


class SubmitRequest(BaseModel):
    code: str = Field(..., description="Python код для исполнения")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    workspace_id: str = Field(..., description="ID persistent workspace")
    task_id: str = Field(..., description="Уникальный ID задачи (генерирует агент)")
    chat_id: str = Field(..., description="ID чата для нотификации")
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

class WorkspaceEditRequest(BaseModel):
    path: str = Field(..., description="Относительный путь к файлу внутри workspace")
    old_str: str = Field(..., description="Точный фрагмент для замены (ровно 1 вхождение)")
    new_str: str = Field(..., description="Текст замены. Пустая строка удаляет old_str")




# ── Утилиты ────────────────────────────────────────────────────────

def _limit_resources():
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024, 100 * 1024 * 1024))
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


def _safe_join(base: str, *paths: str) -> str:
    """Джойн с защитой от path traversal. Бросает ValueError при выходе за base."""
    target = os.path.realpath(os.path.join(base, *paths))
    base_real = os.path.realpath(base)
    if not (target == base_real or target.startswith(base_real + os.sep)):
        raise ValueError(f"Path traversal detected: {paths}")
    return target

def _get_workspace_path(workspace_id: str) -> str | None:
    ws_path = os.path.join(WORKSPACES_DIR, workspace_id)
    if os.path.isdir(ws_path):
        return ws_path
    return None


def _run_code_sync(code: str, timeout: int, run_dir: str, stdin: str = "", task_id: str = None,
    ws_path: str = None, persist_script: bool = False, run_id: str = None) -> dict:
    """
    Синхронный запуск кода в subprocess.
    Используется и в /execute, и в /submit (через run_in_executor).
    """
    base_run_dir = Path(run_dir)

    # 1. Если task_id нет, генерируем временный ID для изолированного выполнения
    exec_task_id = task_id or f"exec_{uuid.uuid4().hex[:8]}"

    # 2. Определяем целевую папку исполнения (всегда используем изолированный .tasks/)
    task_dir = base_run_dir / ".tasks" / exec_task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # 3. Настраиваем общую папку output для воркспейса
    if ws_path:
        workspace_output = Path(ws_path) / OUTPUT_DIR_NAME
    else:
        workspace_output = base_run_dir / OUTPUT_DIR_NAME

    workspace_output.mkdir(parents=True, exist_ok=True)

    # 4. Безопасно монтируем симлинк output/ внутрь таски
    task_output_link = task_dir / OUTPUT_DIR_NAME
    if not task_output_link.exists() and not task_output_link.is_symlink():
        try:
            task_output_link.symlink_to(workspace_output)
        except OSError:
            # Если симлинк не создался (например на Windows без прав), делаем фоллбэк
            pass

    # 4b. Монтируем остальное содержимое корня воркспейса симлинками внутрь таски,
    # чтобы Python-код видел не только output/, но и src/, assets/ и прочие папки
    # (убирает класс ошибок "файл есть в воркспейсе, но из кода не виден").
    # output/ уже смонтирован выше; служебные каталоги пропускаем.
    _WS_MOUNT_SKIP = {".tasks", OUTPUT_DIR_NAME, ".git", ".ws_hold", "__pycache__"}
    ws_root = Path(ws_path) if ws_path else base_run_dir
    try:
        for entry in ws_root.iterdir():
            if entry.name in _WS_MOUNT_SKIP:
                continue
            link = task_dir / entry.name
            if link.exists() or link.is_symlink():
                continue
            try:
                link.symlink_to(entry)
            except OSError:
                pass
    except OSError:
        pass

    script_path = task_dir / "script.py"
    cwd = task_dir

    try:
        script_path.write_text(code, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"Не удалось подготовить исполнение: {e}"}

    # Сохраняем копию скрипта ВНУТРИ workspace (переживёт удаление run_dir),
    # чтобы агент мог его подсмотреть / переиспользовать в рамках сессии.
    saved_script_rel = None
    if ws_path and persist_script:
        try:
            scripts_dir = Path(ws_path) / OUTPUT_DIR_NAME / ".scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{run_id or exec_task_id}.py"
            (scripts_dir / fname).write_text(code, encoding="utf-8")
            # удобный алиас на последний скрипт
            (scripts_dir / "last_script.py").write_text(code, encoding="utf-8")
            saved_script_rel = f"{OUTPUT_DIR_NAME}/.scripts/{fname}"
        except OSError as e:
            log.warning("[persist_script] не удалось сохранить скрипт в workspace: %s", e)

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
                "OPENBLAS_NUM_THREADS": os.getenv("OPENBLAS_NUM_THREADS"),
                "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"),
                "HOME": "/tmp",
                "MAIL_HOST": os.getenv("MAIL_HOST", ""),
                "MAIL_PORT": os.getenv("MAIL_PORT", ""),
                "MAIL_LOGIN": os.getenv("MAIL_LOGIN", ""),
                "MAIL_PASSWORD": os.getenv("MAIL_PASSWORD", ""),
                "MAIL_SMTP_PORT": os.getenv("MAIL_SMTP_PORT", ""),
                "GITVERSE_TOKEN": os.getenv("GITVERSE_TOKEN", ""),
                "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""),
                "DOCKER_HOST": os.getenv("DOCKER_HOST", "tcp://sandbox_dind:2375"),
                "GATEWAY_IP": os.getenv("GATEWAY_IP", ""),
                "CAPTION_API_PORT": os.getenv("CAPTION_API_PORT", ""),
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
            "run_id": run_id or exec_task_id,
            "saved_script": saved_script_rel,
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


def _run_commands_sync(commands: str | list[str], timeout: int, run_dir: str, stdin: str = "", task_id: str = None, ws_path: str = None) -> dict:
    """
    Синхронный запуск системных команд в subprocess.
    Строка ('ls | grep py && echo ok') выполняется через bash -lc (shell=True):
    работают пайпы |, цепочки && || ;, редиректы > >>, подстановки $() и glob *.
    Список ['git','commit',...] выполняется напрямую (shell=False, back-compat).
    Песочница изолированная, поэтому shell-режим допустим.
    """
    use_shell = isinstance(commands, str)
    if use_shell:
        if not commands.strip():
            return {"ok": False, "error": "Пустая команда."}
        exec_arg = ["/bin/bash", "-lc", commands]
    else:
        if not commands:
            return {"ok": False, "error": "Пустой список команд."}
        exec_arg = commands

    base_run_dir = Path(ws_path) if ws_path else Path(run_dir)

    # 1. Если передана конкретная асинхронная таска — изолируем в .tasks/<task_id>
    if task_id:
        cwd = base_run_dir / ".tasks" / task_id
        cwd.mkdir(parents=True, exist_ok=True)
    else:
        # Для обычных команд (git clone, bash и т.д.) выполняем прямо в корне воркспейса
        cwd = base_run_dir
        cwd.mkdir(parents=True, exist_ok=True)

    # 2. Настраиваем общую папку output для результатов
    workspace_output = base_run_dir / OUTPUT_DIR_NAME
    workspace_output.mkdir(parents=True, exist_ok=True)

    # 3. Если работаем внутри .tasks/<task_id>, гарантируем наличие симлинка на output/
    if task_id:
        task_output_link = cwd / OUTPUT_DIR_NAME
        if not task_output_link.exists() and not task_output_link.is_symlink():
            try:
                task_output_link.symlink_to(workspace_output)
            except OSError:
                pass

    start = time.monotonic()
    try:
        proc = subprocess.run(
            exec_arg,           # строка -> [bash,-lc,cmd] (shell); список -> напрямую
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            preexec_fn=_limit_resources,
            shell=False,    
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PLAYWRIGHT_BROWSERS_PATH": "/opt/playwright-browsers",
                "MPLCONFIGDIR": "/tmp",
                "OPENBLAS_NUM_THREADS": os.getenv("OPENBLAS_NUM_THREADS"),
                "OMP_NUM_THREADS": os.getenv("OMP_NUM_THREADS"),
                "HOME": "/tmp",
                "MAIL_HOST": os.getenv("MAIL_HOST", ""),
                "MAIL_PORT": os.getenv("MAIL_PORT", ""),
                "MAIL_LOGIN": os.getenv("MAIL_LOGIN", ""),
                "MAIL_PASSWORD": os.getenv("MAIL_PASSWORD", ""),
                "MAIL_SMTP_PORT": os.getenv("MAIL_SMTP_PORT", ""),
                "GITVERSE_TOKEN": os.getenv("GITVERSE_TOKEN", ""),
                "GITHUB_TOKEN": os.getenv("GITHUB_TOKEN", ""),
                "DOCKER_HOST": os.getenv("DOCKER_HOST", "tcp://sandbox_dind:2375"),
                "KAFKA_BROKER_DIND": os.getenv("KAFKA_BROKER_DIND", ""),
                "GATEWAY_IP": os.getenv("GATEWAY_IP", ""),
                "CAPTION_API_PORT": os.getenv("CAPTION_API_PORT", ""),
            },
        )
        duration_ms = int((time.monotonic() - start) * 1000)

        stdout, out_trunc = _truncate(proc.stdout)
        stderr, err_trunc = _truncate(proc.stderr)

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
            "error": None,
        }

    except FileNotFoundError:
        # При shell=False, если исполняемый файл (например, "docker") не найден,
        # subprocess выбрасывает FileNotFoundError, а не возвращает код ошибки.
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": False, 
            "error": f"Команда или бинарный файл не найдены: {exec_arg if isinstance(exec_arg, str) else exec_arg[0]}", 
            "duration_ms": duration_ms
        }

    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "ok": False,
            "error": f"Превышен таймаут исполнения ({timeout}с). Процесс прерван.",
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

def _has_git(ws_path: str) -> bool:
    # .git в корне
    if os.path.exists(os.path.join(ws_path, ".git")):
        return True
    # .git в подпапках первого уровня (git clone создаёт подпапку repo/.git)
    for entry in os.listdir(ws_path):
        sub = os.path.join(ws_path, entry)
        if os.path.isdir(sub) and os.path.exists(os.path.join(sub, ".git")):
            return True
    return False

def _has_keep_hold(ws_path: str) -> bool:
    """Секретный маркер-файл держит workspace живым бессрочно, пока файл физически лежит.
    Наличие маркера = полный иммунитет к TTL-удалению (без проверки возраста)."""
    return os.path.exists(os.path.join(ws_path, WORKSPACE_KEEP_MARKER))

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

        if _has_git(ws_path):
            log.debug("[workspace] Пропущен %s (содержит .git)", entry)
            continue

        if _has_keep_hold(ws_path):
            log.debug("[workspace] Пропущен %s (keep-маркер %s)", entry, WORKSPACE_KEEP_MARKER)
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
    if req.is_shell and not req.commands:
        raise HTTPException(status_code=400, detail="Для is_shell=True необходимо передать 'commands' (строкой или списком)")
    if not req.is_shell and not req.code:
        raise HTTPException(status_code=400, detail="Для выполнения Python необходимо передать 'code'")

    if not req.is_shell and req.code and len(req.code.encode("utf-8")) > MAX_CODE_SIZE:
        raise HTTPException(status_code=413, detail=f"Code exceeds {MAX_CODE_SIZE} bytes")

    run_id = uuid.uuid4().hex
    run_dir = os.path.join(WORKDIR, run_id)
    ws_path = None
    if req.workspace_id:
        ws_path = _get_workspace_path(req.workspace_id)
        if ws_path is None:
            raise HTTPException(status_code=404, detail=f"Workspace {req.workspace_id} не найден")

    if req.is_shell:
        result = await asyncio.to_thread(
            _run_commands_sync, req.commands, req.timeout, run_dir, req.stdin, None, ws_path
        )
    else:
        result = await asyncio.to_thread(
            _run_code_sync, req.code, req.timeout, run_dir, req.stdin, None, ws_path,
            req.persist_script, run_id,
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
        run_id=result.get("run_id"),
        saved_script=result.get("saved_script"),
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


@app.post("/workspace/{workspace_id}/upload")
async def upload_to_workspace_endpoint(
    workspace_id: str,
    files: list[UploadFile] = File(...),
    dest: str = Form(""),           # относительная подпапка внутри workspace, напр. "static/js"
    unpack: bool = Form(False),     # если True и файл .zip — распаковать
):
    """
    Заливает один/несколько файлов в workspace.
    - dest: подпапка назначения внутри workspace (создаётся при необходимости)
    - unpack: если True, каждый .zip распаковывается в dest (заливка папок)
    """
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")

    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Слишком много файлов: {len(files)} (лимит {MAX_UPLOAD_FILES})",
        )

    # Директория назначения (с защитой от traversal)
    try:
        dest_dir = _safe_join(ws_path, dest) if dest else os.path.realpath(ws_path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    os.makedirs(dest_dir, exist_ok=True)

    written = []
    for uf in files:
        raw = await uf.read()
        if len(raw) > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Файл {uf.filename} превышает лимит {MAX_UPLOAD_SIZE} байт",
            )

        safe_name = os.path.basename(uf.filename or "unnamed")

        # Распаковка zip
        if unpack and safe_name.lower().endswith(".zip"):
            tmp_zip = _safe_join(dest_dir, safe_name)
            with open(tmp_zip, "wb") as f:
                f.write(raw)
            try:
                with zipfile.ZipFile(tmp_zip) as zf:
                    # Проверяем каждую запись на traversal перед извлечением
                    for member in zf.namelist():
                        _safe_join(dest_dir, member)  # бросит ValueError при выходе за пределы
                    zf.extractall(dest_dir)
                    extracted = zf.namelist()
                os.remove(tmp_zip)
                written.append({
                    "filename": safe_name,
                    "unpacked": True,
                    "members": len(extracted),
                    "dest": os.path.relpath(dest_dir, ws_path),
                })
            except zipfile.BadZipFile:
                raise HTTPException(status_code=400, detail=f"{safe_name} — не валидный zip")
            except ValueError as e:
                os.remove(tmp_zip)
                raise HTTPException(status_code=403, detail=f"zip traversal: {e}")
            continue

        # Обычный файл
        target = _safe_join(dest_dir, safe_name)
        with open(target, "wb") as f:
            f.write(raw)
        written.append({
            "filename": safe_name,
            "unpacked": False,
            "size_bytes": len(raw),
            "path": os.path.relpath(target, ws_path),
        })

    # Обновляем активность, чтобы TTL не убил workspace
    _touch_workspace(workspace_id)

    return {
        "ok": True,
        "workspace_id": workspace_id,
        "written": written,
        "count": len(written),
    }



# ── GET /workspace/{wid}/read — чтение файла с окном offset/limit ────

@app.get("/workspace/{workspace_id}/read")
async def read_workspace_file_endpoint(
    workspace_id: str,
    path: str,
    offset: int = 0,
    limit: int = 0,
):
    """
    Читает текстовый файл ВНУТРИ workspace.
    Возвращает JSON с содержимым и метаданными (по аналогии с локальным read_file).
    Поддерживает окно offset/limit для больших файлов.
    """
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail=f"Workspace {workspace_id} не найден")

    try:
        target = _safe_join(ws_path, path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail=f"Файл '{path}' не найден в workspace")

    size = os.path.getsize(target)
    if size > MAX_FILE_SIZE_FOR_RETURN:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой для чтения ({size} байт)",
        )

    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось прочитать файл: {e}")

    total_lines = len(all_lines)

    # Явное окно от агента — приоритетнее автотрюка head/tail
    if offset or limit:
        start = max(offset, 0)
        end = start + limit if limit > 0 else total_lines
        window = all_lines[start:end]
        content = "".join(window)
        _touch_workspace(workspace_id)
        return {
            "ok": True,
            "workspace_id": workspace_id,
            "path": path,
            "size": size,
            "total_lines": total_lines,
            "shown_lines": f"{start + 1}-{min(end, total_lines)}",
            "truncated": end < total_lines,
            "content": content,
        }

    # Без окна — весь файл целиком (лимит по размеру уже проверен выше)
    content = "".join(all_lines)
    _touch_workspace(workspace_id)
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "path": path,
        "size": size,
        "total_lines": total_lines,
        "truncated": False,
        "content": content,
    }


# ── POST /workspace/{wid}/edit — точечная замена НА СТОРОНЕ ФАБРИКИ ──

@app.post("/workspace/{workspace_id}/edit")
async def edit_workspace_file_endpoint(workspace_id: str, req: WorkspaceEditRequest):
    """
    Точечное редактирование файла ВНУТРИ workspace (find & replace).
    Вся логика read -> count -> replace -> write выполняется на фабрике
    атомарно, агент шлёт только {path, old_str, new_str}.

    Коды ответов:
      404 — workspace или файл не найден
      403 — path traversal
      409 — old_str встречается != 1 раза (0 или >1) — замена неоднозначна
      413 — файл слишком большой
    """
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail=f"Workspace {workspace_id} не найден")

    try:
        target = _safe_join(ws_path, req.path)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not os.path.isfile(target):
        raise HTTPException(
            status_code=404,
            detail=f"Файл '{req.path}' не найден в workspace. Создай его через upload_to_workspace.",
        )

    size = os.path.getsize(target)
    if size > MAX_FILE_SIZE_FOR_EDIT:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой для редактирования ({size} байт)",
        )

    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось прочитать файл: {e}")

    count = content.count(req.old_str)
    if count == 0:
        raise HTTPException(
            status_code=409,
            detail="old_str не найден в файле. Проверь точное совпадение "
                   "(пробелы, отступы, переносы строк).",
        )
    if count > 1:
        raise HTTPException(
            status_code=409,
            detail=f"old_str встречается {count} раз(а) — замена неоднозначна. "
                   f"Добавь больше контекста в old_str, чтобы совпадение было уникальным.",
        )

    new_content = content.replace(req.old_str, req.new_str, 1)

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Не удалось записать файл: {e}")

    _touch_workspace(workspace_id)

    return {
        "ok": True,
        "workspace_id": workspace_id,
        "path": req.path,
        "old_len": len(req.old_str),
        "new_len": len(req.new_str),
        "occurrences_replaced": 1,
        "size_after": len(new_content.encode("utf-8")),
    }



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
        _run_code_sync, req.code, req.timeout, run_dir, req.stdin, req.task_id,
        ws_path, True, req.task_id,
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
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None or not os.path.exists(ws_path):
        raise HTTPException(status_code=404, detail="Workspace не найден")

    real_ws_path = os.path.realpath(ws_path)

    # 1. Прямая проверка пути (как передали: "output/file.txt" или "sub/dir/file.txt")
    target = os.path.abspath(os.path.join(ws_path, filepath))

    # 2. Фоллбэк #1: Пробуем заглянуть в папку output/ если передали только имя файла
    if not os.path.isfile(target):
        output_target = os.path.abspath(os.path.join(ws_path, "output", filepath))
        if os.path.isfile(output_target):
            target = output_target

    # 3. Фоллбэк #2: Рекурсивный поиск по имени файла внутри всей папки workspace
    if not os.path.isfile(target):
        filename = os.path.basename(filepath)
        found_files = list(Path(real_ws_path).rglob(filename))
        
        # Берем первый найденный файл
        for file in found_files:
            if file.is_file():
                target = str(file.resolve())
                break

    # Защита от Path Traversal (обязательно после всех резолвов!)
    if not os.path.realpath(target).startswith(real_ws_path) or not os.path.isfile(target):
        raise HTTPException(
            status_code=404, 
            detail=f"Файл '{filepath}' не найден в workspace '{workspace_id}'"
        )

    return FileResponse(
        path=target,
        filename=os.path.basename(target),
        media_type="application/octet-stream",
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


@app.post("/workspace/{workspace_id}/keep")
async def keep_workspace_endpoint(workspace_id: str):
    """Ставит секретный keep-маркер: workspace не будет удалён, пока маркер лежит."""
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")
    marker = os.path.join(ws_path, WORKSPACE_KEEP_MARKER)
    with open(marker, "w") as f:
        f.write(str(time.time()))
    return {
        "status": "kept",
        "workspace_id": workspace_id,
        "permanent": True,
        "marker": WORKSPACE_KEEP_MARKER,
    }

@app.delete("/workspace/{workspace_id}/keep")
async def unkeep_workspace_endpoint(workspace_id: str):
    """Снимает keep-маркер: workspace возвращается под обычный TTL."""
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")
    marker = os.path.join(ws_path, WORKSPACE_KEEP_MARKER)
    existed = os.path.exists(marker)
    if existed:
        os.remove(marker)
    return {"status": "released" if existed else "no_marker", "workspace_id": workspace_id}


@app.post("/workspace/cleanup")
async def cleanup_workspaces_endpoint():
    removed = _cleanup_expired_workspaces()
    remaining = 0
    if os.path.isdir(WORKSPACES_DIR):
        remaining = len([d for d in os.listdir(WORKSPACES_DIR) if os.path.isdir(os.path.join(WORKSPACES_DIR, d))])
    return {"removed": removed, "remaining": remaining}


# ── Health ─────────────────────────────────────────────────────────

# ── Editor (read-only) ───────────────────────────────────────
# Дерево и чтение файлов воркспейса для веб-редактора (Monaco). Без записи, без исполнения.

EDITOR_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".tasks", ".ws_hold"}
EDITOR_MAX_READ_BYTES = 5 * 1024 * 1024


def _editor_build_tree(base: str) -> list:
    base_real = os.path.realpath(base)

    def walk(dir_path: str) -> list:
        items = []
        try:
            entries = sorted(os.scandir(dir_path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return items
        for e in entries:
            if e.name in EDITOR_SKIP_DIRS:
                continue
            rel = os.path.relpath(e.path, base_real)
            if e.is_dir(follow_symlinks=False):
                items.append({"name": e.name, "path": rel, "type": "dir", "children": walk(e.path)})
            else:
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                items.append({"name": e.name, "path": rel, "type": "file", "size": size})
        return items

    return walk(base_real)


@app.get("/workspace/{workspace_id}/editor/tree")
async def editor_tree_endpoint(workspace_id: str):
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")
    return {"workspace_id": workspace_id, "tree": _editor_build_tree(ws_path)}


@app.get("/workspace/{workspace_id}/editor/read")
async def editor_read_endpoint(workspace_id: str, path: str):
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")
    try:
        target = _safe_join(ws_path, path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Путь вне workspace")
    if not os.path.isfile(target):
        raise HTTPException(status_code=404, detail=f"Файл '{path}' не найден")
    size = os.path.getsize(target)
    if size > EDITOR_MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой ({size} байт)")
    try:
        with open(target, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8")
        is_binary = False
    except UnicodeDecodeError:
        text = ""
        is_binary = True
    return {
        "workspace_id": workspace_id,
        "path": path,
        "size": size,
        "is_binary": is_binary,
        "content": text,
    }


class EditorWriteRequest(BaseModel):
    path: str
    content: str


EDITOR_MAX_WRITE_BYTES = 5 * 1024 * 1024


@app.put("/workspace/{workspace_id}/editor/write")
async def editor_write_endpoint(workspace_id: str, body: EditorWriteRequest):
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")
    try:
        target = _safe_join(ws_path, body.path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Путь вне workspace")
    if os.path.isdir(target):
        raise HTTPException(status_code=400, detail="Путь указывает на директорию")
    data = body.content.encode("utf-8")
    if len(data) > EDITOR_MAX_WRITE_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой ({len(data)} байт)")
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise HTTPException(status_code=404, detail="Родительская папка не найдена")
    tmp = target + ".tmp_editor_" + uuid.uuid4().hex[:8]
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Ошибка записи: {e}")
    return {"workspace_id": workspace_id, "path": body.path, "size": len(data), "ok": True}


@app.get("/workspace/{workspace_id}/editor/ttl")
async def editor_ttl_endpoint(workspace_id: str):
    ws_path = _get_workspace_path(workspace_id)
    if ws_path is None:
        raise HTTPException(status_code=404, detail="Workspace не найден")

    now = time.time()
    immortal = _has_git(ws_path) or _has_keep_hold(ws_path)

    last_active_file = os.path.join(ws_path, ".last_active")
    created_file = os.path.join(ws_path, ".created_at")
    try:
        if os.path.exists(last_active_file):
            ts = float(open(last_active_file).read().strip())
        elif os.path.exists(created_file):
            ts = float(open(created_file).read().strip())
        else:
            ts = now
    except (ValueError, OSError):
        ts = now

    age = max(0.0, now - ts)
    remaining = None if immortal else max(0, int(WORKSPACE_TTL_SECONDS - age))

    return {
        "workspace_id": workspace_id,
        "ttl_seconds": WORKSPACE_TTL_SECONDS,
        "last_active": ts,
        "remaining_seconds": remaining,
        "immortal": immortal,
    }


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
