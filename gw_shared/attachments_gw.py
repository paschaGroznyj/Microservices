"""
Вложения чата для правой панели (attachments-panel.js).

  GET /api/chats/{chat_id}/attachments  -> chats_service /chat/{chat_id}/attachments
  GET /api/chats/{chat_id}/thumb/{message_id}/{file_name}?image_key=&w=
      уменьшенная картинка для сетки «Медиа» (JPEG/WebP, длинная сторона w px).

Общий модуль api_gateway и api_gateway_sigma (gw_shared/, см. gw_shared/__init__.py).
bind(...) вызывается из app.py: http_client создаётся на старте, берём его через функцию.

Превью: оригинал берётся так же, как в /files/... и /image/... (resolve_file по цепочке
форков -> agent_service /web/file, иначе generated_images через /web/db_image), уменьшается
Pillow и кэшируется в памяти (LRU по байтам). Без Pillow отдаётся оригинал.
"""
import asyncio
import io
import logging
from collections import OrderedDict
from typing import Callable

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

try:
    from PIL import Image, ImageOps
except Exception:  # Pillow не установлен — превью не уменьшаем
    Image = None

router = APIRouter()

_get_client: Callable | None = None
_chats_url = ""
_agent_url = ""
_get_user: Callable | None = None

THUMB_SIZES = (144, 216, 320, 480)
MAX_SRC_BYTES = 25 * 1024 * 1024
CACHE_BYTES = 48 * 1024 * 1024
_cache: "OrderedDict[tuple, tuple[bytes, str]]" = OrderedDict()
_cache_size = 0


def bind(*, get_client: Callable, chats_url: str, agent_url: str, get_user: Callable) -> None:
    global _get_client, _chats_url, _agent_url, _get_user
    _get_client = get_client
    _chats_url = chats_url.rstrip("/")
    _agent_url = agent_url.rstrip("/")
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


async def _fetch_original(chat_id: str, message_id: str, file_name: str, image_key: str | None) -> tuple[bytes, str] | None:
    """Оригинал картинки: файл по цепочке форков, иначе generated_images. None — не найден."""
    client = _get_client()

    async def from_db():
        r = await client.get(f"{_agent_url}/web/db_image",
                             params={"message_id": message_id, "image_key": image_key,
                                     "file_name": file_name, "chat_id": chat_id})
        return (r.content, r.headers.get("content-type", "image/png")) if r.status_code == 200 else None

    async def from_file():
        rr = await client.get(f"{_chats_url}/internal/resolve_file",
                              params={"chat_id": chat_id, "message_id": message_id, "file_name": file_name})
        path = (rr.json() or {}).get("path") if rr.status_code == 200 else None
        if not path:
            return None
        r = await client.get(f"{_agent_url}/web/file", params={"path": path, "chat_id": chat_id, "download": 0})
        return (r.content, r.headers.get("content-type", "application/octet-stream")) if r.status_code in (200, 206) else None

    order = (from_db, from_file) if image_key else (from_file, from_db)
    for fn in order:
        try:
            got = await fn()
        except httpx.RequestError as e:
            logging.warning(f"[attachments_gw] thumb {chat_id}/{message_id}: {e}")
            got = None
        if got and got[0]:
            return got
    return None


def _shrink(raw: bytes, w: int) -> tuple[bytes, str] | None:
    """Длинная сторона до w px с учётом EXIF-поворота. None — не картинка (или анимация/SVG)."""
    try:
        im = Image.open(io.BytesIO(raw))
        if getattr(im, "is_animated", False) and im.format == "GIF":
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((w, w), Image.LANCZOS)
        out = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            im.save(out, "WEBP", quality=80, method=4)
            return out.getvalue(), "image/webp"
        im.convert("RGB").save(out, "JPEG", quality=80, optimize=True, progressive=True)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return None


def _cache_put(key: tuple, val: tuple[bytes, str]) -> None:
    global _cache_size
    if key in _cache:
        return
    _cache[key] = val
    _cache_size += len(val[0])
    while _cache_size > CACHE_BYTES and _cache:
        _, old = _cache.popitem(last=False)
        _cache_size -= len(old[0])


@router.get("/api/chats/{chat_id}/thumb/{message_id}/{file_name}")
async def chat_thumb(chat_id: str, message_id: str, file_name: str, request: Request,
                     image_key: str | None = Query(None, max_length=128),
                     w: int = Query(216)):
    await _user(request)
    _check_part(chat_id, "chat_id", 128)
    _check_part(message_id, "message_id", 128)
    _check_part(file_name, "file_name")
    w = min(THUMB_SIZES, key=lambda s: abs(s - w))
    # Проверка доступа как у /files/... и /image/...: только авторизация (владение чатом
    # там тоже не проверяется). Ключ кэша включает chat_id и message_id.
    key = (chat_id, message_id, file_name, image_key or "", w)
    hit = _cache.get(key)
    if hit:
        _cache.move_to_end(key)
        data, ctype = hit
    else:
        got = await _fetch_original(chat_id, message_id, file_name, image_key)
        if not got:
            raise HTTPException(status_code=404, detail="Image not found")
        raw, ctype = got
        data = raw
        if Image is not None and len(raw) <= MAX_SRC_BYTES and "svg" not in ctype:
            small = await asyncio.to_thread(_shrink, raw, w)
            if small and len(small[0]) < len(raw):
                data, ctype = small
        if len(data) <= 2 * 1024 * 1024:
            _cache_put(key, (data, ctype))
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "private, max-age=86400",
                             "X-Content-Type-Options": "nosniff"})
