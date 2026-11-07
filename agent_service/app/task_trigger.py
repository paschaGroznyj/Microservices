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


def owner_user_id(payload: dict):
    """Чьими правами работает агент: владелец задачи. Без него вход отбрасывается."""
    task = payload.get("task") or {}
    uid = payload.get("owner_user_id") or (task.get("owner") or {}).get("user_id")
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def _fmt_ms(ms) -> str:
    s = int((ms or 0) / 1000)
    return f"{s // 60} мин {s % 60} с" if s >= 60 else f"{s} с"


def build_message(payload: dict) -> str:
    t = payload.get("task") or {}
    status = t.get("status") or "?"
    lines = [
        f"[TASK TRIGGER — служебное сообщение планировщика, chat_id: {payload.get('chat_id')}]",
        "Это не сообщение пользователя. Фоновая задача, которую ты поставил через submit_task, "
        f"{STATUS_RU.get(status, status)}. Разбери результат и коротко сообщи пользователю итог: "
        "что получилось, где файлы, что делать дальше. При ошибке найди причину и предложи "
        "исправление или перезапуск. Карточка задачи уже показана в чате, не пересказывай её поля.",
        "",
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
    return "\n".join(lines)
