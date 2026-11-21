"""Вложения общего / группового чата для правой панели (attachments-panel.js через gw_shared/attachments_gw.py).

GET /group/{room_uid}/attachments?tab=media|files|links&author=all|me|others&viewer_id=&cursor=&limit=
Первая страница (без cursor) дополнительно отдаёт counts. Веток у групп нет.
"""
import logging

from fastapi import APIRouter, HTTPException, Query

from app.db.group_attachments_db import AUTHORS, TABS, GroupAttachmentsDB

router = APIRouter()
_db: GroupAttachmentsDB | None = None


def bind(pool) -> None:
    global _db
    _db = GroupAttachmentsDB(pool)


@router.get("/group/{room_uid}/attachments")
async def group_attachments(
    room_uid: str,
    viewer_id: int = Query(...),
    tab: str = Query("media"),
    author: str = Query("all"),
    cursor: str | None = Query(None, max_length=32),
    limit: int = Query(30, ge=1, le=60),
):
    if tab not in TABS:
        raise HTTPException(status_code=400, detail="bad tab")
    if author not in AUTHORS:
        raise HTTPException(status_code=400, detail="bad author")
    if not room_uid or len(room_uid) > 128:
        raise HTTPException(status_code=400, detail="bad room_uid")
    if _db is None or _db.pool is None:
        raise HTTPException(status_code=503, detail="db not ready")
    try:
        res = await _db.list(room_uid, viewer_id, tab=tab, author=author, cursor=cursor, limit=limit)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad cursor")
    except Exception as e:
        logging.error(f"group attachments {room_uid}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить вложения")
    if res is None:
        raise HTTPException(status_code=404, detail="Комната не найдена")
    return res
