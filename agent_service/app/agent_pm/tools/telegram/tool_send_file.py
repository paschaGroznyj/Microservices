"""
tools/telegram/tool_send_file.py
────────────────────────────────────────
Явная отправка уже существующего файла пользователю в Telegram.
Не создаёт файл — только валидирует путь и передаёт его дальше
по тому же контракту saved_files, что и execute_python_code.
"""

import logging
import os

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


def make_send_file_tool(agent):

    @tool
    async def send_file_to_user(path: str) -> dict:
        """
        Отправляет пользователю уже существующий файл из песочницы или
        папки сгенерированных файлов. Используй, когда пользователь просит
        прислать/показать/скинуть файл, который был создан ранее
        (например через execute_python_code в этой же или предыдущей сессии).

        Не создаёт новый файл — только помечает существующий на отправку.
        Если файла не существует, сообщи об этом пользователю вместо повтора попыток.

        Аргументы:
            path: полный путь к файлу, например "/app/generated_files/{run_id}/chart.png"
                  или "/sandbox/draft.py". Обычно берётся из поля "path" в ответе
                  execute_python_code (saved_files) или write_sandbox_file.
        """
        target = _valid_send_path(path)
        if target is None:
            return {"ok": False, "error": f"Путь '{path}' недоступен для отправки (разрешены только {ALLOWED_SEND_ROOTS})."}

        if not os.path.isfile(target):
            return {"ok": False, "error": f"Файл не найден: {path}. Проверь путь через list_tree или find_files."}

        try:
            size = os.path.getsize(target)
        except OSError as e:
            return {"ok": False, "error": f"Не удалось получить размер файла: {e}"}

        if size > MAX_SEND_FILE_SIZE:
            return {"ok": False, "error": f"Файл слишком большой для отправки ({size} байт, лимит {MAX_SEND_FILE_SIZE})."}

        return {
            "ok": True,
            "saved_files": [{
                "filename": os.path.basename(target),
                "path": target,
                "size_bytes": size,
            }],
        }

    return send_file_to_user