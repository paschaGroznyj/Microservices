"""
telegram_service/app/bot.py
───────────────────────────
Тонкий Telegram-шлюз в микросервис CatchPM.

Поток (Коммит 1):
  update → allow-list (Redis `tg:allowed_chats`, фолбэк HTTP chats_service)
         → буфер последних реплик в Redis (`tg:buf:{chat_id}`, с маркером last_flushed)
         → решение:
             • @mention / reply на бота  → одобрено СРАЗУ
             • иначе                       → оркестратор agent_service /api/tg/should_respond
         → если одобрено: склеиваем накопленный буфер «с последнего ответа агента»
           в одну строку → POST chats_service /send_message (Kafka, gateway_target="telegram")
           → агент отвечает → endpoint_consumer → /push_message боту.
  Одобренные сообщения логируются в chats через сам пайплайн send_message.
  Молчаливый поток НЕ логируется (память агента не засоряется).

Host-режим: контейнеры доступны через проброшенные порты хоста (127.0.0.1:*).

Env:
  TELEGRAM_BOT_TOKEN  — токен @BotFather
  CHATS_SERVICE_URL   — http://127.0.0.1:8001 (send_message + /tg/*)
  AGENT_SERVICE_URL   — http://127.0.0.1:9001 (оркестратор should_respond)
  REDIS_URL           — redis://127.0.0.1:6379/0
  TG_PUSH_PORT        — порт push API (default 8010)
  ORCH_THROTTLE_SEC   — минимальный интервал вызова оркестратора на чат (default 6)
  BUF_MAXLEN          — сколько реплик держим в буфере (default 40)
  BUF_TTL             — TTL буфера, сек (default 3600)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import pathlib
import random
import re
import time
import uuid

import httpx
import redis.asyncio as aioredis
from telegram import (
    Update, Message, MessageEntity,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters,
)

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHATS_SERVICE_URL  = os.environ.get("CHATS_SERVICE_URL", "http://127.0.0.1:8001")
AGENT_SERVICE_URL  = os.environ.get("AGENT_SERVICE_URL", "http://127.0.0.1:9001")
REDIS_URL          = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
TG_PUSH_PORT       = int(os.environ.get("TG_PUSH_PORT", "8010"))

ORCH_THROTTLE_SEC  = float(os.environ.get("ORCH_THROTTLE_SEC", "6"))
BUF_MAXLEN         = int(os.environ.get("BUF_MAXLEN", "40"))
BUF_TTL            = int(os.environ.get("BUF_TTL", "3600"))
# Обрезка ответа агента перед записью в буфер оркестратора (чтобы не раздувать промпт).
AGENT_BUF_CLIP     = int(os.environ.get("AGENT_BUF_CLIP", "400"))
ALLOWED_TTL        = int(os.environ.get("ALLOWED_TTL", "60"))

# ── Проактивные триггеры (будильники + poke) ────────────────────────────────
# 3 broadcast-слота (завтрак/обед/ужин) по МСК + адресные poke между ними.
TG_TZ              = os.environ.get("TG_TZ", "Europe/Moscow")
# Вероятность дёрнуть конкретный чат на ежечасном тике poke (0..1).
TG_POKE_PROBABILITY = float(os.environ.get("TG_POKE_PROBABILITY", "0.18"))
# Окно адресных poke: часы МСК [start, end) — до 18, слоты 9/13 исключаются.
TG_POKE_HOUR_START = int(os.environ.get("TG_POKE_HOUR_START", "10"))
TG_POKE_HOUR_END   = int(os.environ.get("TG_POKE_HOUR_END", "18"))  # exclusive
# Джиттер запуска poke относительно ровного часа, ± минут.
TG_POKE_JITTER_MIN = int(os.environ.get("TG_POKE_JITTER_MIN", "15"))
# Окно ожидания ответа после адресной инициации (грусть), секунды: рандом [lo, hi].
TG_SAD_WAIT_LO     = int(os.environ.get("TG_SAD_WAIT_LO", "300"))   # 5 мин
TG_SAD_WAIT_HI     = int(os.environ.get("TG_SAD_WAIT_HI", "600"))   # 10 мин
# Суточный throttle адресных poke на чат (сек).
TG_POKE_DAILY_TTL  = int(os.environ.get("TG_POKE_DAILY_TTL", "86400"))

MAX_FILE_SIZE = 20 * 1024 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tg_bot")

api = FastAPI(title="TG Bot Push API")

_bot_app: Application | None = None
_redis: aioredis.Redis | None = None

# message_id последнего "Думаю..." по chat_id (для edit_thinking)
_thinking_messages: dict[int, int] = {}


# ── Redis keys ──────────────────────────────────────────────────────────────────
def _k_allowed() -> str:                return "tg:allowed_chats"
def _k_buf(chat_id: int) -> str:        return f"tg:buf:{chat_id}"
def _k_buf_mark(chat_id: int) -> str:   return f"tg:buf_mark:{chat_id}"
def _k_orch_ts(chat_id: int) -> str:    return f"tg:orch_ts:{chat_id}"
# Суточный throttle адресных poke на чат.
def _k_poke(chat_id: int) -> str:       return f"tg:poke:{chat_id}"
# Ожидание ответа целевого юзера после адресной инициации (флаг «грусти»).
def _k_await(chat_id: int, uid: int) -> str: return f"tg:await:{chat_id}:{uid}"
def _k_gate(chat_id: int) -> str:       return f"tg:gate:{chat_id}"
def _k_setname(chat_id: int, uid: int) -> str: return f"tg:setname:{chat_id}:{uid}"


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


# ── allow-list: Redis-кэш + HTTP-фолбэк в chats_service ─────────────────────────
async def is_allowed(chat_id: int) -> bool:
    """Проверка вхождения chat_id в allow-list. Кэш в Redis (set, TTL)."""
    r = await get_redis()
    key = _k_allowed()
    try:
        if await r.exists(key):
            return await r.sismember(key, str(chat_id))
    except Exception as e:
        log.warning(f"redis allow-check fail: {e}")
    # кэш пуст/протух → тянем из chats_service
    ids = await _fetch_allowed_from_chats()
    if ids is None:
        # fail-open в сторону запрета: если сервис недоступен, не пускаем чужих
        return False
    try:
        pipe = r.pipeline()
        pipe.delete(key)
        if ids:
            pipe.sadd(key, *[str(x) for x in ids])
        pipe.expire(key, ALLOWED_TTL)
        await pipe.execute()
    except Exception as e:
        log.warning(f"redis allow-cache set fail: {e}")
    return str(chat_id) in {str(x) for x in ids}


async def _fetch_allowed_from_chats() -> list[str] | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CHATS_SERVICE_URL}/tg/allowed")
            resp.raise_for_status()
            return resp.json().get("chat_ids", [])
    except Exception as e:
        log.error(f"fetch allowed fail: {e}")
        return None


async def get_chat_settings(chat_id: int) -> dict:
    """Настройки чата (модель/тулы/триггер) из chats_service. Fail-open → дефолты."""
    defaults = {
        "agent_model": None, "selected_tools": None,
        "trigger_mode": "orchestrator", "orchestrator_context_n": 6,
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CHATS_SERVICE_URL}/tg/settings/{chat_id}")
            resp.raise_for_status()
            s = resp.json().get("settings")
            return s if s else defaults
    except Exception as e:
        log.warning(f"get_chat_settings fail chat={chat_id}: {e}")
        return defaults


# ── Redis-буфер реплик группы (для оркестратора и склейки) ──────────────────────
async def buffer_push(chat_id: int, author: str, text: str):
    """Добавляет реплику в буфер (rpush + ltrim + TTL)."""
    r = await get_redis()
    line = f"{author}: {text}".replace("\n", " ").strip()
    try:
        pipe = r.pipeline()
        pipe.rpush(_k_buf(chat_id), line)
        pipe.ltrim(_k_buf(chat_id), -BUF_MAXLEN, -1)
        pipe.expire(_k_buf(chat_id), BUF_TTL)
        await pipe.execute()
    except Exception as e:
        log.warning(f"buffer_push fail chat={chat_id}: {e}")


async def buffer_lines(chat_id: int) -> list[str]:
    r = await get_redis()
    try:
        return await r.lrange(_k_buf(chat_id), 0, -1)
    except Exception as e:
        log.warning(f"buffer_lines fail chat={chat_id}: {e}")
        return []


async def buffer_push_agent(chat_id: int, agent_name: str | None, text: str):
    """Кладёт ОТВЕТ агента в тот же буфер, что и реплики людей.

    Без этого оркестратор видит только реплики людей и теряет связь
    «это продолжение диалога с ботом». Ответ обрезаем до AGENT_BUF_CLIP
    символов, чтобы не раздувать промпт оркестратора.
    """
    author = (agent_name or "").strip() or "Ассистент"
    body = (text or "").replace("\n", " ").strip()
    if not body:
        return
    if len(body) > AGENT_BUF_CLIP:
        body = body[:AGENT_BUF_CLIP].rstrip() + "…"
    await buffer_push(chat_id, author, body)


async def buffer_flush(chat_id: int) -> list[str]:
    """
    Склейка для отправки агенту: берёт реплики ПОСЛЕ маркера
    last_flushed (то, что ещё не отдавалось агенту).

    В отличие от старой версии буфер НЕ удаляется — мы лишь сдвигаем
    маркер на текущую длину. Так оркестратор (buffer_lines) всегда
    видит скользящее окно последних реплик включая ответы агента,
    а агенту уходит только новое — без дублей.
    """
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
        if mark > total:  # буфер усёкся (ltrim/TTL) — маркер устарел
            mark = 0
        fresh = all_lines[mark:]
        # сдвигаем маркер на конец (всё отдано), TTL = как у буфера
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


# ── Оркестратор (agent_service llm_lite) ────────────────────────────────────────
async def orchestrator_should_respond(chat_id: int, lines: list[str]) -> bool:
    if not lines:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{AGENT_SERVICE_URL}/api/tg/should_respond",
                json={"chat_id": str(chat_id), "buffer_lines": lines},
            )
            resp.raise_for_status()
            return bool(resp.json().get("respond", False))
    except Exception as e:
        log.warning(f"orchestrator call fail chat={chat_id}: {e}")
        return False  # fail-open в сторону молчания


# ── Отправка одобренного сообщения в chats_service (Kafka) ──────────────────────
async def dispatch_to_agent(chat_id: int, user_id: int, username: str,
                            merged_text: str, file_b64: str | None = None,
                            file_name: str | None = None):
    """
    Формирует ChatMessage (gateway_target="telegram") и шлёт в chats_service.
    Агент ответит асинхронно → endpoint_consumer → /push_message боту.
    """
    settings = await get_chat_settings(chat_id)
    request_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    has_files = bool(file_b64)

    payload = {
        "user_id": user_id,
        "username": username,
        "chat_id": str(chat_id),
        "message": merged_text,
        "type_agent": "pm",
        "gateway_target": "telegram",
        "request_id": request_id,
        "message_id": message_id,
        "selected_tools": settings.get("selected_tools") or [],
        "agent_model": settings.get("agent_model"),
        "context_size": "medium",
        "has_files": has_files,
        "is_from_bot": False,
        "tb_name": None,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{CHATS_SERVICE_URL}/send_message", json=payload)
            resp.raise_for_status()
        log.info(f"dispatched to agent chat={chat_id} req={request_id}")
    except Exception as e:
        log.error(f"dispatch_to_agent fail chat={chat_id}: {e}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# ПРОАКТИВНЫЕ ТРИГГЕРЫ: сбор участников + инициация + «грусть»
# ══════════════════════════════════════════════════════════════════════════════
async def upsert_member(chat_id: int, user_id: int, username: str | None,
                        first_name: str | None):
    """Пассивный сбор участников чата → chats_service (для рандомной инициации)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{CHATS_SERVICE_URL}/tg/members", json={
                "chat_id": str(chat_id),
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
            })
    except Exception as e:
        log.warning(f"upsert_member fail chat={chat_id}: {e}")


async def get_random_member(chat_id: int) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CHATS_SERVICE_URL}/tg/members/{chat_id}/random")
            resp.raise_for_status()
            return resp.json().get("member")
    except Exception as e:
        log.warning(f"get_random_member fail chat={chat_id}: {e}")
        return None


async def list_proactive_chats() -> list[str]:
    """Чаты с включёнными будильниками (is_active + proactive_enabled)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CHATS_SERVICE_URL}/tg/proactive_chats")
            resp.raise_for_status()
            rows = resp.json().get("chats", [])
            return [str(r["chat_id"]) for r in rows]
    except Exception as e:
        log.warning(f"list_proactive_chats fail: {e}")
        return []


async def dispatch_initiation(chat_id: int, slot: str, addressed: bool,
                              target: dict | None, sad_followup: bool = False):
    """
    Шлёт агенту проактивный payload (initiated=True). Текст задачи агент
    сгенерит сам из промпта инициации/грусти (см. tg_integration).
    """
    settings = await get_chat_settings(chat_id)
    target_uid = (target or {}).get("user_id")
    target_username = (target or {}).get("username") or (target or {}).get("first_name")
    payload = {
        "user_id": target_uid if target_uid else 0,
        "username": target_username or "чат",
        "chat_id": str(chat_id),
        "message": "",
        "type_agent": "pm",
        "gateway_target": "telegram",
        "request_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "selected_tools": settings.get("selected_tools") or [],
        "agent_model": settings.get("agent_model"),
        "context_size": "medium",
        "has_files": False,
        "is_from_bot": False,
        "tb_name": None,
        # проактив-поля
        "initiated": True,
        "slot": slot,
        "addressed": addressed,
        "target_username": target_username,
        "sad_followup": sad_followup,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{CHATS_SERVICE_URL}/send_message", json=payload)
            resp.raise_for_status()
        log.info(f"proactive dispatched chat={chat_id} slot={slot} "
                 f"addressed={addressed} sad={sad_followup} target={target_username}")
    except Exception as e:
        log.error(f"dispatch_initiation fail chat={chat_id}: {e}")


async def _sadness_watch(chat_id: int, target: dict):
    """
    После адресной инициации ждём рандом 5-10 мин. Если целевой юзер не ответил
    (флаг tg:await:{chat}:{uid} ещё жив) → шлём ОДИН грустный follow-up.
    Ответ юзера снимает флаг в on_text_message.
    """
    uid = target.get("user_id")
    if not uid:
        return
    r = await get_redis()
    wait = random.randint(TG_SAD_WAIT_LO, TG_SAD_WAIT_HI)
    try:
        # ставим флаг ожидания на время окна
        await r.set(_k_await(chat_id, uid), "1", ex=wait + 30)
    except Exception as e:
        log.warning(f"sadness set-await fail chat={chat_id}: {e}")
    await asyncio.sleep(wait)
    try:
        still_waiting = await r.get(_k_await(chat_id, uid))
    except Exception:
        still_waiting = None
    if still_waiting:
        # человек не ответил → грусть (один раз), снимаем флаг
        try:
            await r.delete(_k_await(chat_id, uid))
        except Exception:
            pass
        await dispatch_initiation(chat_id, "poke", addressed=True,
                                  target=target, sad_followup=True)


async def run_broadcast_slot(slot: str):
    """Завтрак/обед/ужин: broadcast во ВСЕ proactive-чаты, без адресата."""
    chats = await list_proactive_chats()
    log.info(f"[scheduler] broadcast slot={slot} chats={len(chats)}")
    for cid in chats:
        try:
            await dispatch_initiation(int(cid), slot, addressed=False, target=None)
        except Exception as e:
            log.warning(f"broadcast fail chat={cid}: {e}")


async def run_poke_tick():
    """
    Ежечасный (с джиттером) адресный poke между слотами.
    Для каждого proactive-чата с вероятностью TG_POKE_PROBABILITY и не чаще
    1 раза в сутки (Redis throttle) дёргаем рандомного участника + запускаем
    «грусть»-вотчер.
    """
    chats = await list_proactive_chats()
    r = await get_redis()
    log.info(f"[scheduler] poke tick, candidate chats={len(chats)}")
    for cid in chats:
        try:
            if random.random() > TG_POKE_PROBABILITY:
                continue
            # суточный throttle: NX ставит ключ только если его нет
            fresh = await r.set(_k_poke(int(cid)), "1", nx=True, ex=TG_POKE_DAILY_TTL)
            if not fresh:
                continue
            member = await get_random_member(int(cid))
            if not member:
                # некого дёргать — снимаем throttle-ключ, чтобы попробовать позже
                await r.delete(_k_poke(int(cid)))
                continue
            await dispatch_initiation(int(cid), "poke", addressed=True, target=member)
            asyncio.create_task(_sadness_watch(int(cid), member))
        except Exception as e:
            log.warning(f"poke fail chat={cid}: {e}")


def _schedule_poke_with_jitter(scheduler):
    """
    Планирует ОДИН запуск run_poke_tick со случайным сдвигом ±TG_POKE_JITTER_MIN
    минут от момента вызова (вызывается ежечасно в окне poke).
    """
    from datetime import datetime, timedelta
    jitter = random.randint(-TG_POKE_JITTER_MIN, TG_POKE_JITTER_MIN)
    run_at = datetime.now() + timedelta(minutes=max(0, jitter) + 0)
    # отрицательный джиттер = запустить почти сразу (в прошлое нельзя)
    if jitter < 0:
        run_at = datetime.now() + timedelta(seconds=5)
    scheduler.add_job(run_poke_tick, "date", run_date=run_at,
                      id=f"poke-{uuid.uuid4().hex[:8]}", misfire_grace_time=120)
    log.info(f"[scheduler] poke scheduled at {run_at} (jitter={jitter}m)")


def build_scheduler():
    """APScheduler: 3 cron-слота (МСК) + ежечасный джиттер-poke в окне."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = AsyncIOScheduler(timezone=TG_TZ)
    # Завтрак / обед / ужин — broadcast.
    sched.add_job(run_broadcast_slot, CronTrigger(hour=9,  minute=0, timezone=TG_TZ),
                  args=["morning"], id="slot-morning", misfire_grace_time=300)
    sched.add_job(run_broadcast_slot, CronTrigger(hour=13, minute=0, timezone=TG_TZ),
                  args=["lunch"], id="slot-lunch", misfire_grace_time=300)
    sched.add_job(run_broadcast_slot, CronTrigger(hour=18, minute=0, timezone=TG_TZ),
                  args=["dinner"], id="slot-dinner", misfire_grace_time=300)
    # Ежечасный тик в окне [start, end): планирует poke со случайным джиттером.
    hours = ",".join(str(h) for h in range(TG_POKE_HOUR_START, TG_POKE_HOUR_END))
    sched.add_job(_schedule_poke_with_jitter,
                  CronTrigger(hour=hours, minute=0, timezone=TG_TZ),
                  args=[sched], id="poke-hourly", misfire_grace_time=120)
    return sched


# ══════════════════════════════════════════════════════════════════════════════
# PUSH API (ответ агента → телеграм)
# ══════════════════════════════════════════════════════════════════════════════
TELEGRAM_MAX_LEN = 4000


def split_message_safely(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks, remaining = [], text
    while len(remaining) > max_len:
        cut = _find_safe_cut(remaining, max_len)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return _fix_unclosed_tags(chunks)


def _find_safe_cut(text: str, max_len: int) -> int:
    window = text[:max_len]
    last_lt = window.rfind("<")
    last_gt = window.rfind(">")
    if last_lt > last_gt:
        return last_lt if last_lt > 0 else max_len
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep)
        if idx > max_len * 0.5:
            return idx + len(sep)
    return max_len


def _fix_unclosed_tags(chunks: list[str]) -> list[str]:
    """Простейшая балансировка парных HTML-тегов между чанками."""
    paired = ["b", "i", "u", "s", "code", "pre"]
    fixed = []
    for chunk in chunks:
        opened = []
        for tag in paired:
            o = len(re.findall(fr"<{tag}>", chunk))
            c = len(re.findall(fr"</{tag}>", chunk))
            if o > c:
                opened.extend([tag] * (o - c))
        closing = "".join(f"</{t}>" for t in reversed(opened))
        fixed.append(chunk + closing)
    return fixed


class PushMessageRequest(BaseModel):
    chat_id: int
    text: str
    parse_mode: str = "HTML"
    edit_thinking: bool = False
    files: list | None = None


class PushMessageResponse(BaseModel):
    ok: bool
    message_ids: list[int] = []
    error: str | None = None


def chat_id_has_thinking(chat_id: int) -> bool:
    return chat_id in _thinking_messages


# ── DEBUG: ручной запуск проактивных триггеров (для теста без ожидания cron) ──
# Защищён токеном TG_DEBUG_TOKEN (если задан). Если токен не задан в env —
# эндпоинт ОТКЛЮЧЁН (возвращает 404), чтобы не палить в проде.
TG_DEBUG_TOKEN = os.environ.get("TG_DEBUG_TOKEN", "")


class DebugTriggerResponse(BaseModel):
    ok: bool
    action: str
    detail: str | None = None


@api.post("/tg/debug/trigger", response_model=DebugTriggerResponse)
async def debug_trigger(
    slot: str = "poke",
    chat_id: int | None = None,
    token: str = "",
):
    """
    Ручной запуск проактивного триггера (для теста, минуя APScheduler).

    Параметры (query):
      slot    — 'morning' | 'lunch' | 'dinner' → broadcast во все proactive-чаты
                (или в один chat_id, если задан);
                'poke' → адресный poke рандомному участнику chat_id.
      chat_id — конкретный чат. Для broadcast опционален (без него — все
                proactive-чаты); для 'poke' ОБЯЗАТЕЛЕН.
      token   — TG_DEBUG_TOKEN (должен совпасть с env).

    Пример:
      POST /tg/debug/trigger?slot=poke&chat_id=-100123&token=SECRET
      POST /tg/debug/trigger?slot=lunch&token=SECRET
    """
    if not TG_DEBUG_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    if token != TG_DEBUG_TOKEN:
        raise HTTPException(status_code=403, detail="Bad debug token")

    slot = (slot or "poke").lower()

    if slot == "poke":
        if not chat_id:
            raise HTTPException(status_code=400, detail="chat_id required for poke")
        if not await is_allowed(chat_id):
            raise HTTPException(status_code=400, detail="chat not registered")
        member = await get_random_member(chat_id)
        if not member:
            return DebugTriggerResponse(ok=False, action="poke",
                                        detail="no members collected yet")
        await dispatch_initiation(chat_id, "poke", addressed=True, target=member)
        asyncio.create_task(_sadness_watch(chat_id, member))
        who = member.get("username") or member.get("first_name") or member.get("user_id")
        return DebugTriggerResponse(ok=True, action="poke",
                                    detail=f"poked {who} in chat {chat_id}")

    if slot in ("morning", "lunch", "dinner"):
        if chat_id:
            if not await is_allowed(chat_id):
                raise HTTPException(status_code=400, detail="chat not registered")
            await dispatch_initiation(chat_id, slot, addressed=False, target=None)
            return DebugTriggerResponse(ok=True, action=f"broadcast:{slot}",
                                        detail=f"broadcast to chat {chat_id}")
        chats = await list_proactive_chats()
        for cid in chats:
            try:
                await dispatch_initiation(int(cid), slot, addressed=False, target=None)
            except Exception as e:
                log.warning(f"debug broadcast fail chat={cid}: {e}")
        return DebugTriggerResponse(ok=True, action=f"broadcast:{slot}",
                                    detail=f"broadcast to {len(chats)} proactive chats")

    raise HTTPException(status_code=400,
                        detail="slot must be one of: morning|lunch|dinner|poke")


@api.post("/push_message", response_model=PushMessageResponse)
async def push_message(req: PushMessageRequest):
    if _bot_app is None:
        raise HTTPException(status_code=503, detail="Bot ещё не инициализирован")

    # файлы агента (если пришли) — шлём документами/фото
    if req.files:
        for f in req.files:
            try:
                await _send_agent_file(req.chat_id, f)
            except Exception as e:
                log.warning(f"push file fail: {e}")

    if not req.text:
        return PushMessageResponse(ok=False, error="Empty text")

    chunks = split_message_safely(req.text)
    sent_ids: list[int] = []
    try:
        if req.edit_thinking and chat_id_has_thinking(req.chat_id):
            thinking_id = _thinking_messages.pop(req.chat_id)
            try:
                await _bot_app.bot.edit_message_text(
                    chat_id=req.chat_id, message_id=thinking_id,
                    text=chunks[0], parse_mode=req.parse_mode,
                )
                sent_ids.append(thinking_id)
                chunks = chunks[1:]
            except Exception as e:
                log.warning(f"edit thinking fail: {e}")
        else:
            # снять "Думаю..." если было
            tid = _thinking_messages.pop(req.chat_id, None)
            if tid:
                try:
                    await _bot_app.bot.delete_message(chat_id=req.chat_id, message_id=tid)
                except Exception:
                    pass

        for chunk in chunks:
            msg = await _bot_app.bot.send_message(
                chat_id=req.chat_id, text=chunk, parse_mode=req.parse_mode,
            )
            sent_ids.append(msg.message_id)
        # Ответ агента → в буфер оркестратора (только группы), чтобы он
        # видел связь «бот участвует в диалоге». Не роняем ответ при ошибке.
        try:
            if _is_group(req.chat_id):
                settings = await get_chat_settings(req.chat_id)
                await buffer_push_agent(req.chat_id, settings.get("agent_name"), req.text)
        except Exception as e:
            log.warning(f"buffer_push_agent fail chat={req.chat_id}: {e}")
        return PushMessageResponse(ok=True, message_ids=sent_ids)
    except Exception as e:
        log.error(f"push_message fail chat={req.chat_id}: {e}", exc_info=True)
        # фолбэк — без parse_mode (плохой HTML от агента)
        try:
            msg = await _bot_app.bot.send_message(chat_id=req.chat_id, text=req.text[:4000])
            return PushMessageResponse(ok=True, message_ids=[msg.message_id], error=f"sent as plain: {e}")
        except Exception as e2:
            return PushMessageResponse(ok=False, error=str(e2))


async def _send_agent_file(chat_id: int, f: dict):
    """Файл из ответа агента. Поддерживаем path (локальный) и file_b64."""
    from io import BytesIO
    raw = None
    if f.get("file_b64"):
        raw = base64.b64decode(f["file_b64"])
    elif f.get("path") and os.path.exists(f["path"]):
        raw = pathlib.Path(f["path"]).read_bytes()
    if not raw:
        return
    name = f.get("name") or "file"
    bio = BytesIO(raw); bio.name = name
    ext = pathlib.Path(name).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        await _bot_app.bot.send_photo(chat_id=chat_id, photo=bio, caption=f.get("caption", ""))
    else:
        await _bot_app.bot.send_document(chat_id=chat_id, document=bio, filename=name,
                                         caption=f.get("caption", ""))


@api.get("/health")
async def health():
    return {"status": "ok", "bot_initialized": _bot_app is not None}


# ══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════════════════════
def _is_group(chat_id: int) -> bool:
    return chat_id < 0


def _strip_bot_tag(text: str, bot_username: str) -> tuple[str, bool]:
    tag = f"@{bot_username}"
    if tag in text:
        return text.replace(tag, "").strip(), True
    return text, False


def _is_reply_to_bot(msg: Message, bot_username: str) -> bool:
    r = msg.reply_to_message
    return bool(r and r.from_user and r.from_user.username == bot_username)


def _registration_gate_text(chat_id: int) -> str:
    """Единый текст отказа для незарегистрированного чата (/start + on_text_message)."""
    return (
        "\u0423 \u0432\u0430\u0441 \u043d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u043a \u0430\u0433\u0435\u043d\u0442\u0443 \u0432 \u044d\u0442\u043e\u043c \u0447\u0430\u0442\u0435.\n\n"
        f"\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u0430\u0446\u0438\u044f \u0447\u0430\u0442\u043e\u0432 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0442\u043e\u043b\u044c\u043a\u043e \u0447\u0435\u0440\u0435\u0437 \u0438\u043d\u0442\u0435\u0440\u0444\u0435\u0439\u0441 CatchPM: {CATCHPM_URL}\n\n"
        f"\u041f\u0435\u0440\u0435\u0434\u0430\u0439\u0442\u0435 \u0430\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0430\u0442\u043e\u0440\u0443 chat_id \u044d\u0442\u043e\u0433\u043e \u0447\u0430\u0442\u0430: {chat_id}"
    )


def _is_direct_address(update: Update, bot_username: str) -> bool:
    """
    Прямое обращение к боту:
      - в группе: @mention бота ИЛИ reply на сообщение бота;
      - в личке: любое сообщение.
    Фоновая болтовня в группе прямым обращением НЕ считается.
    """
    chat_id = update.effective_chat.id
    if not _is_group(chat_id):
        return True
    msg = update.message
    if not msg:
        return False
    raw = msg.text or msg.caption or ""
    _, tagged = _strip_bot_tag(raw, bot_username)
    return tagged or _is_reply_to_bot(msg, bot_username)


def extract_urls_from_message(msg: Message) -> list[str]:
    urls = []
    has_text, has_caption = bool(msg.text), bool(msg.caption)
    entities = msg.entities or msg.caption_entities or []
    for entity in entities:
        try:
            if entity.type == MessageEntity.TEXT_LINK:
                label = (msg.parse_entity(entity) if has_text
                         else msg.parse_caption_entity(entity) if has_caption else "")
                urls.append(f"{label} -> {entity.url}")
            elif entity.type == MessageEntity.URL:
                extracted = (msg.parse_entity(entity) if has_text
                             else msg.parse_caption_entity(entity) if has_caption else "")
                if extracted:
                    urls.append(extracted)
        except Exception:
            continue
    return urls


# ══════════════════════════════════════════════════════════════════════════════
# /start — интерактивное меню настроек чата (Коммит 2)
# Доступно всем участникам, из группы И из лички. Настройки строго per-chat.
# Обязательное условие: чат зарегистрирован (allow-list). Иначе — отбойник
# со ссылкой на регистрацию через интерфейс CatchPM.
# ══════════════════════════════════════════════════════════════════════════════

CATCHPM_URL = os.environ.get("CATCHPM_URL", "https://catchpm.cds.uva-advanced.ru")

# Доступные модели агента (без админских). Ключ = agent_model в БД.
TG_MODELS = ["coder", "designer", "universal"]

# Тул-категории, которые можно включать из телеги. Админская категория
# намеренно ОТСУТСТВУЕТ — из телеги её включить нельзя.
TG_TOOL_CATEGORIES = ["pm_research", "internet", "content", "sessions", "skills"]

TG_TRIGGERS = ["mention", "orchestrator"]


def _settings_summary(settings: dict) -> str:
    model = settings.get("agent_model") or "coder"
    mode = settings.get("trigger_mode") or "orchestrator"
    tools = settings.get("selected_tools") or []
    tools_str = ", ".join(tools) if tools else "\u043d\u0435\u0442 (\u0442\u043e\u043b\u044c\u043a\u043e \u0431\u0430\u0437\u043e\u0432\u044b\u0435)"
    name = settings.get("agent_name")
    name_str = name.strip() if (name and name.strip()) else "\u043f\u043e \u0443\u043c\u043e\u043b\u0447\u0430\u043d\u0438\u044e"
    return (
        "<b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u0430\u0433\u0435\u043d\u0442\u0430 \u0434\u043b\u044f \u044d\u0442\u043e\u0433\u043e \u0447\u0430\u0442\u0430</b>\n\n"
        f"\u0418\u043c\u044f: <code>{name_str}</code>\n"
        f"\u041c\u043e\u0434\u0435\u043b\u044c: <code>{model}</code>\n"
        f"\u0422\u0440\u0438\u0433\u0433\u0435\u0440: <code>{mode}</code>\n"
        f"\u0418\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u044b: <code>{tools_str}</code>"
    )


def _menu_root(settings: dict | None = None) -> InlineKeyboardMarkup:
    settings = settings or {}
    proactive = settings.get("proactive_enabled")
    if proactive is None:
        proactive = True
    # \u0411\u0443\u0434\u0438\u043b\u044c\u043d\u0438\u043a\u0438 (\u0437\u0430\u0432\u0442\u0440\u0430\u043a/\u043e\u0431\u0435\u0434/\u0443\u0436\u0438\u043d + poke)
    poke_label = ("\U0001f514 \u0411\u0443\u0434\u0438\u043b\u044c\u043d\u0438\u043a\u0438: \u0432\u043a\u043b" if proactive
                  else "\U0001f515 \u0411\u0443\u0434\u0438\u043b\u044c\u043d\u0438\u043a\u0438: \u0432\u044b\u043a\u043b")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u0418\u043c\u044f \u0430\u0433\u0435\u043d\u0442\u0430", callback_data="tg:menu:name")],
        [InlineKeyboardButton("\u041c\u043e\u0434\u0435\u043b\u044c", callback_data="tg:menu:model")],
        [InlineKeyboardButton("\u0418\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u044b", callback_data="tg:menu:tools")],
        [InlineKeyboardButton("\u0422\u0440\u0438\u0433\u0433\u0435\u0440", callback_data="tg:menu:trigger")],
        [InlineKeyboardButton(poke_label, callback_data="tg:toggle:proactive")],
        [InlineKeyboardButton("\u0417\u0430\u043a\u0440\u044b\u0442\u044c", callback_data="tg:close")],
    ])


def _menu_model(current) -> InlineKeyboardMarkup:
    cur = current or "coder"
    rows = []
    for m in TG_MODELS:
        mark = "\u2713 " if m == cur else ""
        rows.append([InlineKeyboardButton(f"{mark}{m}", callback_data=f"tg:set:model:{m}")])
    rows.append([InlineKeyboardButton("\u2039 \u041d\u0430\u0437\u0430\u0434", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


def _menu_tools(selected) -> InlineKeyboardMarkup:
    sel = set(selected or [])
    rows = []
    for c in TG_TOOL_CATEGORIES:
        mark = "\u2611 " if c in sel else "\u2610 "
        rows.append([InlineKeyboardButton(f"{mark}{c}", callback_data=f"tg:tool:{c}")])
    rows.append([InlineKeyboardButton("\u2039 \u041d\u0430\u0437\u0430\u0434", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


def _menu_trigger(current) -> InlineKeyboardMarkup:
    cur = current or "orchestrator"
    rows = []
    for t in TG_TRIGGERS:
        mark = "\u2713 " if t == cur else ""
        rows.append([InlineKeyboardButton(f"{mark}{t}", callback_data=f"tg:set:trigger:{t}")])
    rows.append([InlineKeyboardButton("\u2039 \u041d\u0430\u0437\u0430\u0434", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


async def save_chat_settings(chat_id: int, patch: dict) -> bool:
    """POST /tg/settings/{chat_id} - tochechnoe obnovlenie (belyj spisok polej)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{CHATS_SERVICE_URL}/tg/settings/{chat_id}", json=patch,
            )
            resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"save_chat_settings fail chat={chat_id}: {e}")
        return False


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start - menu nastroek, per-chat. Dostupno vsem (gruppa + lichka).
    Snachala proverka registracii v BD (allow-list). Ne zaregistrirovan -
    return s instrukciej zaregistrirovat chat cherez interfejs CatchPM.
    """
    chat_id = update.effective_chat.id
    if not await is_allowed(chat_id):
        await update.message.reply_text(_registration_gate_text(chat_id))
        return
    settings = await get_chat_settings(chat_id)
    await update.message.reply_text(
        _settings_summary(settings),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_root(settings),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obrabotka nazhatij inline-knopok menu /start. per-chat, dostupno vsem."""
    query = update.callback_query
    chat_id = query.message.chat.id if query.message else update.effective_chat.id
    data = query.data or ""

    if not await is_allowed(chat_id):
        await query.answer("\u0427\u0430\u0442 \u043d\u0435 \u0437\u0430\u0440\u0435\u0433\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d.", show_alert=True)
        return

    async def _refresh(kb_fn):
        settings = await get_chat_settings(chat_id)
        try:
            await query.edit_message_text(
                _settings_summary(settings),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_fn(settings),
            )
        except Exception:
            pass

    if data == "tg:close":
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if data == "tg:menu:root":
        await query.answer()
        await _refresh(lambda s: _menu_root(s))
        return

    if data == "tg:toggle:proactive":
        settings = await get_chat_settings(chat_id)
        cur = settings.get("proactive_enabled")
        if cur is None:
            cur = True
        ok = await save_chat_settings(chat_id, {"proactive_enabled": not cur})
        await query.answer(
            ("\u0411\u0443\u0434\u0438\u043b\u044c\u043d\u0438\u043a\u0438 " + ("\u0432\u044b\u043a\u043b" if cur else "\u0432\u043a\u043b"))
            if ok else "\u041e\u0448\u0438\u0431\u043a\u0430"
        )
        await _refresh(lambda s: _menu_root(s))
        return
    if data == "tg:menu:name":
        # ставим флаг ожидания ввода имени от нажавшего юзера
        uid = query.from_user.id if query.from_user else 0
        try:
            r = await get_redis()
            await r.set(_k_setname(chat_id, uid), "1", ex=300)
        except Exception:
            pass
        await query.answer()
        try:
            await query.edit_message_text(
                "\u041e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 \u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u043c \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435\u043c \u0438\u043c\u044f \u0430\u0433\u0435\u043d\u0442\u0430 \u0434\u043b\u044f \u044d\u0442\u043e\u0433\u043e \u0447\u0430\u0442\u0430.\n"
                "\u041d\u0430\u043f\u0440\u0438\u043c\u0435\u0440: <code>\u041c\u0430\u043a\u0441</code> \u0438\u043b\u0438 <code>\u0410\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442</code>.\n"
                "\u0427\u0442\u043e\u0431\u044b \u0441\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0438\u043c\u044f \u2014 \u043e\u0442\u043f\u0440\u0430\u0432\u044c\u0442\u0435 <code>-</code>.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        return

    if data == "tg:menu:model":
        await query.answer()
        await _refresh(lambda s: _menu_model(s.get("agent_model")))
        return
    if data == "tg:menu:tools":
        await query.answer()
        await _refresh(lambda s: _menu_tools(s.get("selected_tools")))
        return
    if data == "tg:menu:trigger":
        await query.answer()
        await _refresh(lambda s: _menu_trigger(s.get("trigger_mode")))
        return

    if data.startswith("tg:set:model:"):
        model = data.split(":", 3)[3]
        if model not in TG_MODELS:
            await query.answer("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043c\u043e\u0434\u0435\u043b\u044c", show_alert=True)
            return
        ok = await save_chat_settings(chat_id, {"agent_model": model})
        await query.answer("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e" if ok else "\u041e\u0448\u0438\u0431\u043a\u0430")
        await _refresh(lambda s: _menu_model(s.get("agent_model")))
        return

    if data.startswith("tg:set:trigger:"):
        trig = data.split(":", 3)[3]
        if trig not in TG_TRIGGERS:
            await query.answer("\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u044b\u0439 \u0442\u0440\u0438\u0433\u0433\u0435\u0440", show_alert=True)
            return
        ok = await save_chat_settings(chat_id, {"trigger_mode": trig})
        await query.answer("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e" if ok else "\u041e\u0448\u0438\u0431\u043a\u0430")
        await _refresh(lambda s: _menu_trigger(s.get("trigger_mode")))
        return

    if data.startswith("tg:tool:"):
        cat = data.split(":", 2)[2]
        if cat not in TG_TOOL_CATEGORIES:
            await query.answer("\u041d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430\u044f \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u044f", show_alert=True)
            return
        settings = await get_chat_settings(chat_id)
        sel = set(settings.get("selected_tools") or [])
        if cat in sel:
            sel.discard(cat)
        else:
            sel.add(cat)
        ok = await save_chat_settings(chat_id, {"selected_tools": sorted(sel)})
        await query.answer("\u0421\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e" if ok else "\u041e\u0448\u0438\u0431\u043a\u0430")
        await _refresh(lambda s: _menu_tools(s.get("selected_tools")))
        return

    await query.answer()


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    bot_username = context.bot.username
    if not await is_allowed(chat_id):
        # вариант A: отвечаем ТОЛЬКО на прямое обращение (mention/reply/личка),
        # на фоновую болтовню в чужой группе — молчим. Отказ throttle-им.
        if _is_direct_address(update, bot_username):
            try:
                r = await get_redis()
                fresh = await r.set(_k_gate(chat_id), "1", nx=True,
                                    ex=int(os.environ.get("TG_GATE_COOLDOWN", "300")))
            except Exception:
                fresh = True
            if fresh:
                await update.message.reply_text(_registration_gate_text(chat_id))
        return

    msg = update.message
    raw_text = msg.text or msg.caption or ""
    user = update.effective_user
    author = user.full_name or user.username or "Аноним"

    # ── перехват ввода имени агента (после кнопки «Имя агента») ──
    try:
        r = await get_redis()
        awaiting_name = await r.get(_k_setname(chat_id, user.id))
    except Exception:
        awaiting_name = None
    if awaiting_name:
        try:
            await r.delete(_k_setname(chat_id, user.id))
        except Exception:
            pass
        new_name = (raw_text or "").strip()
        if new_name in ("-", "—", ""):
            ok = await save_chat_settings(chat_id, {"agent_name": ""})
            await msg.reply_text("Имя агента сброшено (дефолт)." if ok else "Ошибка сохранения.")
        else:
            new_name = new_name[:64]
            ok = await save_chat_settings(chat_id, {"agent_name": new_name})
            await msg.reply_text(("Имя агента: " + new_name) if ok else "Ошибка сохранения.")
        return

    # разбор упоминания/реплая
    is_group = _is_group(chat_id)
    approved = False
    text_for_agent = raw_text

    if is_group:
        stripped, tagged = _strip_bot_tag(raw_text, bot_username)
        reply_to_bot = _is_reply_to_bot(msg, bot_username)
        approved = tagged or reply_to_bot
        if approved:
            text_for_agent = stripped or raw_text
    else:
        # личка — всегда обращение к агенту
        approved = True

    # обогащение ссылками/форвардом/реплаем
    text_for_agent = _enrich_text(msg, text_for_agent)

    # пассивный сбор участников (для рандомной проактивной инициации)
    await upsert_member(chat_id, user.id, user.username, user.first_name)

    # человек ответил → снимаем флаг ожидания «грусти» (если ждали именно его)
    try:
        r = await get_redis()
        await r.delete(_k_await(chat_id, user.id))
    except Exception:
        pass

    # всегда пишем реплику в буфер (для оркестратора и склейки контекста)
    if raw_text.strip():
        await buffer_push(chat_id, author, raw_text.strip())

    # если не одобрено тегом — спросить оркестратор (в группе), с throttle
    if not approved and is_group:
        if await orchestrator_allowed(chat_id):
            lines = await buffer_lines(chat_id)
            approved = await orchestrator_should_respond(chat_id, lines)

    if not approved:
        return

    # одобрено → склеиваем накопленный буфер «с последнего ответа» в одну строку
    merged_lines = await buffer_flush(chat_id)
    if merged_lines:
        merged_text = "\n".join(merged_lines)
    else:
        merged_text = f"{author}: {text_for_agent}"

    thinking = await msg.reply_text("Думаю...")
    _thinking_messages[chat_id] = thinking.message_id

    try:
        await dispatch_to_agent(chat_id, user.id, author, merged_text)
    except Exception as e:
        await msg.reply_text(f"Не удалось связаться с агентом: {str(e)[:200]}")


def _enrich_text(msg: Message, base_text: str) -> str:
    """Добавляет ссылки, пересланное и reply-контекст к тексту."""
    try:
        main_urls = extract_urls_from_message(msg)
        urls_str = ("\n[Ссылки: " + "; ".join(main_urls) + "]") if main_urls else ""
        parts = [f"{base_text}{urls_str}".strip()]
        if msg.forward_origin:
            origin = msg.forward_origin
            if origin.type == "user":
                f_author = origin.sender_user.full_name
            elif origin.type == "hidden_user":
                f_author = origin.sender_user_name
            elif origin.type in ("chat", "channel"):
                f_author = origin.chat.title
            else:
                f_author = "источник"
            parts.append(f"[Переслано от {f_author}]")
        if msg.reply_to_message:
            r = msg.reply_to_message
            r_author = r.from_user.full_name if r.from_user else "Неизвестный"
            r_content = r.text or r.caption or "[медиа]"
            parts.append(f"[В ответ на {r_author}: {r_content}]")
        return "\n".join(p for p in parts if p.strip())
    except Exception:
        return base_text


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await is_allowed(chat_id):
        return

    msg = update.message
    raw_text = msg.caption or ""
    user = update.effective_user
    author = user.full_name or user.username or "Аноним"
    bot_username = context.bot.username

    approved = True
    if _is_group(chat_id):
        stripped, tagged = _strip_bot_tag(raw_text, bot_username)
        approved = tagged or _is_reply_to_bot(msg, bot_username)
        raw_text = stripped or raw_text
        if not approved:
            return  # файл без тега в группе — игнор

    doc = msg.document
    if not doc:
        return
    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await msg.reply_text("Файл слишком большой (лимит 20 MB).")
        return

    fname = pathlib.Path(doc.file_name or f"file_{doc.file_id[:8]}").name
    tg_file = await doc.get_file()
    file_bytearray = await tg_file.download_as_bytearray()
    file_b64 = base64.b64encode(file_bytearray).decode("utf-8")

    thinking = await msg.reply_text("Обрабатываю файл...")
    _thinking_messages[chat_id] = thinking.message_id

    merged_text = f"{author}: {raw_text}".strip() if raw_text else f"{author}: [файл {fname}]"
    try:
        await dispatch_to_agent(chat_id, user.id, author, merged_text,
                                file_b64=file_b64, file_name=fname)
    except Exception as e:
        await msg.reply_text(f"Не удалось связаться с агентом: {str(e)[:200]}")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error: %s", context.error, exc_info=context.error)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
async def run_both():
    global _bot_app
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(on_callback, pattern=r"^tg:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_error_handler(_on_error)
    _bot_app = application

    config = uvicorn.Config(api, host="0.0.0.0", port=TG_PUSH_PORT, log_level="info")
    server = uvicorn.Server(config)

    # Проактивный планировщик (будильники + poke)
    scheduler = build_scheduler()

    async with application:
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        scheduler.start()
        log.info(f"tg_bot запущен: polling + push API на :{TG_PUSH_PORT} + scheduler "
                 f"(slots 9/13/18 {TG_TZ}, poke {TG_POKE_HOUR_START}-{TG_POKE_HOUR_END} p={TG_POKE_PROBABILITY})")
        try:
            await server.serve()
        finally:
            scheduler.shutdown(wait=False)
            await application.updater.stop()
            await application.stop()


def main():
    asyncio.run(run_both())


if __name__ == "__main__":
    main()
