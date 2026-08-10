from langchain.tools import tool

@tool
async def connect_mcp_git():
    """
        АРХИТЕКТУРА
    -----------
    Этот файл — тонкая обёртка над клиентом, который лежит в удалённой sandbox:
        /app/dev_utils/mcp_git.py

    Агент не пишет HTTP-запросы здесь. Вся низкоуровневая логика (initialize,
    session_id, распаковка ответов) инкапсулирована в GitVerseMCPClient.

    КАК АГЕНТ ИСПОЛЬЗУЕТ ЭТОТ ТУЛ
    ------------------------------
    1. Агент вызывает execute_python_code() со скриптом, который:
    a) импортирует GitVerseMCPClient из sandbox/reusable
    b) открывает сессию через client.initialize()
    c) вызывает нужные инструменты через client.call(...)
    d) возвращает результат через print()

    Пример вызова агентом:

        import sys
        sys.path.insert(0, "/app/dev_utils")
        from mcp_git import GitVerseMCPClient

        with GitVerseMCPClient() as client:
            info = client.call("get_my_user_info")
            print(info)

    ПЕРЕМЕННАЯ ОКРУЖЕНИЯ
    --------------------
        GITVERSE_TOKEN — токен из GitVerse → Settings → Applications → Tokens
        Токен должен быть положен в удалённую sandbox перед использованием.

    КРИТИЧЕСКИЕ НЮАНСЫ (выяснены при отладке)
    -----------------------------------------
    1. mcp-session-id — строго lowercase в заголовке
    ПРАВИЛЬНО : headers["mcp-session-id"] = session_id
    НЕПРАВИЛЬНО: headers["Mcp-Session-Id"] → "Invalid session ID"

    2. notifications/initialized — НЕ ВЫЗЫВАТЬ (сервер возвращает 404)

    3. Ответ всегда JSON, не SSE
    Content-Type: application/json — ветка _parse_sse не нужна

    4. Ошибки инструмента — HTTP 200 с полем "error"
    Проверять: if "error" in response → raise RuntimeError

    5. httpx недоступен в sandbox → использовать только urllib.request

    6. get_repository_content: параметр "filepath", НЕ "path"
    Корень директории: filepath="/"

    7. Scope токена влияет на доступность инструментов:
    get_my_user_info  → нужен scope "user"
    get_repository    → нужен scope "repo"
    trigger_workflow  → нужен scope "actions"
    Если 403 — перевыпустить токен с нужным scope в GitVerse → Settings → Tokens

    СХЕМА TУЛ-ВЫЗОВА ДЛЯ АГЕНТА
    -----------------------------
    Агент не вызывает этот файл напрямую как Python-модуль.
    Вместо этого агент:

        1. Читает описание из этого файла (инструменты, нюансы, параметры)
        2. Генерирует скрипт с импортом GitVerseMCPClient из dev_utils
        3. Передаёт скрипт в execute_python_code()


    Класс    : GitVerseMCPClient
    Методы   :
        .initialize()              → открыть сессию (вызывается автоматически в with-блоке)
        .call(tool, args)          → вызвать инструмент, вернуть распарсенный результат
        .list_tools()              → список всех инструментов
        .close()                   → сбросить session_id
    """