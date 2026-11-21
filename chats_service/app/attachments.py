"""Вложения чата для правой панели (attachments-panel.js через gateway attachments_gw.py).

GET /chat/{chat_id}/attachments?tab=media|files|links&author=all|me|agent&cursor=&limit=
Первая страница (без cursor) дополнительно отдаёт counts (для вкладок) и chain (ветки форков).
"""
import logging

from fastapi import APIRouter, Header, HTTPException, Query

from app.db.attachments_db import AUTHORS, TABS, AttachmentsDB

router = APIRouter()
_db: AttachmentsDB | None = None


def bind(pool) -> None:
    global _db
    _db = AttachmentsDB(pool)


@router.get("/chat/{chat_id}/attachments")
async def chat_attachments(
    chat_id: str,
    tab: str = Query("media"),
    author: str = Query("all"),
    cursor: str | None = Query(None, max_length=32),
    limit: int = Query(30, ge=1, le=60),
    x_user_name: str = Header(..., alias="X-User-Name"),
):
    if tab not in TABS:
        raise HTTPException(status_code=400, detail="bad tab")
    if author not in AUTHORS:
        raise HTTPException(status_code=400, detail="bad author")
    if not chat_id or len(chat_id) > 128:
        raise HTTPException(status_code=400, detail="bad chat_id")
    if _db is None or _db.pool is None:
        raise HTTPException(status_code=503, detail="db not ready")
    try:
        res = await _db.list(chat_id, x_user_name, tab=tab, author=author, cursor=cursor, limit=limit)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad cursor")
    except Exception as e:
        logging.error(f"attachments {chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить вложения")
    if res is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return res
