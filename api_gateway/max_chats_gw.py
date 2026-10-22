"""
Гейтвей-роутер управления MAX-чатами (раздел «MAX-чаты» в профиле UI).

Отдельный модуль, чтобы не раздувать app.py (по образцу group_chat_gw).
Проксирует в chats_service /max/* — allow-list чатов + per-chat настройки.
Триггеров/членов у MAX нет (в max_db только max_chats + max_chat_settings).

Маршруты (все требуют авторизации по куке):
  GET    /api/max/chats              → список чатов юзера + настройки
  POST   /api/max/chats              → встроить чат + настройки
  DELETE /api/max/chats/{chat_id}    → деактивировать
  GET    /api/max/settings/{chat_id} → настройки
  POST   /api/max/settings/{chat_id} → обновить настройки

Подключается в app.py:
  max_chats_gw.bind(http_client, chats_url, get_current_user)
  app.include_router(max_chats_gw.router)
"""
import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter()

_http = None
_chats_url = None
_get_current_user = None


def bind(http_client, chats_url, get_current_user):
    global _http, _chats_url, _get_current_user
    _http = http_client
    _chats_url = chats_url
    _get_current_user = get_current_user


class MaxChatUpsert(BaseModel):
    chat_id: str
    system_prompt: Optional[str] = None
    agent_name: Optional[str] = None
    agent_model: Optional[str] = None
    selected_tools: Optional[list] = None
    trigger_mode: Optional[str] = None
    orchestrator_context_n: Optional[int] = None
    proactive_enabled: Optional[bool] = None


class MaxSettingsUpdate(BaseModel):
    system_prompt: Optional[str] = None
    agent_name: Optional[str] = None
    agent_model: Optional[str] = None
    selected_tools: Optional[list] = None
    trigger_mode: Optional[str] = None
    orchestrator_context_n: Optional[int] = None
    proactive_enabled: Optional[bool] = None


async def _require_auth(request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/api/max/chats")
async def api_max_list_chats(request: Request):
    user = await _require_auth(request)
    try:
        resp = await _http.get(
            f"{_chats_url}/max/chats",
            params={"added_by_user_id": user["user_id"]}, timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@router.post("/api/max/chats")
async def api_max_add_chat(request: Request, body: MaxChatUpsert):
    user = await _require_auth(request)
    payload = body.dict()
    payload["added_by_user_id"] = user["user_id"]
    try:
        resp = await _http.post(f"{_chats_url}/max/chats", json=payload, timeout=10.0)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@router.delete("/api/max/chats/{chat_id}")
async def api_max_remove_chat(request: Request, chat_id: str):
    await _require_auth(request)
    try:
        resp = await _http.delete(
            f"{_chats_url}/max/chats/{quote(chat_id, safe='')}", timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@router.get("/api/max/settings/{chat_id}")
async def api_max_get_settings(request: Request, chat_id: str):
    await _require_auth(request)
    try:
        resp = await _http.get(
            f"{_chats_url}/max/settings/{quote(chat_id, safe='')}", timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@router.post("/api/max/settings/{chat_id}")
async def api_max_set_settings(request: Request, chat_id: str, body: MaxSettingsUpdate):
    await _require_auth(request)
    try:
        resp = await _http.post(
            f"{_chats_url}/max/settings/{quote(chat_id, safe='')}",
            json=body.dict(), timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")
