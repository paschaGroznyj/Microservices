"""Превью картинок панели вложений из file_thumbs (core/file_thumbs.py).

GET /chat/{chat_id}/thumb/{file_id}   X-User-Name; файл виден в цепочке форков чата
GET /group/thumb/{file_id}?viewer_id= комната общая или viewer её участник, сообщение не удалено
POST /internal/thumbs/chat_message?chat_id=&message_id=  от chats-consumer: построить превью в фоне
Превью нет и сделать нельзя → 404 (фронт тогда грузит оригинал).
"""
import logging

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response

from app.core import file_thumbs

router = APIRouter()
_pool = None

_CHAT_FILE_VISIBLE = """
    SELECT f.id FROM chat_chain($1) c
    JOIN chat_files f ON f.chat_id = c.chat_id
    WHERE f.id = $2
      AND (c.upto IS NULL OR EXISTS (
            SELECT 1 FROM chats m
            WHERE m.chat_id = c.chat_id AND m.message_id = f.message_id AND m.id <= c.upto))
    LIMIT 1
"""
_OWNS_CHAT = """
    SELECT EXISTS (SELECT 1 FROM chat_sidebar_meta WHERE chat_id = $1 AND user_id = $2 AND NOT is_deleted)
        OR EXISTS (SELECT 1 FROM chats WHERE chat_id = $1 AND user_id = $2)
        OR EXISTS (SELECT 1 FROM chats_forks WHERE child_chat_id = $1 AND user_id = $2)
"""
_GROUP_FILE_VISIBLE = """
    SELECT f.id FROM group_message_files f
    JOIN group_messages m ON m.id = f.message_id AND NOT m.is_deleted
    JOIN group_rooms r ON r.id = f.room_id AND NOT r.is_deleted
    WHERE f.id = $1 AND (r.room_type = 'global' OR EXISTS (
          SELECT 1 FROM group_room_members gm WHERE gm.room_id = r.id AND gm.user_id = $2))
"""


def bind(pool) -> None:
    global _pool
    _pool = pool


def _ready():
    if _pool is None:
        raise HTTPException(status_code=503, detail="db not ready")
    return _pool


def _image(data: bytes | None) -> Response:
    if not data:
        raise HTTPException(status_code=404, detail="Превью нет")
    return Response(content=data, media_type="image/webp")


@router.get("/chat/{chat_id}/thumb/{file_id}")
async def chat_thumb(chat_id: str, file_id: int, x_user_name: str = Header(..., alias="X-User-Name")):
    if not chat_id or len(chat_id) > 128:
        raise HTTPException(status_code=400, detail="bad chat_id")
    pool = _ready()
    async with pool.acquire() as c:
        uid = await c.fetchval("SELECT id FROM users WHERE username = $1", x_user_name)
        ok = uid is not None and await c.fetchval(_OWNS_CHAT, chat_id, uid) \
            and await c.fetchval(_CHAT_FILE_VISIBLE, chat_id, file_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        return _image(await file_thumbs.ensure(pool, "chat", file_id))
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"chat thumb {chat_id}/{file_id}: {e}")
        raise HTTPException(status_code=500, detail="Превью не получено")


@router.get("/group/thumb/{file_id}")
async def group_thumb(file_id: int, viewer_id: int = Query(...)):
    pool = _ready()
    if not await pool.fetchval(_GROUP_FILE_VISIBLE, file_id, viewer_id):
        raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        return _image(await file_thumbs.ensure(pool, "group", file_id))
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"group thumb {file_id}: {e}")
        raise HTTPException(status_code=500, detail="Превью не получено")


@router.post("/internal/thumbs/chat_message", status_code=202)
async def warm_chat_message(chat_id: str = Query(..., max_length=128), message_id: str = Query(..., max_length=128)):
    file_thumbs.schedule_message(_ready(), chat_id, message_id)
    return {"ok": True}
