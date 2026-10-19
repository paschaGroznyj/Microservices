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
import json
import logging
import os
import uuid

import httpx
import redis.asyncio as aioredis
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.api.max_client import MaxClient, MaxApiError
from app.media import (
    collect_message_items, upload_items_to_agent, AlbumBuffer,
)
from app.menu import (
    settings_summary, menu_root, menu_model, menu_tools, menu_trigger,
    MAX_MODELS, MAX_TRIGGERS, MAX_TOOL_CATEGORIES, MAX_ADMIN_TOOL_CATEGORIES,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("max_bot")

MAX_TOKEN_API    = os.getenv("MAX_TOKEN_API", "")
CHATS_SERVICE_URL = os.getenv("CHATS_SERVICE_URL", "http://127.0.0.1:8001")
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://127.0.0.1:9001")
REDIS_URL        = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
MAX_PUSH_PORT    = int(os.getenv("MAX_PUSH_PORT", "8011"))
CATCHPM_URL      = os.getenv("CATCHPM_URL", "https://catchpm.uva-advanced.ru")
POLL_TIMEOUT     = int(os.getenv("MAX_POLL_TIMEOUT", "30"))
ALLOWED_TTL      = int(os.getenv("MAX_ALLOWED_TTL", "60"))

# Типы апдейтов, которые слушаем на этапе ядра.
UPDATE_TYPES = ["message_created", "bot_added", "bot_started", "message_callback"]


def _parse_max_admin_ids() -> set:
    """user_id админов в MAX из ADMINS_INFO (поле max_id). Аналог _parse_tg_admin_ids."""
    raw = os.environ.get("ADMINS_INFO")
    ids = set()
    if not raw:
        return ids
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ids
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return ids
    for item in data:
        if not isinstance(item, dict):
            continue
        mx = item.get("max_id")
        if mx is None:
            continue
        try:
            ids.add(int(mx))
        except (ValueError, TypeError):
            continue
    return ids


MAX_ADMIN_IDS = _parse_max_admin_ids()


def _is_max_admin(uid) -> bool:
    try:
        return int(uid) in MAX_ADMIN_IDS
    except (ValueError, TypeError):
        return False

client: MaxClient | None = None
_album = AlbumBuffer()
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


async def save_chat_settings(chat_id: int, patch: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{CHATS_SERVICE_URL}/max/settings/{chat_id}", json=patch)
            r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"save_chat_settings fail chat={chat_id}: {e}")
        return False


def _registration_gate_text(chat_id: int) -> str:
    return (
        "У вас нет доступа к агенту в этом чате.\n\n"
        f"Регистрация чатов — через интерфейс CatchPM: {CATCHPM_URL}\n\n"
        f"Передайте администратору chat_id этого чата: {chat_id}"
    )


# ══════════════════════════════════════════════════════════════
# Проброс в агента
# ══════════════════════════════════════════════════════════════
async def dispatch_to_agent(chat_id: int, user_id: int, username: str, text: str,
                            items: list | None = None):
    """ChatMessage(gateway_target="max") в chats_service. Если есть вложения
    (items=[{file_b64,file_name,kind}]) — ПОСЛЕ постановки в очередь грузим
    файлы в agent_service /web/upload_files под тем же message_id (агент ждёт
    их через wait_for_files). Аналог telegram_service."""
    settings = await get_chat_settings(chat_id)
    logging.info(f"[settings] {settings}")
    items = (items or [])[:10]
    has_files = bool(items)
    message_id = str(uuid.uuid4())
    payload = {
        "user_id": user_id,
        "username": username or f"max_{user_id}",
        "chat_id": str(chat_id),
        "max_chat_id": str(chat_id),
        "message": text or "",
        "type_agent": "pm",
        "gateway_target": "max",
        "input_format": "text",
        "request_id": str(uuid.uuid4()),
        "message_id": message_id,
        "selected_tools": settings.get("selected_tools") or [],
        "agent_model": settings.get("agent_model"),
        "context_size": "medium",
        "has_files": has_files,
        "is_from_bot": False,
        "tb_name": None,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        resp = await c.post(f"{CHATS_SERVICE_URL}/send_message", json=payload)
        resp.raise_for_status()
    log.info(f"dispatched to agent chat={chat_id} req={payload['request_id']} "
             f"files={len(items)}")
    # Файлы шлём после постановки: agent уже ждёт их по message_id.
    # Ключ media_store — chat_id, с которым зовём агента (payload.chat_id).
    if has_files:
        await upload_items_to_agent(AGENT_SERVICE_URL, message_id, chat_id,
                                    user_id, items)


# ══════════════════════════════════════════════════════════════
# Обработка апдейтов MAX
# ══════════════════════════════════════════════════════════════
def _sender_name(sender: dict) -> str:
    """У MAX нет username — собираем из first_name+last_name, fallback name."""
    first = (sender.get("first_name") or "").strip()
    last = (sender.get("last_name") or "").strip()
    full = (first + " " + last).strip()
    return full or (sender.get("name") or "").strip()


def _extract_message(update: dict) -> dict | None:
    """Из message_created вытаскиваем chat_id/user_id/username/text.

    Структура MAX: update.message.{sender{user_id,name},recipient{chat_id},body{text}}.
    """
    msg = update.get("message") or {}
    recipient = msg.get("recipient") or {}
    sender = msg.get("sender") or {}
    body = msg.get("body") or {}
    chat_id = recipient.get("chat_id")
    text = body.get("text") or ""
    has_att = bool(body.get("attachments"))
    if chat_id is None or (not text and not has_att):
        return None
    return {
        "chat_id": int(chat_id),
        "user_id": int(sender.get("user_id") or 0),
        "username": _sender_name(sender),
        "text": text,
        "message_id": body.get("mid") or msg.get("message_id"),
        "has_attachments": has_att,
    }


async def _send_menu(chat_id: int, settings: dict, is_admin: bool = False) -> None:
    await client.send_message(
        text=settings_summary(settings), chat_id=chat_id, fmt="markdown",
        attachments=menu_root(is_admin=is_admin),
    )


async def cmd_start(chat_id: int, user_id: int | None = None) -> None:
    """Открыть меню настроек. Гейт регистрации — как в telegram_service."""
    if not await is_allowed(chat_id):
        await client.send_message(text=_registration_gate_text(chat_id),
                                  chat_id=chat_id, fmt=None)
        return
    settings = await get_chat_settings(chat_id)
    await _send_menu(chat_id, settings, is_admin=_is_max_admin(user_id))


async def handle_callback(update: dict) -> None:
    """Нажатие inline-кнопки меню (message_callback). per-chat, доступно всем."""
    cb = update.get("callback") or {}
    callback_id = cb.get("callback_id")
    data = cb.get("payload") or ""
    cb_user = cb.get("user") or {}
    user_id = cb_user.get("user_id")
    is_admin = _is_max_admin(user_id)
    msg = update.get("message") or {}
    recipient = msg.get("recipient") or {}
    body = msg.get("body") or {}
    chat_id = recipient.get("chat_id")
    message_id = body.get("mid")
    if chat_id is None or client is None:
        return
    chat_id = int(chat_id)

    async def ack(text: str | None = None):
        if callback_id:
            try:
                await client.answer_callback(callback_id, notification=text)
            except Exception as e:
                log.warning(f"answer_callback fail: {e}")

    async def rerender(attachments: list[dict], settings: dict | None = None):
        s = settings if settings is not None else await get_chat_settings(chat_id)
        if message_id:
            try:
                await client.edit_message(message_id, settings_summary(s),
                                          attachments=attachments, fmt="markdown")
                return
            except Exception as e:
                log.warning(f"edit_message fail: {e}")
        await client.send_message(text=settings_summary(s), chat_id=chat_id,
                                  fmt="markdown", attachments=attachments)

    if not await is_allowed(chat_id):
        await ack("Чат не зарегистрирован.")
        return

    if data == "max:close":
        await ack()
        if message_id:
            try:
                await client.edit_message(message_id, settings_summary(
                    await get_chat_settings(chat_id)), attachments=[], fmt="markdown")
            except Exception:
                pass
        return

    if data == "max:menu:root":
        await ack()
        await rerender(menu_root(is_admin=is_admin))
        return
    if data == "max:menu:model":
        await ack()
        s = await get_chat_settings(chat_id)
        await rerender(menu_model(s.get("agent_model")), s)
        return
    if data == "max:menu:tools":
        await ack()
        s = await get_chat_settings(chat_id)
        await rerender(menu_tools(s.get("selected_tools"), is_admin=is_admin), s)
        return
    if data == "max:menu:trigger":
        await ack()
        s = await get_chat_settings(chat_id)
        await rerender(menu_trigger(s.get("trigger_mode")), s)
        return

    if data.startswith("max:set:model:"):
        model = data.split(":", 3)[3]
        if model not in MAX_MODELS:
            await ack("Неизвестная модель")
            return
        ok = await save_chat_settings(chat_id, {"agent_model": model})
        await ack("Сохранено" if ok else "Ошибка")
        s = await get_chat_settings(chat_id)
        await rerender(menu_model(s.get("agent_model")), s)
        return

    if data.startswith("max:set:trigger:"):
        trig = data.split(":", 3)[3]
        if trig not in MAX_TRIGGERS:
            await ack("Неизвестный триггер")
            return
        ok = await save_chat_settings(chat_id, {"trigger_mode": trig})
        await ack("Сохранено" if ok else "Ошибка")
        s = await get_chat_settings(chat_id)
        await rerender(menu_trigger(s.get("trigger_mode")), s)
        return

    if data.startswith("max:tool:"):
        cat = data.split(":", 2)[2]
        allowed_cats = set(MAX_TOOL_CATEGORIES)
        if is_admin:
            allowed_cats |= set(MAX_ADMIN_TOOL_CATEGORIES)
        if cat not in allowed_cats:
            await ack("Недоступно" if cat in MAX_ADMIN_TOOL_CATEGORIES
                      else "Неизвестная категория")
            return
        s = await get_chat_settings(chat_id)
        sel = set(s.get("selected_tools") or [])
        sel.discard(cat) if cat in sel else sel.add(cat)
        ok = await save_chat_settings(chat_id, {"selected_tools": sorted(sel)})
        await ack("Сохранено" if ok else "Ошибка")
        s = await get_chat_settings(chat_id)
        await rerender(menu_tools(s.get("selected_tools"), is_admin=is_admin), s)
        return

    await ack()


async def handle_update(update: dict) -> None:
    utype = update.get("update_type") or update.get("type")
    if utype == "message_callback":
        await handle_callback(update)
        return
    if utype in ("bot_started", "bot_added"):
        chat = (update.get("chat_id") or
                (update.get("message", {}).get("recipient", {}) or {}).get("chat_id"))
        starter = (update.get("user") or {}).get("user_id") or update.get("user_id")
        if chat is not None and client is not None:
            if await is_allowed(int(chat)):
                await cmd_start(int(chat), starter)
            else:
                await client.send_message(
                    text=("Доступ к боту ограничен. Регистрация чата — через "
                          f"{CATCHPM_URL}\nchat_id: {chat}"),
                    chat_id=int(chat), fmt=None)
        return
    if utype in ("bot_admin_permissions_changed", "user_added", "user_removed",
                 "chat_title_changed", "bot_removed", "message_edited",
                 "message_removed", "dialog_removed"):
        return  # явно игнорируем на этом этапе
    if utype != "message_created":
        return
    info = _extract_message(update)
    if not info:
        return
    if info["text"].strip().split()[0].lower() in ("/start", "start"):
        await cmd_start(info["chat_id"], info.get("user_id"))
        return
    if not await is_allowed(info["chat_id"]):
        if client is not None:
            await client.send_message(
                text=(f"Доступ к боту ограничен. Регистрация — через {CATCHPM_URL}\n"
                      f"chat_id: {info['chat_id']}"),
                chat_id=info["chat_id"], fmt=None)
        return
    # С вложениями — скачиваем и прогоняем через альбом-буфер (дебаунс),
    # чтобы склеить N отдельных апдейтов по одному файлу в один вызов агента.
    if info.get("has_attachments"):
        try:
            items = await collect_message_items(update)
        except Exception as e:
            log.error(f"[media] collect fail chat={info['chat_id']}: {e}")
            items = []
        if not items:
            # вложения не скачались — шлём хотя бы текст, если он есть
            if info["text"].strip():
                try:
                    await dispatch_to_agent(info["chat_id"], info["user_id"],
                                            info["username"], info["text"])
                except Exception as e:
                    log.error(f"dispatch fail chat={info['chat_id']}: {e}")
            return

        async def _flush(chat_id, text, user_id, username, _mid, flushed_items):
            try:
                await dispatch_to_agent(chat_id, user_id, username, text,
                                        items=flushed_items)
            except Exception as e:
                log.error(f"dispatch(files) fail chat={chat_id}: {e}")

        await _album.add(info["chat_id"], info, items, _flush)
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


import base64
import pathlib

# Классификация файла агента → upload-type MAX Bot API v2 (image|video|audio|file).
# kind-подсказка от агента приоритетнее расширения (как в telegram media_voice).
_VOICE_EXTS = {".ogg", ".oga", ".opus"}
_AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_KIND_TO_MAX = {
    "voice": "audio", "audio": "audio",
    "video": "video",
    "image": "image", "photo": "image",
}


def _max_upload_type(name: str | None, kind: str | None) -> str:
    k = (kind or "").strip().lower()
    if k in _KIND_TO_MAX:
        return _KIND_TO_MAX[k]
    ext = pathlib.Path(name or "").suffix.lower()
    if ext in _VOICE_EXTS or ext in _AUDIO_EXTS:
        return "audio"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _IMAGE_EXTS:
        return "image"
    return "file"


async def _resolve_file_bytes(f: dict) -> bytes | None:
    """Байты файла агента: file_b64 → локальный path → file_url (с agent_service)."""
    if f.get("file_b64"):
        try:
            return base64.b64decode(f["file_b64"])
        except Exception as e:
            log.warning(f"[max-file] b64 decode fail: {e}")
    fpath = f.get("path")
    if fpath and os.path.exists(fpath):
        try:
            return pathlib.Path(fpath).read_bytes()
        except Exception as e:
            log.warning(f"[max-file] read path fail {fpath}: {e}")
    url = f.get("file_url")
    if url:
        if url.startswith("/"):
            url = AGENT_SERVICE_URL.rstrip("/") + url
        try:
            async with httpx.AsyncClient(timeout=120.0) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    return r.content
                log.warning(f"[max-file] pull {url} -> HTTP {r.status_code}")
        except Exception as e:
            log.error(f"[max-file] pull fail {url}: {e}")
    return None


async def _send_agent_file(chat_id: int, f: dict) -> None:
    """Файл из ответа агента → MAX: 2-шаговая загрузка + attach в сообщение."""
    raw = await _resolve_file_bytes(f)
    if not raw:
        log.warning(f"[max-file] нет байтов для {f.get('name')}")
        return
    name = f.get("name") or "file"
    up_type = _max_upload_type(name, f.get("kind"))
    info = await client.upload_file(up_type, name, raw)
    token = info.get("token") or (info.get("photos") and None)
    if not token:
        # для image MAX иногда отдаёт {"photos": {...}} вместо token — берём payload целиком
        if up_type == "image" and isinstance(info.get("photos"), dict):
            attachment = {"type": "image", "payload": info}
        else:
            log.warning(f"[max-file] upload без token: {info}")
            return
    else:
        attachment = {"type": up_type, "payload": {"token": token}}
    await client.send_message(
        text=f.get("caption") or "",
        chat_id=chat_id,
        attachments=[attachment],
        fmt=None,
    )
    log.info(f"[max-file] отправлен {name} как {up_type}")


@api.post("/push_message", response_model=PushMessageResponse)
async def push_message(req: PushMessageRequest):
    if client is None:
        raise HTTPException(status_code=503, detail="MAX bot не инициализирован")

    # файлы агента (если пришли) — грузим и шлём отдельными сообщениями-вложениями
    if req.files:
        for f in req.files:
            try:
                await _send_agent_file(int(req.chat_id), f)
            except MaxApiError as e:
                log.warning(f"[max-file] API error {e.status}: {e.data}")
            except Exception as e:
                log.warning(f"[max-file] push file fail: {e}")

    if not req.text:
        return PushMessageResponse(ok=True)
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
    try:
        await client.set_commands([
            {"name": "start", "description": "Настройки агента для этого чата"},
        ])
    except Exception as e:
        log.warning(f"set_commands не удался (продолжаю): {e}")
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
