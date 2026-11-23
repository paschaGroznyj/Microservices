"""
Вложения чата для правой панели (attachments-panel.js).

  GET /api/chats/{chat_id}/attachments     -> chats_service /chat/{chat_id}/attachments
  GET /api/chats/{chat_id}/thumb/{file_id} -> chats_service /chat/{chat_id}/thumb/{file_id}
      превью 384 px (WebP) для сетки «Медиа»; file_id = chat_files.id из списка вложений.

Общий модуль api_gateway и api_gateway_sigma (gw_shared/, см. gw_shared/__init__.py).
bind(...) вызывается из app.py: http_client создаётся на старте, берём его через функцию.

Превью делает и хранит chats_service (таблица file_thumbs, core/file_thumbs.py): один раз
при записи файла, для старых файлов при первом запросе. Gateway только проксирует ответ
с проверкой авторизации, оригиналы не качает и картинки не уменьшает.
"""
import logging
from typing import Callable

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

router = APIRouter()

_get_client: Callable | None = None
_chats_url = ""
_get_user: Callable | None = None

THUMB_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}


def bind(*, get_client: Callable, chats_url: str, get_user: Callable) -> None:
    global _get_client, _chats_url, _get_user
    _get_client = get_client
    _chats_url = chats_url.rstrip("/")
    _get_user = get_user


def _check_part(v: str, what: str, n: int = 256) -> None:
    if not v or len(v) > n or "/" in v or "\\" in v or v in (".", ".."):
        raise HTTPException(status_code=400, detail=f"bad {what}")


async def _user(request: Request) -> dict:
    user = await _get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@router.get("/api/chats/{chat_id}/attachments")
async def chat_attachments(chat_id: str, request: Request,
                           tab: str = Query("media", pattern="^(media|files|links)$"),
                           author: str = Query("all", pattern="^(all|me|agent)$"),
                           cursor: str | None = Query(None, max_length=32),
                           limit: int = Query(30, ge=1, le=60)):
    user = await _user(request)
    _check_part(chat_id, "chat_id", 128)
    params = {"tab": tab, "author": author, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    try:
        r = await _get_client().get(f"{_chats_url}/chat/{chat_id}/attachments", params=params,
                                    headers={"X-User-Name": user["username"]}, timeout=10.0)
    except httpx.RequestError as e:
        logging.warning(f"[attachments_gw] {chat_id}: {e}")
        raise HTTPException(status_code=502, detail="Chats service unreachable")
    if r.status_code in (400, 404):
        raise HTTPException(status_code=r.status_code, detail="Чат не найден" if r.status_code == 404 else "Неверный запрос")
    if r.status_code >= 400:
        logging.warning(f"[attachments_gw] {chat_id}: HTTP {r.status_code}")
        raise HTTPException(status_code=502, detail="Chats service error")
    return Response(content=r.content, media_type="application/json",
                    headers={"Cache-Control": "no-store"})


async def proxy_thumb(url: str, *, params: dict | None = None, headers: dict | None = None,
                      tag: str = "attachments_gw") -> Response:
    """Готовое превью из chats_service. 404 — превью нет (фронт возьмёт оригинал)."""
    try:
        r = await _get_client().get(url, params=params, headers=headers, timeout=20.0)
    except httpx.RequestError as e:
        logging.warning(f"[{tag}] thumb {url}: {e}")
        raise HTTPException(status_code=502, detail="Chats service unreachable")
    if r.status_code in (400, 404):
        raise HTTPException(status_code=r.status_code, detail="Image not found" if r.status_code == 404 else "Bad request")
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not r.content or not ctype.startswith("image/"):
        logging.warning(f"[{tag}] thumb {url}: HTTP {r.status_code}")
        raise HTTPException(status_code=502, detail="Chats service error")
    return Response(content=r.content, media_type=ctype, headers=THUMB_HEADERS)


@router.get("/api/chats/{chat_id}/thumb/{file_id}")
async def chat_thumb(chat_id: str, file_id: int, request: Request):
    user = await _user(request)
    _check_part(chat_id, "chat_id", 128)
    return await proxy_thumb(f"{_chats_url}/chat/{chat_id}/thumb/{file_id}",
                             headers={"X-User-Name": user["username"]})
