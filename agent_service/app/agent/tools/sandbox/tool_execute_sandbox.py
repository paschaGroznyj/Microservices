"""
tools/sandbox/tool_execute_sandbox.py
────────────────────────────────────────
Исполнение Python-кода в изолированном sandbox-сервисе (agent_sandbox).
Модель получает только stdout/stderr/exit_code — бинарные файлы (если код
их создал) сохраняются локально в /app/generated_files, модель получает
только путь к файлу, не его содержимое.

Подключение в langchain_agent.py:
    from tools.sandbox.tool_execute_sandbox import make_sandbox_execute_tool
    exec_tool = make_sandbox_execute_tool(agent)
    tools += exec_tool
"""

import base64
import logging
import os
import time
import uuid
import io
import re
import zipfile

import httpx
from langchain.tools import tool
from pathlib import Path



log = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")
SAVE_DIR = Path("/app/generated_files")
SKILLS_PATH = Path("/app/app/agent/SKILLS")

ALLOWED_UPLOAD_ROOTS = (
    "/sandbox",
    "/app/generated_files"
)

MAX_LOCAL_UPLOAD_SIZE = 100 * 1024 * 1024  # 100 MB (совпадает с лимитом фабрики)


DEFAULT_TIMEOUT = 10
MAX_TIMEOUT = 120
HTTP_TIMEOUT_BUFFER = 5  # добавка к request timeout сверх timeout исполнения кода

# Defense in depth: даже если sandbox-сервис пришлёт файл больше лимита,
# agent_service сам его отклонит при сохранении
MAX_FILE_SIZE_TO_SAVE = 1 * 1024 * 1024  # 1MB
MAX_FILES_PER_CALL = 5


def _ensure_save_dir() -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

def _save_script(code: str, run_id: str, ok: bool, reusable: bool = False) -> str | None:
    """Сохраняет исходный код в /sandbox ВСЕГДА — и при успехе, и при ошибке.

    При ошибке кладёт в executed_scripts/failed/ — чтобы агент мог перечитать и точечно починить через edit_sandbox_file,
    а не переписывать весь скрипт заново."""
    try:
        from agent.tools.filesystem.tool_write_sandbox import SANDBOX_ROOT
        if reusable:
            script_dir = Path(SANDBOX_ROOT) / "reusable"
        elif ok:
            script_dir = Path(SANDBOX_ROOT) / "executed_scripts"
        else:
            script_dir = Path(SANDBOX_ROOT) / "executed_scripts" / "failed"

        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"{run_id}.py"
        script_path.write_text(code, encoding="utf-8")
        return str(script_path)
    except OSError as e:
        log.warning(f"Не удалось сохранить скрипт {run_id}: {e}")
        return None


def _extract_error_context(stderr: str, code: str, window: int = 6) -> tuple[int | None, str | None]:
    """Из traceback вытаскивает номер строки с ошибкой и вырезает окно строк вокруг неё."""
    if not stderr or not code:
        return None, None
    matches = re.findall(r'line (\d+)', stderr)
    if not matches:
        return None, None
    err_line = int(matches[-1])  # последняя — обычно самая точная (наш script.py)
    lines = code.split("\n")
    if err_line < 1 or err_line > len(lines):
        return err_line, None
    start = max(0, err_line - window)
    end = min(len(lines), err_line + window)
    out = []
    for i in range(start, end):
        ln = i + 1
        marker = ">>> " if ln == err_line else "    "
        out.append(f"{marker}{ln:4d} | {lines[i]}")
    return err_line, "\n".join(out)

def _save_output_files(files: list[dict], run_id: str) -> tuple[list[dict], list[str]]:
    """Декодирует base64 из ответа sandbox-сервиса и пишет на диск. Модели возвращаем только метаданные."""
    saved = []
    warnings = []

    if len(files) > MAX_FILES_PER_CALL:
        warnings.append(f"Sandbox вернул {len(files)} файлов, сохраняю первые {MAX_FILES_PER_CALL}")
        files = files[:MAX_FILES_PER_CALL]

    run_folder = os.path.join(SAVE_DIR, run_id)

    for f in files:
        try:
            filename = f.get("filename", "unnamed")
            content_b64 = f.get("content_base64", "")
            size_bytes = f.get("size_bytes", 0)

            # if size_bytes > MAX_FILE_SIZE_TO_SAVE:
            #     warnings.append(f"{filename} ({size_bytes} байт) превышает локальный лимит {MAX_FILE_SIZE_TO_SAVE}, пропущен")
            #     continue

            raw = base64.b64decode(content_b64)

            os.makedirs(run_folder, exist_ok=True)
            safe_name = os.path.basename(filename)  # защита от path traversal через имя файла
            fpath = os.path.join(run_folder, safe_name)

            with open(fpath, "wb") as out:
                out.write(raw)

            saved.append({"filename": safe_name, "path": fpath, "size_bytes": len(raw)})

        except (base64.binascii.Error, ValueError) as e:
            warnings.append(f"Не удалось декодировать {f.get('filename', '?')}: {e}")
        except OSError as e:
            warnings.append(f"Не удалось сохранить {f.get('filename', '?')}: {e}")

    return saved, warnings


async def _call_sandbox(
    code: str | None = None, 
    commands: str | list[str] | None = None, 
    is_shell: bool = False, 
    timeout: int = 30, 
    stdin: str = "", 
    workspace_id: str | None = None,
    persist_script: bool = True,
    changed_files: str | list[str] = "",
) -> dict:
    try:
        payload = {
            "code": code,
            "commands": commands,
            "is_shell": is_shell,
            "timeout": timeout,
            "stdin": stdin,
            "workspace_id": workspace_id,
            "persist_script": persist_script,
            "changed_files": changed_files or "",
        }
        
        async with httpx.AsyncClient(timeout=timeout + HTTP_TIMEOUT_BUFFER) as client:
            resp = await client.post(
                f"{SANDBOX_URL}/execute",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    except httpx.TimeoutException:
        return {"ok": False, "error": "Sandbox-сервис не ответил вовремя (network-level timeout)."}
    except httpx.ConnectError:
        return {"ok": False, "error": "Sandbox-сервис недоступен. Проверь, что agent_sandbox запущен."}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "error": f"Sandbox вернул ошибку {e.response.status_code}: {e.response.text[:300]}"}
    except Exception as e:
        log.exception("Неожиданная ошибка при вызове sandbox-сервиса")
        return {"ok": False, "error": f"Внутренняя ошибка при вызове sandbox: {type(e).__name__}: {e}"}

async def _process_sandbox_result(
    result: dict, 
    start_time: float, 
    run_id: str, 
    code_for_saving: str | None, 
    reusable: bool
) -> dict:
    duration_ms = int((time.monotonic() - start_time) * 1000)

    if not result.get("ok") and "stdout" not in result:
        return {
            "ok": False,
            "error": result.get("error", "unknown error"),
            "duration_ms": duration_ms,
        }

    raw_files = result.get("files", [])
    saved_files, save_warnings = ([], [])
    if raw_files:
        saved_files, save_warnings = _save_output_files(raw_files, run_id)
    
    response = {
        "ok": result.get("ok", False),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code"),
        "truncated": result.get("truncated", False),
        "duration_ms": result.get("duration_ms", duration_ms),
        "run_id": result.get("run_id"),
        "saved_files": [{"filename": f["filename"], "path": f["path"], "size_bytes": f["size_bytes"]} for f in saved_files],
    }

    # Путь к скрипту, сохранённому ВНУТРИ workspace самим sandbox-сервисом
    # (output/.scripts/<run_id>.py) — агент может прочитать его через
    # read_workspace_file / list_workspace_files и переиспользовать.
    if result.get("saved_script"):
        response["saved_script_in_workspace"] = result["saved_script"]

    # Сохраняем скрипт ВСЕГДА (и при ok, и при ошибке)
    if code_for_saving:
        saved_script_path = _save_script(code_for_saving, run_id, result.get("ok", False), reusable)
        logging.info(f"Сохраненный скрипт: {saved_script_path}")
        response["saved_script_path"] = saved_script_path

    if not result.get("ok") and code_for_saving:
        err_line, err_ctx = _extract_error_context(response.get("stderr", ""), code_for_saving)
        response["code_bytes"] = len(code_for_saving.encode("utf-8"))
        if err_line is not None:
            response["error_line"] = err_line
        if err_ctx:
            response["error_context"] = err_ctx
        loc = response.get("saved_script_path") or response.get("saved_script_in_workspace")
        if loc:
            response["hint"] = (
                f"Скрипт целиком дошёл до sandbox ({response['code_bytes']} байт) и сохранён в {loc}. "
                "Ошибка синтаксическая, НЕ транспортная — код НЕ обрезался. "
                "Перечитай скрипт вокруг error_line и исправь ТОЧЕЧНО через edit_sandbox_file. "
                "НЕ переписывай весь скрипт заново."
            )

    if result.get("error"):
        response["error"] = result["error"]
    if save_warnings:
        response["warnings"] = save_warnings

    return response

async def _call_sandbox_endpoint(method: str, path: str, **kwargs) -> dict:
    """Универсальный HTTP-клиент для workspace-эндпоинтов."""
    url = f"{SANDBOX_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        return {
            "ok": False,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _is_under_allowed_root(path: str) -> bool:
    real = os.path.realpath(path)
    return any(
        real == os.path.realpath(root) or real.startswith(os.path.realpath(root) + os.sep)
        for root in ALLOWED_UPLOAD_ROOTS
    )


def _zip_directory(dir_path: str) -> tuple[bytes, str]:
    """Пакует папку в zip (in-memory). Возвращает (bytes, имя_архива.zip)."""
    buf = io.BytesIO()
    base = os.path.basename(os.path.normpath(dir_path))
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(dir_path):
            for fname in files:
                full = os.path.join(root, fname)
                # arcname — путь внутри архива относительно самой папки
                arcname = os.path.relpath(full, dir_path)
                zf.write(full, arcname)
    buf.seek(0)
    return buf.read(), f"{base}.zip"


_MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


async def _fetch_generated_image(agent, image_key: str):
    """Тянет сгенерированную картинку из generated_images по image_key.

    Возвращает (content: bytes, upload_name: str, err: str|None).
    upload_name — имя файла для заливки в workspace: file_name из БД, если он
    осмысленный, иначе {image_key}.{ext по mime_type}.
    """
    db = getattr(agent, "db", None)
    pool = getattr(db, "pool", None) if db else None
    if pool is None:
        return None, None, "нет пула БД — картинки из хранилища недоступны"
    key = str(image_key).strip()
    if not key:
        return None, None, "пустой image_key"
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT image_data, mime_type, file_name FROM generated_images "
                "WHERE image_key::text = $1 LIMIT 1",
                key,
            )
    except Exception as e:
        return None, None, f"ошибка чтения из БД: {type(e).__name__}: {e}"
    if not row or not row["image_data"]:
        return None, None, f"картинка image_key={key} не найдена в generated_images"

    content = bytes(row["image_data"])
    mime = (row["mime_type"] or "image/jpeg").lower()
    ext = _MIME_EXT.get(mime, ".jpg")

    raw_name = (row["file_name"] or "").strip()
    safe_name = os.path.basename(raw_name) if raw_name else ""
    if safe_name and os.path.splitext(safe_name)[1].lower() in _MIME_EXT.values():
        upload_name = safe_name
    elif safe_name:
        upload_name = safe_name + ext
    else:
        upload_name = f"{key}{ext}"
    return content, upload_name, None


def make_sandbox_execute_tool(agent):

    @tool
    async def execute_python_code(code: str, timeout: int = DEFAULT_TIMEOUT, reusable: bool = False,
        workspace_id: str = None, persist_script: bool = True, changed_files: str = "") -> dict:
        """
        Executes Python code in an isolated sandbox (a separate container,         there is internet access, but no access to the project’s file system).
        Use it for calculations, data processing, generating graphs/files/presentations, parsing web pages, and checking code before showing it to the user.

        Arguments:
            code: Python code to execute. Write print() statements during execution —
                  all output will be returned to stdout. Traceback on error — to stderr.
            timeout: execution timeout in seconds, maximum 30 (default 10).
            reusable: True — saves code to /sandbox/reusable/ for reuse
                      in future tasks (use for generic functions/templates,
                      not for one‑off scripts). Default is False.
            workspace_id: workspace ID (transmit only during the final aggregation of the results of parallel tasks)
persist_script: True (by default) — with the specified workspace_id
                  The sandbox service will save the EXECUTED script INSIDE the workspace.
                  in output/.scripts/<run_id>.py (+ alias output/.scripts/last_script.py ).
So you can then spy on it/reuse it within the session.
                  via read_workspace_file / list_workspace_files, without rewriting
                  from scratch. The path is returned in the saved_script_in_workspace field, and the
                  run_id — in the run_id field of the response. Set False for one-time scripts,
                  which do not need to be stored. Without the workspace_id, the argument is ignored.

        ── Files and file system boundaries (READ CAREFULLY) ─────────────────
        The code runs in a FRESH sandbox container. Only the following are visible from the code:
          • the relative output/ folder — save the results here:
            open("output/name.ext", "wb"). The folder is created automatically.
          • the current working directory of the launch (for intermediate files).

        Paths from saved_files (in the form /app/generated_files/<uuid>/file) are file system paths.
        The agent_service service, NOT the sandbox. DO NOT use open() with this path
        inside the code — the file is not there, you will get a FileNotFoundError. These paths
        are needed ONLY for the agent_service tools: send_file_to_user, analyze_image,
        send_email. /app/generated_files and /sandbox are NOT visible from the sandbox code.

        PERSISTENCE between calls:
          • WITHOUT workspace_id: output/ and the entire container is EPHEMERAL — erased
            after EACH call. A file created in a single execute_python_code,
            it is no longer AVAILABLE in the next call. Do the whole chain
            (data → build → conversion → QA-render) in ONE call.
          • With workspace_id: files in output/ survive calls (live for 10 hours).
            To transfer a file between calls, work with a single workspace_id
            or upload it via upload_to_workspace and read it from there.

        For analyze_image to see the image created in the sandbox: first,
        download_workspace_file (it places the file in /app/generated_files and returns
        the path), then call analyze_image using that path. It doesn’t see the local output/.

        Do not print binary data using print().

        ── Successful scripts are saved automatically ───────────────────
        When ok=True, the code is saved in /sandbox/executed_scripts/{run_id}.py —
        it is available via read_file for reuse. Failed attempts are not saved.

        ── Ready‑made libraries in dev_utils (use them, don’t write from scratch) ─

        1. HTML → JPEG rendering (one slide/screenshot):
            import sys; sys.path.insert(0, "/app")
            from dev_utils.render import render_html_to_image
            await render_html_to_image(
                html_path, output_path,
                full_page=False,   # True — the entire page height, for long documents;
                                    # False — only the viewport, for slides of a fixed size
                viewport={"width": 1920, "height": 1080},
                jpeg_quality=90,
            )
            Loading pattern: wait_until="domcontentloaded" (NOT networkidle — freezes
            on advertising pixels/analytics), then await page.evaluate("document.fonts.ready").
        
        IMPORTANT: the code is executed as normal.py script, top-level await is NOT working.
        All await calls (render_slides_to_pdf, render_html_to_image, parse_web_page)
        wrap in async def main(): ...; asyncio.run(main())

        2. A presentation consisting of several HTML slides → ready‑made PDF (use THIS,
        rather than manually assembling Pillow multi‑page):

            async def main():
                import sys; sys.path.insert(0, "/app")
                from dev_utils.slides_to_pdf import render_slides_to_pdf

                # Write HTML files with RELATIVE paths to the current working directory —
                # NOT in /sandbox (not accessible from the sandbox) and not in /tmp manually (not necessary,
                # the working directory already exists and is unique to this run)
                with open("slide1.html", "w") as f:
                    f.write(html_content_1)
                with open("slide2.html", "w") as f:
                    f.write(html_content_2)

                result = await render_slides_to_pdf(
                    html_paths=["slide1.html", "slide2.html"],
                    output_pdf_path="output/presentation.pdf",
                )
                print(result)

            asyncio.run(main())

            # result = {"ok": bool, "output_path": str, "slides_count": int, "error": str|None}
            # ONLY the final PDF will be saved in saved_files — the intermediate JPEG slides
            # are deleted automatically; there’s no need to clean them up manually.
            Each slide is a SEPARATE HTML file (don't try to make one HTML with
            page-break-after — rendering removes only one viewport, not page-by-page).

            IMPORTANT: the code is executed as .py script, top-level await is not working —
            always wrap asynchronous calls in async def main(): ...; asyncio.run(main()).

        3. PDF-ПРЕЗЕНТАЦИЯ БЕЗ БЕЛЫХ ПОЛЕЙ (прямой Playwright → PNG → Pillow).
           Используй ЭТОТ способ, а НЕ render_slides_to_pdf, когда нужна preza
           фиксированного формата 16:9 (1920×1080) с вшитыми картинками и БЕЗ полей/
           смещений по краям. Каждый слайд рендерится напрямую в PNG фиксированного
           размера, затем PNG склеиваются в один PDF через Pillow.

           ПРИЧИНА ПОЛЕЙ: белые поля появляются, когда размер .slide в HTML ≠ размеру
           viewport рендера ≠ размеру страницы PDF. Решение — свести все три к одному
           размеру (1920×1080) + CSS-reset в <head> КАЖДОГО слайда:
               * { margin:0; padding:0; box-sizing:border-box; }
               html,body { width:1920px; height:1080px; overflow:hidden; }
               .slide    { width:1920px; height:1080px; overflow:hidden; position:relative; }
           Картинку покрывать весь лист: <img style="width:100%;height:100%;object-fit:cover">
           или background. Вшивать локально — через base64 (нет внешних зависимостей):
               b64 = base64.b64encode(open(img_path,"rb").read()).decode()
               img = f'<img src="data:image/png;base64,{b64}" style="width:100%;height:100%;object-fit:cover">'
           либо <img> с ОТНОСИТЕЛЬНЫМ путём к файлу рядом с HTML в рабочей директории.

           Скелет (viewport == размеру слайда, full_page=False — иначе поедут поля):

               import asyncio, base64
               from playwright.async_api import async_playwright
               from PIL import Image

               async def render_slide(html_path, out_png, w=1920, h=1080, scale=1):
                   async with async_playwright() as p:
                       browser = await p.chromium.launch(args=["--no-sandbox"])
                       page = await browser.new_page(
                           viewport={"width": w, "height": h},
                           device_scale_factor=scale,   # 2..3 — физический растр до 4K
                       )                                 # ХОЛСТ оставить логическим 1920×1080
                       await page.goto(f"file://{html_path}", wait_until="domcontentloaded")
                       await page.evaluate("document.fonts.ready")
                       await page.wait_for_timeout(400)
                       await page.screenshot(path=out_png, type="png", full_page=False)
                       await browser.close()

               async def main(html_files, out_pdf):
                   pngs = []
                   for i, html in enumerate(html_files):
                       out = f"slide_{i}.png"
                       await render_slide(html, out)
                       pngs.append(out)
                   imgs = [Image.open(p).convert("RGB") for p in pngs]
                   imgs[0].save(out_pdf, save_all=True, append_images=imgs[1:])

               asyncio.run(main(["slide1.html","slide2.html"], "output/deck.pdf"))

           ЗАМЕТКИ:
           - top-level await не работает — всё в async def main(): ...; asyncio.run(main()).
           - PNG lossless даёт резкие линии/код; JPEG q96 — если нужен компактный файл.
           - device_scale_factor=2..3 поднимает растр до 4K БЕЗ изменения вёрстки
             (холст логический 1920×1080, резкость множителем, а не размером канвы).
           - ПРОВЕРЯЙ фактический размер PNG после рендера: должен быть ровно W×H (×scale).
             Если нет — где-то поле/несовпадение размеров .slide/viewport.
           - Каждый слайд — ОТДЕЛЬНЫЙ HTML-файл (не page-break-after в одном HTML).

        ── Fonts ─────────────────────────────────────────────────────────
        /app/fonts/InterVariable.ttf — variable, NOT for reportlab/Pillow
        /app/fonts/extras/ttf/Inter-{Regular,Bold,Italic}.ttf — for reportlab/Pillow ✅
        /app/fonts/static/ — PlayfairDisplay ✅
        DejaVu (matplotlib mpl-data/fonts/ttf/) — fallback without explicit specification

        ── Libraries ─────────────────────────────────────────────────────
        pandas==2.2.2
        numpy==1.26.4
        matplotlib==3.9.0
        requests==2.32.3
        Pillow==10.3.0
        python-pptx==1.0.2
        python-docx>=1.1.0
        cairosvg
        svglib
        reportlab
        qrcode
        jinja2
        openpyxl
        nbformat
        python-dotenv==1.0.0
        playwright==1.58.0
        pathlib
        fastapi
        uvicorn[standard]
        aiokafka
        beautifulsoup4
        apscheduler
        python-multipart
        gdown

            changed_files: what this run changes inside the workspace (only with workspace_id),
                  for the user's live web editor. "" (default) — nothing / don't notify;
                  "*" — files changed but you don't know which (full refresh);
                  otherwise comma-separated paths relative to the workspace root
                  (e.g. "repo/app.py,repo/static/main.css"). The editor is notified
                  only after the run finishes. Pass it whenever you write/delete files.

        Returns: stdout, stderr, exit_code, saved_files (paths to the created files).

        4. Playwright MCP via CDP (for JS-SPA without WAF):
        Direct launch `npx @playwright/mcp --browser chromium` does NOT work —
        MCP is looking for chrome-for-testing, not the system /usr/bin/chromium.
        The working scheme is to run the system chromium as a CDP server and
        connect MCP to it:

            chromium --headless=new --no-sandbox --remote-debugging-port=9222 &
            # wait ~4s, then:
            npx @playwright/mcp --cdp-endpoint http://localhost:9222
            # transport — stdin/stdout JSON-RPC, NOT HTTP-SSE

        Timeouts: chromium start ~4s, MCP initialization ~3s,
        navigation wait 8-12s.

        @playwright/mcp is already installed globally (read-only FS; a subsequent
        npm install -g will fail with EROFS — this is normal, the package is already in place).

        Available commands: browser_navigate, browser_snapshot,
        browser_evaluate, browser_click, browser_fill.

        For parsing Telegram resources:
            - t.me/s/ returns ~20 latest posts. To get

        Allow users to use Git commands if they have provided their key
        """
        timeout = MAX_TIMEOUT
        run_id = uuid.uuid4().hex

        _ensure_save_dir()

        start = time.monotonic()
        result = await _call_sandbox(
            code=code, 
            timeout=timeout, 
            workspace_id=workspace_id, 
            is_shell=False,
            persist_script=persist_script,
            changed_files=changed_files,
        )
        return await _process_sandbox_result(result, start, run_id, code_for_saving=code, reusable=reusable)

    @tool
    async def execute_shell_commands(commands: str, timeout: int = DEFAULT_TIMEOUT, workspace_id: str = None,
                                     changed_files: str = "") -> dict:
        """
        Executes a system command in an isolated sandbox via bash.
        Suitable for running third‑party binaries (git, docker, curl, npx, etc.).

        PASS THE COMMAND AS ONE STRING — not a list. The string runs through
        `bash -lc`, so FULL shell syntax works: pipes |, chaining && || ;,
        redirects > >>, command substitution $(), globs *.
            "git add -A && git commit -m \"fix: обновил конфиг\""
            "ls -la | grep .py | wc -l"
            "cat file.txt | head -20"
            "cd /tmp/exec/workspaces/<id>/repo && git status"
            "docker run --rm alpine echo hello"
        The sandbox is isolated, so shell execution is safe here.
        (A list of args is still accepted for back-compat — it runs directly
        without a shell, so pipes/chaining in list mode are NOT interpreted.)

            changed_files: what these commands changes inside the workspace (only with workspace_id),
                  for the user's live web editor. "" (default) — nothing / don't notify;
                  "*" — files changed but you don't know which (full refresh);
                  otherwise comma-separated paths relative to the workspace root
                  (e.g. "repo/app.py,repo/static/main.css"). The editor is notified
                  only after the run finishes. Pass it whenever you write/delete files.

        Available features:
        - git clone / git pull / git push (internet access, tokens via env)
        - docker build / docker run / docker compose up (DinD, the daemon is available)
        - curl, wget — network requests
        - apt-get install — package installation within the session

        If you plan to work with files within the session, create a workspace
        via create_workspace (files inside the area live for 10 hours).
        If you init a git repo inside /tmp/exec/workspaces/<id>, it won't be swept by the timer.

        Docker specifics:
        - The daemon is accessible via unix:///var/run/docker.sock
        - Build image: "docker build -t my-image /path/to/context"
        - Compose: "docker compose -f /path/compose.yml up -d"
        - Kafka inside DinD: 172.28.0.10:9092

        """
        run_id = uuid.uuid4().hex
        _ensure_save_dir()
        
        start = time.monotonic()
        result = await _call_sandbox(
            commands=commands, 
            timeout=timeout, 
            workspace_id=workspace_id, 
            is_shell=True,
            changed_files=changed_files,
        )
        return await _process_sandbox_result(result, start, run_id, code_for_saving=None, reusable=False)


    @tool
    async def edit_and_run(
        library_id: int,
        need_context: bool,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Extracts a reusable script from the code_library by library_id
        and performs one of two actions — the agent chooses which one via need_context:

        need_context=False — the script runs as is, without any modifications:
          • The tool reads the path from the database
          • Passes the file directly to the sandbox for execution
          • Returns the result identically to execute_python_code
          • Automatically increments use_count and updates last_used_at

        need_context=True — the agent needs to review the code before running:
          • The tool reads the path from the database and returns the file text to the agent
          • The agent makes точечные edits via edit_sandbox_file
          • The agent itself calls execute_python_code with the corrected code
          • use_count is NOT updated here — the agent will update it after a successful run

        Agent decision (need_context):
          False — the request matches 1-to-1 with the description in the database, the parameters have not changed.
          True — edits are needed: different URL, parameters, output format.

        Parameters:
          library_id   — the ID of the record in the code_library table.
          need_context — bool, the agent specifies this itself.
          timeout      — execution timeout in seconds (only for need_context=False).

        Returns when need_context=False:
          ok, stdout, stderr, exit_code, saved_files, duration_ms

        Returns when need_context=True:
        source_code (str), path (str), title (str), description (str)
        """
        
        # ── 1. Тянем запись из БД ─────────────────────────────
        try:
            async with agent.db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, title, description, code_type,
                           path, run_id, author, use_count
                    FROM code_library
                    WHERE id = $1
                    """,
                    library_id,
                )
        except Exception as e:
            log.error("edit_and_run: ошибка SELECT code_library id=%s: %s", library_id, e)
            return {"ok": False, "error": f"Ошибка запроса к БД: {e}"}

        if row is None:
            return {
                "ok": False,
                "error": f"Скрипт с library_id={library_id} не найден в code_library.",
            }

        script_path = row["path"]
        title       = row["title"]
        description = row["description"]

        # ── 2. Читаем файл ────────────────────────────────────
        try:
            source_code = Path(script_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return {
                "ok": False,
                "error": (
                    f"Файл не найден: {script_path}. "
                    f"Возможно скрипт был удалён. Обнови path в code_library (id={library_id})."
                ),
            }
        except OSError as e:
            return {"ok": False, "error": f"Ошибка чтения файла {script_path}: {e}"}

        # ── 3a. need_context=True — отдаём код агенту ────────
        if need_context:
            log.info(
                "edit_and_run: need_context=True, отдаю код агенту. id=%s path=%s",
                library_id, script_path,
            )
            return {
                "ok":          True,
                "need_context": True,
                "library_id":  library_id,
                "title":       title,
                "description": description,
                "path":        script_path,
                "source_code": source_code,
                # Подсказка агенту что делать дальше
                "next_step": (
                    "Прочитай source_code, внеси правки через edit_sandbox_file, "
                    "затем вызови execute_python_code с исправленным кодом. "
                    "После успешного запуска вызови touch_code_library(library_id) "
                    "для обновления use_count."
                ),
            }

        # ── 3b. need_context=False — запускаем напрямую ───────
        log.info(
            "edit_and_run: need_context=False, запускаю напрямую. id=%s path=%s",
            library_id, script_path,
        )

        timeout = min(max(timeout, 1), MAX_TIMEOUT)
        start = time.monotonic()
        result = await _call_sandbox(source_code, timeout)

        duration_ms = int((time.monotonic() - start) * 1000)

        # ── 4. Сохраняем output-файлы (если есть) ────────────
        saved_files = []
        warnings    = []
        raw_files   = result.get("files", [])

        if raw_files:
            import base64
            run_id     = uuid.uuid4().hex
            save_dir   = Path("/app/generated_files") / run_id
            save_dir.mkdir(parents=True, exist_ok=True)

            for f in raw_files[:5]:  # не больше 5 файлов
                try:
                    raw  = base64.b64decode(f.get("content_base64", ""))
                    name = Path(f.get("filename", "unnamed")).name  # защита от path traversal
                    fpath = save_dir / name
                    fpath.write_bytes(raw)
                    saved_files.append({"filename": name, "path": str(fpath), "size_bytes": len(raw)})
                except Exception as e:
                    warnings.append(f"Не удалось сохранить {f.get('filename', '?')}: {e}")

        # ── 5. touch: use_count++ + last_used_at ──────────────
        if result.get("ok"):
            try:
                await agent.db.touch_code_library(library_id)
            except Exception as e:
                # некритично — логируем и едем дальше
                log.warning("edit_and_run: touch_code_library failed id=%s: %s", library_id, e)
                warnings.append(f"use_count не обновлён: {e}")

        # ── 6. Возврат результата ─────────────────────────────
        response = {
            "ok":          result.get("ok", False),
            "library_id":  library_id,
            "title":       title,
            "stdout":      result.get("stdout", ""),
            "stderr":      result.get("stderr", ""),
            "exit_code":   result.get("exit_code"),
            "truncated":   result.get("truncated", False),
            "duration_ms": result.get("duration_ms", duration_ms),
            "saved_files": saved_files,
        }
        if result.get("error"):
            response["error"] = result["error"]
        if warnings:
            response["warnings"] = warnings

        return response

        # ══════════════════════════════════════════════════════════════
    # 2. CREATE_WORKSPACE
    # ══════════════════════════════════════════════════════════════

    @tool
    async def create_workspace() -> dict:
        """
        Creates a new persistent workspace in the sandbox service.
        Returns the workspace_id, which must be passed to execute_python_code
        to save files between calls.

        Use for multi‑step tasks:
        - Data collection (step 1) → parsing (step 2) → file generation (step 3)
        - When each step is a separate call to execute_python_code,
          but files from step 1 are needed in step 3

        IMPORTANT (working with files/presentations/media): if the user requests
        assemble/send a file, presentation, document, video, or any other
        the artifact that needs to be given to him — FIRST create a workspace through
        this tool, collect the artifact in it (upload_to_workspace /
        edit_workspace_file / execute_python_code with this workspace_id), put
        the result is in output/ and send via send_file_to_user along the way
        /tmp/exec/workspaces/{workspace_id}/output/<filename>. Give it away without a workspace
        heavy/composite files will not work reliably.

        ALWAYS create a workspace and pass its id in a bunch of heavy operations to generate execute_python_code.

        The workspace is automatically deleted after 10 hours of inactivity, so
        It is not necessary to delete an area within the dialog.

        To preserve the workspace permanently, you need to create a file at the workspace root—specifically
        at `/tmp/exec/workspaces/<wid>/.ws_hold`—alongside your folders (not inside `output/` or `repo/`, but at the root itself).

        Save the workspace only if the user plans to continue working with it.

        Returns: workspace_id, created_at, path
        """
        result = await _call_sandbox_endpoint("POST", "/workspace/create")

        if "workspace_id" not in result:
            return str({
                "ok": False,
                "error": result.get("error", "Не удалось создать workspace"),
                "detail": result.get("detail", ""),
            })

        log.info(f"[workspace] Создан {result['workspace_id']}")
        return str({
            "ok": True,
            "workspace_id": result["workspace_id"],
            "created_at": result.get("created_at"),
            "path": result.get("path"),
            "hint": (
                "Передавай этот workspace_id в execute_python_code для "
                "сохранения файлов между вызовами. Файлы пишутся в output/ "
                "и доступны в последующих вызовах с тем же workspace_id."
            ),
        })

    # ══════════════════════════════════════════════════════════════
    # 3. LIST_WORKSPACE_FILES
    # ══════════════════════════════════════════════════════════════

    @tool
    async def list_workspace_files(workspace_id: str) -> dict:
        """
        Returns a list of all files in the workspace (including subfolders).
        Use this to check which files are available from previous steps,
        before running the next execute_python_code.

        Arguments:
            workspace_id: workspace ID obtained from create_workspace

        Returns: a list of files with paths and sizes, total size.
        """
        result = await _call_sandbox_endpoint(
            "GET", f"/workspace/{workspace_id}/files"
        )

        if "files" not in result:
            return str({
                "ok": False,
                "error": result.get("error", "Workspace не найден"),
                "workspace_id": workspace_id,
            })

        return str({
            "ok": True,
            "workspace_id": workspace_id,
            "files": [
                {
                    "filename": f["filename"],
                    "size_bytes": f["size_bytes"],
                }
                for f in result.get("files", [])
            ],
            "total_size_bytes": result.get("total_size_bytes", 0),
            "file_count": len(result.get("files", [])),
        })

    # ══════════════════════════════════════════════════════════════
    # 4. DOWNLOAD_WORKSPACE_FILE
    # ══════════════════════════════════════════════════════════════

    @tool
    async def download_workspace_file(
        workspace_id: str,
        filepath: str,
    ) -> dict:
        """
        Downloads a specific file from workspace and saves it to
        /app/generated_files to send to the user.

        Use it when the final file is created in workspace and it needs to be
        give it to the user via send_file_to_user.

        Arguments:
            workspace_id: ID workspace
            filepath: the relative path to the file inside the workspace
                      (for example, "output/result.xlsx" or "output/data.json")

        Returns: the path to the saved file in /app/generated_files
        """
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(
                    f"{SANDBOX_URL}/workspace/{workspace_id}/file/{filepath}"
                )
                resp.raise_for_status()
                content = resp.content
        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            })
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        # Сохраняем в generated_files
        run_id = uuid.uuid4().hex
        run_folder = os.path.join(SAVE_DIR, run_id)
        os.makedirs(run_folder, exist_ok=True)

        safe_name = os.path.basename(filepath)
        local_path = os.path.join(run_folder, safe_name)

        with open(local_path, "wb") as f:
            f.write(content)

        log.info(
            f"[workspace] Файл {filepath} из {workspace_id} "
            f"сохранён в {local_path}"
        )

        return str({
            "ok": True,
            "filename": safe_name,
            "path": local_path,
            "size_bytes": len(content),
            "hint": (
                "Файл сохранён локально. Используй send_file_to_user "
                f"с path={local_path} для отправки пользователю."
            ),
        })

    # ══════════════════════════════════════════════════════════════
    # 5. DELETE_WORKSPACE
    # ══════════════════════════════════════════════════════════════

    @tool
    async def delete_workspace(workspace_id: str) -> dict:
        """
        Deletes the workspace and all files in it.
        Call this after completing a multi‑step task to release resources.
        If you don’t call it, the workspace will be deleted automatically after 1 hour of inactivity.

        Arguments:
            workspace_id: ID of the workspace to delete

        Returns: status, workspace_id
        """
        result = await _call_sandbox_endpoint(
            "DELETE", f"/workspace/{workspace_id}"
        )

        if result.get("status") == "deleted":
            log.info(f"[workspace] Удалён {workspace_id}")
            return str({
                "ok": True,
                "status": "deleted",
                "workspace_id": workspace_id,
            })

        return str({
            "ok": False,
            "error": result.get("error", "Не удалось удалить workspace"),
            "workspace_id": workspace_id,
        })

    # ══════════════════════════════════════════════════════════════
    # 6. CLEANUP_WORKSPACES
    # ══════════════════════════════════════════════════════════════

    @tool
    async def cleanup_workspaces() -> dict:
        """
        Forcibly clears all expired workspaces (with an expired TTL).
        Call this if you receive the error “workspace limit reached”.

        Returns: the number of deleted workspaces, the number of remaining ones.
        """
        result = await _call_sandbox_endpoint("POST", "/workspace/cleanup")

        return str({
            "ok": True,
            "removed": result.get("removed", 0),
            "remaining": result.get("remaining", 0),
        })

    
    @tool
    async def presentation_design_skill():
        """
        Call this tool before generating or modifying PowerPoint presentations.

        Returns design guidelines for:
        - slide layouts
        - typography
        - spacing
        - visual hierarchy
        - color usage
        - icon usage
        - presentation QA

        Use these instructions when planning slide structure and visual composition.

         Visual elements:
        - Every slide should contain at least one visual element.
        - Prefer SVG icons over emojis.
        - Search for SVG icons relevant to the slide topic.
        - Prefer Tabler Icons.
        - If not available, use Heroicons or Lucide.
        - Keep one icon style across the whole deck.
        - Do not mix outline and filled icons.

        Search examples:
        - "tabler icons database"
        - "tabler icons process"
        - "tabler icons workflow"
        - "tabler icons analytics"

        To find the required SVG icons, you should use the template search with search_code_library
        params:
         query="tabler icons", tags=["tabler"]
        """
        try:
            with open(SKILLS_PATH / "pptx_maker.md", mode='r', encoding='utf-8') as f:
                text = f.read()
            return text
        except Exception as e:
            return "Не получилось открыть скилл, возможно он перемещен или удален"
        
    @tool
    async def html_design_skill():
        """
        Call before generating HTML pages in a modern style, with design, animation, and transitions.
        ALWAYS use execute_python_code to search for text skills if the user requests a beautiful presentation
        SKILLS ARE NOT LOCAL; you need to write a script using execute_python_code to retrieve them from the remote sandbox.
        ## 1. How it works in the remote sandbox

        ```
        /app/
        ├── skills-lock.json          # registry: version, list of skills, hashes
        ├── .agents/skills/           # working directory for skills
        │   ├── animate/SKILL.md
        │   ├── animation-vocabulary/SKILL.md
        │   ├── apple-design/SKILL.md
        │   ├── emil-design-eng/SKILL.md
        │   ├── find-animation-opportunities/SKILL.md
        │   ├── improve-animations/SKILL.md
        │   ├── pick-ui-library/SKILL.md
        │   ├── prototype/SKILL.md
        │   └── review-animations/SKILL.md
        └── agent/skills/             # mirror (the same files)
        ```

        ### skills-lock.json

        ---

        ## 2. How to get the content of skills (for documentation)

        ```python
        import os

        skill_bases = ["/app/.agents/skills", "/app/agent/skills"]
        seen = set()

        for base in skill_bases:
            if not os.path.isdir(base):
                continue
            for skill_name in sorted(os.listdir(base)):
                skill_path = os.path.join(base, skill_name, "SKILL.md")
                key = skill_name
                if key in seen:
                    resume
                seen.add(key)
                if os.path.isfile(skill_path):
                    with open(skill_path) as f:
                        content = f.read()
                    print(f"=== {skill_name} ===")
                    print(content)
                    print()
        ```
        """
    
    @tool
    async def upload_to_workspace(
        workspace_id: str,
        local_path: str | None = None,
        image_keys: list[str] | None = None,
        media_ids: list[str] | None = None,
        dest: str = "",
    ) -> dict:
        """
        When you need to GIVE the user the presentation/document/video/archive: at first
        create_workspace, upload the source code/assets here, assemble the artifact,
        put it in output/ and send it via send_file_to_user.

        Uploads artifacts to the remote workspace of the factory, where they will become
        available for execute_python_code / execute_shell_commands with the same
        workspace_id (the reverse operation of download_workspace_file).

        Two sources, can be combined (at least one is required):

            local_path is the path to a LOCAL file or folder in the file system.
                         the agent. The following roots are allowed: /sandbox, /app/generated_files.
                         the file → is filled in as it is; the folder → is packed in zip and
                         unpacked at the factory (the structure is preserved).

            image_keys — a list of image_key (UUID) images generated by you
                         from the generated_images repository. That's how the generated
                         through generate_image, images are sent to the REMOTE
                         FACTORY: the binary of the image is in the database (BYTEA), physical
                         the agent doesn't have the file, so the usual local_path doesn't contain it.
                         transfer. Tool gets the bytes from the database itself and fills them with a file.
                         in the workspace. Use this when you need to insert
                         the generated image in a presentation/document that
                         it is built using a python script in the factory. image_key is taken
                         from the generate_image report or from find_generated_image.

        Arguments:
            workspace_id: ID of the target workspace (from create_workspace)
            local_path: optional — local file/folder (see above)
            image_keys: optional — a list of image_keys from generated_images
            media_ids: optional — a list of media_ids from the chat's MEDIA STORAGE
                          (Redis, files/images from recent posts, live
                          ~24h; the registry is shown in the "CHAT MEDIA STORAGE" block).
                          Redis Bridge→factory: tool takes binary out of storage
                          and fills it with a file in the workspace. So to the factory
                          There are also NON-picture files (docx/pdf/csv...)
                          sent by the user earlier, and not just pictures.
            dest: The destination subfolder inside the workspace, e.g. "assets/img".
                          The empty string is the root of the workspace. Applies to both
                          sources.

        Returns: {ok, workspace_id, dest, uploaded:[...],
                     images: {image_key: "dest/file_name"}, result}
        The images map indicates which path in the workspace each image is located at —
        use these paths in the Python code for building the presentation.
        """
        local_path = (local_path or "").strip() or None
        # Нормализация image_keys (LLM может прислать строкой/JSON/CSV)
        keys: list[str] = []
        if image_keys is not None:
            if isinstance(image_keys, list):
                keys = [str(k).strip() for k in image_keys if str(k).strip()]
            else:
                s = str(image_keys).strip()
                if s:
                    try:
                        import json as _json
                        parsed = _json.loads(s)
                        if isinstance(parsed, list):
                            keys = [str(k).strip() for k in parsed if str(k).strip()]
                        else:
                            keys = [str(parsed).strip()]
                    except Exception:
                        keys = [p.strip() for p in s.strip("[]").split(",") if p.strip()]

        # Нормализация media_ids (LLM может прислать строкой/JSON/CSV)
        mids: list[str] = []
        if media_ids is not None:
            if isinstance(media_ids, list):
                mids = [str(k).strip() for k in media_ids if str(k).strip()]
            else:
                s = str(media_ids).strip()
                if s:
                    try:
                        import json as _json
                        parsed = _json.loads(s)
                        if isinstance(parsed, list):
                            mids = [str(k).strip() for k in parsed if str(k).strip()]
                        else:
                            mids = [str(parsed).strip()]
                    except Exception:
                        mids = [p.strip() for p in s.strip("[]").split(",") if p.strip()]

        if not local_path and not keys and not mids:
            return str({
                "ok": False,
                "error": "Не указан ни local_path, ни image_keys, ни media_ids — нечего заливать.",
            })

        # Собираем список payload'ов: (upload_name, bytes, unpack, source_label,
        # image_key|None)
        payloads: list[tuple] = []

        # 1) Локальный файл/папка
        if local_path:
            if not _is_under_allowed_root(local_path):
                return str({
                    "ok": False,
                    "error": f"Путь {local_path} вне разрешённых корней "
                             f"{ALLOWED_UPLOAD_ROOTS}",
                })
            if not os.path.exists(local_path):
                return str({"ok": False, "error": f"Путь не найден: {local_path}"})
            is_dir = os.path.isdir(local_path)
            try:
                if is_dir:
                    content, upload_name = _zip_directory(local_path)
                    unpack = "true"
                else:
                    with open(local_path, "rb") as f:
                        content = f.read()
                    upload_name = os.path.basename(local_path)
                    unpack = "false"
            except OSError as e:
                return str({"ok": False, "error": f"Не удалось прочитать {local_path}: {e}"})
            if len(content) > MAX_LOCAL_UPLOAD_SIZE:
                return str({
                    "ok": False,
                    "error": f"Размер {len(content)} байт превышает лимит "
                             f"{MAX_LOCAL_UPLOAD_SIZE}",
                })
            payloads.append((upload_name, content, unpack, local_path, None))

        # 2) Картинки из generated_images по image_key
        img_errors: list[str] = []
        for k in keys:
            content, upload_name, err = await _fetch_generated_image(agent, k)
            if err:
                img_errors.append(f"image_key={k}: {err}")
                continue
            if len(content) > MAX_LOCAL_UPLOAD_SIZE:
                img_errors.append(f"image_key={k}: превышает лимит {MAX_LOCAL_UPLOAD_SIZE} б")
                continue
            payloads.append((upload_name, content, "false", f"image_key={k}", k))

        # 2b) Файлы/картинки из пер-чат media_store (Redis) по media_id
        media_errors: list[str] = []
        if mids:
            chat_id = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))
            if chat_id is None:
                media_errors.append("chat_id неизвестен — media_ids пропущены")
            else:
                try:
                    from agent.media import media_store as _ms
                except Exception:
                    _ms = None
                if _ms is None:
                    media_errors.append("media_store недоступен")
                else:
                    for _mid in mids:
                        try:
                            raw, meta = await _ms.get_media(chat_id, _mid)
                        except Exception as e:
                            media_errors.append(f"media_id={_mid}: {type(e).__name__}: {e}")
                            continue
                        if not raw:
                            media_errors.append(f"media_id={_mid}: не найдено (истёк TTL 24ч или неверный id)")
                            continue
                        if len(raw) > MAX_LOCAL_UPLOAD_SIZE:
                            media_errors.append(f"media_id={_mid}: превышает лимит {MAX_LOCAL_UPLOAD_SIZE} б")
                            continue
                        _name = (meta or {}).get("name") or f"{_mid}.bin"
                        payloads.append((_name, raw, "false", f"media_id={_mid}", None))

        if not payloads:
            return str({
                "ok": False,
                "error": "Ни один источник не удалось подготовить.",
                "image_errors": img_errors,
                "media_errors": media_errors,
            })

        # 3) Заливаем каждый payload на фабрику (unpack различается для zip/файла,
        #    поэтому отдельным POST на элемент)
        uploaded: list[dict] = []
        images_map: dict[str, str] = {}
        last_result = None
        dest_prefix = (dest.rstrip("/") + "/") if dest else ""
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                for upload_name, content, unpack, label, key in payloads:
                    files = {"files": (upload_name, content, "application/octet-stream")}
                    data = {"dest": dest, "unpack": unpack}
                    resp = await client.post(
                        f"{SANDBOX_URL}/workspace/{workspace_id}/upload",
                        files=files,
                        data=data,
                    )
                    resp.raise_for_status()
                    last_result = resp.json()
                    ws_path = f"{dest_prefix}{upload_name}" if unpack == "false" else (dest or ".")
                    uploaded.append({
                        "source": label,
                        "name": upload_name,
                        "kind": "dir" if unpack == "true" else "file",
                        "path": ws_path,
                        "bytes": len(content),
                    })
                    if key is not None:
                        images_map[key] = ws_path
                    log.info(
                        f"[workspace] Залито {upload_name} ({label}, {len(content)} б) "
                        f"в {workspace_id}/{dest or '.'}"
                    )
        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
                "uploaded_before_error": uploaded,
            })
        except httpx.ConnectError:
            return str({"ok": False, "error": "Sandbox-сервис недоступен."})
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        out = {
            "ok": True,
            "workspace_id": workspace_id,
            "dest": dest or ".",
            "uploaded": uploaded,
            "count": len(uploaded),
            "images": images_map,
            "result": last_result,
        }
        if img_errors:
            out["image_errors"] = img_errors
        if media_errors:
            out["media_errors"] = media_errors
        return str(out)
    
    return [
        execute_python_code,
        execute_shell_commands,
        create_workspace,
        list_workspace_files,
        download_workspace_file,
        delete_workspace,
        cleanup_workspaces,
        edit_and_run,
        presentation_design_skill,
        html_design_skill,
        upload_to_workspace,
    ]
