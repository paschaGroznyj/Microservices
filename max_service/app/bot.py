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
import time
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
    menu_alarms, menu_sessions,
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

# ── Оркестратор / буфер реплик (как в telegram_service) ──
ORCH_THROTTLE_SEC = float(os.getenv("ORCH_THROTTLE_SEC", "6"))
BUF_MAXLEN        = int(os.getenv("BUF_MAXLEN", "40"))
BUF_TTL           = int(os.getenv("BUF_TTL", "3600"))
AGENT_BUF_CLIP    = int(os.getenv("AGENT_BUF_CLIP", "400"))

# Типы апдейтов, которые слушаем на этапе ядра.
UPDATE_TYPES = ["message_created", "bot_added", "bot_started", "message_callback"]

# mid последней плашки "Думаю..." по chat_id — чтобы ответ агента отредактировал её.
_thinking_messages: dict[int, str] = {}


def _extract_mid(resp: dict) -> str | None:
    """mid из ответа POST /messages: {"message":{"body":{"mid":...}}}."""
    try:
        return (resp or {}).get("message", {}).get("body", {}).get("mid")
    except Exception:
        return None


async def _send_thinking(chat_id: int) -> None:
    """Плашка «Думаю...» сразу после входящего сообщения. mid запоминаем,
    первый текстовый чанк ответа агента снимет её через edit_message."""
    if client is None:
        return
    try:
        resp = await client.send_message(text="Думаю...", chat_id=chat_id, fmt=None)
        mid = _extract_mid(resp)
        if mid:
            _thinking_messages[chat_id] = mid
    except Exception as e:
        log.warning(f"thinking send fail chat={chat_id}: {e}")


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
# Инфо о самом боте (из get_me) — для распознавания обращения в группах.
_bot_id: int | None = None
_bot_name: str | None = None
_bot_username: str | None = None

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


# ══════════════════════════════════════════════════════════════
# Оркестрация: буфер реплик в Redis + lite-LLM-фильтр (как в TG)
# ══════════════════════════════════════════════════════════════
def _k_buf(chat_id: int) -> str:      return f"max:buf:{chat_id}"
def _k_buf_mark(chat_id: int) -> str: return f"max:buf_mark:{chat_id}"
def _k_orch_ts(chat_id: int) -> str:  return f"max:orch_ts:{chat_id}"


async def buffer_push(chat_id: int, author: str, text: str) -> None:
    """Добавляет реплику в буфер (rpush + ltrim + TTL)."""
    r = await get_redis()
    line = f"{author}: {text}".replace("\n", " ").strip()
    if not line:
        return
    try:
        pipe = r.pipeline()
        pipe.rpush(_k_buf(chat_id), line)
        pipe.ltrim(_k_buf(chat_id), -BUF_MAXLEN, -1)
        pipe.expire(_k_buf(chat_id), BUF_TTL)
        await pipe.execute()
    except Exception as e:
        log.warning(f"buffer_push fail chat={chat_id}: {e}")


async def buffer_lines(chat_id: int) -> list[str]:
    """Скользящее окно последних реплик (для оркестратора)."""
    r = await get_redis()
    try:
        return await r.lrange(_k_buf(chat_id), 0, -1)
    except Exception as e:
        log.warning(f"buffer_lines fail chat={chat_id}: {e}")
        return []


async def buffer_push_agent(chat_id: int, agent_name: str | None, text: str) -> None:
    """Кладёт ОТВЕТ агента в тот же буфер — чтобы оркестратор видел связь
    «это продолжение диалога с ботом». Обрезаем до AGENT_BUF_CLIP."""
    author = (agent_name or "").strip() or "Ассистент"
    body = (text or "").replace("\n", " ").strip()
    if not body:
        return
    if len(body) > AGENT_BUF_CLIP:
        body = body[:AGENT_BUF_CLIP].rstrip() + "…"
    await buffer_push(chat_id, author, body)


async def buffer_flush(chat_id: int) -> list[str]:
    """Берёт реплики ПОСЛЕ маркера (что ещё не отдавали агенту) и сдвигает
    маркер. Буфер НЕ удаляется — оркестратор держит скользящее окно."""
    r = await get_redis()
    try:
        async with r.pipeline(transaction=True) as pipe:
            pipe.lrange(_k_buf(chat_id), 0, -1)
            pipe.get(_k_buf_mark(chat_id))
            res = await pipe.execute()
        all_lines = res[0] or []
        try:
            mark = int(res[1]) if res[1] is not None else 0
        except (TypeError, ValueError):
            mark = 0
        total = len(all_lines)
        if mark > total:
            mark = 0
        fresh = all_lines[mark:]
        await r.set(_k_buf_mark(chat_id), str(total), ex=BUF_TTL)
        return fresh
    except Exception as e:
        log.warning(f"buffer_flush fail chat={chat_id}: {e}")
        return []


async def orchestrator_allowed(chat_id: int) -> bool:
    """Throttle: не дёргать оркестратор чаще ORCH_THROTTLE_SEC на чат."""
    r = await get_redis()
    try:
        ok = await r.set(_k_orch_ts(chat_id), "1", nx=True, ex=int(ORCH_THROTTLE_SEC))
        return bool(ok)
    except Exception as e:
        log.warning(f"orch throttle fail chat={chat_id}: {e}")
        return True


async def orchestrator_should_respond(chat_id: int, lines: list[str]) -> bool:
    """Спрашивает lite-LLM в agent_service — реагировать на буфер или нет.
    Переиспользуем общий эндпоинт /api/tg/should_respond (платформонезависим)."""
    if not lines:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(
                f"{AGENT_SERVICE_URL}/api/tg/should_respond",
                json={"chat_id": str(chat_id), "buffer_lines": lines},
            )
            resp.raise_for_status()
            return bool(resp.json().get("respond", False))
    except Exception as e:
        log.warning(f"orchestrator call fail chat={chat_id}: {e}")
        return False  # fail в сторону молчания


async def upsert_member(chat_id: int, user_id: int, username: str | None,
                        first_name: str | None) -> None:
    """Пассивный сбор участников MAX-чата → chats_service /max/members.
    Fail-open: если эндпоинта ещё нет (404) — молча пропускаем."""
    if not user_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"{CHATS_SERVICE_URL}/max/members", json={
                "chat_id": str(chat_id),
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
            })
    except Exception as e:
        log.warning(f"upsert_member fail chat={chat_id}: {e}")


def _is_group(recipient: dict) -> bool:
    """MAX: recipient.chat_type ∈ dialog|chat|channel. Группа — не dialog."""
    ct = (recipient or {}).get("chat_type")
    if ct:
        return ct != "dialog"
    # Фолбэк, если chat_type не пришёл: в dialog chat_id == user_id собеседника.
    return True


def _mentions_bot(msg: dict, text: str) -> bool:
    """Прямое обращение к боту в группе: reply на сообщение бота ИЛИ
    текстовый тег по имени/username бота. markup во входящем MAX-теле нет,
    поэтому ловим reply (message.link.type==reply + sender==bot) и тег."""
    # 1) reply на бота
    link = msg.get("link") or {}
    if link.get("type") == "reply":
        ls = (link.get("sender") or {})
        if _bot_id and ls.get("user_id") == _bot_id:
            return True
    # 2) тег по username / имени бота в тексте
    low = (text or "").lower()
    if _bot_username and f"@{_bot_username.lower()}" in low:
        return True
    if _bot_name:
        name = _bot_name.strip().lower()
        if name and name in low:
            return True
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
# ════════════════════════════════════════════════════════════
# Сессии + имя агента (per-chat, Redis-реестр по аналогии с telegram_service)
# ════════════════════════════════════════════════════════════
# Физический max chat_id — адрес доставки; session_id (chat_id в chats_service)
# — ключ памяти/истории агента. Один чат → N сессий.
def _k_session(max_chat_id: int) -> str: return f"max:session:{max_chat_id}"
def _k_sessions_reg(max_chat_id: int) -> str: return f"max:sessions:{max_chat_id}"
def _k_setname(max_chat_id: int, uid) -> str: return f"max:setname:{max_chat_id}:{uid}"


async def get_current_session(max_chat_id: int):
    try:
        r = await get_redis()
        return await r.get(_k_session(max_chat_id))
    except Exception as e:
        log.warning(f"get_current_session fail chat={max_chat_id}: {e}")
        return None


async def set_current_session(max_chat_id: int, session_id) -> None:
    try:
        r = await get_redis()
        await r.set(_k_session(max_chat_id), str(session_id))
    except Exception as e:
        log.warning(f"set_current_session fail chat={max_chat_id}: {e}")


async def create_session(user_id) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(f"{CHATS_SERVICE_URL}/chat/new", json={"user_id": user_id})
            resp.raise_for_status()
            return str(resp.json().get("chat_id"))
    except Exception as e:
        log.error(f"create_session fail user={user_id}: {e}")
        return None


async def register_session(max_chat_id: int, session_id, title: str = "") -> None:
    try:
        r = await get_redis()
        payload = json.dumps({"title": title or "", "ts": int(time.time())})
        await r.hset(_k_sessions_reg(max_chat_id), str(session_id), payload)
    except Exception as e:
        log.warning(f"register_session fail chat={max_chat_id}: {e}")


async def touch_session_title(max_chat_id: int, session_id, last_text: str) -> None:
    try:
        r = await get_redis()
        key = _k_sessions_reg(max_chat_id)
        raw = await r.hget(key, str(session_id))
        meta = json.loads(raw) if raw else {}
        if not meta.get("title"):
            t = " ".join((last_text or "").split())
            meta["title"] = t[:60] if t else ""
        meta["ts"] = int(time.time())
        await r.hset(key, str(session_id), json.dumps(meta))
    except Exception as e:
        log.warning(f"touch_session_title fail chat={max_chat_id}: {e}")


async def ensure_session(max_chat_id: int, user_id) -> str:
    sid = await get_current_session(max_chat_id)
    if sid:
        return sid
    sid = await create_session(user_id)
    if not sid:
        return str(max_chat_id)
    await set_current_session(max_chat_id, sid)
    await register_session(max_chat_id, sid)
    log.info(f"session created chat={max_chat_id} user={user_id} -> {sid}")
    return sid


async def list_sessions(max_chat_id: int) -> list:
    try:
        r = await get_redis()
        raw = await r.hgetall(_k_sessions_reg(max_chat_id))
        out = []
        for sid, meta_raw in (raw or {}).items():
            try:
                meta = json.loads(meta_raw)
            except Exception:
                meta = {}
            out.append({"id": sid, "title": meta.get("title") or "",
                        "ts": int(meta.get("ts") or 0)})
        out.sort(key=lambda x: x["ts"], reverse=True)
        return out
    except Exception as e:
        log.error(f"list_sessions fail chat={max_chat_id}: {e}")
        return []


async def _consume_setname(max_chat_id: int, user_id, text: str) -> bool:
    """Если для чата/юзера висит флаг ожидания имени — сохраняем имя
    и гасим флаг. Возвращает True, если сообщение было поглощено."""
    try:
        r = await get_redis()
        key = _k_setname(max_chat_id, user_id)
        if not await r.get(key):
            return False
        await r.delete(key)
    except Exception as e:
        log.warning(f"consume_setname fail chat={max_chat_id}: {e}")
        return False
    raw = (text or "").strip()
    new_name = "" if raw == "-" else raw[:40]
    ok = await save_chat_settings(max_chat_id, {"agent_name": new_name})
    if client is not None:
        if not ok:
            msg = "Не удалось сохранить имя, попробуйте позже."
        elif new_name:
            msg = f"Имя агента: {new_name}"
        else:
            msg = "Имя сброшено к дефолту."
        try:
            await client.send_message(text=msg, chat_id=max_chat_id, fmt=None)
        except Exception:
            pass
    return True


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
    # chat_id здесь — ФИЗИЧЕСКИЙ max chat_id. Ключ памяти агента — активная сессия.
    session_id = await ensure_session(chat_id, user_id)
    await touch_session_title(chat_id, session_id, text)
    payload = {
        "user_id": user_id,
        "username": username or f"max_{user_id}",
        "chat_id": str(session_id),
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
        await upload_items_to_agent(AGENT_SERVICE_URL, message_id, session_id,
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
    is_group = _is_group(recipient)
    return {
        "chat_id": int(chat_id),
        "user_id": int(sender.get("user_id") or 0),
        "username": _sender_name(sender),
        "text": text,
        "message_id": body.get("mid") or msg.get("message_id"),
        "has_attachments": has_att,
        "is_group": is_group,
        # прямое обращение к боту (reply/тег) — считаем только в группе
        "is_mention": bool(is_group and _mentions_bot(msg, text)),
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

    # Будильники (ЗАГЛУШКА: пресеты без бека проактивных триггеров)
    if data == "max:menu:alarms":
        await ack()
        await rerender(menu_alarms())
        return
    if data.startswith("max:alarm:stub"):
        await ack("Будильники скоро — в разработке")
        return

    # Кто жал /start (АДМИН, ЗАГЛУШКА: у MAX пока нет start_log на беке)
    if data == "max:admin:startlog":
        if not is_admin:
            await ack("Только для админа")
            return
        await ack()
        if message_id:
            try:
                await client.edit_message(
                    message_id,
                    "**🔒 Кто жал /start**\n\nЖурнал входов скоро — в разработке.",
                    attachments=menu_root(is_admin=is_admin), fmt="markdown")
            except Exception as e:
                log.warning(f"startlog stub edit fail: {e}")
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

    # ── Имя агента: ставим флаг ожидания, следующее сообщение юзера = имя
    if data == "max:menu:name":
        await ack()
        try:
            r = await get_redis()
            await r.set(_k_setname(chat_id, user_id), "1", ex=300)
        except Exception as e:
            log.warning(f"setname flag fail: {e}")
        if client is not None:
            await client.send_message(
                text=("Отправьте новое имя агента одним сообщением.\n"
                      "Для сброса к имени по умолчанию отправьте `-`."),
                chat_id=chat_id, fmt="markdown")
        return

    # ── Сессии
    if data.startswith("max:sess:list:"):
        await ack()
        try:
            page = int(data.split(":", 3)[3])
        except (ValueError, IndexError):
            page = 0
        active = await get_current_session(chat_id)
        items = await list_sessions(chat_id)
        await rerender(menu_sessions(items, page, active))
        return

    if data == "max:sess:new":
        await ack("Новая сессия")
        sid = await create_session(user_id)
        if sid:
            await set_current_session(chat_id, sid)
            await register_session(chat_id, sid)
            log.info(f"session switch(new) chat={chat_id} -> {sid}")
        active = await get_current_session(chat_id)
        items = await list_sessions(chat_id)
        await rerender(menu_sessions(items, 0, active))
        return

    if data.startswith("max:sess:open:"):
        sid = data.split(":", 3)[3]
        await set_current_session(chat_id, sid)
        log.info(f"session switch(open) chat={chat_id} -> {sid}")
        await ack("Переключено")
        active = await get_current_session(chat_id)
        items = await list_sessions(chat_id)
        await rerender(menu_sessions(items, 0, active))
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
    _words = (info.get("text") or "").strip().split()
    if _words and _words[0].lower() in ("/start", "start"):
        await cmd_start(info["chat_id"], info.get("user_id"))
        return
    # Ввод имени агента: если висит флаг ожидания — следующее сообщение = имя.
    if await _consume_setname(info["chat_id"], info.get("user_id"), info.get("text") or ""):
        return
    if not await is_allowed(info["chat_id"]):
        if client is not None:
            await client.send_message(
                text=(f"Доступ к боту ограничен. Регистрация — через {CATCHPM_URL}\n"
                      f"chat_id: {info['chat_id']}"),
                chat_id=info["chat_id"], fmt=None)
        return

    # ── Оркестрация (как в telegram_service) ──────────────────────────────
    # Пассивный сбор участников (для будущих проактивных сценариев).
    await upsert_member(info["chat_id"], info.get("user_id"),
                        info.get("username"), info.get("username"))

    is_group = info.get("is_group", True)
    approved = True
    if is_group:
        # В группе всегда пишем реплику в буфер (контекст для оркестратора).
        if (info.get("text") or "").strip():
            await buffer_push(info["chat_id"], info.get("username") or "user",
                              info["text"].strip())
        # Прямое обращение (reply на бота / тег по имени) — отвечаем сразу.
        if info.get("is_mention"):
            approved = True
        else:
            # Иначе решает режим чата: mention — молчим, orchestrator — спрашиваем lite-LLM.
            _settings = await get_chat_settings(info["chat_id"])
            trigger_mode = (_settings or {}).get("trigger_mode") or "orchestrator"
            approved = False
            if trigger_mode == "orchestrator" and await orchestrator_allowed(info["chat_id"]):
                lines = await buffer_lines(info["chat_id"])
                approved = await orchestrator_should_respond(info["chat_id"], lines)
        if not approved:
            return
    # Личка (dialog) — всегда обращение к агенту, отвечаем без фильтра.

    # Плашка "Думаю..." сразу на входящее — ответ агента отредактирует её.
    # Для альбома (дебаунс) шлём одну плашку на первое вложение серии.
    if info["chat_id"] not in _thinking_messages:
        await _send_thinking(info["chat_id"])
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
            else:
                # ни файлов, ни текста — снимаем висящую плашку "Думаю..."
                _tm = _thinking_messages.pop(info["chat_id"], None)
                if _tm and client is not None:
                    try:
                        await client.delete_message(_tm)
                    except Exception:
                        pass
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
    # send_message_with_attachment ретраит POST /messages при
    # attachment.not.ready (файл ещё обрабатывается на стороне MAX после upload).
    await client.send_message_with_attachment(
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

    # chat_id для доставки в MAX ДОЛЖЕН быть числовым (физический max_chat_id).
    # consumer присылает его из bot_message.max_chat_id; если прилетит нечисловой
    # (напр. session_id-UUID при старом контракте) — не роняем 500, логируем и выходим.
    try:
        cid = int(req.chat_id)
    except (TypeError, ValueError):
        log.error(f"push_message: нечисловой chat_id={req.chat_id!r} — доставка невозможна")
        return PushMessageResponse(ok=False, error=f"non-numeric chat_id: {req.chat_id}")

    # файлы агента (если пришли) — грузим и шлём отдельными сообщениями-вложениями
    if req.files:
        for f in req.files:
            try:
                await _send_agent_file(cid, f)
            except MaxApiError as e:
                log.warning(f"[max-file] API error {e.status}: {e.data}")
            except Exception as e:
                log.warning(f"[max-file] push file fail: {e}")

    thinking_mid = _thinking_messages.pop(cid, None)

    if not req.text:
        # Ответ без текста (напр. только файлы): снимаем висящую плашку.
        if thinking_mid:
            try:
                if req.files:
                    await client.delete_message(thinking_mid)
                else:
                    await client.edit_message(thinking_mid, "✅", fmt=None)
            except Exception as e:
                log.warning(f"thinking cleanup fail chat={cid}: {e}")
        return PushMessageResponse(ok=True)
    fmt = (req.parse_mode or "markdown").lower()
    if fmt not in ("markdown", "html"):
        fmt = None
    try:
        first = True
        for chunk in split_message(req.text):
            if first and thinking_mid:
                # Первый чанк заменяет плашку "Думаю..." через edit.
                try:
                    await client.edit_message(thinking_mid, chunk, fmt=fmt)
                except Exception as e:
                    log.warning(f"edit thinking fail chat={cid}: {e}; фоллбэк send")
                    await client.send_message(text=chunk, chat_id=cid, fmt=fmt)
            else:
                await client.send_message(text=chunk, chat_id=cid, fmt=fmt)
            first = False
        # Ответ агента — в буфер оркестратора (чтобы видел продолжение диалога).
        try:
            await buffer_push_agent(cid, None, req.text)
        except Exception as e:
            log.warning(f"buffer_push_agent fail chat={cid}: {e}")
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
    global client, _poll_task, _bot_id, _bot_name, _bot_username
    client = MaxClient(MAX_TOKEN_API)
    try:
        me = await client.get_me()
        _bot_id = me.get("user_id")
        _bot_name = me.get("name")
        _bot_username = me.get("username")
        log.info(f"MAX bot: {_bot_name} (user_id={_bot_id}, username={_bot_username})")
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
