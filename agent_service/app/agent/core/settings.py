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
        "transcribe_media",
        "submit_task",
        "check_task_status",
        "cancel_task",
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
    "transcribe_media": "Дословно расшифровывает аудио и видео с таймкодами и спикерами (gemini-3.1-pro), сохраняет .txt/.srt/.json.",
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
    "cancel_task": "Отменяет фоновую задачу песочницы по просьбе пользователя.",
    "send_email": "Отправляет письмо (текст + вложения до 20МБ) от имени агента.",
    "manage_trigger": "Ставит/снимает собственные Telegram-триггеры (recurring/once) по МСК — агент сам планирует отложенные само-запуски.",
    # --- рантайм-тулзы (аппендятся внутри цикла) ---
    "request_more_steps": "Запрашивает дополнительные шаги в текущем цикле, когда задача почти готова.",
    "call_subagent": "Делегирует подзадачу специализированному субагенту (изолированный LLM-цикл).",
}


# Рантайм-тулзы: аппендятся внутри цикла (не выбираются в UI), но тоже
# попадают в tool_trace.
RUNTIME_TOOLS: set[str] = {"request_more_steps", "call_subagent"}

# ── Что сохранять в tool_trace (память о вызовах тулов между ходами) ──────
# Одна точка правды для всех тулов из TOOLS_SETTINGS / UNIVERSAL_TOOLS /
# RUNTIME_TOOLS / TOOL_DESCRIPTIONS. Логика — agent/common/tool_trace.py.
#
# Поля политики:
#   skip        — True: вызов не пишется в trace вообще.
#   formatter   — имя спец-форматтера из tool_trace.FORMATTERS
#                 (если не задан — общий: «name(args) → result»).
#   args        — кортеж аргументов, которые писать (None — все).
#   arg_limits  — лимит символов на конкретный аргумент (иначе TRACE_ARG_LIMIT).
#   result      — лимит символов результата (0 — результат не пишем,
#                 None — без обрезки).
# Тул, которого нет в политике, получает TRACE_DEFAULT_POLICY и warning в лог,
# а не теряется молча, как раньше со STATEFUL_TOOLS.
TRACE_ARG_LIMIT = 300
TRACE_DEFAULT_POLICY: dict = {"args": None, "result": 1000}

TOOL_TRACE_POLICY: dict[str, dict] = {
    # --- internet ---
    # Поисковая выдача: только строки [S1]..[Sn] (заголовок + URL).
    "search_internet": {"formatter": "search_internet"},
    # Ссылки целиком, из текста страницы — начало (сам текст можно перечитать).
    "parse_web_page": {"args": ("urls", "chunk_indices"), "result": 1000},

    # --- content ---
    # Успех — только файлы/скрипт; ошибка — код + текст ошибки.
    "execute_python_code": {"formatter": "python_run"},
    "edit_and_run": {"formatter": "python_run"},
    "search_code_library": {"args": ("query",), "result": 600},
    # json_description — это весь код сниппета, в trace только id/статус.
    "save_to_code_library": {"args": (), "result": 300},
    "send_file_to_user": {"args": ("paths", "path", "image_keys", "image_key"), "result": 400},
    # image_key в результате нужен для последующих правок/отправки.
    "generate_image": {"formatter": "generate_image"},
    "find_generated_image": {"formatter": "find_generated_image"},
    # Транскрипт: начало текста + пути .txt/.srt/.json.
    "transcribe_media": {"formatter": "transcribe_media"},
    # Код задачи большой и уже есть в вызове — пишем workspace и task_id.
    "submit_task": {"args": ("workspace_id", "title", "timeout"), "result": 500},
    "check_task_status": {"args": ("task_id", "workspace_id"), "result": 800},
    "cancel_task": {"args": ("task_id",), "result": 300},

    # --- sessions ---
    "work_with_agent_journal": {
        "args": ("action", "entry_type", "entry_id", "replace", "content", "archive_summary"),
        "arg_limits": {"content": 300, "replace": 150},
        "result": 300,
    },
    "session_point": {"result": 300},

    # --- media_gen: промпт + пути/URL готового файла ---
    "generate_video": {
        "args": ("prompt", "provider", "aspect_ratio", "duration", "resolution"),
        "result": 800,
    },
    "generate_audio": {"args": ("text", "voice", "style"), "arg_limits": {"text": 200}, "result": 600},
    "generate_music": {"args": ("prompt",), "result": 600},

    # --- admin ---
    # Схему можно перезапросить, в trace только начало.
    "get_db_schema": {"args": (), "result": 400},
    "execute_sql_query": {"args": ("sql",), "arg_limits": {"sql": 500}, "result": 800},
    "list_tree": {"result": 1000},
    "read_file": {"result": 1000},
    "grep_search": {"result": 1000},
    "find_files": {"result": 1000},
    # Содержимое файла не дублируем — путь и что записано.
    "write_sandbox_file": {"args": ("path", "mode", "content"), "arg_limits": {"content": 200}, "result": 300},
    "edit_sandbox_file": {"args": ("path", "old_str", "new_str"), "arg_limits": {"old_str": 200, "new_str": 200}, "result": 300},
    "work_with_email": {"result": 800},
    "execute_shell_commands": {"formatter": "shell"},

    # --- universal ---
    # Что спрашивали у vision-модели и её ответ — агент помнит картинку.
    "analyze_image": {"formatter": "analyze_image"},
    "manage_skills": {
        "args": ("action", "skill_id", "theme", "query", "old_str", "new_str"),
        "arg_limits": {"old_str": 150, "new_str": 150},
        "result": 600,
    },
    "create_workspace": {"result": 400},
    "list_workspace_files": {"result": 800},
    "download_workspace_file": {"result": 400},
    "upload_to_workspace": {"result": 500},
    "read_workspace_file": {"result": 1000},
    "edit_workspace_file": {
        "args": ("workspace_id", "path", "old_str", "new_str"),
        "arg_limits": {"old_str": 200, "new_str": 200},
        "result": 300,
    },
    "delete_workspace": {"result": 200},
    "cleanup_workspaces": {"result": 200},
    "send_email": {
        "args": ("to", "subject", "body", "attachment_paths"),
        "arg_limits": {"body": 300},
        "result": 300,
    },
    "manage_trigger": {"result": 400},

    # --- design skills: возвращают длинный гайд, в trace — факт вызова ---
    "presentation_design_skill": {"result": 200},
    "html_design_skill": {"result": 200},

    # --- runtime ---
    "request_more_steps": {"skip": True},  # служебный шаг-буст цикла
    # Ответ субагента — единственный носитель его работы, пишем целиком.
    "call_subagent": {"args": ("agent_name", "task"), "arg_limits": {"task": 800}, "result": None},
}
