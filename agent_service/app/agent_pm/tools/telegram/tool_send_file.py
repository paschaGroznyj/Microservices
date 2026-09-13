"""
tools/telegram/tool_send_file.py
────────────────────────────────────────
Явная отправка уже существующего файла пользователю в Telegram.
Не создаёт файл — только валидирует путь и передаёт его дальше
по тому же контракту saved_files, что и execute_python_code.
"""

import logging
import os
import re
import httpx

from langchain.tools import tool

log = logging.getLogger(__name__)

# Файл может лежать либо в /app/generated_files (результаты execute_python_code),
# либо в /sandbox (черновики write_sandbox_file) — разрешаем оба
ALLOWED_SEND_ROOTS = ["/app/generated_files", "/sandbox"]

MAX_SEND_FILE_SIZE = 20 * 1024 * 1024 


def _valid_send_path(path: str) -> str | None:
    abs_path = os.path.abspath(path)
    if not any(abs_path.startswith(r + os.sep) or abs_path == r for r in ALLOWED_SEND_ROOTS):
        return None
    return abs_path

# Находит пути вида: /tmp/exec/workspaces/<workspace_id>/<filepath>
WORKSPACE_PATH_REGEX = re.compile(
    r".*?/workspaces/(?P<ws_id>[a-f0-9\-]+)/(?P<rel_path>.+)"
)
SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")


def _extract_workspace_info(path: str):
    """Извлекает workspace_id и относительный путь из строки пути."""
    match = WORKSPACE_PATH_REGEX.match(path)
    if match:
        return match.group("ws_id"), match.group("rel_path")
    return None, None


async def _fetch_file_from_sandbox(
    workspace_id: str, filepath: str, local_dest: str
) -> bool:
    """Скачивает файл из эндпоинта песочницы и сохраняет его локально."""
    url = f"{SANDBOX_URL}/workspace/{workspace_id}/file/{filepath}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                os.makedirs(os.path.dirname(local_dest), exist_ok=True)
                with open(local_dest, "wb") as f:
                    f.write(response.content)
                return True
            return False
    except Exception as e:
        print(f"[send_file_to_user] Ошибка при скачивании из sandbox: {e}")
        return False


def make_send_file_tool(agent):

    @tool
    async def send_file_to_user(path: str) -> dict:
        """
        Отправляет пользователю уже существующий файл из песочницы или
        папки сгенерированных файлов. Используй, когда пользователь просит
        прислать/показать/скинуть файл, который был создан ранее
        (например через execute_python_code в этой же или предыдущей сессии).

        Не создаёт новый файл — только помечает существующий на отправку.

        РАБОТА С ФАЙЛАМИ/ПРЕЗАМИ/МЕДИА: если нужно отдать пользователю
        собранный на фабрике артефакт (преза, видео, архив, док):
        создай workspace (create_workspace), собери там файл, положи в output/
        и вызови этот тул с путём
        /tmp/exec/workspaces/{workspace_id}/output/<filename> — файл будет авто-
        подкачан с фабрики и отправлен.
        Если файла не существует, сообщи об этом пользователю вместо повтора попыток.

        Аргументы:
            path: полный путь к файлу, например "/app/generated_files/{run_id}/chart.png"
                  или "/sandbox/draft.py". Обычно берётся из поля "path" в ответе
                  execute_python_code (saved_files) или write_sandbox_file.
            Возможна автоматическая подкачка файлов из Фабрики, для этого нужно задать путь к файлу
            в таком виде: /tmp/exec/workspaces/{workspace_id}/output/<filename>
            Файл автоматически будет сохранен в /app/generated_files/{workspace_id}.
        """
        target = _valid_send_path(path)

        # 2. Если файла нет локально — пробуем подгрузить его из Sandbox
        if target is None or not os.path.isfile(target):
            ws_id, rel_path = _extract_workspace_info(path)
            print(f"ИЗВЛЕЧЕННЫЕ ПУТИ: id={ws_id}, path={rel_path}")
            if ws_id and rel_path:
                download_dir = f"/app/generated_files/{ws_id}"
                filename = os.path.basename(rel_path)
                downloaded_target = os.path.join(download_dir, filename)

                # Запрашиваем файл у сервиса песочницы
                success = await _fetch_file_from_sandbox(
                    workspace_id=ws_id,
                    filepath=rel_path,
                    local_dest=downloaded_target,
                )

                if success:
                    target = downloaded_target
                else:
                    return {
                        "ok": False,
                        "error": f"Файл не найден локально и не удалось скачать из workspace '{ws_id}': {rel_path}",
                    }
            else:
                return {
                    "ok": False,
                    "error": f"Файл не найден локально по пути '{path}'. Проверь путь через list_tree.",
                }

        try:
            size = os.path.getsize(target)
        except OSError as e:
            return {
                "ok": False,
                "error": f"Не удалось получить размер файла: {e}",
            }

        if size > MAX_SEND_FILE_SIZE:
            return {
                "ok": False,
                "error": f"Файл слишком большой для отправки ({size} байт, лимит {MAX_SEND_FILE_SIZE}).",
            }

        return {
            "ok": True,
            "saved_files": [
                {
                    "filename": os.path.basename(target),
                    "path": target,
                    "size_bytes": size,
                }
            ],
        }

    return send_file_to_user