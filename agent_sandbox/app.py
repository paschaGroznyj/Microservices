"""
agent_sandbox/app.py
────────────────────────────────────────
Изолированный HTTP-сервис для исполнения произвольного Python-кода.
Не имеет доступа к файловой системе других сервисов, не имеет сети наружу
(ограничивается через docker-compose network_mode/security опции),
принимает только текст кода в теле запроса — не пути.
"""

import logging
import os
import resource
import subprocess
import tempfile
import time
import uuid
import shutil
import base64

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sandbox")

app = FastAPI(title="Agent Code Sandbox")

# ── Конфиг лимитов ────────────────────────────────────────────────
MAX_CODE_SIZE = 50_000          # 50KB на присланный код
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 300
MAX_OUTPUT_SIZE = 20_000        # обрезаем stdout/stderr при возврате
WORKDIR = "/tmp/exec"           # tmpfs, монтируется отдельно в compose
MAX_MEMORY_MB = 3072
MAX_CPU_SECONDS = 200

OUTPUT_DIR_NAME = "output"
MAX_FILE_SIZE_FOR_RETURN = 50 * 1024 * 1024  # 1MB на файл
MAX_FILES_RETURNED = 5


class OutputFile(BaseModel):
    filename: str
    size_bytes: int
    content_base64: str

class ExecuteRequest(BaseModel):
    code: str = Field(..., description="Python код для исполнения")
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)
    stdin: str = Field(default="", description="Опциональный stdin для процесса")


class ExecuteResponse(BaseModel):
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    truncated: bool = False
    duration_ms: int = 0
    error: str | None = None
    files: list[OutputFile] = Field(default_factory=list)

def _limit_resources():
    try:
        mem_bytes = MAX_MEMORY_MB * 1024 * 1024
        
        resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
        # resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        resource.setrlimit(resource.RLIMIT_FSIZE, (30 * 1024 * 1024, 30 * 1024 * 1024))
    except Exception as e:
        os.write(2, f"[sandbox] resource limit warning: {e}\n".encode())


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) > MAX_OUTPUT_SIZE:
        return text[:MAX_OUTPUT_SIZE] + "\n...(truncated)...", True
    return text, False


def _collect_output_files(output_dir: str) -> tuple[list[OutputFile], list[str]]:
    """Читает всё, что код сохранил в output/, кодирует в base64. Возвращает файлы и warnings."""
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
                warnings.append(f"Файл {name} ({size} байт) превышает лимит {MAX_FILE_SIZE_FOR_RETURN}, пропущен")
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


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    if len(req.code.encode("utf-8")) > MAX_CODE_SIZE:
        raise HTTPException(status_code=413, detail=f"Code exceeds {MAX_CODE_SIZE} bytes")

    run_id = uuid.uuid4().hex
    run_dir = os.path.join(WORKDIR, run_id)
    output_dir = os.path.join(run_dir, OUTPUT_DIR_NAME)
    script_path = os.path.join(run_dir, "script.py")

    try:
        os.makedirs(output_dir, exist_ok=True)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(req.code)
    except OSError as e:
        log.error(f"Не смог подготовить run_dir: {e}")
        return ExecuteResponse(ok=False, error=f"Не удалось подготовить исполнение: {e}")

    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["python3", "script.py"],
            input=req.stdin,
            capture_output=True,
            text=True,
            timeout=req.timeout,
            cwd=run_dir,  # относительные "output/file.png" внутри кода пишутся сюда
            preexec_fn=_limit_resources,
            env = {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "PLAYWRIGHT_BROWSERS_PATH": "/opt/playwright-browsers",
                "MPLCONFIGDIR": "/tmp",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "HOME": "/tmp"
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

        files, warnings = _collect_output_files(output_dir)
        if warnings:
            stderr = (stderr + "\n[sandbox warnings] " + "; ".join(warnings)).strip()

        return ExecuteResponse(
            ok=proc.returncode == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            truncated=out_trunc or err_trunc,
            duration_ms=duration_ms,
            files=files,
            error=missing_module_hint,
        )

    except subprocess.TimeoutExpired:
        duration_ms = int((time.monotonic() - start) * 1000)
        log.warning(f"Sandbox timeout после {req.timeout}s, run_id={run_id}")
        return ExecuteResponse(
            ok=False,
            error=f"Превышен таймаут исполнения ({req.timeout}с). Код прерван.",
            duration_ms=duration_ms,
        )

    except MemoryError:
        return ExecuteResponse(ok=False, error="Превышен лимит памяти при подготовке процесса")

    except OSError as e:
        log.error(f"OS ошибка при запуске sandbox-процесса: {e}")
        return ExecuteResponse(ok=False, error=f"Системная ошибка при исполнении: {e}")

    except Exception as e:
        log.exception(f"Неожиданная ошибка в /execute, run_id={run_id}")
        return ExecuteResponse(ok=False, error=f"Внутренняя ошибка сервиса: {type(e).__name__}: {e}")

    finally:
        try:
            shutil.rmtree(run_dir, ignore_errors=True)
        except OSError as e:
            log.warning(f"Не удалось удалить {run_dir}: {e}")


@app.get("/health")
async def health():
    return {"status": "ok"}