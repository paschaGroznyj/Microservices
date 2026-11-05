TOOLS_SETTINGS: dict[str, list[str]] = {
    "internet": [
        "parse_web_page",
        "search_internet",
    ],
    "content": [
        "execute_python_code",
        "edit_and_run",
        "search_code_library",
        "save_to_code_library",
        "send_file_to_user",
        "generate_image",
        "find_generated_image",
        "submit_task",
        "check_task_status",
    ],
    "sessions": [
        # "session_point",
        "work_with_agent_journal",
    ],
    # Admin-only paid media generation via RouterAPI (routerai.ru).
    "media_gen": [
        "generate_video",
        "generate_audio",
        "generate_music",
    ],
    "admin": [
        "get_db_schema",
        "execute_sql_query",
        "edit_sandbox_file", 
        "list_tree",
        "read_file",
        "write_sandbox_file",
        "grep_search",
        "find_files",
        # "push_message_to_telegram",
        # "work_with_email",
        "execute_shell_commands",
        "execute_python_code",
    ],
}

ADMIN_ONLY_CATEGORIES = {"admin"}

# Универсальные тулы — доступны всегда, независимо от выбранных категорий
# (не фильтруются по selected_tools). Пополняем сюда тулы, которые должны
# быть в наборе агента по умолчанию: базовые способности вроде анализа
# изображений, которые не привязаны к конкретному режиму работы.
UNIVERSAL_TOOLS: set[str] = {
    "analyze_image",
    "manage_skills",
    "create_workspace",
    "list_workspace_files",
    "download_workspace_file",
    "upload_to_workspace",
    "read_workspace_file",
    "edit_workspace_file",
    "send_file_to_user",
    "delete_workspace",
    "cleanup_workspaces",
    "send_email",
    "manage_trigger",
}

# Короткие описания инструментов (1 строка на тул) для ФОНОВОГО СУПЕРВИЗОРА.
# Супервизору передаётся каталог ТОЛЬКО тех тулов, что реально доступны
# агенту в текущем запросе (selected_tools + UNIVERSAL_TOOLS + рантайм-тулзы),
# чтобы он понимал, ЧТО умеет агент, и не засорять контекст выключенными.
# pm_research НАМЕРЕННО не описан — категория будет убрана.
TOOL_DESCRIPTIONS: dict[str, str] = {
    # --- internet ---
    "parse_web_page": "Парсит содержимое веб-страницы по URL и возвращает её текст.",
    "search_internet": "Ищет информацию в интернете и возвращает список релевантных ссылок/сниппетов.",
    # --- content ---
    "execute_python_code": "Запускает Python-код в песочнице (расчёты, обработка данных, генерация файлов).",
    "edit_and_run": "Правит и перезапускает ранее выполненный Python-код в песочнице.",
    "search_code_library": "Ищет готовые сниппеты/решения в библиотеке кода.",
    "save_to_code_library": "Сохраняет полезный фрагмент кода в библиотеку для переиспользования.",
    "presentation_design_skill": "Генерирует дизайн/структуру презентации.",
    "html_design_skill": "Генерирует HTML-вёрстку/дизайн страницы.",
    "generate_image": "Генерирует изображение по текстовому описанию.",
    # --- media_gen (admin-only, RouterAPI) ---
    "generate_video": "Генерирует короткое видео по промпту (RouterAPI, админ, платно).",
    "generate_audio": "Синтезирует речь из текста (TTS, RouterAPI, админ, платно).",
    "generate_music": "Генерирует инструментальную/фоновую музыку (mp3) по описанию (Lyria, RouterAPI, админ, платно).",
    # --- sessions ---
    "session_point": "Фиксирует точку сессии (начало/конец диалога) и делает саммари.",
    "work_with_agent_journal": "Ведёт бортовой журнал памяти внутри чата (запомнить/обновить/архив факты).",
    # --- skills ---
    "manage_skills": "Управляет библиотекой скиллов (создать/список/лайк/экспорт/правка).",
    # --- admin ---
    "get_db_schema": "Показывает схему таблиц базы знаний для составления SQL.",
    "execute_sql_query": "Выполняет SQL-запрос к БД (SELECT/INSERT/UPDATE).",
    "edit_sandbox_file": "Точечно правит файл в /sandbox через find&replace.",
    "list_tree": "Показывает дерево директорий и файлов проекта.",
    "read_file": "Читает содержимое файла проекта (/app, /sandbox).",
    "write_sandbox_file": "Записывает файл в /sandbox (черновик-зона).",
    "grep_search": "Ищет текст внутри файлов проекта (grep -rn).",
    "find_files": "Ищет файлы по маске имени (find -iname).",
    "work_with_email": "Работа с почтовым ящиком (чтение inbox, папки, счётчики).",
    "execute_shell_commands": "Выполняет системные команды в песочнице (git/docker/curl без shell).",
    # --- universal (доступны всегда) ---
    "analyze_image": "Анализирует изображение и отвечает на вопрос о нём (OCR, описание, данные с графика).",
    "create_workspace": "Создаёт persistent workspace на фабрике и сразу открывает его в веб-редакторе /editor (возвращает editor_url).",
    "list_workspace_files": "Показывает список файлов в workspace фабрики.",
    "download_workspace_file": "Скачивает файл из workspace в generated_files для отправки пользователю.",
    "upload_to_workspace": "Заливает файлы/сгенерированные картинки в workspace фабрики.",
    "read_workspace_file": "Читает текстовый файл внутри workspace фабрики.",
    "edit_workspace_file": "Точечно правит файл внутри workspace фабрики (find&replace).",
    "send_file_to_user": "Отправляет пользователю один или список существующих файлов из песочницы/generated_files за один вызов.",
    "delete_workspace": "Удаляет workspace и все файлы в нём.",
    "cleanup_workspaces": "Очищает просроченные workspace (при лимите).",
    "submit_task": "Ставит долгую задачу на асинхронное исполнение в песочнице (не блокирует).",
    "check_task_status": "Проверяет статус асинхронных задач в песочнице.",
    "send_email": "Отправляет письмо (текст + вложения до 20МБ) от имени агента.",
    "manage_trigger": "Ставит/снимает собственные Telegram-триггеры (recurring/once) по МСК — агент сам планирует отложенные само-запуски.",
    # --- рантайм-тулзы (аппендятся внутри цикла) ---
    "request_more_steps": "Запрашивает дополнительные шаги в текущем цикле, когда задача почти готова.",
    "call_subagent": "Делегирует подзадачу специализированному субагенту (изолированный LLM-цикл).",
}
