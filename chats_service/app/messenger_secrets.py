"""Личные ключи пользователей мессенджеров (RouterAPI и др.).

Боты TG/MAX вызывают /tg/secrets или /max/secrets, chats_service резолвит
владельца чата (личный чат, активный, added_by_user_id) и форвардит в auth_service.

Только личные чаты: в групповых ключ мог бы дать любой участник, но оплачивал
бы владелец чата, что небезопасно. Групповые чаты могут использовать общий
ROUTER_API_KEY только админу (см. agent core/user_secrets.py).
Хранилище и шифрование те же, что у профиля: auth_service /profile/secrets/*.
"""
import logging
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
log = logging.getLogger(__name__)

# Инжектятся при старте chats_service (см. app.py on_event("startup"))
_tg_db = None
_max_db = None
_auth_url = ""

def bind(tg_db, max_db, auth_url: str) -> None:
    global _tg_db, _max_db, _auth_url
    _tg_db = tg_db
    _max_db = max_db
    _auth_url = auth_url.rstrip("/")


class SecretRequest(BaseModel):
    chat_id: str
    provider: str = "routerai"
    name: str = "default"
    secret: str | None = None  # только для set


async def _get_owner(chat_id: str, messenger: str) -> int:
    """Возвращает web user_id владельца личного активного чата или поднимает 400/404."""
    db = _tg_db if messenger == "tg" else _max_db
    if not db or not db.pool:
        raise HTTPException(status_code=503, detail="DB не готова")
    
    async with db.pool.acquire() as conn:
        table = "telegram_chats" if messenger == "tg" else "max_chats"
        row = await conn.fetchrow(
            f"SELECT chat_type, is_active, added_by_user_id FROM {table} WHERE chat_id = $1",
            str(chat_id))
    
    if not row:
        raise HTTPException(status_code=404, detail="Чат не зарегистрирован")
    if not row["is_active"]:
        raise HTTPException(status_code=400, detail="Чат деактивирован")
    if row["chat_type"] != "private":
        raise HTTPException(status_code=400, detail="Ключи API доступны только в личных чатах")
    if not row["added_by_user_id"]:
        raise HTTPException(status_code=400, detail="Владелец чата не определён")
    
    return int(row["added_by_user_id"])


async def _call_auth(path: str, payload: dict) -> dict:
    """Вызов auth_service /profile/secrets/{path}."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{_auth_url}/profile/secrets/{path}", json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.RequestError as e:
        log.warning(f"[messenger_secrets] auth call fail {path}: {type(e).__name__}")
        raise HTTPException(status_code=502, detail="Auth service unavailable")
    except httpx.HTTPStatusError as e:
        # Прокидываем статус и текст ошибки от auth_service (400/404 и т.п.)
        try:
            detail = e.response.json().get("detail", str(e))
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=e.response.status_code, detail=detail)


@router.get("/tg/secrets")
async def tg_secrets_list(chat_id: str):
    """GET /tg/secrets?chat_id=123 → список ключей владельца личного чата."""
    user_id = await _get_owner(chat_id, "tg")
    return await _call_auth("list", {"user_id": user_id})


@router.post("/tg/secrets")
async def tg_secrets_set(body: SecretRequest):
    """POST /tg/secrets {"chat_id", "provider", "name", "secret"} → сохранить ключ."""
    user_id = await _get_owner(body.chat_id, "tg")
    if not body.secret or not body.secret.strip():
        raise HTTPException(status_code=400, detail="Пустой ключ")
    return await _call_auth("set", {
        "user_id": user_id, "provider": body.provider,
        "name": body.name, "secret": body.secret.strip()
    })


@router.post("/tg/secrets/delete")
async def tg_secrets_delete(body: SecretRequest):
    """POST /tg/secrets/delete {"chat_id", "provider", "name"} → удалить ключ."""
    user_id = await _get_owner(body.chat_id, "tg")
    return await _call_auth("delete", {
        "user_id": user_id, "provider": body.provider, "name": body.name
    })


@router.get("/max/secrets")
async def max_secrets_list(chat_id: str):
    user_id = await _get_owner(chat_id, "max")
    return await _call_auth("list", {"user_id": user_id})


@router.post("/max/secrets")
async def max_secrets_set(body: SecretRequest):
    user_id = await _get_owner(body.chat_id, "max")
    if not body.secret or not body.secret.strip():
        raise HTTPException(status_code=400, detail="Пустой ключ")
    return await _call_auth("set", {
        "user_id": user_id, "provider": body.provider,
        "name": body.name, "secret": body.secret.strip()
    })


@router.post("/max/secrets/delete")
async def max_secrets_delete(body: SecretRequest):
    user_id = await _get_owner(body.chat_id, "max")
    return await _call_auth("delete", {
        "user_id": user_id, "provider": body.provider, "name": body.name
    })
