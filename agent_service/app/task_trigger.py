"""
task_trigger.py — служебный вход агента после фоновой задачи песочницы.

chats_service (task_consumer) по событию finished кладёт в chat-messages
payload от TASK_TRIGGER_USER_ID с task_trigger=True и снимком задачи.
Сам вход в БД не сохраняется: в истории чата остаётся только ответ агента
и карточка задачи (agent_tasks).

Права на тулы, журнал и навыки берутся от владельца задачи (owner_user_id),
а не от служебного id, поэтому агент работает так же, как в запросе, где
задача ставилась.
"""
TASK_TRIGGER_USER_ID = -2
TASK_TRIGGER_USERNAME = "task-trigger"

STATUS_RU = {"completed": "успешно завершена", "failed": "завершилась с ошибкой",
             "cancelled": "отменена"}


def is_task_trigger(payload: dict) -> bool:
    try:
        uid = int(payload.get("user_id"))
    except (TypeError, ValueError):
        return False
    return bool(payload.get("task_trigger")) and uid == TASK_TRIGGER_USER_ID \
        and not payload.get("is_from_bot")


def payload_tasks(payload: dict) -> list:
    """Все задачи побудки: tasks (пачка) или одиночная task (старый формат)."""
    tasks = payload.get("tasks")
    if isinstance(tasks, list) and tasks:
        return [t for t in tasks if isinstance(t, dict)]
    t = payload.get("task")
    return [t] if isinstance(t, dict) else []


def build_manual_block(tasks) -> str:
    """Ручной триггер: результаты задач, которые пользователь отправил своим сообщением."""
    tasks = [t for t in (tasks or []) if isinstance(t, dict)]
    if not tasks:
        return ""
    lines = [
        "", "",
        f"[ЗАДАЧНИК — пользователь отправил на разбор результаты фоновых задач: {len(tasks)}]",
        "Ты ставил эти задачи через submit_task. Разбери результаты и сообщи итог по каждой: что "
        "получилось, где файлы, что делать дальше. При ошибке найди причину и предложи исправление "
        "или перезапуск. Задачи из этого блока повторно не ставь.",
    ]
    for n, t in enumerate(tasks, 1):
        lines += ["", f"── Задача {n} из {len(tasks)} ──"] + _task_lines(t)
    return "\n".join(lines)


def owner_user_id(payload: dict):
    """Чьими правами работает агент: владелец задачи. Без него вход отбрасывается."""
    tasks = payload_tasks(payload)
    task = tasks[0] if tasks else {}
    uid = payload.get("owner_user_id") or (task.get("owner") or {}).get("user_id")
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def _fmt_ms(ms) -> str:
    s = int((ms or 0) / 1000)
    return f"{s // 60} мин {s % 60} с" if s >= 60 else f"{s} с"


def _task_lines(t: dict) -> list:
    status = t.get("status") or "?"
    lines = [
        f"task_id: {t.get('task_id')}",
        f"Название: {t.get('label')}",
        f"Тип: {t.get('kind')}",
        f"Статус: {status} (exit_code={t.get('exit_code')})",
        f"Время выполнения: {_fmt_ms(t.get('elapsed_ms'))}",
        f"workspace_id: {t.get('workspace_id')}",
    ]
    stages = t.get("stages") or []
    if stages:
        lines.append("Этапы: " + "; ".join(f"{s.get('title')} — {s.get('status')}" for s in stages))
    if t.get("error"):
        lines.append(f"Ошибка: {t['error']}")
    files = t.get("files") or []
    if files:
        lines.append("Файлы в output/: " + ", ".join(
            str(f.get("filename") or f.get("path")) for f in files[:20]))
    if t.get("stderr_tail"):
        lines += ["", "stderr (хвост):", t["stderr_tail"]]
    if t.get("stdout_tail"):
        lines += ["", "stdout (хвост):", t["stdout_tail"]]
    return lines


def build_message(payload: dict) -> str:
    tasks = payload_tasks(payload)
    head = f"[TASK TRIGGER — служебное сообщение планировщика, chat_id: {payload.get('chat_id')}]"
    tail = ("Карточки задач уже показаны в чате, не пересказывай их поля. Задачи из этого "
            "сообщения повторно не ставь: они уже выполнены.")
    if len(tasks) <= 1:
        t = tasks[0] if tasks else {}
        status = t.get("status") or "?"
        lines = [
            head,
            "Это не сообщение пользователя. Фоновая задача, которую ты поставил через submit_task, "
            f"{STATUS_RU.get(status, status)}. Разбери результат и коротко сообщи пользователю итог: "
            "что получилось, где файлы, что делать дальше. При ошибке найди причину и предложи "
            "исправление или перезапуск. " + tail,
            "",
        ] + _task_lines(t)
        return "\n".join(lines)
    ok = sum(1 for t in tasks if t.get("status") == "completed")
    lines = [
        head,
        f"Это не сообщение пользователя. Завершились фоновые задачи, которые ты поставил через "
        f"submit_task: {len(tasks)} (успешно {ok}, с ошибкой {len(tasks) - ok}). Разбери результаты "
        "вместе и одним ответом сообщи пользователю итог по каждой: что получилось, где файлы, что "
        "делать дальше. При ошибке найди причину и предложи исправление или перезапуск. " + tail,
    ]
    for n, t in enumerate(tasks, 1):
        lines += ["", f"── Задача {n} из {len(tasks)} ──"] + _task_lines(t)
    return "\n".join(lines)
