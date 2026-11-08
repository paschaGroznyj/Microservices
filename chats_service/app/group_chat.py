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
import asyncio
import io
import json
import logging
import time
import uuid

import os
from fastapi import (APIRouter, File, Form, Header, HTTPException, Request,
                     Response, UploadFile)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import obs_file_storage
from app.core.thumbhash import media_meta_from_bytes

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


class ReactionOp(BaseModel):
    message_id: int
    user_id: int
    emoji: str = ""


class ReactionBatch(BaseModel):
    ops: list[ReactionOp]


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
async def group_messages(room_uid: str, limit: int = 50, offset: int = 0,
                         before_id: int | None = None):
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    try:
        result = await group_db.get_messages(room_uid, limit=limit, offset=offset,
                                             before_id=before_id)
        return {
            "room_uid": room_uid,
            "messages": result["messages"],
            "total_count": result["total_count"],
            "has_more": result["has_more"],
            "limit": max(1, min(limit, 200)),
            "offset": max(0, offset),
            "before_id": before_id,
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
        # размеры кадра и ThumbHash для лоадера; ошибка разбора не ломает загрузку
        try:
            meta = await asyncio.to_thread(
                media_meta_from_bytes, body, mime, up.filename or "")
        except Exception as e:
            logger.info(f"[group-chat] media meta {up.filename}: {e}")
            meta = {}
        rec = await group_db.add_file(
            room_uid=room_uid, client_msg_id=client_msg_id, obs_key=obs_key,
            file_name=up.filename or "file", mime_type=mime,
            size_bytes=len(body), is_image=is_image,
            width=meta.get("width"), height=meta.get("height"),
            thumbhash=meta.get("thumbhash"))
        if rec:
            saved.append(rec)
    return {"ok": True, "files": saved}


_MEDIA_MIME_BY_EXT = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "ogv": "video/ogg",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "mp3": "audio/mpeg",
    "mpga": "audio/mpeg",
    "wav": "audio/wav",
    "wave": "audio/wav",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "weba": "audio/webm",
    "caf": "audio/x-caf",
    "aif": "audio/aiff",
    "aiff": "audio/aiff",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
_STREAM_MAX_CHUNK = 2 * 1024 * 1024  # 2 МБ (аналог sendfile_max_chunk 2m)


def _guess_media_mime(file_name: str | None, stored_mime: str | None) -> str:
    ext = os.path.splitext(file_name or "")[1].lower().lstrip(".")
    if ext in _MEDIA_MIME_BY_EXT:
        return _MEDIA_MIME_BY_EXT[ext]
    if stored_mime and stored_mime != "application/octet-stream":
        return stored_mime
    return "application/octet-stream"


def _parse_byte_range(range_header: str, total_size: int, max_chunk: int = _STREAM_MAX_CHUNK) -> tuple[int, int] | None:
    if not range_header or not range_header.strip().lower().startswith("bytes=") or total_size <= 0:
        return None
    spec = range_header.strip()[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    start_s, end_s = start_s.strip(), end_s.strip()
    try:
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                return None
            start = max(0, total_size - suffix)
            end = total_size - 1
        else:
            start = int(start_s)
            if start < 0 or start >= total_size:
                return None
            if end_s == "":
                end = min(start + max_chunk - 1, total_size - 1)
            else:
                end = min(int(end_s), total_size - 1)
        if start > end:
            return None
        return start, end
    except ValueError:
        return None


@router.get("/file/{file_id}")
async def group_file(file_id: int, request: Request, download: int = 0):
    """Отдаёт файл вложения из OBS с поддержкой потокового HTTP Range (206 Partial Content)."""
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    rec = await group_db.get_file_by_id(file_id)
    if not rec or not rec.get("obs_key"):
        raise HTTPException(status_code=404, detail="файл не найден")
    fname = rec.get("file_name") or "file"
    mime = _guess_media_mime(fname, rec.get("mime_type"))
    disposition = "attachment" if download else "inline"
    base_headers = {
        "Content-Disposition": _content_disposition(fname, disposition),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
    }
    range_hdr = request.headers.get("range")
    total_size = int(rec.get("size_bytes") or 0)

    if range_hdr and not download and total_size > 0:
        parsed = _parse_byte_range(range_hdr, total_size)
        if parsed is None:
            return Response(
                status_code=416,
                headers={**base_headers, "Content-Range": f"bytes */{total_size}"},
            )
        start, end = parsed
        chunk_data, obs_total = await obs_file_storage.download_range(rec["obs_key"], start, end)
        if chunk_data is not None:
            actual_end = start + len(chunk_data) - 1
            total = obs_total or total_size
            headers = {
                **base_headers,
                "Content-Range": f"bytes {start}-{actual_end}/{total}",
                "Content-Length": str(len(chunk_data)),
            }
            return Response(content=chunk_data, status_code=206, media_type=mime, headers=headers)

    data = await obs_file_storage.download_bytes(rec["obs_key"])
    if data is None:
        raise HTTPException(status_code=404, detail="файл недоступен в хранилище")
    total_size = len(data)
    if range_hdr and not download and total_size > 0:
        parsed = _parse_byte_range(range_hdr, total_size)
        if parsed is None:
            return Response(
                status_code=416,
                headers={**base_headers, "Content-Range": f"bytes */{total_size}"},
            )
        start, end = parsed
        sub = data[start : end + 1]
        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{start + len(sub) - 1}/{total_size}",
            "Content-Length": str(len(sub)),
        }
        return Response(content=sub, status_code=206, media_type=mime, headers=headers)

    headers = {**base_headers, "Content-Length": str(total_size)}
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


@router.post("/reactions/apply")
async def group_reactions_apply(batch: ReactionBatch):
    """Пачка реакций от дебаунсера гейтвея (api_gateway/reactions_bus.py).
    Пишем в БД одной транзакцией, итог по каждому сообщению транслируем
    событием reactions в его комнату."""
    if group_db is None:
        raise HTTPException(status_code=503, detail="db not ready")
    if len(batch.ops) > 2000:
        raise HTTPException(status_code=413, detail="слишком большая пачка")
    changed = await group_db.apply_reactions(
        [op.model_dump() if hasattr(op, "model_dump") else op.dict() for op in batch.ops])
    by_room: dict = {}
    for row in changed:
        by_room.setdefault(row["room_uid"], []).append(
            {"message_id": row["message_id"], "reactions": row["reactions"]})
    for room_uid, items in by_room.items():
        _broadcast_event(room_uid, {"event": "reactions", "items": items})
    return {"ok": True, "applied": len(changed)}


async def _room_id_of(message_id: int):
    async with group_db.pool.acquire() as conn:
        return await conn.fetchval("SELECT room_id FROM group_messages WHERE id = $1", message_id)
