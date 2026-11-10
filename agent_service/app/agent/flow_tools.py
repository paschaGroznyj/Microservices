"""FlowMap: перевод имени тула в обозначения для фронта.

Имя тула (в том числе админских) на фронт не уходит: в событии flow только
слот на карте, человекочитаемая подпись и, если есть, внешний контейнер.
Новый тул без записи здесь показывается как слот "other" / "Инструмент".
"""

# name -> (slot, label[, ext])
TOOL_FLOW: dict[str, tuple] = {
    "search_internet": ("web", "Ищу в интернете", "searxng"),
    "parse_web_page": ("web", "Читаю страницу", "parsers"),
    "execute_python_code": ("sandbox", "Запускаю код", "sandbox_c"),
    "execute_shell_commands": ("sandbox", "Выполняю команды", "sandbox_c"),
    "edit_and_run": ("sandbox", "Правлю и запускаю", "sandbox_c"),
    "create_workspace": ("sandbox", "Готовлю рабочую папку"),
    "list_workspace_files": ("sandbox", "Смотрю файлы"),
    "read_workspace_file": ("sandbox", "Читаю файл"),
    "edit_workspace_file": ("sandbox", "Правлю файл"),
    "upload_to_workspace": ("sandbox", "Загружаю файлы"),
    "download_workspace_file": ("sandbox", "Забираю файл"),
    "delete_workspace": ("sandbox", "Убираю рабочую папку"),
    "cleanup_workspaces": ("sandbox", "Чищу рабочие папки"),
    "write_sandbox_file": ("sandbox", "Пишу файл"),
    "edit_sandbox_file": ("sandbox", "Правлю файл"),
    "read_file": ("sandbox", "Читаю код"),
    "list_tree": ("sandbox", "Смотрю структуру"),
    "grep_search": ("sandbox", "Ищу в коде"),
    "find_files": ("sandbox", "Ищу файлы"),
    "save_to_code_library": ("sandbox", "Сохраняю скрипт"),
    "search_code_library": ("sandbox", "Ищу скрипт"),
    "connect_mcp_git": ("sandbox", "Работаю с git"),
    "generate_image": ("media", "Рисую картинку"),
    "analyze_image": ("media", "Рассматриваю картинку"),
    "generate_video": ("media", "Рендерю видео"),
    "transcribe_media": ("media", "Расшифровываю запись"),
    "generate_audio": ("media", "Озвучиваю"),
    "generate_music": ("media", "Пишу музыку"),
    "find_generated_image": ("media", "Ищу картинку"),
    "execute_sql_query": ("kb", "Смотрю в базе знаний"),
    "get_db_schema": ("kb", "Смотрю схему базы"),
    "work_with_agent_journal": ("kb", "Листаю журнал"),
    "manage_skills": ("kb", "Смотрю скиллы"),
    "session_point": ("kb", "Отмечаю точку сессии"),
    "submit_task": ("tasks", "Ставлю фоновую задачу"),
    "check_task_status": ("tasks", "Проверяю задачу"),
    "cancel_task": ("tasks", "Отменяю задачу"),
    "manage_trigger": ("tasks", "Ставлю напоминание"),
    "send_file_to_user": ("mail", "Отправляю файл"),
    "send_email": ("mail", "Пишу письмо"),
    "call_subagent": ("other", "Зову помощника"),
    "request_more_steps": ("other", "Прошу ещё шагов"),
}

FLOW_SLOTS = {"web", "sandbox", "media", "kb", "tasks", "mail", "other"}
FLOW_EXT = {"searxng", "parsers", "sandbox_c"}


def tool_flow_fields(name: str) -> dict:
    """{'slot', 'label'[, 'ext']} для события tool_start/tool_end. Без имени тула."""
    spec = TOOL_FLOW.get(name or "")
    if not spec:
        return {"slot": "other", "label": "Инструмент"}
    out = {"slot": spec[0], "label": spec[1]}
    if len(spec) > 2:
        out["ext"] = spec[2]
    return out
