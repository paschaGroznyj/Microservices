"""
Steps API: шаги цикла агента и вызовы тулов по ответам чата (вкладка «Шаги» карточки памяти).

Эндпоинты:
- GET /web/steps/{chat_id}              — последние ответы агента: шаги, вызовы тулов, сводка по тулам
- GET /web/steps/{chat_id}/{message_id} — один ответ целиком

Источник — chats.agent_steps и chats.tool_calls ([{step, name, ok}]), их пишет chats_service
из финального payload ответа. Ответы, сохранённые до появления колонок, не возвращаются.
Видимость по форкам как у истории: chat_chain() с границей точки ветвления.
Авторизация: user_id от гейтвея, владение — та же проверка, что у /web/memory.
"""
import json
import logging
from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.memory_api import _check_ownership
try:
    from agent.core.agent_provider import get_model_label
except ImportError:  # pragma: no cover - запуск из корня репозитория
    from app.agent.core.agent_provider import get_model_label
import api.memory_api as _mem

steps_router = APIRouter()

_VISIBLE = """
    FROM chat_chain($1) ch
    JOIN chats m ON m.chat_id = ch.chat_id
    WHERE (ch.upto IS NULL OR m.id <= ch.upto)
      AND m.is_from_bot = TRUE AND m.agent_steps IS NOT NULL
"""


def _calls(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [c for c in raw if isinstance(c, dict) and c.get("name")] if isinstance(raw, list) else []


def _run(row) -> dict[str, Any]:
    calls = _calls(row["tool_calls"])
    by_step: dict[int, list[dict]] = {}
    for c in calls:
        by_step.setdefault(int(c.get("step") or 0), []).append({"name": c["name"], "ok": c.get("ok") is not False})
    return {
        "id": row["id"],
        "message_id": row["message_id"],
        "chat_id": row["chat_id"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "model": row["model_type"],
        "model_label": get_model_label(row["model_type"]),
        "steps": row["agent_steps"],
        "tool_calls": len(calls),
        "failed": sum(1 for c in calls if c.get("ok") is False),
        "tools": [{"name": n, "count": k} for n, k in Counter(c["name"] for c in calls).most_common()],
        "by_step": [{"step": s, "calls": by_step[s]} for s in sorted(by_step)],
    }


async def _guard(chat_id: str, user_id: int):
    if not _mem._db or not _mem._db.pool:
        raise HTTPException(status_code=503, detail="DB not available")
    if not await _check_ownership(chat_id, user_id):
        raise HTTPException(status_code=403, detail="Access denied")


@steps_router.get("/web/steps/{chat_id}")
async def get_steps(
    chat_id: str,
    user_id: int = Query(..., description="User ID from gateway"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    await _guard(chat_id, user_id)
    async with _mem._db.pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT m.id, m.chat_id, m.message_id, m.created_at, m.model_type, m.agent_steps, m.tool_calls
            {_VISIBLE}
            ORDER BY m.id DESC
            LIMIT $2 OFFSET $3
        """, chat_id, limit, offset)
        agg = await conn.fetchrow(f"""
            SELECT COUNT(*) AS runs, COALESCE(SUM(m.agent_steps), 0) AS steps,
                   COALESCE(SUM(COALESCE(jsonb_array_length(m.tool_calls), 0)), 0) AS tool_calls
            {_VISIBLE}
        """, chat_id)
    logging.debug(f"[steps_api] chat={chat_id} rows={len(rows)}")
    return {
        "runs": [_run(r) for r in rows],
        "total": agg["runs"] or 0,
        "totals": {"steps": int(agg["steps"] or 0), "tool_calls": int(agg["tool_calls"] or 0)},
        "limit": limit,
        "offset": offset,
    }


@steps_router.get("/web/steps/{chat_id}/{message_id}")
async def get_steps_one(
    chat_id: str,
    message_id: str,
    user_id: int = Query(..., description="User ID from gateway"),
) -> dict[str, Any]:
    await _guard(chat_id, user_id)
    async with _mem._db.pool.acquire() as conn:
        row = await conn.fetchrow(f"""
            SELECT m.id, m.chat_id, m.message_id, m.created_at, m.model_type, m.agent_steps, m.tool_calls
            {_VISIBLE} AND m.message_id = $2
            ORDER BY m.id DESC LIMIT 1
        """, chat_id, message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Нет данных о шагах")
    return _run(row)
