"""
task_gw.py — задачник в гейтвее: события фоновых задач и служебный вход агента.

Модуль одинаковый в api_gateway и api_gateway_sigma (копия: у гейтвеев разные
контексты сборки). Отличается только транспорт, гейтвей передаёт его в bind():
  push(chat_id, event)               доставка на фронт: WS (main) или store для опроса (sigma)
  register(request_id, chat_id)      связь request_id -> чат для /internal/bot_status и bot_stream
  mark_running(chat_id, request_id)  флаг «агент в работе», блокирует кнопку отправки

Вызывает chats_service (app/task_consumer.py):
  POST /internal/task_event    {"chat_id", "task"}                          -> событие task_update
  POST /internal/task_trigger  {"chat_id", "request_id", "task_id", "label"} -> агент проверяет задачу
task_update, в отличие от bot_message, не снимает флаг «агент в работе».
Хуки могут быть синхронными или async.
"""
import inspect
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()
_hooks: dict = {}


def bind(*, push, register=None, mark_running=None) -> None:
    _hooks.clear()
    _hooks.update(push=push, register=register, mark_running=mark_running)


async def _call(name, *args):
    fn = _hooks.get(name)
    if fn is None:
        return None
    res = fn(*args)
    if inspect.isawaitable(res):
        res = await res
    return res


class TaskEventIn(BaseModel):
    chat_id: str
    task: dict


class TaskTriggerIn(BaseModel):
    chat_id: str
    request_id: str
    task_id: str | None = None
    label: str | None = None


def task_update_event(chat_id: str, task: dict) -> dict:
    return {"type": "task_update", "chat_id": chat_id, "task": task}


def _require_bound():
    if _hooks.get("push") is None:
        raise HTTPException(status_code=503, detail="task_gw not bound")


@router.post("/internal/task_event")
async def task_event(body: TaskEventIn):
    _require_bound()
    await _call("push", body.chat_id, task_update_event(body.chat_id, body.task))
    return {"ok": True}


@router.post("/internal/task_trigger")
async def task_trigger(body: TaskTriggerIn):
    _require_bound()
    await _call("register", body.request_id, body.chat_id)
    await _call("mark_running", body.chat_id, body.request_id)
    status = f"Проверяю результат задачи «{body.label}»" if body.label else "Проверяю результат задачи"
    await _call("push", body.chat_id, {
        "type": "agent_status", "status": status, "task_trigger": True,
        "task_id": body.task_id, "request_id": body.request_id,
    })
    log.info(f"[task_gw] триггер агента chat={body.chat_id} task={body.task_id} req={body.request_id}")
    return {"ok": True}
