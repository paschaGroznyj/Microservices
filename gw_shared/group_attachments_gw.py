"""
Вложения общего / группового чата для правой панели (attachments-panel.js, без веток).

  GET /api/group/{room_uid}/attachments  -> chats_service /group/{room_uid}/attachments?viewer_id=
  GET /api/group/thumb/{file_id}?w=       уменьшенная картинка для сетки «Медиа»

Общий модуль api_gateway и api_gateway_sigma (gw_shared/). Уменьшение и LRU-кэш те же,
что у превью чатов агента (attachments_gw). Оригинал берётся из chats_service /group/file/{id}
(OBS), доступ как у /api/group/file/{id}: только авторизация.
"""
import asyncio
import logging
from typing import Callable

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from gw_shared import attachments_gw as _att

router = APIRouter()

_get_client: Callable | None = None
_chats_url = ""
_get_user: Callable | None = None


def bind(*, get_client: Callable, chats_url: str, get_user: Callable) -> None:
    global _get_client, _chats_url, _get_user
    _get_client = get_client
    _chats_url = chats_url.rstrip("/")
    _get_user = get_user


async def _viewer(request: Request) -> int:
    user = await _get_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user["user_id"])


@router.get("/api/group/{room_uid}/attachments")
async def group_attachments(room_uid: str, request: Request,
                            tab: str = Query("media", pattern="^(media|files|links)$"),
                            author: str = Query("all", pattern="^(all|me|others)$"),
                            cursor: str | None = Query(None, max_length=32),
                            limit: int = Query(30, ge=1, le=60)):
    viewer = await _viewer(request)
    _att._check_part(room_uid, "room_uid", 128)
    params = {"tab": tab, "author": author, "limit": limit, "viewer_id": viewer}
    if cursor:
        params["cursor"] = cursor
    try:
        r = await _get_client().get(f"{_chats_url}/group/{room_uid}/attachments", params=params, timeout=10.0)
    except httpx.RequestError as e:
        logging.warning(f"[group_attachments_gw] {room_uid}: {e}")
        raise HTTPException(status_code=502, detail="Chats service unreachable")
    if r.status_code in (400, 404):
        raise HTTPException(status_code=r.status_code,
                            detail="Комната не найдена" if r.status_code == 404 else "Неверный запрос")
    if r.status_code >= 400:
        logging.warning(f"[group_attachments_gw] {room_uid}: HTTP {r.status_code}")
        raise HTTPException(status_code=502, detail="Chats service error")
    return Response(content=r.content, media_type="application/json", headers={"Cache-Control": "no-store"})


@router.get("/api/group/thumb/{file_id}")
async def group_thumb(file_id: int, request: Request, w: int = Query(216)):
    await _viewer(request)
    w = min(_att.THUMB_SIZES, key=lambda s: abs(s - w))
    key = ("group", file_id, w)
    hit = _att._cache.get(key)
    if hit:
        _att._cache.move_to_end(key)
        data, ctype = hit
    else:
        try:
            r = await _get_client().get(f"{_chats_url}/group/file/{file_id}", params={"download": 0}, timeout=20.0)
        except httpx.RequestError as e:
            logging.warning(f"[group_attachments_gw] thumb {file_id}: {e}")
            raise HTTPException(status_code=502, detail="Chats service unreachable")
        ctype = r.headers.get("content-type", "application/octet-stream")
        if r.status_code != 200 or not r.content or not ctype.startswith("image/"):
            raise HTTPException(status_code=404, detail="Image not found")
        raw = data = r.content
        if _att.Image is not None and len(raw) <= _att.MAX_SRC_BYTES and "svg" not in ctype:
            small = await asyncio.to_thread(_att._shrink, raw, w)
            if small and len(small[0]) < len(raw):
                data, ctype = small
        if len(data) <= 2 * 1024 * 1024:
            _att._cache_put(key, (data, ctype))
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})
