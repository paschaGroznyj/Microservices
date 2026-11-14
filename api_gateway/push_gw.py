"""push_gw.py — Web Push в гейтвее: service worker, VAPID, подписки и «вкладка на экране».

  GET  /sw.js                 service worker (с корня — иначе scope не покроет весь сайт)
  GET  /api/push/vapid        публичный VAPID-ключ (из chats_service, кэш в памяти)
  POST /api/push/subscribe    {"endpoint", "keys": {"p256dh", "auth"}} -> chats_service /push/subscribe
  POST /api/push/unsubscribe  {"endpoint"}                             -> chats_service /push/unsubscribe
  POST /api/push/viewing      {"chat_id", "visible"} -> Redis viewing:{uid}:{chat_id} EX VIEWING_TTL

Ключ viewing ставит heartbeat push.js, пока вкладка с чатом видима; chats-consumer
не шлёт push, пока ключ жив. Настройка вкл/выкл идёт через /api/user/settings
(notify_enabled, дебаунс 1 с в run_status.push_notify_setting).
"""
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()

VIEWING_TTL = int(os.getenv("PUSH_VIEWING_TTL", "35"))
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")

_http = None
_chats_url = ""
_get_current_user = None
_get_redis = None
_gw = "main"
_sw_path: Path | None = None
_vapid_public: str | None = None


def bind(http_client, chats_url, get_current_user, get_redis, static_dir, gw: str = "main"):
    global _http, _chats_url, _get_current_user, _get_redis, _gw, _sw_path, _vapid_public
    _http = http_client
    _chats_url = chats_url
    _get_current_user = get_current_user
    _get_redis = get_redis
    _gw = "sigma" if gw == "sigma" else "main"
    _sw_path = Path(static_dir) / "js" / "sw.js"
    _vapid_public = None


def viewing_key(user_id, chat_id) -> str:
    return f"viewing:{int(user_id)}:{chat_id}"


async def _uid(request: Request) -> int:
    user = await _get_current_user(request) if _get_current_user else None
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return int(user["user_id"])


@router.get("/sw.js", include_in_schema=False)
async def service_worker():
    if _sw_path is None or not _sw_path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        _sw_path,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@router.get("/api/push/vapid")
async def push_vapid(request: Request):
    global _vapid_public
    await _uid(request)
    if _vapid_public is None:
        try:
            r = await _http.get(f"{_chats_url}/push/vapid", timeout=5.0)
            if r.status_code == 200:
                _vapid_public = r.json().get("public_key") or None
        except Exception as e:
            logger.warning("push_gw: vapid fetch failed: %s", e)
    if not _vapid_public:
        raise HTTPException(status_code=503, detail="Push unavailable")
    return {"public_key": _vapid_public}


async def _proxy(path: str, uid: int, body: dict):
    try:
        r = await _http.post(f"{_chats_url}{path}", headers={"X-User-Id": str(uid)}, json=body, timeout=5.0)
    except Exception as e:
        logger.warning("push_gw: %s failed: %s", path, e)
        raise HTTPException(status_code=502, detail="chats_service unavailable")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="push request rejected")
    return r.json()


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    uid = await _uid(request)
    data = await request.json()
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    body = {
        "endpoint": str(data.get("endpoint") or ""),
        "keys": {"p256dh": str(keys.get("p256dh") or ""), "auth": str(keys.get("auth") or "")},
        "gw": _gw,
        "user_agent": request.headers.get("user-agent", "")[:300],
    }
    return await _proxy("/push/subscribe", uid, body)


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    uid = await _uid(request)
    data = await request.json()
    return await _proxy("/push/unsubscribe", uid, {"endpoint": str(data.get("endpoint") or "")})


@router.post("/api/push/viewing")
async def push_viewing(request: Request):
    uid = await _uid(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    redis = _get_redis() if _get_redis else None
    if redis is None:
        return {"ok": False}
    visible = bool(data.get("visible", True))
    chat_id = str(data.get("chat_id") or "").strip()
    prev = str(data.get("prev_chat_id") or "").strip()
    try:
        if prev and prev != chat_id and _CHAT_ID_RE.match(prev):
            await redis.delete(viewing_key(uid, prev))
        if chat_id and _CHAT_ID_RE.match(chat_id):
            if visible:
                await redis.set(viewing_key(uid, chat_id), "1", ex=VIEWING_TTL)
            else:
                await redis.delete(viewing_key(uid, chat_id))
    except Exception as e:
        logger.debug("push_gw: viewing failed: %s", e)
        return {"ok": False}
    return {"ok": True}
