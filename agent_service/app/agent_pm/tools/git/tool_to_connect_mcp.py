from langchain.tools import tool

@tool
async def connect_mcp_git():
    """
    Инструмент-справка и клиентура для работы с MCP GitVerse через sandbox.

    СХЕМА ИСПОЛЬЗОВАНИЯ АГЕНТОМ
    --------------------------
    Этот файл НЕ вызывается напрямую как Python-модуль.
    Агент читает этот docstring, генерирует Python-скрипт и выполняет его 
    через `execute_python_code()`.

    Скрипт импортирует клиентуру `GitVerseMCPClient` из `/app/dev_utils/mcp_git.py` 
    (все низкоуровневые HTTP-детали и MCP-сессии уже инкапсулированы внутри класса).

    ПРИМЕР ИСПОЛЬЗОВАНИЯ В EXECUTE_PYTHON_CODE
    -------------------------------------------
    ```python
    import sys
    sys.path.insert(0, "/app/dev_utils")
    from mcp_git import GitVerseMCPClient

    with GitVerseMCPClient() as client:
        # Список доступных инструментов
        tools = client.list_tools()
        
        # Вызов любого MCP-инструмента
        user_info = client.call("get_my_user_info")
        print(user_info)
    ```

    ИНТЕРФЕЙС КЛИЕНТА (GitVerseMCPClient)
    -------------------------------------
    • .initialize()      — открыть сессию (в `with`-блоке вызывается автоматически)
    • .call(tool, args)  — вызвать инструмент, вернуть распарсенный результат
    • .list_tools()      — вернуть список всех доступных MCP-инструментов
    • .close()           — завершить работу и сбросить session_id

    КРИТИЧЕСКИЕ ПРАВИЛА WAF И КАТЕГОРИЧЕСКИЕ ОГРАНИЧЕНИЯ (ПРИОРИТЕТ 1)
    ----------------------------------------------------------------
    При вызове `create_or_update_file` и работе с контентом:

    1. SHA при ОБНОВЛЕНИИ обязателен:
       Перед `update` файла ОБЯЗАТЕЛЬНО вызвать `get_repository_content` и взять 
       `sha` из `result.get("Result", {}).get("sha")`. Без `sha` WAF режет запрос (HTTP 403).

    2. SHA при СОЗДАНИИ запрещён:
       Если файл создаётся с нуля — параметр `sha` НЕ передавать вообще (не `None`, не `""`).

    3. НИКАКИХ Markdown-таблиц (`|col|col|`):
       WAF GitVerse моментально блокирует запросы с таблицами. Заменять на списки (`- Ключ: Значение`).

    4. НИКАКИХ Кодблоков (тройных бэктиков ```):
       Символы тройных бэктиков в `content` провоцируют блокировку WAF. Структуры кода или директорий писать plain-текстом с отступами.

    5. Ограничение на размер контента:
       Большой контент (>3KB) с таблицами или кодблоками режется WAF. При соблюдении правил 3 и 4 проходит любой объем.

    6. Кодировка контента:
       Передавать обычный Plain Text. Ручное base64-кодирование НЕ ТРЕБУЕТСЯ (клиент кодирует сам).

    ТЕХНИЧЕСКИЕ И API НЮАНСЫ
    -----------------------
    • Имя параметра пути: В `get_repository_content` использовать параметр `filepath` (НЕ `path`). 
      Корень репозитория: `filepath="/"`.
    • Заголовки: Заголовок `mcp-session-id` строго в lowercase.
    • Инициализация: Метод `notifications/initialized` НЕ вызывается (сервер выдаст 404).
    • Окружение: Использовать `urllib.request` (модуль `httpx` в sandbox недоступен).
    • Токен: Требуется `GITVERSE_TOKEN` в окружении sandbox. Права регулируются scope:
      - `user`    → для `get_my_user_info`
      - `repo`    → для `get_repository`, `create_or_update_file`
      - `actions` → для `trigger_workflow`

    ПРИМЕР ПРАВИЛЬНОГО ПУША / ОБНОВЛЕНИЯ ФАЙЛА
    ------------------------------------------
    ```python
    import sys
    sys.path.insert(0, "/app/dev_utils")
    from mcp_git import GitVerseMCPClient

    with GitVerseMCPClient() as client:
        owner = "pascha_groznyj"
        repo = "Microservices"
        filepath = "service/README.md"
        
        # 1. Проверяем существование файла и берем SHA
        file_sha = None
        try:
            res = client.call("get_repository_content", {"owner": owner, "repo": repo, "filepath": filepath})
            file_sha = res.get("Result", {}).get("sha")
        except Exception:
            pass # Файла нет, создаем новый

        # 2. Формируем безопасный текст (БЕЗ таблиц '|' и БЕЗ тройных бэктиков)
        safe_content = (
            "# Описание сервиса\n\n"
            "Параметры конфигурации:\n"
            "- Port: 8080\n"
            "- Env: Production\n"
        )

        # 3. Собираем payload
        payload = {
            "owner": owner,
            "repo": repo,
            "filepath": filepath,
            "content": safe_content,
            "message": "docs: update readme"
        }
        if file_sha:
            payload["sha"] = file_sha  # Передаем sha ТОЛЬКО при обновлении

        # 4. Вызываем инструмент
        result = client.call("create_or_update_file", payload)
        print(result)
    ```
    """