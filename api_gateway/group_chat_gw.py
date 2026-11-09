"""
Гейтвей-роутер ОБЩЕГО чата (main, WebSocket-доставка).

Вынесено в отдельный модуль, чтобы не раздувать app.py.

Маршруты:
  WS   /ws/group/{room_uid}            — подписка на комнату (realtime)
  POST /api/group/{room_uid}/send      — отправка (прокси в chats_service -> Kafka)
  GET  /api/group/{room_uid}/messages  — выгрузка истории
  GET  /api/group/{room_uid}/pinned    — закреплённые
  POST /api/group/message/delete|edit|pin
  POST /api/group/reactions             — пачка реакций -> reactions_bus
  POST /api/group/{room_uid}/read       — отметка прочтения -> reads_bus
  GET  /api/group/message/{id}/readers  — кто прочитал (только автору)
  GET  /api/group/{room_uid}/unread     — счётчик непрочитанных для плашки в сайдбаре
  POST /internal/group_message         — приём событий от консюмера -> broadcast по WS

Подключается в app.py: group_chat_gw.bind(http_client, chats_url, get_current_user, admin_check)
                        app.include_router(group_chat_gw.router)
"""
import logging
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

import reactions_bus
import reads_bus

logger = logging.getLogger(__name__)

router = APIRouter()

# подписки на комнаты: room_uid -> [WebSocket]
group_connections: dict[str, list] = {}

# выставляются из app.py через bind()
_http = None
_chats_url = None
_get_current_user = None
_admin_usernames = set()


def bind(http_client, chats_url, get_current_user, admin_usernames):
    global _http, _chats_url, _get_current_user, _admin_usernames
    _http = http_client
    _chats_url = chats_url
    _get_current_user = get_current_user
    _admin_usernames = set(admin_usernames or [])


async def _broadcast(room_uid: str, event: dict):
    dead = []
    for ws in group_connections.get(room_uid, []):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        try:
            group_connections.get(room_uid, []).remove(ws)
        except ValueError:
            pass


@router.websocket("/ws/group/{room_uid}")
async def ws_group(websocket: WebSocket, room_uid: str):
    await websocket.accept()
    group_connections.setdefault(room_uid, []).append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        try:
            group_connections.get(room_uid, []).remove(websocket)
        except ValueError:
            pass


class GroupSendBody(BaseModel):
    message: str
    client_msg_id: str | None = None


@router.post("/api/group/{room_uid}/send")
async def api_group_send(room_uid: str, body: GroupSendBody, request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    username = request.headers.get("X-Authentik-Name")
    if username:
        try:
            username = username.encode("latin-1").decode("utf-8")
        except Exception:
            pass
    else:
        username = user.get("username")
    payload = {
        "room_uid": room_uid,
        "user_id": user["user_id"],
        "username": username,
        "message": body.message,
        "client_msg_id": body.client_msg_id,
        "gateway_target": "main",
    }
    resp = await _http.post(f"{_chats_url}/group/send", json=payload)
    if resp.status_code >= 400:
        # фронт по 5xx повторит отправку с тем же client_msg_id
        raise HTTPException(status_code=resp.status_code, detail="group send failed")
    return resp.json()


@router.get("/api/group/{room_uid}/messages")
async def api_group_messages(room_uid: str, request: Request, limit: int = 50, offset: int = 0,
                             before_id: int | None = None):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await _http.get(
        f"{_chats_url}/group/{room_uid}/messages",
        params={k: v for k, v in {"limit": limit, "offset": offset, "before_id": before_id,
                                  "viewer_id": user.get("user_id")}.items() if v is not None},
    )
    return resp.json()


@router.get("/api/group/{room_uid}/unread")
async def api_group_unread(room_uid: str, request: Request):
    user = await _get_current_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await _http.get(f"{_chats_url}/group/{room_uid}/unread",
                           params={"viewer_id": int(user["user_id"])})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="unread unavailable")
    return resp.json()


@router.get("/api/group/{room_uid}/pinned")
async def api_group_pinned(room_uid: str, request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await _http.get(f"{_chats_url}/group/{room_uid}/pinned")
    return resp.json()


@router.post("/api/group/{room_uid}/upload")
async def api_group_upload(
    room_uid: str,
    request: Request,
    client_msg_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    """Проксирует multipart-вложения в chats_service (заливка в OBS там).
    Только main — sigma без загрузки через интерфейс."""
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    fwd = [("files", (f.filename, await f.read(),
                      f.content_type or "application/octet-stream"))
           for f in files]
    resp = await _http.post(
        f"{_chats_url}/group/{room_uid}/upload",
        data={"client_msg_id": client_msg_id},
        files=fwd,
    )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code,
                            detail=resp.text)
    return resp.json()


@router.get("/api/group/file/{file_id}")
async def api_group_file(file_id: int, request: Request, download: int = 0):
    """Проксирует скачивание/потоковую отдачу (HTTP Range 206) файла из chats_service (OBS)."""
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    fwd_headers = {}
    if request.headers.get("range"):
        fwd_headers["Range"] = request.headers["range"]
    resp = await _http.get(
        f"{_chats_url}/group/file/{file_id}",
        params={"download": download},
        headers=fwd_headers,
    )
    if resp.status_code == 416:
        out_hdrs = {}
        if resp.headers.get("content-range"):
            out_hdrs["Content-Range"] = resp.headers["content-range"]
        return StreamingResponse(iter([b""]), status_code=416, headers=out_hdrs)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="файл не найден")
    out_headers = {
        "Content-Disposition": resp.headers.get("content-disposition", "inline"),
        "Accept-Ranges": resp.headers.get("accept-ranges", "bytes"),
        "Cache-Control": resp.headers.get("cache-control", "public, max-age=3600"),
    }
    if resp.headers.get("content-range"):
        out_headers["Content-Range"] = resp.headers["content-range"]
    if resp.headers.get("content-length"):
        out_headers["Content-Length"] = resp.headers["content-length"]
    return StreamingResponse(
        iter([resp.content]),
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers=out_headers,
    )


class GroupMsgActionBody(BaseModel):
    message_id: int


class GroupEditBody(BaseModel):
    message_id: int
    new_text: str


class GroupPinBody(BaseModel):
    message_id: int
    is_pinned: bool


@router.post("/api/group/message/delete")
async def api_group_delete(body: GroupMsgActionBody, request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    is_admin = user.get("username") in _admin_usernames
    resp = await _http.post(f"{_chats_url}/group/message/delete", json={
        "message_id": body.message_id, "user_id": user["user_id"], "is_admin": is_admin,
    })
    return resp.json()


@router.post("/api/group/message/edit")
async def api_group_edit(body: GroupEditBody, request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await _http.post(f"{_chats_url}/group/message/edit", json={
        "message_id": body.message_id, "user_id": user["user_id"], "new_text": body.new_text,
    })
    return resp.json()


@router.post("/api/group/message/pin")
async def api_group_pin(body: GroupPinBody, request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # закреплять может только админ
    if user.get("username") not in _admin_usernames:
        raise HTTPException(status_code=403, detail="Только админ может закреплять")
    resp = await _http.post(f"{_chats_url}/group/message/pin", json={
        "message_id": body.message_id, "is_pinned": body.is_pinned,
    })
    return resp.json()


class ReactionItem(BaseModel):
    message_id: int = Field(gt=0)
    emoji: str = Field(default="", max_length=16)


class ReactionsBody(BaseModel):
    items: list[ReactionItem] = Field(max_length=100)


@router.post("/api/group/reactions")
async def api_group_reactions(body: ReactionsBody, request: Request):
    """Пачка реакций с фронта (дебаунс 1 с там) -> Redis -> дебаунсер
    reactions_bus -> chats_service. Набор эмодзи проверяет chats_service."""
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    items = [i.model_dump() for i in body.items]
    via = await reactions_bus.enqueue(int(user["user_id"]), items)
    return {"ok": True, "queued": len(items), "via": via}


class ReadBody(BaseModel):
    last_read_id: int = Field(gt=0)


@router.post("/api/group/{room_uid}/read")
async def api_group_read(room_uid: str, body: ReadBody, request: Request):
    """Новый максимум прочитанного (фронт шлёт не чаще 1 раза в секунду)
    -> Redis zset (ZADD GT) -> дебаунсер reads_bus -> chats_service."""
    user = await _get_current_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not reads_bus.valid_room(room_uid):
        raise HTTPException(status_code=400, detail="bad room")
    via = await reads_bus.enqueue(room_uid, int(user["user_id"]), user.get("username") or "",
                                  body.last_read_id)
    return {"ok": True, "via": via}


@router.get("/api/group/message/{message_id}/readers")
async def api_group_readers(message_id: int, request: Request):
    user = await _get_current_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await _http.get(f"{_chats_url}/group/message/{message_id}/readers",
                           params={"viewer_id": int(user["user_id"])})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="readers unavailable")
    return resp.json()


@router.post("/internal/group_message")
async def internal_group_message(request: Request):
    """Приём событий от group-chat-consumer -> рассылка по WS всем в комнате."""
    body = await request.json()
    room_uid = body.get("room_uid", "global")
    await _broadcast(room_uid, body)
    return {"ok": True}
