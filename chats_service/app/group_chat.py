"""
Эндпоинты ОБЩЕГО / группового чата (chats_service).

Вынесено в отдельный APIRouter, чтобы не раздувать app.py.

Поток сообщений (ТОЛЬКО Kafka, НЕ пишем в БД напрямую):
  gateway POST /group/send -> кладём в Kafka topic 'group-chat-messages'
     -> group_endpoint_consumer (group-db-consumer-group) спокойно вставляет в БД
     -> шлёт обратно на оба гейтвея /internal/group_message (main->WS, sigma->SSE)

Фичи (delete/edit/pin) идут синхронно в БД (они редкие и требуют ответа),
но результат тоже транслируется в комнату через ту же Kafka-шину (event-типы).
"""
import io
import json
import logging
import time
import uuid

from fastapi import (APIRouter, File, Form, Header, HTTPException, Request,
                     UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import obs_file_storage

logger = logging.getLogger(__name__)

GROUP_TOPIC = "group-chat-messages"


def _content_disposition(filename: str, disposition: str = "attachment") -> str:
    """RFC 6266 Content-Disposition: latin-1 fallback + filename* (UTF-8),
    чтобы кириллица/юникод в имени не ломали заголовок (UnicodeEncodeError)."""
    from urllib.parse import quote
    fallback = (filename or "file").encode("ascii", "ignore").decode("ascii") or "file"
    quoted = quote(filename or "file", safe="")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quoted}"


# Лимит на один файл: 20 МБ (согласовано)
MAX_GROUP_FILE_SIZE = 20 * 1024 * 1024
MAX_GROUP_FILES = 10
_IMAGE_MIME_PREFIX = "image/"

router = APIRouter(prefix="/group", tags=["group-chat"])

# выставляются из app.py при startup
group_db = None       # GroupChatDB
producer = None       # KafkaProducer (общий с основным app)


def bind(db_instance, kafka_producer):
    """Вызывается из app.py после инициализации пула и продьюсера."""
    global group_db, producer
    group_db = db_instance
    producer = kafka_producer


# ── Модели ───────────────────────────────────────────────

class GroupSendPayload(BaseModel):
    room_uid: str = "global"
    user_id: int
    username: str
    message: str
    client_msg_id: str | None = None
    gateway_target: str = "main"


class GroupMessageAction(BaseModel):
    message_id: int
    user_id: int
    is_admin: bool = False


class GroupEditAction(BaseModel):
    message_id: int
    user_id: int
    new_text: str


class GroupPinAction(BaseModel):
    message_id: int
    is_pinned: bool


# ── Отправка (только в Kafka) ───────────────────────────────

@router.post("/send")
async def group_send(payload: GroupSendPayload):
    """Кладёт сообщение пользователя в Kafka. В БД не пишем здесь —
    это делает консюмер, не нагружая БД на пиках."""
    if producer is None:
        raise HTTPException(status_code=503, detail="producer not ready")
    msg = {
        "event": "new_message",
        "room_uid": payload.room_uid,
        "user_id": payload.user_id,
        "username": payload.username,
        "message": payload.message,
        "client_msg_id": payload.client_msg_id or str(uuid.uuid4()),
        "gateway_target": payload.gateway_target,
        "ts": time.time(),
    }
    start = time.perf_counter()
    producer.send(GROUP_TOPIC, key=payload.room_uid, value=msg)
    producer.flush()
    logger.info(f"[group-chat] send -> kafka room={payload.room_uid} "
                f"user={payload.user_id} ({time.perf_counter()-start:.4f}s)")
    return {"status": "ok", "client_msg_id": msg["client_msg_id"]}


# ── Выгрузка истории ────────────────────────────────────

@router.get("/{room_uid}/messages")
async def group_messages(room_uid: str, limit: int = 50, offset: int = 0):
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    try:
        result = await group_db.get_messages(room_uid, limit=limit, offset=offset)
        return {
            "room_uid": room_uid,
            "messages": result["messages"],
            "total_count": result["total_count"],
            "has_more": result["has_more"],
            "limit": max(1, min(limit, 200)),
            "offset": max(0, offset),
        }
    except Exception as e:
        logger.error(f"[group-chat] messages error: {e}")
        raise HTTPException(status_code=500, detail="не удалось загрузить сообщения")


@router.get("/{room_uid}/pinned")
async def group_pinned(room_uid: str):
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    try:
        return {"room_uid": room_uid, "pinned": await group_db.get_pinned(room_uid)}
    except Exception as e:
        logger.error(f"[group-chat] pinned error: {e}")
        raise HTTPException(status_code=500, detail="не удалось загрузить закреплённые")


# ── Файлы (вложения группового чата) ──────────────────────────

@router.post("/{room_uid}/upload")
async def group_upload(
    room_uid: str,
    client_msg_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Заливает вложения группового сообщения в OBS и регистрирует их
    в group_message_files с message_id=NULL. Привязка к сообщению
    произойдёт в консюмере по client_msg_id. Возвращает метаданные
    файлов (без байтов) — фронт сразу рисует коллаж-плейсхолдер."""
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    if not files:
        raise HTTPException(status_code=400, detail="нет файлов")
    if len(files) > MAX_GROUP_FILES:
        raise HTTPException(status_code=400,
                            detail=f"максимум {MAX_GROUP_FILES} файлов за раз")

    run_id = uuid.uuid4().hex[:12]
    saved: list[dict] = []
    for up in files:
        body = await up.read()
        if len(body) > MAX_GROUP_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"файл {up.filename} больше 20 МБ")
        mime = up.content_type or "application/octet-stream"
        is_image = mime.startswith(_IMAGE_MIME_PREFIX)
        obs_key = obs_file_storage.build_group_obs_key(
            room_uid, run_id, up.filename or "file")
        stored = await obs_file_storage.upload_bytes(obs_key, body, mime)
        if not stored:
            raise HTTPException(status_code=502,
                                detail=f"не удалось сохранить {up.filename}")
        rec = await group_db.add_file(
            room_uid=room_uid, client_msg_id=client_msg_id, obs_key=obs_key,
            file_name=up.filename or "file", mime_type=mime,
            size_bytes=len(body), is_image=is_image)
        if rec:
            saved.append(rec)
    return {"ok": True, "files": saved}


@router.get("/file/{file_id}")
async def group_file(file_id: int, download: int = 0):
    """Отдаёт файл вложения из OBS (StreamingResponse). Общий эндпоинт
    для main и sigma — оба только скачивают."""
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    rec = await group_db.get_file_by_id(file_id)
    if not rec or not rec.get("obs_key"):
        raise HTTPException(status_code=404, detail="файл не найден")
    data = await obs_file_storage.download_bytes(rec["obs_key"])
    if data is None:
        raise HTTPException(status_code=404, detail="файл недоступен в хранилище")
    mime = rec.get("mime_type") or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    headers = {
        "Content-Disposition": _content_disposition(
            rec.get("file_name") or "file", disposition),
        "Cache-Control": "private, max-age=86400",
    }
    return StreamingResponse(io.BytesIO(data), media_type=mime, headers=headers)


# ── Фичи: delete / edit / pin ─────────────────────────────────

def _broadcast_event(room_uid: str, event: dict, gateway_target: str = "both"):
    """Транслирует событие (edit/delete/pin) в комнату через ту же Kafka-шину."""
    if producer is None:
        return
    payload = dict(event)
    payload["room_uid"] = room_uid
    payload["gateway_target"] = gateway_target
    producer.send(GROUP_TOPIC, key=room_uid, value=payload)
    producer.flush()


@router.post("/message/delete")
async def group_delete(action: GroupMessageAction):
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    ok = await group_db.soft_delete(action.message_id, action.user_id, action.is_admin)
    if ok:
        # узнаём room_uid для трансляции (сообщение уже помечено, но room нужен)
        room_uid = await group_db._room_uid_by_id(
            await _room_id_of(action.message_id)
        )
        if room_uid:
            _broadcast_event(room_uid, {"event": "delete_message", "message_id": action.message_id})
    return {"ok": ok}


@router.post("/message/edit")
async def group_edit(action: GroupEditAction):
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    row = await group_db.edit_message(action.message_id, action.user_id, action.new_text)
    if not row:
        raise HTTPException(status_code=403, detail="нельзя отредактировать")
    _broadcast_event(row["room_uid"], {"event": "edit_message", "message": row})
    return {"ok": True, "message": row}


@router.post("/message/pin")
async def group_pin(action: GroupPinAction):
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    row = await group_db.set_pin(action.message_id, action.is_pinned)
    if not row:
        raise HTTPException(status_code=404, detail="сообщение не найдено")
    _broadcast_event(row["room_uid"], {"event": "pin_message", "message": row})
    return {"ok": True, "message": row}


async def _room_id_of(message_id: int):
    async with group_db.pool.acquire() as conn:
        return await conn.fetchval("SELECT room_id FROM group_messages WHERE id = $1", message_id)
