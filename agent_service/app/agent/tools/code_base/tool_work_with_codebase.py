
import json
import logging
import os
from langchain_core.tools import tool

from agent.core import sandbox_scripts_cleaner as _ssc

def make_work_codebase_tool(agent):
    @tool
    async def save_to_code_library(json_description: str) -> str:
        """
        Documents a reusable script in the code_library table.

        The agent calls ONLY after the successful execution of a script with reusable=True.
        Before insertion, the tool dynamically checks the table schema (without get_db_schema).

        The json_description parameter is a JSON string with the following fields:
        title       (str, required) — a brief description of the script
        description (str, required) — what it does, input parameters, output result
        code_type   (str, required) — parser|scraper|excel|pptx|digest|generator|util|migration|core
        tags        (list[str])       — search tags
        author      (str)             — agent|human|human:pasha (default: "agent")
        path        (str, required) — full path /sandbox/reusable/{uuid}.py — saved_script_path from execute_python_code(reusable=True); the file is deleted after 30 min unless saved here
        run_id      (str, optional) — UUID from execute_python_code

        Returns: a message with the ID of the created record.
        """
        try:
            data = json.loads(json_description)
        except json.JSONDecodeError as e:
            return f"Ошибка парсинга JSON: {e}. Проверь формат json_description."

        required = ["title", "description", "code_type", "path"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return f"Отсутствуют обязательные поля: {missing}"

        # Долго живут только скрипты из reusable/, записанные в code_library.
        # Остальные пути (executed_scripts, generated_files) чистятся по TTL,
        # запись в библиотеке на них быстро стала бы битой.
        reusable_root = os.path.realpath(str(_ssc.REUSABLE_DIR))
        real_path = os.path.realpath(str(data["path"]))
        if not real_path.startswith(reusable_root + os.sep) or not os.path.isfile(real_path):
            return (
                f"path={data['path']!r} не подходит: нужен существующий файл в "
                f"{_ssc.REUSABLE_DIR}/. Запусти скрипт через execute_python_code(reusable=True) "
                f"и передай saved_script_path из ответа. Без записи в библиотеку файл "
                f"удаляется через 30 минут."
            )
        data["path"] = real_path

        try:
            record_id = await agent.db.save_to_code_library(
                title=data["title"],
                description=data["description"],
                code_type=data["code_type"],
                tags=data.get("tags", []),
                author=data.get("author", "agent"),
                path=data["path"],
                run_id=data.get("run_id"),
            )
            return (
                f"✅ Скрипт сохранён в code_library.\n"
                f"id={record_id}, title={data['title']!r}, path={data['path']}"
            )
        except RuntimeError as e:
            return f"Ошибка таблицы: {e}"
        except Exception as e:
            logging.error("save_to_code_library failed: %s", e)
            return f"Неожиданная ошибка при сохранении: {e}"


    # ── Тулза 2: search_code_library ─────────────────────────────

    @tool
    async def search_code_library(
        query: str,
        code_type: str = None,
        tags: list[str] = None,
        author: str = None,
        path: str = None,
        limit: int = 5,
    ) -> str:
        """
        Search in the script library (BM25 via websearch_to_tsquery).

        The agent MUST call this tool FIRST when any code writing request is made —
        to check whether a ready‑made script already exists.

        Parameters:
        query     — a search query in natural language (for example, “excel job parser”)
        code_type — filter by type: parser|scraper|excel|pptx|digest|generator|util|migration|core
        tags      — filter by tags, for example ["hh", "excel"]
        author    — filter by author: agent|human|human:pasha
        limit — the number of results (default is 5)
        path — search for a script by file path (specify if it is defined in the tool description)

        Returns: a list of found scripts with the following fields: id, title, description,
        code_type, tags, author, path, use_count, score.
        If nothing is found, it explicitly reports this.

        The agent’s decision after receiving the results:
        - Found a suitable one → call edit_and_run(library_id, need_context=...)
        - Didn’t find any → generate a new script via execute_python_code(reusable=True)

        📚 code_library — a mandatory trio:
        search_code_library → execute_python_code(reusable=True) → save_to_code_library

        🔍 BM25 in search_code_library — queries should be a maximum of 2–3 words; longer ones yield 0 results.
        """

        try:
            results = await agent.db.search_code_library(
                query=query,
                code_type=code_type,
                tags=tags,
                author=author,
                path=path,
                limit=limit,
            )
        except Exception as e:
            logging.error("search_code_library failed: %s", e)
            return f"Ошибка поиска по code_library: {e}"

        if not results:
            return (
                f"По запросу {query!r} ничего не найдено в code_library. "
                f"Генерируй новый скрипт."
            )

        lines = [f"Найдено {len(results)} скрипт(ов) по запросу {query!r}:\n"]
        for r in results:
            tags_str = ", ".join(r["tags"]) if r["tags"] else "—"
            used_str = r["last_used_at"] or "никогда"
            lines.append(
                f"• id={r['id']} | {r['title']!r}\n"
                f"  Тип: {r['code_type']} | Автор: {r['author']} | "
                f"Использований: {r['use_count']} | Последнее: {used_str}\n"
                f"  Теги: {tags_str}\n"
                f"  Описание: {r['description']}\n"
                f"  Путь: {r['path']}\n"
                f"  Score BM25: {r['score']:.4f}\n"
            )

        return "\n".join(lines)
    
    return [save_to_code_library, search_code_library]