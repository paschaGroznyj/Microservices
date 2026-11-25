"""Ключи внешних сервисов в профиле: прокси к auth_service /profile/secrets/*.

Общий модуль api_gateway и api_gateway_sigma. user_id берётся только из сессии
(get_user), из тела запроса — провайдер, имя и значение ключа. Значение ключа
gateway не логирует и назад не получает: auth_service отдаёт только last4 и даты.
"""
import logging
from typing import Callable

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_get_client: Callable | None = None
_auth_url: str = ""
_get_user: Callable | None = None

MAX_BODY = 4096


def bind(*, get_client: Callable, auth_url: str, get_user: Callable) -> None:
    global _get_client, _auth_url, _get_user
    _get_client = get_client
    _auth_url = auth_url.rstrip("/")
    _get_user = get_user


async def _user_id(request: Request) -> int:
    user = await _get_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user["user_id"])


async def _body(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(status_code=413, detail="Слишком большой запрос")
    try:
        data = await request.json() if raw else {}
    except ValueError:
        raise HTTPException(status_code=400, detail="bad json")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="bad json")
    return data


async def _call(path: str, payload: dict):
    try:
        r = await _get_client().post(f"{_auth_url}/profile/secrets/{path}", json=payload, timeout=10.0)
    except httpx.RequestError as e:
        logging.warning(f"[secrets_gw] {path}: {type(e).__name__}")
        raise HTTPException(status_code=502, detail="Auth service unavailable")
    try:
        content = r.json()
    except ValueError:
        content = {"detail": "bad upstream response"}
    return JSONResponse(content=content, status_code=r.status_code,
                        headers={"Cache-Control": "no-store"})


def _pick(data: dict) -> dict:
    return {"provider": str(data.get("provider") or "")[:32],
            "name": str(data.get("name") or "default")[:32]}


@router.get("/api/profile/secrets")
async def secrets_list(request: Request):
    return await _call("list", {"user_id": await _user_id(request)})


@router.put("/api/profile/secrets")
async def secrets_set(request: Request):
    uid = await _user_id(request)
    data = await _body(request)
    secret = data.get("secret")
    if not isinstance(secret, str) or not secret.strip():
        raise HTTPException(status_code=400, detail="Пустой ключ")
    return await _call("set", {"user_id": uid, **_pick(data), "secret": secret})


@router.delete("/api/profile/secrets")
async def secrets_delete(request: Request):
    uid = await _user_id(request)
    data = await _body(request)
    return await _call("delete", {"user_id": uid, **_pick(data)})
