"""
tools/filesystem/tool_edit_sandbox.py
────────────────────────────────────────
Точечное редактирование файлов в /sandbox через find/replace.
В отличие от write_sandbox_file(mode="write") не требует
пересылать содержимое файла целиком.
"""

import logging
import os

from langchain.tools import tool
from agent_pm.tools.filesystem.tool_write_sandbox import _valid_sandbox_path, SANDBOX_ROOT

log = logging.getLogger(__name__)

MAX_FILE_SIZE_FOR_EDIT = 200_000

MAX_CONTENT_SIZE = 200_000  # 200KB на файл
MAX_FILES_IN_SANDBOX = 50   # защита от захламления


def _edit_file(path: str, old_str: str, new_str: str) -> dict:
    if not os.path.isfile(path):
        return {"error": f"Файл не найден: {path}. Сначала создай его через write_sandbox_file."}

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE_FOR_EDIT:
        return {"error": f"Файл слишком большой для редактирования ({size} байт)"}

    with open(path, encoding="utf-8", errors="replace") as f:
        content = f.read()

    count = content.count(old_str)
    if count == 0:
        return {"error": "old_str не найден в файле. Проверь точное совпадение (пробелы, отступы, переносы строк)."}
    if count > 1:
        return {
            "error": f"old_str встречается {count} раз(а) в файле — замена неоднозначна. "
                     f"Добавь больше контекста в old_str, чтобы совпадение было уникальным."
        }

    new_content = content.replace(old_str, new_str, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return {
        "path": path,
        "old_len": len(old_str),
        "new_len": len(new_str),
        "total_size_after": len(new_content.encode("utf-8")),
    }


def make_sandbox_edit_tool(agent):

    @tool
    async def edit_sandbox_file(path: str, old_str: str, new_str: str) -> str:
        """
        Редактирует файл в /sandbox через точечную замену текста (find & replace).
        Файл должен уже существовать — создай его сначала через write_sandbox_file.

        Аргументы:
            path: относительный путь внутри /sandbox
            old_str: точный фрагмент текста для замены (должен встречаться РОВНО один раз в файле —
                     если встречается несколько раз, добавь больше окружающего контекста
                     в old_str, чтобы он стал уникальным)
            new_str: текст, на который заменить. Пустая строка удаляет old_str.

        Используй для точечных правок вместо перезаписи всего файла — так безопаснее
        и экономичнее по токенам. Сначала прочитай файл через read_file, скопируй
        точный фрагмент для old_str.
        """
        target = _valid_sandbox_path(path)
        if target is None:
            return f"Ошибка: путь '{path}' вне /sandbox."
        result = _edit_file(target, old_str, new_str)
        return str(result)

    return edit_sandbox_file



