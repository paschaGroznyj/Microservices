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
import zipfile

import httpx
from langchain.tools import tool
from pathlib import Path



log = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")
SAVE_DIR = Path("/app/generated_files")
SKILLS_PATH = Path("/app/app/agent_pm/SKILLS")

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
    print(SAVE_DIR)
    SAVE_DIR.mkdir(parents=True, exist_ok=True)

def _save_script_if_ok(code: str, run_id: str, ok: bool, reusable: bool = False) -> str | None:
    """Сохраняет исходный код в /sandbox, если исполнение прошло успешно."""
    if not ok:
        return None
    try:
        from agent_pm.tools.filesystem.tool_write_sandbox import SANDBOX_ROOT
        if reusable:
            script_dir = Path(SANDBOX_ROOT) / "reusable"
        else:
            script_dir = Path(SANDBOX_ROOT) / "executed_scripts"

        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / f"{run_id}.py"
        script_path.write_text(code, encoding="utf-8")
        return str(script_path)
    except OSError as e:
        log.warning(f"Не удалось сохранить рабочий скрипт {run_id}: {e}")
        return None

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
    commands: list[str] | None = None, 
    is_shell: bool = False, 
    timeout: int = 30, 
    stdin: str = "", 
    workspace_id: str | None = None
) -> dict:
    try:
        payload = {
            "code": code,
            "commands": commands,
            "is_shell": is_shell,
            "timeout": timeout,
            "stdin": stdin,
            "workspace_id": workspace_id
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
        "saved_files": [{"filename": f["filename"], "path": f["path"], "size_bytes": f["size_bytes"]} for f in saved_files],
    }
    
    # Сохраняем скрипт, только если это был Python код
    if code_for_saving:
        saved_script_path = _save_script_if_ok(code_for_saving, run_id, result.get("ok", False), reusable)
        logging.info(f"Сохраненный скрипт: {saved_script_path}")
        response["saved_script_path"] = saved_script_path

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


def make_sandbox_execute_tool(agent):

    @tool
    async def execute_python_code(code: str, timeout: int = DEFAULT_TIMEOUT, reusable: bool = False,
        workspace_id: str = None) -> dict:
        """
        Исполняет Python-код в изолированной песочнице (отдельный контейнер,
        доступ в интернет есть, доступа к файловой системе проекта нет).
        Используй для вычислений, обработки данных, генерации графиков/файлов/
        презентаций, парсинга веб-страниц, проверки кода перед показом пользователю.

        Название: ЖДУН ФАБРИКА, с фиксированным таймаутом, нужно ждать выполнение задачи

        Аргументы:
            code: Python-код для исполнения. Пиши print() по ходу выполнения —
                  весь вывод вернётся в stdout. Traceback при ошибке — в stderr.
            timeout: таймаут исполнения в секундах, максимум 30 (по умолчанию 10).
            reusable: True — сохраняет код в /sandbox/reusable/ для переиспользования
                      в будущих задачах (используй для универсальных функций/шаблонов,
                      не для одноразовых скриптов). По умолчанию False.
            workspace_id: ID workspace (передавай только при финальной агрегации результатов параллельных задач)

        ── Файлы ──────────────────────────────────────────────────────────
        Сохраняй результат через open("output/имя_файла.ext", "wb") — путь
        ОТНОСИТЕЛЬНЫЙ, папка output/ создаётся автоматически. Путь появится
        в ответе (saved_files) — содержимое файла тебе не передаётся, только путь.
        Не печатай бинарные данные через print().
        /tmp не персистентен между вызовами execute_python_code — если нужны
        промежуточные файлы (например HTML перед рендером), создавай и используй
        их в пределах ОДНОГО вызова.

        ── Успешные скрипты сохраняются автоматически ───────────────────
        При ok=True код сохраняется в /sandbox/executed_scripts/{run_id}.py —
        доступен через read_file для переиспользования. Неудачные попытки не сохраняются.

        ── Готовые библиотеки в dev_utils (используй их, не пиши заново) ─

        1. Рендер HTML → JPEG (один слайд/скриншот):
            import sys; sys.path.insert(0, "/app")
            from dev_utils.render import render_html_to_image
            await render_html_to_image(
                html_path, output_path,
                full_page=False,   # True — вся высота страницы, для длинных документов;
                                    # False — только viewport, для слайдов фикс. размера
                viewport={"width": 1920, "height": 1080},
                jpeg_quality=90,
            )
            Паттерн загрузки: wait_until="domcontentloaded" (НЕ networkidle — зависает
            на рекламных пикселях/аналитике), затем await page.evaluate("document.fonts.ready").
        
        ВАЖНО: код исполняется как обычный .py скрипт, top-level await НЕ работает.
        Все await-вызовы (render_slides_to_pdf, render_html_to_image, parse_web_page)
        оборачивай в async def main(): ... ; asyncio.run(main())

        2. Презентация из нескольких HTML-слайдов → готовый PDF (используй ЭТО,
        а не собирай Pillow multi-page вручную):

            async def main():
                import sys; sys.path.insert(0, "/app")
                from dev_utils.slides_to_pdf import render_slides_to_pdf

                # Пиши HTML-файлы ОТНОСИТЕЛЬНЫМИ путями в текущую рабочую директорию —
                # НЕ в /sandbox (недоступен из песочницы) и не в /tmp вручную (не нужно,
                # рабочая директория уже существует и уникальна на этот запуск)
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
            # В saved_files попадёт ТОЛЬКО итоговый PDF — промежуточные JPEG-слайды
            # удаляются автоматически, не нужно чистить их вручную.
            Каждый слайд — ОТДЕЛЬНЫЙ HTML-файл (не пытайся сделать один HTML с
            page-break-after — рендер снимает только один viewport, не постранично).

            ВАЖНО: код исполняется как .py скрипт, top-level await не работает —
            всегда оборачивай асинхронные вызовы в async def main(): ...; asyncio.run(main()).

        ── Шрифты ─────────────────────────────────────────────────────────
        /app/fonts/InterVariable.ttf — вариативный, НЕ для reportlab/Pillow
        /app/fonts/extras/ttf/Inter-{Regular,Bold,Italic}.ttf — для reportlab/Pillow ✅
        /app/fonts/static/ — PlayfairDisplay ✅
        DejaVu (matplotlib mpl-data/fonts/ttf/) — фолбэк без явного указания

        ── Библиотеки ─────────────────────────────────────────────────────
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

        Возвращает: stdout, stderr, exit_code, saved_files (пути созданных файлов).

        4. Playwright MCP через CDP (для JS-SPA без WAF):
        Прямой запуск `npx @playwright/mcp --browser chromium` НЕ работает —
        MCP ищет chrome-for-testing, а не системный /usr/bin/chromium.
        Рабочая схема — поднять системный chromium как CDP-сервер и
        подключить MCP к нему:

            chromium --headless=new --no-sandbox --remote-debugging-port=9222 &
            # подождать ~4с, затем:
            npx @playwright/mcp --cdp-endpoint http://localhost:9222
            # транспорт — stdin/stdout JSON-RPC, НЕ HTTP-SSE

        Таймауты: старт chromium ~4с, инициализация MCP ~3с,
        ожидание навигации 8-12с.

        @playwright/mcp уже установлен глобально (read-only FS, повторный
        npm install -g упадёт с EROFS — это нормально, пакет уже на месте).

        Доступные команды: browser_navigate, browser_snapshot,
        browser_evaluate, browser_click, browser_fill.

        Для парсинга Telegram-ресурсов:
            - t.me/s/ отдаёт ~20 последних постов. Чтобы получить больше, нужно пагинировать через ?before=POST_ID

        Готовый блок правил для системного промпта агента (системных инструкций `agent_service`). Его можно скопировать и вставить в секцию инструкций по обработке данных и работе с файлами:

        ### 📋 Правила работы агента: Формирование выгрузок и обработка артефактов
        #### 1. Маршрутизация файлов и работа с окружением

        * **Запрет прямых путей:**
        * Строго запрещено передавать в функцию отправки файлов (`send_file_to_user`) пути из рабочих директорий изолированного окружения (`/tmp/exec/workspaces/...`).

        * **Корректный каталог артефактов:**
        * Файл должен формироваться или копироваться в разрешённую целевую директорию (например, `output/...`, сохраняемую в `/app/generated_files`).
        * Отправлять клиенту только пути, вернувшиеся и подтверждённые в поле `saved_files` результата выполнения инструмента.

        #### 2. Обработка ошибок инфраструктуры

        * Если выполнение кода вернуло `'ok': False`, ошибку окружения (например, `[Errno 2]`) или пустой `saved_files`:
        * **Категорически запрещено** отвечать пользователю в духе *"Файл отправлен"* или *"Я сформировал файл"*.
        * Агент обязан честно сообщить об инфраструктурной ошибке подготовки/выполнения среды и предложить повторную сборку.

        PATH RULE: Internal workspace storage root is /tmp/exec/workspaces/{workspace_id}/output/. 
        Never reference files using output/<filename> as a relative path. 
        Always use the absolute path /tmp/exec/workspaces/{workspace_id}/output/<filename> or a direct relative filename <filename>.


        """
        timeout = MAX_TIMEOUT
        run_id = uuid.uuid4().hex

        _ensure_save_dir()

        start = time.monotonic()
        result = await _call_sandbox(
            code=code, 
            timeout=timeout, 
            workspace_id=workspace_id, 
            is_shell=False
        )
        return await _process_sandbox_result(result, start, run_id, code_for_saving=code, reusable=reusable)

    @tool
    async def execute_shell_commands(commands: list[str], timeout: int = DEFAULT_TIMEOUT, workspace_id: str = None) -> dict:
        """
        Выполняет системные команды в изолированной песочнице без использования оболочки (shell).
        Подходит для запуска сторонних бинарников (например, docker, curl, git).

        ОБРАТИ ВНИМАНИЕ: Команды нужно передавать строго списком аргументов!
        Например: ["docker", "run", "--rm", "alpine", "echo", "hello"]

        Доступные возможности:
        - git clone / git pull / git push (интернет есть, токены через env)
        - docker build / docker run / docker compose up (DinD, демон доступен)
        - curl, wget — сетевые запросы
        - apt-get install — установка пакетов в рамках сессии

        Если планируешь работать с файлами в рамках сессии, нужно создавать workspace
        через create_workspace (файлы внутри области будут жить только 3600 сек!)

        Если ты иницилизируешь репозиторий .git внутри /tmp/exec/workspaces/<workspace_name>, он не будет удален по таймеру

        Docker-специфика:
        - Демон доступен через unix:///var/run/docker.sock
        - Для сборки образа: ["docker", "build", "-t", "my-image", "/path/to/context"]
        - Для compose: ["docker", "compose", "-f", "/path/compose.yml", "up", "-d"]
        - Kafka bootstrap внутри DinD: 172.28.0.10:9092
        """
        run_id = uuid.uuid4().hex
        _ensure_save_dir()
        
        start = time.monotonic()
        result = await _call_sandbox(
            commands=commands, 
            timeout=timeout, 
            workspace_id=workspace_id, 
            is_shell=True
        )
        return await _process_sandbox_result(result, start, run_id, code_for_saving=None, reusable=False)


    @tool
    async def edit_and_run(
        library_id: int,
        need_context: bool,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Извлекает переиспользуемый скрипт из библиотеки code_library по library_id
        и выполняет одно из двух действий — агент выбирает сам через need_context:

        need_context=False — скрипт запускается как есть, без правок:
          • Тулза читает path из БД
          • Передаёт файл напрямую в sandbox на исполнение
          • Возвращает результат идентично execute_python_code
          • Автоматически инкрементирует use_count и обновляет last_used_at

        need_context=True — агенту нужно посмотреть код перед запуском:
          • Тулза читает path из БД и возвращает текст файла агенту
          • Агент делает точечные правки через edit_sandbox_file
          • Агент сам вызывает execute_python_code с исправленным кодом
          • use_count НЕ обновляется здесь — агент обновит после успешного запуска

        Решение агента (need_context):
          False — запрос совпадает 1-в-1 с описанием в БД, параметры не менялись
          True  — нужны правки: другой URL, параметры, формат вывода

        Параметры:
          library_id   — id записи в таблице code_library
          need_context — bool, агент указывает сам
          timeout      — таймаут исполнения в секундах (только для need_context=False)

        Возвращает при need_context=False:
          ok, stdout, stderr, exit_code, saved_files, duration_ms

        Возвращает при need_context=True:
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
        Создаёт новый persistent workspace в sandbox-сервисе.
        Возвращает workspace_id, который нужно передавать в execute_python_code
        для сохранения файлов между вызовами.

        Используй для многошаговых задач:
        - Сбор данных (шаг 1) → парсинг (шаг 2) → генерация файла (шаг 3)
        - Когда каждый шаг — отдельный вызов execute_python_code,
          но файлы из шага 1 нужны в шаге 3

        Workspace автоматически удаляется через 1 час неактивности.
        Явное удаление — через delete_workspace.

        Возвращает: workspace_id, created_at, path

        НАЗВАНИЕ: НЕЖДУН ФАБРИКА, пустил-вернулся, АВТОНОМНАЯ ФАБРИКА
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
        Возвращает список всех файлов в workspace (включая подпапки).
        Используй чтобы проверить, какие файлы доступны из предыдущих шагов,
        перед запуском следующего execute_python_code.

        Аргументы:
            workspace_id: ID workspace, полученный из create_workspace

        Возвращает: список файлов с путями и размерами, общий размер.
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
        Скачивает конкретный файл из workspace и сохраняет его в
        /app/generated_files для отправки пользователю.

        Используй когда финальный файл создан в workspace и его нужно
        отдать пользователю через send_file_to_user.

        Аргументы:
            workspace_id: ID workspace
            filepath: относительный путь к файлу внутри workspace
                      (например "output/result.xlsx" или "output/data.json")

        Возвращает: путь к сохранённому файлу в /app/generated_files
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
        Удаляет workspace и все файлы в нём.
        Вызывай после завершения многошаговой задачи для освобождения ресурсов.
        Если не вызвать — workspace удалится автоматически через 1 час неактивности.

        Аргументы:
            workspace_id: ID workspace для удаления

        Возвращает: status, workspace_id
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
        Принудительно очищает все просроченные workspace (с истёкшим TTL).
        Вызывай если получил ошибку "достигнут лимит workspace".

        Возвращает: количество удалённых, количество оставшихся.
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
        Вызвать перед генерацией HTML-страниц в современном стиле, дизайном, анимациями и переходами.
        
        ВСЕГДА использовать execute_python_code для поиска текста скиллов, если пользователь запроси красивую презентацию

        СКИЛЛЫ ЛЕЖАТ НЕ ЛОКАЛЬНО, необходимо писать скрипт с помощью execute_python_code, чтобы достать их из удаленной песочницы.
                
        ## 1. Как это устроено в удаленной песочнице

        ```
        /app/
        ├── skills-lock.json          # реестр: версия, список скилов, хэши
        ├── .agents/skills/           # рабочая директория скилов
        │   ├── animate/SKILL.md
        │   ├── animation-vocabulary/SKILL.md
        │   ├── apple-design/SKILL.md
        │   ├── emil-design-eng/SKILL.md
        │   ├── find-animation-opportunities/SKILL.md
        │   ├── improve-animations/SKILL.md
        │   ├── pick-ui-library/SKILL.md
        │   ├── prototype/SKILL.md
        │   └── review-animations/SKILL.md
        └── agent/skills/             # зеркало (те же файлы)
        ```

        ### skills-lock.json

        ---

        ## 2. Как получить содержимое скилов (для докстрингов)

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
                    continue
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
        local_path: str,
        workspace_id: str,
        dest: str = "",
    ) -> dict:
        """
        Заливает локальный файл ИЛИ папку из файловой системы агента
        в удалённый workspace фабрики (обратная операция к download_workspace_file).

        Используй когда нужно перекинуть подготовленный локально артефакт
        (скрипт, ассет, целую папку) в workspace, где он будет доступен для
        execute_python_code / execute_shell_commands с тем же workspace_id.

        Аргументы:
            local_path:   путь к файлу или папке. Разрешены только корни:
                          /sandbox, /app/generated_files, /tmp
            workspace_id: ID целевого workspace (из create_workspace)
            dest:         подпапка назначения внутри workspace, напр. "static/js".
                          Пустая строка — корень workspace.

        Поведение:
            - файл  → заливается как есть в dest/
            - папка → пакуется в zip и распаковывается на стороне фабрики в dest/
                      (структура папки сохраняется)

        Возвращает: {ok, written:[...], count} или {ok:False, error}
        """
        # 1. Валидация пути
        if not _is_under_allowed_root(local_path):
            return str({
                "ok": False,
                "error": f"Путь {local_path} вне разрешённых корней "
                         f"{ALLOWED_UPLOAD_ROOTS}",
            })
        if not os.path.exists(local_path):
            return str({"ok": False, "error": f"Путь не найден: {local_path}"})

        # 2. Готовим payload: файл как есть или папку → zip
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

        # 3. Отправка multipart на фабрику
        files = {"files": (upload_name, content, "application/octet-stream")}
        data = {"dest": dest, "unpack": unpack}
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{SANDBOX_URL}/workspace/{workspace_id}/upload",
                    files=files,
                    data=data,
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
            })
        except httpx.ConnectError:
            return str({"ok": False, "error": "Sandbox-сервис недоступен."})
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        log.info(
            f"[workspace] Залито {upload_name} "
            f"({'папка' if is_dir else 'файл'}, {len(content)} б) "
            f"в {workspace_id}/{dest or '.'}"
        )
        return str({
            "ok": True,
            "source": local_path,
            "kind": "dir" if is_dir else "file",
            "workspace_id": workspace_id,
            "dest": dest or ".",
            "result": result,
        })
    
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
