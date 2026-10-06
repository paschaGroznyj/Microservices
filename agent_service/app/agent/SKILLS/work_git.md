════════════════════════════════════════════════════════════════
    РЕЖИМ 1 — GitVerse MCP
    ════════════════════════════════════════════════════════════════

    АРХИТЕКТУРА
    -----------
    Клиент лежит в удалённой sandbox:
        /sandbox/reusable/c3187d5881694cc3aa445917b67e7c88.py

    Агент не пишет HTTP-запросы вручную. Вся низкоуровневая логика
    (initialize, session_id, распаковка ответов) инкапсулирована в GitVerseMCPClient.

    ПЕРЕМЕННАЯ ОКРУЖЕНИЯ
    --------------------
        GITVERSE_TOKEN — токен из GitVerse → Settings → Applications → Tokens
        Нужные scope: user, repo, actions (в зависимости от задачи)

    ШАБЛОН ВЫЗОВА
    -------------
        import sys
        sys.path.insert(0, "/sandbox/reusable")
        from c3187d5881694cc3aa445917b67e7c88 import GitVerseMCPClient

        with GitVerseMCPClient() as client:
            result = client.call("get_repository_content", {
                "owner": "pascha_groznyj",
                "repo":  "Microservices",
                "filepath": "agent_service/app.py",
            })
            print(result)

    МЕТОДЫ КЛИЕНТА
    --------------
        .initialize()        — открыть сессию (в with-блоке автоматически)
        .call(tool, args)    — вызвать инструмент, вернуть распарсенный результат
        .list_tools()        — список всех инструментов (41 штука)
        .close()             — закрыть сессию

    ДОСТУПНЫЕ ИНСТРУМЕНТЫ (41, проверено 06.08.2026)
    -------------------------------------------------

    Репозитории:
        get_repository              — мета репо (name, language, updated_at, ...)
        get_repository_content      — файл или листинг директории
                                    ПАРАМЕТР: "filepath" (не "path"!)
                                    Корень: filepath="/"
                                    Листинг: result["Result"]["entries"]
                                    Файл: result["Result"]["content"] (plain utf-8, не base64)
        get_repository_tree         — дерево файлов (параметр "sha", не "ref")
        create_repository           — создать репо
        update_repository           — обновить настройки репо
        fork_repository             — форк
        create_branch               — создать ветку
        list_branches               — список веток
        create_tag                  — создать тег
        create_or_update_file       — создать/обновить файл
                                    SHA обязателен при UPDATE (взять из get_repository_content)
                                    SHA запрещён при CREATE
        delete_file                 — удалить файл
        get_commit                  — коммит по sha
        list_commits                — список коммитов
        star_repository             — добавить в избранное
        unstar_repository           — убрать из избранного
        list_starred_repositories   — список избранных репо

    Pull Requests:
        create_pull_request         — создать PR
        update_pull_request         — обновить PR
        list_pull_requests          — список PR (state: open|closed|all)
        pull_request_read           — детали PR
        list_pull_request_files     — файлы PR
        update_pull_request_branch  — обновить ветку PR
        add_issue_comment           — комментарий к issue/PR

    Issues:
        list_issues                 — список issues
        issue_read                  — детали issue

    Релизы:
        create_release              — создать релиз
        update_release              — обновить релиз
        get_release                 — релиз по ID
        get_release_by_tag          — релиз по тегу
        list_releases               — список релизов

    CI/CD:
        actions_get                 — получить workflow
        actions_list                — список workflow
        trigger_workflow_dispatch   — запустить workflow
        get_workflow_dispatch_inputs — входные параметры workflow
        get_job_logs                — логи job
        get_job_failure_context     — контекст ошибки job

    Пользователи:
        get_my_user_info            — профиль текущего пользователя (scope: user)
        get_user_by_username        — профиль по логину
        search_users                — поиск пользователей
        get_teams                   — список команд организации
        get_team_members            — члены команды

    КРИТИЧЕСКИЕ НЮАНСЫ WAF И ПРОТОКОЛА
    ------------------------------------
    1. mcp-session-id — строго lowercase в заголовке
    ПРАВИЛЬНО:    headers["mcp-session-id"] = session_id
    НЕПРАВИЛЬНО:  headers["Mcp-Session-Id"] → "Invalid session ID"

    2. notifications/initialized — НЕ ВЫЗЫВАТЬ (сервер возвращает 404)

    3. Ответ всегда JSON, не SSE

    4. Ошибки инструмента — HTTP 200 с полем "error"
    Проверять: if "error" in response → raise RuntimeError

    5. httpx недоступен в sandbox → только urllib.request

    6. Markdown-таблицы (|col|col|) и тройные бэктики (```) в content
    блокируются WAF — заменять на plain-text списки

    7. SHA при CREATE — не передавать вообще (даже None)
    SHA при UPDATE — обязателен, брать из get_repository_content заранее

    ИТЕРАЦИЯ ПО СТРУКТУРЕ РЕПО (нет встроенного tree-walk)
    -------------------------------------------------------
    Так как нет git clone — читать структуру нужно вручную:
        1. client.call("get_repository_content", {..., "filepath": "/"})
        → получить entries корня
        2. Для каждой папки повторить вызов с filepath="папка/"
        3. Для чтения файла: filepath="папка/файл.py"

    Для больших репо это медленно. Если нужна вся структура разом
    — использовать РЕЖИМ 2 (GitHub subprocess).

    ОГРАНИЧЕНИЯ GitVerse MCP (важно для выбора режима)
    --------------------------------------------------
    - Нет git clone — нельзя получить весь репо за один вызов
    - Нет git log — история коммитов через list_commits (постранично)
    - Нет grep/search по коду внутри репо
    - Нет создания веток через push — только create_branch отдельным вызовом
    - Только GitVerse, GitHub не поддерживается


    ════════════════════════════════════════════════════════════════
    РЕЖИМ 2 — GitHub (subprocess + Personal Access Token)
    ════════════════════════════════════════════════════════════════

    КОГДА ИСПОЛЬЗОВАТЬ
    ------------------
    - Репозиторий на GitHub (не GitVerse)
    - Нужен полный git clone (вся структура разом)
    - Нужна история коммитов (git log)
    - Нужен grep/поиск по всем файлам репо
    - Нужен быстрый цикл: clone → edit → commit → push

    ПЕРЕМЕННАЯ ОКРУЖЕНИЯ
    --------------------
        GITHUB_TOKEN — Personal Access Token (classic)
        Минимальные scope: repo, workflow, read:user
        Токен кладётся в env Фабрики через Павла (pisoldatkin-2149725)

    АУТЕНТИФИКАЦИЯ В URL
    --------------------
        repo_url = f"https://{token}@github.com/owner/repo.git"
        Это стандартный git credential в URL — токен нигде не логируется.

    ШАБЛОН: ПОЛНЫЙ ЦИКЛ clone → read → edit → push
    -----------------------------------------------
        import subprocess, os

        token    = os.getenv("GITHUB_TOKEN")
        owner    = "paschaGroznyj"
        repo     = "Agentikus"
        clone_to = f"/tmp/{repo}"
        repo_url = f"https://{token}@github.com/{owner}/{repo}.git"

        # 1. Клонируем (shallow для скорости)
        subprocess.run(
            ["git", "clone", "--depth=10", repo_url, clone_to],
            check=True, timeout=60
        )

        # 2. Читаем структуру
        for dirpath, dirnames, filenames in os.walk(clone_to):
            dirnames[:] = [d for d in dirnames
                        if d not in {".git", "__pycache__", "node_modules", ".venv"}]
            level = dirpath.replace(clone_to, "").count(os.sep)
            print("  " * level + os.path.basename(dirpath) + "/")
            for f in filenames:
                size = os.path.getsize(os.path.join(dirpath, f))
                print("  " * (level + 1) + f + f"  [{size} bytes]")

        # 3. История коммитов
        log = subprocess.run(
            ["git", "-C", clone_to, "log", "--oneline", "--all"],
            capture_output=True, text=True
        )
        print(log.stdout)

        # 4. Создаём/редактируем файл
        with open(f"{clone_to}/AGENT_NOTE.md", "w") as fh:
            fh.write("# Изменение от агента\n\nДата: 17.08.2026\n")

        # 5. git identity (нужна для коммита)
        subprocess.run(["git", "-C", clone_to, "config",
                        "user.email", "catchpm-agent@agents.uva-advanced.ru"])
        subprocess.run(["git", "-C", clone_to, "config",
                        "user.name", "CatchPM Agent"])

        # 6. Коммит и пуш
        subprocess.run(["git", "-C", clone_to, "add", "-A"])
        subprocess.run(["git", "-C", clone_to, "commit", "-m", "agent: update note"])
        subprocess.run(["git", "-C", clone_to, "push", repo_url, "main"], check=True)

    ШАБЛОН: ТОЛЬКО ЧТЕНИЕ (без клонирования, через API)
    ----------------------------------------------------
    Для одного файла без клонирования можно через GitHub REST API:

        import urllib.request, json, os

        token = os.getenv("GITHUB_TOKEN")
        owner, repo, path = "paschaGroznyj", "Agentikus", "README.md"
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3.raw",  # raw content
        })
        with urllib.request.urlopen(req) as resp:
            print(resp.read().decode())

    ДОСТУПНЫЕ ОПЕРАЦИИ ЧЕРЕЗ subprocess + git
    ------------------------------------------
        git clone --depth=N          — клонировать (shallow для скорости)
        git log --oneline --all      — история коммитов
        git diff HEAD~1              — diff последнего коммита
        git -C <path> branch -r     — список remote-веток
        git -C <path> checkout -b   — создать новую ветку
        git -C <path> add -A        — добавить все изменения
        git -C <path> commit -m     — коммит
        git -C <path> push url branch — пуш (токен в URL)
        git -C <path> pull           — pull
        grep -rn "pattern" <path>    — поиск по коду (после clone)

    ИЗВЕСТНЫЕ РЕПОЗИТОРИИ
    ---------------------
        paschaGroznyj/Agentikus      — agent_service, вынесен отдельно,
                                    токен в GITHUB_TOKEN работает (проверено 17.08.2026)

    ОГРАНИЧЕНИЯ GitHub subprocess
    ------------------------------
    - Требует GITHUB_TOKEN в env Фабрики (кладёт только Павел)
    - git clone работает в /tmp — файлы живут в рамках одного вызова execute_python_code
    Если нужно между вызовами — использовать Persistent Workspace
    - Интернет в sandbox есть, но только через urllib/requests (не httpx)
    - Docker socket недоступен из sandbox без явного монтирования


    ════════════════════════════════════════════════════════════════
    ВЫБОР РЕЖИМА: КОГДА КАКОЙ
    ════════════════════════════════════════════════════════════════

        Задача                                Режим
        ─────────────────────────────────────────────────────────
        Обновить один файл в GitVerse         GitVerse MCP
        Читать структуру монорепо целиком     GitHub subprocess (clone)
        История коммитов                      GitHub subprocess (git log)
        Grep/поиск по коду                    GitHub subprocess (clone + grep)
        Создать PR / Issue в GitVerse         GitVerse MCP
        CI/CD trigger в GitVerse              GitVerse MCP
        Полный цикл clone→edit→push в GitHub  GitHub subprocess
        Работа с приватным репо GitVerse      GitVerse MCP (GITVERSE_TOKEN)
        Работа с публичным репо GitHub        GitHub subprocess (GITHUB_TOKEN)


    ════════════════════════════════════════════════════════════════
    КОНСТАНТЫ
    ════════════════════════════════════════════════════════════════
    ```
    # GitVerse MCP
    SANDBOX_CLIENT_PATH   = "/sandbox/reusable/c3187d5881694cc3aa445917b67e7c88.py"
    SANDBOX_CLIENT_IMPORT = "c3187d5881694cc3aa445917b67e7c88"
    SANDBOX_CLIENT_CLASS  = "GitVerseMCPClient"
    MCP_ENDPOINT          = "https://mcp.gitverse.ru"
    MCP_PROTOCOL          = "2024-11-05"

    # GitHub
    GITHUB_API_BASE       = "https://api.github.com"
    GITHUB_TOKEN_ENV      = "GITHUB_TOKEN"
    GITHUB_AGENT_EMAIL    = "catchpm-agent@agents.uva-advanced.ru"
    GITHUB_AGENT_NAME     = "CatchPM Agent"
    ```