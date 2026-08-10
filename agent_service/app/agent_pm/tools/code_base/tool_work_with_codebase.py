
import json
import logging
from langchain_core.tools import tool

def make_work_codebase_tool(agent):
    @tool
    async def save_to_code_library(json_description: str) -> str:
        """
        Документирует переиспользуемый скрипт в таблицу code_library.

        Агент вызывает ТОЛЬКО после успешного выполнения скрипта с reusable=True.
        Перед вставкой тулза динамически проверяет схему таблицы (без get_db_schema).

        Параметр json_description — JSON-строка со следующими полями:
        title       (str, обязателен) — краткое название скрипта
        description (str, обязателен) — что делает, входные параметры, выходной результат
        code_type   (str, обязателен) — parser|scraper|excel|pptx|digest|generator|util|migration|core
        tags        (list[str])       — теги для поиска
        author      (str)             — agent|human|human:паша (по умолчанию "agent")
        path        (str, обязателен) — полный путь /sandbox/reusable/{uuid}.py
        run_id      (str, опционально) — UUID из execute_python_code

        Возвращает: сообщение с id созданной записи.
        """
        try:
            data = json.loads(json_description)
        except json.JSONDecodeError as e:
            return f"Ошибка парсинга JSON: {e}. Проверь формат json_description."

        required = ["title", "description", "code_type", "path"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return f"Отсутствуют обязательные поля: {missing}"

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
        Поиск по библиотеке скриптов (BM25 через websearch_to_tsquery).

        Агент ОБЯЗАН вызывать этот инструмент ПЕРВЫМ при любом запросе на написание кода —
        чтобы проверить, есть ли уже готовый скрипт.

        Параметры:
        query     — поисковый запрос на естественном языке (например "парсер вакансий excel")
        code_type — фильтр по типу: parser|scraper|excel|pptx|digest|generator|util|migration|core
        tags      — фильтр по тегам, например ["hh", "excel"]
        author    — фильтр по автору: agent|human|human:паша
        limit     — количество результатов (по умолчанию 5)
        path - поиск скрипта по пути к файлу (указывать, если он задан в описании тула)

        Возвращает: список найденных скриптов с полями id, title, description,
        code_type, tags, author, path, use_count, score.
        Если ничего не найдено — сообщает об этом явно.

        Решение агента после получения результатов:
        - Нашёл подходящий → вызвать edit_and_run(library_id, need_context=...)
        - Не нашёл → генерировать новый скрипт через execute_python_code(reusable=True)

        📚 code_library — обязательная тройка:
        search_code_library → execute_python_code(reusable=True) → save_to_code_library

        🔍 BM25 в search_code_library — запросы максимум 2–3 слова, длинные дают 0 результатов
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