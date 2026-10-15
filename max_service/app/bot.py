"""
max_service — интеграция бота мессенджера MAX в CatchPM (аналог telegram_service).

Ядро (Коммит 1):
  • long-polling GET /updates (marker-based) — собственный цикл на MaxClient;
  • приём message_created → dispatch_to_agent (gateway_target="max")
    → POST chats_service /send_message → Kafka → agent;
  • обратный путь: agent → endpoint_consumer → POST /push_message (этот сервис)
    → MaxClient.send_message;
  • allow-list чатов через chats_service (/max/* — аналог /tg/*, Redis-кэш);
  • FastAPI push-API + /health.

ENV:
  MAX_TOKEN_API       — токен бота (business.max.ru / @MasterBot)
  CHATS_SERVICE_URL   — http://127.0.0.1:8001 (send_message + /max/*)
  AGENT_SERVICE_URL   — http://127.0.0.1:9001
  REDIS_URL           — redis://127.0.0.1:6379/0
  MAX_PUSH_PORT       — порт push-API (default 8011)
  CATCHPM_URL         — ссылка для гейта регистрации
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

import httpx
import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.api.max_client import MaxClient, MaxApiError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("max_bot")

MAX_TOKEN_API    = os.getenv("MAX_TOKEN_API", "")
CHATS_SERVICE_URL = os.getenv("CHATS_SERVICE_URL", "http://127.0.0.1:8001")
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:9001")
REDIS_URL        = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_PUSH_PORT    = int(os.getenv("MAX_PUSH_PORT", "8011"))
CATCHPM_URL      = os.getenv("CATCHPM_URL", "https://catchpm.cds.uva-advanced.ru")
POLL_TIMEOUT     = int(os.getenv("MAX_POLL_TIMEOUT", "30"))
ALLOWED_TTL      = int(os.getenv("MAX_ALLOWED_TTL", "60"))

# Типы апдейтов, которые слушаем на этапе ядра.
UPDATE_TYPES = ["message_created", "bot_added", "bot_started", "message_callback"]

client: MaxClient | None = None
_redis: aioredis.Redis | None = None
_poll_task: asyncio.Task | None = None

api = FastAPI(title="MAX Bot Push API")


# ══════════════════════════════════════════════════════════════
# Redis + allow-list
# ══════════════════════════════════════════════════════════════
async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def _k_allowed() -> str:
    return "max:allowed_chats"


async def _fetch_allowed_from_chats() -> list[str] | None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{CHATS_SERVICE_URL}/max/allowed_chats")
            if r.status_code == 200:
                return [str(x) for x in r.json().get("chats", [])]
    except Exception as e:
        log.warning(f"fetch allowed fail: {e}")
    return None


async def is_allowed(chat_id: int) -> bool:
    r = await get_redis()
    key = _k_allowed()
    cached = await r.smembers(key)
    if not cached:
        chats = await _fetch_allowed_from_chats()
        if chats is None:
            # fail-open на этапе ядра: если chats_service недоступен — не блокируем.
            return True
        if chats:
            await r.sadd(key, *chats)
            await r.expire(key, ALLOWED_TTL)
        cached = set(chats)
    return str(chat_id) in cached


async def get_chat_settings(chat_id: int) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{CHATS_SERVICE_URL}/max/settings/{chat_id}")
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        log.warning(f"get_chat_settings fail: {e}")
    return {}


# ══════════════════════════════════════════════════════════════
# Проброс в агента
# ══════════════════════════════════════════════════════════════
async def dispatch_to_agent(chat_id: int, user_id: int, username: str, text: str):
    settings = await get_chat_settings(chat_id)
    payload = {
        "user_id": user_id,
        "username": username or f"max_{user_id}",
        "chat_id": str(chat_id),
        "max_chat_id": str(chat_id),
        "message": text,
        "type_agent": "pm",
        "gateway_target": "max",
        "input_format": "text",
        "request_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "selected_tools": settings.get("selected_tools") or [],
        "agent_model": settings.get("agent_model"),
        "context_size": "medium",
        "has_files": False,
        "is_from_bot": False,
        "tb_name": None,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        resp = await c.post(f"{CHATS_SERVICE_URL}/send_message", json=payload)
        resp.raise_for_status()
    log.info(f"dispatched to agent chat={chat_id} req={payload['request_id']}")


# ══════════════════════════════════════════════════════════════
# Обработка апдейтов MAX
# ══════════════════════════════════════════════════════════════
def _extract_message(update: dict) -> dict | None:
    """Из message_created вытаскиваем chat_id/user_id/username/text.

    Структура MAX: update.message.{sender{user_id,name},recipient{chat_id},body{text}}.
    """
    msg = update.get("message") or {}
    recipient = msg.get("recipient") or {}
    sender = msg.get("sender") or {}
    body = msg.get("body") or {}
    chat_id = recipient.get("chat_id")
    text = body.get("text")
    if chat_id is None or not text:
        return None
    return {
        "chat_id": int(chat_id),
        "user_id": int(sender.get("user_id") or 0),
        "username": sender.get("username") or sender.get("name") or "",
        "text": text,
    }


async def handle_update(update: dict) -> None:
    utype = update.get("update_type") or update.get("type")
    if utype in ("bot_started", "bot_added"):
        chat = (update.get("chat_id") or
                (update.get("message", {}).get("recipient", {}) or {}).get("chat_id"))
        if chat is not None and client is not None:
            if not await is_allowed(int(chat)):
                await client.send_message(
                    text=("Доступ к боту ограничен. Регистрация чата — через "
                          f"{CATCHPM_URL}\nchat_id: {chat}"),
                    chat_id=int(chat), fmt=None)
        return
    if utype != "message_created":
        return
    info = _extract_message(update)
    if not info:
        return
    if not await is_allowed(info["chat_id"]):
        if client is not None:
            await client.send_message(
                text=(f"Доступ к боту ограничен. Регистрация — через {CATCHPM_URL}\n"
                      f"chat_id: {info['chat_id']}"),
                chat_id=info["chat_id"], fmt=None)
        return
    try:
        await dispatch_to_agent(info["chat_id"], info["user_id"],
                                info["username"], info["text"])
    except Exception as e:
        log.error(f"dispatch fail chat={info['chat_id']}: {e}")


async def poll_loop() -> None:
    """Long-polling цикл: marker храним локально, передаём следующему запросу."""
    assert client is not None
    marker: int | None = None
    log.info("MAX poll loop стартовал")
    while True:
        try:
            data = await client.get_updates(marker=marker, timeout=POLL_TIMEOUT,
                                            types=UPDATE_TYPES)
            for upd in data.get("updates", []):
                try:
                    await handle_update(upd)
                except Exception as e:
                    log.error(f"handle_update error: {e}")
            new_marker = data.get("marker")
            if new_marker is not None:
                marker = new_marker
        except MaxApiError as e:
            log.error(f"get_updates API error {e.status}: {e.data}")
            await asyncio.sleep(3)
        except Exception as e:
            log.error(f"poll loop error: {e}")
            await asyncio.sleep(3)


# ══════════════════════════════════════════════════════════════
# Push-API (ответ агента → MAX)
# ══════════════════════════════════════════════════════════════
class PushMessageRequest(BaseModel):
    chat_id: str
    text: str
    files: list | None = None
    edit_thinking: bool = False
    agent_message_id: str | None = None
    parse_mode: str | None = "markdown"


class PushMessageResponse(BaseModel):
    ok: bool
    error: str | None = None


MAX_MSG_LIMIT = 4000


def split_message(text: str, limit: int = MAX_MSG_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            if buf:
                parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts or [text[:limit]]


@api.post("/push_message", response_model=PushMessageResponse)
async def push_message(req: PushMessageRequest):
    if client is None:
        raise HTTPException(status_code=503, detail="MAX bot не инициализирован")
    if not req.text:
        return PushMessageResponse(ok=False, error="Empty text")
    fmt = (req.parse_mode or "markdown").lower()
    if fmt not in ("markdown", "html"):
        fmt = None
    try:
        for chunk in split_message(req.text):
            await client.send_message(text=chunk, chat_id=int(req.chat_id), fmt=fmt)
        return PushMessageResponse(ok=True)
    except MaxApiError as e:
        log.error(f"push_message API error {e.status}: {e.data}")
        return PushMessageResponse(ok=False, error=str(e))
    except Exception as e:
        log.error(f"push_message error: {e}")
        return PushMessageResponse(ok=False, error=str(e))


@api.get("/health")
async def health():
    bot_ok = client is not None
    poll_ok = _poll_task is not None and not _poll_task.done()
    return {"ok": bot_ok, "polling": poll_ok}


# ══════════════════════════════════════════════════════════════
# Lifecycle
# ══════════════════════════════════════════════════════════════
@api.on_event("startup")
async def _startup():
    global client, _poll_task
    client = MaxClient(MAX_TOKEN_API)
    try:
        me = await client.get_me()
        log.info(f"MAX bot: {me.get('name')} (user_id={me.get('user_id')})")
    except Exception as e:
        log.error(f"get_me не удался (продолжаю): {e}")
    _poll_task = asyncio.create_task(poll_loop())


@api.on_event("shutdown")
async def _shutdown():
    global _poll_task
    if _poll_task:
        _poll_task.cancel()
    if client:
        await client.close()


def main():
    uvicorn.run(api, host="0.0.0.0", port=MAX_PUSH_PORT, log_level="info")


if __name__ == "__main__":
    main()
