"""
Память чата в UI (только просмотр): прокси к agent_service /web/memory/*.

Один и тот же модуль в api_gateway и api_gateway_sigma.
bind(get_client=..., agent_url=..., get_user=...) вызывается из app.py:
http_client создаётся на старте приложения, поэтому берём его через функцию.
"""
import logging
from typing import Callable

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()

_get_client: Callable | None = None
_agent_url: str = ""
_get_user: Callable | None = None


def bind(*, get_client: Callable, agent_url: str, get_user: Callable) -> None:
    global _get_client, _agent_url, _get_user
    _get_client = get_client
    _agent_url = agent_url.rstrip("/")
    _get_user = get_user


async def _proxy(request: Request, chat_id: str, tail: str, params: dict | None = None):
    user = await _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not chat_id or len(chat_id) > 128:
        raise HTTPException(status_code=400, detail="bad chat_id")
    q = {"user_id": user["user_id"], **(params or {})}
    try:
        r = await _get_client().get(f"{_agent_url}/web/memory/{chat_id}/{tail}", params=q, timeout=10.0)
    except httpx.RequestError as e:
        logging.warning(f"[memory_gw] {tail} {chat_id}: {e}")
        raise HTTPException(status_code=502, detail="Agent service unreachable")
    if r.status_code in (403, 404):
        raise HTTPException(status_code=r.status_code, detail="Нет доступа к чату")
    if r.status_code >= 400:
        logging.warning(f"[memory_gw] {tail} {chat_id}: HTTP {r.status_code}")
        raise HTTPException(status_code=502, detail="Agent service error")
    return r.json()


@router.get("/api/chats/{chat_id}/memory/stats")
async def memory_stats(chat_id: str, request: Request):
    return await _proxy(request, chat_id, "stats")


@router.get("/api/chats/{chat_id}/memory/journal")
async def memory_journal(chat_id: str, request: Request,
                         limit: int = Query(20, ge=1, le=100),
                         offset: int = Query(0, ge=0)):
    return await _proxy(request, chat_id, "journal", {"limit": limit, "offset": offset})


@router.get("/api/chats/{chat_id}/memory/summary")
async def memory_summary(chat_id: str, request: Request):
    return await _proxy(request, chat_id, "summary")
