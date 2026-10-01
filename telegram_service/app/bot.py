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
import json
import logging
import os
import pathlib
import random
import re
import time
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
import redis.asyncio as aioredis
from telegram import (
    Update, Message, MessageEntity,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, MessageReactionHandler, filters,
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
# Окно связывания «форвард из другого чата» ↔ следующее тег-сообщение (сек).
TG_FWD_TTL         = int(os.environ.get("TG_FWD_TTL", "90"))
# Окно, в течение которого тег-сообщение БЕЗ своего контента ЖДЁТ форвард (сек).
TG_TAG_TTL         = int(os.environ.get("TG_TAG_TTL", "20"))
# Сколько ждать форвард, прежде чем ОТВЕТИТЬ на сам тег-вопрос (fallback).
# Вариант 2: тег кладётся в stash, планируется отложенный ответ через
# TG_TAG_WAIT сек. Если за это время форвард связался (pop_tag его выдернул) —
# отложенный ответ тихо выходит. Иначе отвечаем на исходный текст тега.
# Должно быть < TG_TAG_TTL, чтобы тег ещё был жив в Redis к моменту fallback.
TG_TAG_WAIT        = int(os.environ.get("TG_TAG_WAIT", "10"))

MAX_FILE_SIZE = 20 * 1024 * 1024
# Максимум картинок на один пост-альбом (агент тоже режет до 10).
TG_ALBUM_MAX   = int(os.environ.get("TG_ALBUM_MAX", "10"))
# Окно дебаунса сборки альбома: TG шлёт кадры N отдельными
# апдейтами с общим media_group_id; ждём тишины перед dispatch.
TG_ALBUM_DEBOUNCE = float(os.environ.get("TG_ALBUM_DEBOUNCE", "1.3"))

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

# Буфер альбомов: media_group_id -> {items, chat_id, user_id, author,
# text, thinking_mid, task}. Кадры копятся, дебаунс-таск флашит одним dispatch.
_album_buffers: dict[str, dict] = {}


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
# Последний форвард из другого чата/канала (текст + опц. картинка), ждёт тег.
def _k_lastfwd(chat_id: int) -> str:    return f"tg:lastfwd:{chat_id}"
# Последнее тег-сообщение без контента, которое ЖДЁТ форвард.
def _k_lasttag(chat_id: int) -> str:    return f"tg:lasttag:{chat_id}"
# Дедуп альбома-форварда: первое фото media-group уходит на анализ, остальные — молча.
def _k_fwdgroup(chat_id: int, gid: str) -> str: return f"tg:fwdgroup:{chat_id}:{gid}"
# Единый дедуп ПОСТА: один пост (текст+тег + 1..N фото, альбом или форвард) =
# один вызов агента. Ключ по media_group_id (альбом) либо message_id (одиночное).
def _k_post(chat_id: int, pid: str) -> str: return f"tg:post:{chat_id}:{pid}"
# дедуп срабатывания триггера в конкретную минуту (tid + YYYYMMDDHHMM)
def _k_trig(tid: int, stamp: str) -> str: return f"tg:trig:{tid}:{stamp}"
def _k_alarmedit(chat_id: int, uid: int) -> str: return f"tg:alarmedit:{chat_id}:{uid}"
# Связка tg_message_id бота -> agent_message_id + текст (для реакций). TTL сутки.
def _k_msgmap(chat_id: int, tg_mid: int) -> str: return f"tg:msgmap:{chat_id}:{tg_mid}"
# Буфер реакций для большого агента (очищается только после next-сообщения, без TTL).
def _k_reactbuf(chat_id: int) -> str: return f"tg:reactions:{chat_id}"
# Throttle на neg-проактив (1 в 5 мин на чат).
def _k_react_neg(chat_id: int) -> str: return f"tg:react:neg:{chat_id}"
# Счётчик pos-реакций на конкретное сообщение (порог 3+ -> триггер 1 раз).
def _k_react_pos(chat_id: int, tg_mid: int) -> str: return f"tg:react:pos:{chat_id}:{tg_mid}"


async def fwd_group_already_seen(chat_id: int, media_group_id) -> bool:
    """True, если фото из этого media_group уже принято (значит текущее — дубль).

    Пересланный АЛЬБОМ приходит в Telegram как несколько отдельных photo-сообщений
    с общим media_group_id. Чтобы на анализ ушла максимум ОДНА картинка из пересыла,
    первое фото группы ставит флаг в Redis (TTL 120с), остальные — игнорируем.
    Одиночное фото (media_group_id пустой) — всегда пропускаем.
    """
    if not media_group_id:
        return False
    try:
        r = await get_redis()
        # SET NX: True при первой установке (не видели), None если ключ уже есть (видели)
        ok = await r.set(_k_fwdgroup(chat_id, str(media_group_id)), "1", nx=True, ex=120)
        return ok is None
    except Exception as e:
        log.warning(f"fwd_group_already_seen fail chat={chat_id}: {e}")
        return False


async def post_already_dispatched(chat_id: int, msg: Message) -> bool:
    """Единый guard: True, если по ЭТОМУ ПОСТУ уже был вызов агента.

    Один пост в Telegram может прийти несколькими апдейтами (альбом = N photo-
    сообщений с общим media_group_id; текст-тег + фото; форвард + связанный тег).
    Все эти пути вызывают dispatch_to_agent независимо → дубли ответов.
    Этот guard координирует их по общему ключу поста и пропускает ТОЛЬКО первый.

    Ключ поста:
      - media_group_id  — для альбома (несколько фото/док одного поста);
      - message_id      — для одиночного сообщения.
    SET NX EX: первый вызывающий получает True на set (→ пропускаем dispatch),
    остальные видят существующий ключ (→ дубль, глушим).
    """
    try:
        gid = getattr(msg, "media_group_id", None)
        pid = str(gid) if gid else f"m{msg.message_id}"
        r = await get_redis()
        ok = await r.set(_k_post(chat_id, pid), "1", nx=True, ex=120)
        return ok is None
    except Exception as e:
        log.warning(f"post_already_dispatched fail chat={chat_id}: {e}")
        return False


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


# ── Буфер последнего форварда из другого чата/канала ────────────────────────────
# Форвард обычно приходит ОТДЕЛЬНЫМ сообщением (без тега бота), а тег («гоша,
# что скажешь?») — соседним. Reply на форвард в группе не работает (это форвард,
# а не reply). Поэтому кладём контент форварда в Redis с коротким TTL и
# подхватываем его при следующем тег-сообщении без собственного контента.
def _fwd_origin_author(msg: Message) -> str:
    origin = msg.forward_origin
    try:
        if origin.type == "user":
            return origin.sender_user.full_name
        if origin.type == "hidden_user":
            return origin.sender_user_name
        if origin.type in ("chat", "channel"):
            return origin.chat.title
    except Exception:
        pass
    return "источник"


async def stash_forward(chat_id: int, msg: Message):
    """Сохраняет контент форварда (текст + опц. картинка base64) в tg:lastfwd."""
    try:
        text = (msg.text or msg.caption or "").strip()
        author = _fwd_origin_author(msg)
        file_b64 = None
        file_name = None
        photo = msg.photo[-1] if msg.photo else None
        # Форвард-альбом = несколько photo-сообщений с общим media_group_id.
        # Берём картинку только из ПЕРВОГО сообщения группы, остальные — без фото
        # (экономим токены vision: максимум одна картинка из пересыла).
        dup_album = await fwd_group_already_seen(chat_id, msg.media_group_id)
        if photo and not dup_album and not (photo.file_size and photo.file_size > MAX_FILE_SIZE):
            tg_file = await photo.get_file()
            raw = await tg_file.download_as_bytearray()
            file_b64 = base64.b64encode(raw).decode("utf-8")
            file_name = f"fwd_{photo.file_unique_id}.jpg"
        # Дубль-кадр альбома без своего текста и без картинки — не перетираем stash.
        if dup_album and not text and not file_b64:
            log.info(f"[fwd] skip extra album photo chat={chat_id} gid={msg.media_group_id}")
            return
        payload = {
            "author": author,
            "text": text,
            "file_b64": file_b64,
            "file_name": file_name,
        }
        r = await get_redis()
        await r.set(_k_lastfwd(chat_id), json.dumps(payload), ex=TG_FWD_TTL)
        log.info(f"[fwd] stashed forward chat={chat_id} from={author} "
                 f"text={len(text)}b img={'yes' if file_b64 else 'no'}")
    except Exception as e:
        log.warning(f"stash_forward fail chat={chat_id}: {e}")


async def pop_forward(chat_id: int) -> dict | None:
    """Забирает и удаляет сохранённый форвард (одноразовый подхват)."""
    try:
        r = await get_redis()
        raw = await r.get(_k_lastfwd(chat_id))
        if not raw:
            return None
        await r.delete(_k_lastfwd(chat_id))
        return json.loads(raw)
    except Exception as e:
        log.warning(f"pop_forward fail chat={chat_id}: {e}")
        return None


async def stash_tag(chat_id: int, user_id: int, username: str, tag_text: str):
    """Сохраняет тег-сообщение без контента, чтобы догнать форвард (обратный порядок)."""
    try:
        payload = {"user_id": user_id, "username": username, "text": (tag_text or "").strip()}
        r = await get_redis()
        await r.set(_k_lasttag(chat_id), json.dumps(payload), ex=TG_TAG_TTL)
        log.info(f"[fwd] stashed tag chat={chat_id} uid={user_id} wait_fwd={TG_TAG_TTL}s")
    except Exception as e:
        log.warning(f"stash_tag fail chat={chat_id}: {e}")

async def pop_tag(chat_id: int) -> dict | None:
    """Забирает и удаляет отложенный тег (одноразовый подхват)."""
    try:
        r = await get_redis()
        raw = await r.get(_k_lasttag(chat_id))
        if not raw:
            return None
        await r.delete(_k_lasttag(chat_id))
        return json.loads(raw)
    except Exception as e:
        log.warning(f"pop_tag fail chat={chat_id}: {e}")
        return None


async def _deferred_tag_answer(chat_id: int, user_id: int, username: str,
                              tag_text: str):
    """ВАРИАНТ 2 (fallback по таймауту): тег-вопрос БЕЗ форварда.

    Спим TG_TAG_WAIT сек. Если за это время форвард прилетел — он вызвал
    handle_incoming_forward -> pop_tag, который ВЫДЕРНУЛ тег из Redis и уже
    ответил (связка тег↔форвард). Тогда наш pop_tag вернёт None -> тихо
    выходим (без дубля). Если форварда не было — тег ещё жив, pop_tag его
    заберёт, и мы отвечаем на ИСХОДНЫЙ тег-вопрос (тег больше не теряется).
    """
    try:
        await asyncio.sleep(TG_TAG_WAIT)
    except asyncio.CancelledError:
        return
    # Атомарно забираем тег: кто первым сделал pop_tag (мы или форвард-ветка) —
    # тот и обрабатывает. None => форвард уже связал и ответил.
    waiting = await pop_tag(chat_id)
    if not waiting:
        log.info(f"[fwd] deferred tag already linked by forward chat={chat_id}")
        return
    text = (waiting.get("text") or tag_text or "").strip()
    if not text:
        # голый @-тег без текста и без форварда — отвечать не на что
        log.info(f"[fwd] deferred bare tag expired w/o forward chat={chat_id}")
        return
    log.info(f"[fwd] deferred fallback: answer tag itself chat={chat_id} "
             f"after {TG_TAG_WAIT}s (no forward)")
    merged = f"{username}: {text}"
    try:
        thinking = await _bot_app.bot.send_message(chat_id, "Думаю...")
        _thinking_messages[chat_id] = thinking.message_id
    except Exception as e:
        log.warning(f"deferred tag thinking fail chat={chat_id}: {e}")
    try:
        await dispatch_to_agent(chat_id, user_id, username, merged)
    except Exception as e:
        log.warning(f"deferred tag dispatch fail chat={chat_id}: {e}")


async def handle_incoming_forward(chat_id: int, msg: Message) -> bool:
    """Обрабатывает форвард без тега.

    Если перед этим был голый тег (tg:lasttag ещё жив) — связываем
    обратный порядок (тег → форвард): шлём агенту сразу,
    возвращаем True (связали). Иначе стешим форвард и возвращаем False.

    ВАРИАНТ Б (склейка тег↔форвард-альбом): форвард-альбом приходит N кадрами
    (общий media_group_id), и КАЖДЫЙ кадр без тега попадает сюда. Чтобы тег
    подхватился ровно ОДИН раз и dispatch был один — на входе ставим единый
    guard поста. Первый кадр проходит (ставит ключ, связывает тег, шлёт агенту),
    остальные кадры видят ключ и молча выходят (тег не выдёргивают повторно).
    """
    # ВАРИАНТ Б: единый per-post guard в САМОМ НАЧАЛЕ (до pop_tag/stash_forward).
    # Форвард-альбом приходит N кадрами с общим media_group_id, и каждый кадр
    # без тега залетает сюда. Первый кадр ставит ключ поста и идёт дальше
    # (свяжет тег, dispatch). Остальные кадры того же альбома видят ключ и молча
    # выходят — НЕ трогают pop_tag (тег не выдёргивают повторно) и НЕ засоряют
    # stash_forward. Для одиночного форварда (media_group_id пустой) ключ по
    # message_id — тоже безопасно, второй апдейт по тому же message_id не придёт.
    if await post_already_dispatched(chat_id, msg):
        log.info(f"[post] skip duplicate forward frame (handle_incoming_forward) "
                 f"chat={chat_id} mid={msg.message_id} gid={msg.media_group_id}")
        return True
    # Порядок важен: тег может ещё не прийти (прямой порядок: форвард раньше тега).
    # Тогда стешим форвард и выходим. NB: guard уже поставлен выше — если следом
    # прилетит тег в on_text по ЭТОМУ ЖЕ посту, он тоже увидит guard; но тег
    # приходит ОТДЕЛЬНЫМ апдейтом (свой message_id / без media_group_id форварда),
    # поэтому его ключ поста другой — коллизии нет.
    waiting = await pop_tag(chat_id)
    if not waiting:
        await stash_forward(chat_id, msg)
        return False
    # есть ждущий тег → собираем контент форварда и шлём агенту
    f_author = _fwd_origin_author(msg)
    f_text = (msg.text or msg.caption or "").strip()
    file_b64 = None
    file_name = None
    photo = msg.photo[-1] if msg.photo else None
    # Максимум одна картинка из пересыла: если это доп. кадр альбома —
    # фото не тянем (первое фото группы уже ушло/уйдёт).
    dup_album = await fwd_group_already_seen(chat_id, msg.media_group_id)
    if photo and dup_album:
        photo = None
    if photo and not (photo.file_size and photo.file_size > MAX_FILE_SIZE):
        try:
            tg_file = await photo.get_file()
            raw = await tg_file.download_as_bytearray()
            file_b64 = base64.b64encode(raw).decode("utf-8")
            file_name = f"fwd_{photo.file_unique_id}.jpg"
        except Exception as e:
            log.warning(f"handle_incoming_forward photo dl fail: {e}")
    tag_text = (waiting.get("text") or "").strip()
    tag_user = waiting.get("username") or "Аноним"
    tag_uid = int(waiting.get("user_id") or 0)
    if f_text:
        merged = f"{tag_user}: {tag_text}\n[Переслано от {f_author}]: {f_text}".strip()
    else:
        merged = f"{tag_user}: {tag_text}\n[Переслано от {f_author}: картинка]".strip()
    log.info(f"[fwd] linked tag->forward (reverse order) chat={chat_id} "
             f"img={'yes' if file_b64 else 'no'}")
    # Плашка «Думаю...» — как в прямой ветке on_text_message, чтобы ответ
    # в обратном порядке тоже приходил с приставкой (edit_thinking снимет её).
    try:
        thinking = await msg.reply_text("Думаю...")
        _thinking_messages[chat_id] = thinking.message_id
    except Exception as e:
        log.warning(f"handle_incoming_forward thinking fail: {e}")
    try:
        await dispatch_to_agent(chat_id, tag_uid, tag_user, merged,
                                file_b64=file_b64, file_name=file_name)
    except Exception as e:
        log.warning(f"handle_incoming_forward dispatch fail: {e}")
    return True


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
async def _upload_files_to_agent(message_id, chat_id, user_id, items):
    """Доставка 1..N вложений агенту одним multipart-POST (как web-gateway).

    items: list[dict] с ключами file_b64, file_name. Агент ждёт файлы по
    message_id через wait_for_files(); /web/upload_files принимает список
    и сам режет до 10 шт. Каждый файл -> отдельный [IMAGE #idx] маркер.
    """
    if not items:
        return
    try:
        files = []
        for it in items:
            raw = base64.b64decode(it["file_b64"])
            fname = it.get("file_name") or "file"
            ext = pathlib.Path(fname).suffix.lower()
            ctype = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
            }.get(ext, "application/octet-stream")
            files.append(("files", (fname, raw, ctype)))
        data = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "user_id": str(user_id),
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{AGENT_SERVICE_URL}/web/upload_files", data=data, files=files
            )
            resp.raise_for_status()
        log.info(f"files uploaded to agent chat={chat_id} msg={message_id} count={len(files)}")
    except Exception as e:
        log.error(f"upload files to agent fail chat={chat_id} msg={message_id}: {e}")


async def dispatch_to_agent(chat_id: int, user_id: int, username: str,
                            merged_text: str, file_b64: str | None = None,
                            file_name: str | None = None,
                            files: list | None = None):
    """
    Формирует ChatMessage (gateway_target="telegram") и шлёт в chats_service.
    Агент ответит асинхронно → endpoint_consumer → /push_message боту.

    Если приложен файл (file_b64) — ПОСЛЕ постановки сообщения в очередь
    отправляем сам файл напрямую в agent_service /web/upload_files с тем же
    message_id (агент ждёт его через wait_for_files). Иначе has_files=True
    висит 20с в таймауте и агент отвечает без файла (галлюцинирует).
    """
    settings = await get_chat_settings(chat_id)
    request_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    # Единый список вложений: новый путь (files=[{file_b64,file_name}]) или
    # старый одиночный (file_b64+file_name) — обратная совместимость.
    items = list(files) if files else ([] )
    if not items and file_b64:
        items = [{"file_b64": file_b64, "file_name": file_name or "file"}]
    items = items[:10]  # жёсткий лимит 10 картинок на сообщение
    has_files = bool(items)

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

    # Файл шлём после постановки в очередь: agent_service уже ждёт его
    # по message_id (wait_for_files, окно 20с). Не роняем диалог при ошибке.
    if has_files:
        await _upload_files_to_agent(message_id, chat_id, user_id, items)


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



# ══════════════════════════════════════════════════════════════════════════════
# РЕАКЦИИ НА СООБЩЕНИЯ БОТА: lite-анализ + буфер фидбека + проактив
# ══════════════════════════════════════════════════════════════════════════════
POS_THRESHOLD = int(os.environ.get("TG_REACT_POS_THRESHOLD", "3"))   # 3+ pos -> проактив
NEG_THROTTLE_SEC = int(os.environ.get("TG_REACT_NEG_THROTTLE", "300"))  # 1 neg-ответ / 5 мин


def _reaction_emojis(reaction_list) -> list:
    """Из списка ReactionType вытаскивает emoji-строки."""
    out = []
    for rt in (reaction_list or []):
        emoji = getattr(rt, "emoji", None)
        if emoji:
            out.append(emoji)
        else:
            ce = getattr(rt, "custom_emoji_id", None)
            if ce:
                out.append("[custom]")
    return out


async def reactions_feedback_flush(chat_id: int) -> str:
    """Читает буфер реакций и СРАЗУ очищает его (после next-сообщения).
    Возвращает готовый feedback-блок для большого агента (или "")."""
    try:
        r = await get_redis()
        key = _k_reactbuf(chat_id)
        raw = await r.lrange(key, 0, -1)
        if not raw:
            return ""
        await r.delete(key)
        items = []
        for x in raw:
            try:
                items.append(json.loads(x))
            except Exception:
                pass
        if not items:
            return ""
        # свежие сверху (LPUSH) -> развернём в хронологию
        items.reverse()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{AGENT_SERVICE_URL}/api/tg/build_reactions_feedback",
                json={"reactions": items},
            )
            resp.raise_for_status()
            return resp.json().get("feedback", "") or ""
    except Exception as e:
        log.warning(f"reactions_feedback_flush fail chat={chat_id}: {e}")
        return ""


async def on_message_reaction(update, context: ContextTypes.DEFAULT_TYPE):
    """Ловим реакцию на сообщение бота -> lite-анализ -> буфер + проактив.

    Только реакции на сообщения БОТА (по msgmap: tg_message_id -> agent uuid).
    add: сравниваем old->new, берём НОВЫЕ emoji. remove игнорируем для проактива,
    но пишем в БД как action=remove.
    """
    log.info(f"[reaction] RAW update received: has_mr={update.message_reaction is not None}")
    mr = update.message_reaction
    if mr is None:
        return
    chat_id = mr.chat.id
    tg_mid = mr.message_id
    ruser = mr.user
    if ruser is None:
        return  # анонимная реакция (message_reaction_count) — пропускаем

    # это сообщение бота? ищем связку в Redis
    try:
        r = await get_redis()
        raw = await r.get(_k_msgmap(chat_id, tg_mid))
    except Exception:
        raw = None
    if not raw:
        log.info(f"[reaction] msgmap MISS chat={mr.chat.id} tg_mid={mr.message_id} (not bot msg or expired)")
        return  # реакция не на сообщение бота (или связка протухла)
    try:
        mm = json.loads(raw)
    except Exception:
        return
    agent_mid = mm.get("aid")
    agent_text = mm.get("text") or ""

    old_e = set(_reaction_emojis(mr.old_reaction))
    new_e = set(_reaction_emojis(mr.new_reaction))
    added = list(new_e - old_e)
    removed = list(old_e - new_e)

    username = ruser.username or ruser.full_name or "кто-то"
    uid = ruser.id

    # обрабатываем и добавленные, и снятые (снятые — только аналитика в БД)
    events = [(e, "add") for e in added] + [(e, "remove") for e in removed]
    if not events:
        log.info(f"[reaction] no add/remove events chat={mr.chat.id}")
        return

    for emoji, action in events:
        # lite-анализ + запись в БД (аналитика) — через agent_service
        sentiment, note = "neutral", ""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.post(
                    f"{AGENT_SERVICE_URL}/api/tg/analyze_reaction",
                    json={
                        "chat_id": chat_id,
                        "agent_message_id": agent_mid,
                        "emoji": emoji,
                        "action": action,
                        "user_id": uid,
                        "username": username,
                        "agent_text": agent_text,
                    },
                )
                resp.raise_for_status()
                jd = resp.json()
                sentiment = jd.get("sentiment", "neutral")
                note = jd.get("note", "")
        except Exception as e:
            log.warning(f"analyze_reaction call fail chat={chat_id}: {e}")

        # снятие реакции — только в аналитику, дальше не тянем
        if action != "add":
            continue

        # кладём в буфер фидбека для большого агента (без TTL)
        rec = {
            "username": username, "emoji": emoji, "action": action,
            "sentiment": sentiment, "note": note,
            "agent_snippet": agent_text[:120],
        }
        try:
            r = await get_redis()
            await r.lpush(_k_reactbuf(chat_id), json.dumps(rec))
            await r.ltrim(_k_reactbuf(chat_id), 0, 49)
        except Exception as e:
            log.warning(f"reactbuf push fail chat={chat_id}: {e}")

        # ── ПРОАКТИВ ──
        try:
            if sentiment == "neg":
                # одна негативная -> сразу шутливый отклик, throttle 5 мин на чат
                r = await get_redis()
                ok = await r.set(_k_react_neg(chat_id), "1", nx=True, ex=NEG_THROTTLE_SEC)
                if ok:
                    prompt = await _build_react_prompt("neg", [rec])
                    if prompt:
                        await dispatch_initiation(chat_id, slot="reaction",
                                                  addressed=False, target=None,
                                                  custom_prompt=prompt)
                        log.info(f"[react] NEG proactive fired chat={chat_id} emoji={emoji}")
            elif sentiment == "pos":
                # копим pos по КОНКРЕТНОМУ сообщению; порог -> триггер 1 раз
                r = await get_redis()
                cnt = await r.incr(_k_react_pos(chat_id, tg_mid))
                await r.expire(_k_react_pos(chat_id, tg_mid), 3600)
                if cnt == POS_THRESHOLD:
                    # собираем все pos по этому сообщению из буфера
                    prompt = await _build_react_prompt("pos", [rec], count=cnt)
                    if prompt:
                        await dispatch_initiation(chat_id, slot="reaction",
                                                  addressed=False, target=None,
                                                  custom_prompt=prompt)
                        log.info(f"[react] POS proactive fired chat={chat_id} mid={tg_mid} cnt={cnt}")
        except Exception as e:
            log.warning(f"reaction proactive fail chat={chat_id}: {e}")


async def _build_react_prompt(kind: str, reactions: list, count: int = 0) -> str:
    """Запрашивает у agent_service готовый проактив-промпт по реакциям."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{AGENT_SERVICE_URL}/api/tg/build_reaction_prompt",
                json={"kind": kind, "reactions": reactions, "count": count},
            )
            resp.raise_for_status()
            return resp.json().get("prompt", "") or ""
    except Exception as e:
        log.warning(f"build_react_prompt fail: {e}")
        return ""


async def dispatch_initiation(chat_id: int, slot: str, addressed: bool,
                              target: dict | None, sad_followup: bool = False,
                              custom_prompt: str | None = None):
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
        # индивидуальный промпт триггера (None -> агент берёт дефолт по slot)
        "custom_prompt": custom_prompt,
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


async def fetch_due_triggers(now_utc: datetime) -> list[dict]:
    """Спросить chats_service: какие триггеры должны сработать сейчас."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{CHATS_SERVICE_URL}/tg/triggers_due",
                params={"utc_ts": now_utc.astimezone(timezone.utc).isoformat()},
            )
            resp.raise_for_status()
            return resp.json().get("triggers", []) or []
    except Exception as e:
        log.warning(f"fetch_due_triggers fail: {e}")
        return []


async def mark_trigger_fired_remote(chat_id: str, trigger_id: int) -> None:
    """Пометить триггер как сработавший (важно для once)."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{CHATS_SERVICE_URL}/tg/triggers/{chat_id}/{trigger_id}/fired")
    except Exception as e:
        log.warning(f"mark_trigger_fired fail tid={trigger_id}: {e}")


async def run_trigger_scan():
    """Тик-сканер (раз в минуту): индивидуальные триггеры чатов.
    Сравнивает текущее UTC-время с локальной timezone каждого чата в БД.
    Идемпотентность: Redis-флаг tg:trig:{id}:{YYYYMMDDHHMM} NX EX 120.
    """
    now_utc = datetime.now(timezone.utc)
    stamp = now_utc.strftime("%Y%m%d%H%M")

    triggers = await fetch_due_triggers(now_utc)
    if not triggers:
        return
    r = await get_redis()
    log.info(f"[scheduler] trigger-scan utc={now_utc.isoformat()} due={len(triggers)}")
    for t in triggers:
        tid = t["id"]
        cid = t["chat_id"]
        try:
            # дедуп по ЗАПЛАНИРОВАННОЙ минуте (fire_stamp из chats_service),
            # а не по текущему тику: окно матча 120с покрывает 2 тика,
            # стабильный ключ не даёт стрельнуть дважды. Fallback на stamp.
            fire_stamp = t.get("fire_stamp") or stamp
            fresh = await r.set(_k_trig(tid, fire_stamp), "1", nx=True, ex=180)
            if not fresh:
                continue
            addressed = bool(t.get("addressed"))
            target = None
            if addressed:
                target = await get_random_member(int(cid))
                if not target:
                    addressed = False
            slot = t.get("preset_key") or t.get("kind") or "custom"
            await dispatch_initiation(
                int(cid), slot=slot, addressed=addressed, target=target,
                custom_prompt=t.get("prompt"),
            )
            # гасим last_fired_at для ЛЮБОГО kind: это второй слой дедупа
            # в окне (scan_due_triggers скипнет, если last_fired_at >= scheduled)
            await mark_trigger_fired_remote(cid, tid)
            if addressed and target:
                asyncio.create_task(_sadness_watch(int(cid), target))
        except Exception as e:
            log.warning(f"trigger-scan fire fail tid={tid} chat={cid}: {e}")


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
    """APScheduler: ежечасный джиттер-poke + минутный тик-сканер триггеров из БД (meal вкл)."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = AsyncIOScheduler(timezone=TG_TZ)
    # meal-триггеры (завтрак/обед/ужин) БОЛЬШЕ НЕ на хардкод-cron.
    # Единственный источник — таблица telegram_triggers (preset_key breakfast/lunch/dinner),
    # которую читает run_trigger_scan. Время/промпт/вкл-выкл настраиваются через /start.
    # run_broadcast_slot остаётся только для ручного дебага (/tg/debug/trigger).
    # Ежечасный тик в окне [start, end): планирует poke со случайным джиттером.
    hours = ",".join(str(h) for h in range(TG_POKE_HOUR_START, TG_POKE_HOUR_END))
    sched.add_job(_schedule_poke_with_jitter,
                  CronTrigger(hour=hours, minute=0, timezone=TG_TZ),
                  args=[sched], id="poke-hourly", misfire_grace_time=120)
    # Тик-сканер индивидуальных триггеров: раз в минуту сверяется с БД.
    from apscheduler.triggers.interval import IntervalTrigger
    sched.add_job(run_trigger_scan,
                  IntervalTrigger(minutes=1, timezone=TG_TZ),
                  id="trigger-scan", misfire_grace_time=45, max_instances=1,
                  coalesce=True)
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


# ══════════════════════════════════════════════════════════════════════════════
# MARKDOWN → TELEGRAM-HTML
# Агент отдаёт финальный ответ в Markdown (единый формат для web и TG). Telegram
# markdown сам не рендерит, а MarkdownV2 капризен к экранированию — поэтому
# конвертим в безопасный HTML-сабсет Telegram (parse_mode="HTML"):
#   <b> <i> <u> <s> <code> <pre> <a>. Таблицы TG не умеет → рендерим в <pre>.
# ══════════════════════════════════════════════════════════════════════════════
_HTML_ESCAPE = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _esc(text: str) -> str:
    """Экранирование под Telegram-HTML (амперсанд первым)."""
    out = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return out


def _inline_md_to_html(text: str) -> str:
    """Инлайн-разметка внутри уже экранированной строки.

    Обрабатываем: `code`, **bold**, __bold__, *italic*, _italic_, ~~strike~~,
    [text](url). Код вырезаем плейсхолдерами, чтобы внутри не трогать разметку.
    """
    codes: list[str] = []

    def _stash_code(m):
        codes.append(m.group(1))
        return f"\x00CODE{len(codes) - 1}\x00"

    # 1) inline-code `...` — вырезаем первым, внутри ничего не форматируем
    text = re.sub(r"`([^`]+)`", _stash_code, text)

    # 2) ссылки [text](url) — стэшим готовый <a>, чтобы italic/bold не лезли в label/url.
    # Разрешаем схемы http(s) И tg:// (пинги по user_id: [Имя](tg://user?id=123)).
    links: list[str] = []

    def _link(m):
        label = m.group(1)
        url = m.group(2)
        # url уже прошёл _esc на уровне строки (& -> &amp;), что валидно для href.
        # label может содержать _ / * — он уже экранирован, больше не трогаем.
        links.append(f'<a href="{url}">{label}</a>')
        return f"\x00LINK{len(links) - 1}\x00"

    text = re.sub(r"\[([^\]]+)\]\(((?:https?|tg)://[^\s)]+)\)", _link, text)

    # 3) bold **...** / __...__
    text = re.sub(r"\*\*([^\*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__([^_]+)__", r"<b>\1</b>", text)
    # 4) strike ~~...~~
    text = re.sub(r"~~([^~]+)~~", r"<s>\1</s>", text)
    # 5a) голые @username_with_underscores — стэшим, чтобы italic не съел _ в нике.
    mentions: list[str] = []

    def _stash_mention(m):
        mentions.append(m.group(0))
        return f"\x00MENT{len(mentions) - 1}\x00"

    text = re.sub(r"@[A-Za-z0-9_]{3,}", _stash_mention, text)

    # 5) italic *...* / _..._ (после bold, чтобы не съесть **)
    text = re.sub(r"(?<!\*)\*(?!\*)([^\*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_(?!_)([^_\n]+?)_(?!_)", r"<i>\1</i>", text)

    # 5b) возвращаем меншены
    def _restore_mention(m):
        return mentions[int(m.group(1))]

    text = re.sub(r"\x00MENT(\d+)\x00", _restore_mention, text)

    # 6) возвращаем ссылки и код обратно (после всей inline-разметки)
    def _restore_link(m):
        return links[int(m.group(1))]

    text = re.sub(r"\x00LINK(\d+)\x00", _restore_link, text)

    def _restore(m):
        return "<code>" + codes[int(m.group(1))] + "</code>"

    text = re.sub(r"\x00CODE(\d+)\x00", _restore, text)
    return text


def md_to_telegram_html(md: str) -> str:
    """Конвертер Markdown → Telegram-HTML (построчный, устойчивый).

    Поддержка: заголовки (## → жирная строка), нумерованные/маркированные
    списки (→ • / номер), блоки кода ``` → <pre>, инлайн-разметка, таблицы
    (| a | b |) → моноширинный <pre>. Всё, что не разметка, экранируется.
    """
    if not md:
        return ""
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # ── блок кода ``` ──
        if line.lstrip().startswith("```"):
            fence_indent = line[: len(line) - len(line.lstrip())]
            i += 1
            code_lines: list[str] = []
            while i < n and not lines[i].lstrip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # закрывающий фенс
            code_body = _esc("\n".join(code_lines))
            out.append(f"<pre>{code_body}</pre>")
            continue

        # ── таблица markdown (2+ строки с | и разделителем ---) ──
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:-]+\|[\s:|-]*$", lines[i + 1]):
            tbl: list[str] = [line]
            i += 2  # шапка + разделитель
            while i < n and "|" in lines[i]:
                tbl.append(lines[i])
                i += 1
            rows = []
            for ridx, raw in enumerate(tbl):
                cells = [c.strip() for c in raw.strip().strip("|").split("|")]
                rows.append(cells)
            # выравниваем в моноширинный вид
            widths: dict[int, int] = {}
            for r in rows:
                for ci, c in enumerate(r):
                    widths[ci] = max(widths.get(ci, 0), len(c))
            plain = []
            for r in rows:
                plain.append("  ".join(c.ljust(widths.get(ci, 0)) for ci, c in enumerate(r)))
            out.append("<pre>" + _esc("\n".join(plain)) + "</pre>")
            continue

        stripped = line.strip()

        # ── пустая строка ──
        if not stripped:
            out.append("")
            i += 1
            continue

        # ── заголовки ## / ### → жирная строка ──
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            out.append("<b>" + _inline_md_to_html(_esc(m.group(2))) + "</b>")
            i += 1
            continue

        # ── горизонтальный разделитель --- / *** ──
        if re.match(r"^([-*_])\1{2,}$", stripped):
            out.append("─" * 12)
            i += 1
            continue

        # ── цитата > ──
        if stripped.startswith(">"):
            body = stripped[1:].strip()
            out.append("<i>" + _inline_md_to_html(_esc(body)) + "</i>")
            i += 1
            continue

        # ── маркированный список - / * / • ──
        m = re.match(r"^[-*•]\s+(.*)$", stripped)
        if m:
            out.append("• " + _inline_md_to_html(_esc(m.group(1))))
            i += 1
            continue

        # ── нумерованный список 1. ──
        m = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if m:
            out.append(f"{m.group(1)}. " + _inline_md_to_html(_esc(m.group(2))))
            i += 1
            continue

        # ── обычная строка ──
        out.append(_inline_md_to_html(_esc(line)))
        i += 1

    # схлопываем 3+ пустых строк до одной
    html = "\n".join(out)
    html = re.sub(r"\n{3,}", "\n\n", html).strip()
    return html


class PushMessageRequest(BaseModel):
    chat_id: int
    text: str
    parse_mode: str = "HTML"
    edit_thinking: bool = False
    files: list | None = None
    agent_message_id: str | None = None   # agent uuid для связки TG-реакций


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
    trigger_id: int | None = None,
    token: str = "",
):
    """
    Ручной запуск проактивного триггера (для теста, минуя APScheduler).

    Параметры (query):
      trigger_id — id индивидуального триггера из telegram_triggers.
                   Если задан — читаем триггер из БД и шлём немедленно
                   с его prompt/addressed (минуя проверку времени/дня).
                   Имеет ПРИОРИТЕТ над slot.
      slot    — 'morning' | 'lunch' | 'dinner' → broadcast во все proactive-чаты
                (или в один chat_id, если задан);
                'poke' → адресный poke рандомному участнику chat_id.
      chat_id — конкретный чат. Для broadcast опционален (без него — все
                proactive-чаты); для 'poke' ОБЯЗАТЕЛЕН.
      token   — TG_DEBUG_TOKEN (должен совпасть с env).

    Пример:
      POST /tg/debug/trigger?trigger_id=3&token=SECRET
      POST /tg/debug/trigger?slot=poke&chat_id=-100123&token=SECRET
      POST /tg/debug/trigger?slot=lunch&token=SECRET
    """
    if not TG_DEBUG_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    if token != TG_DEBUG_TOKEN:
        raise HTTPException(status_code=403, detail="Bad debug token")

    # ── Приоритет: прогон конкретного индивидуального триггера по id ──
    if trigger_id is not None:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{CHATS_SERVICE_URL}/tg/trigger/{trigger_id}")
                resp.raise_for_status()
                trig = resp.json().get("trigger")
        except Exception as e:
            return DebugTriggerResponse(ok=False, action="trigger",
                                        detail=f"fetch fail: {e}")
        if not trig:
            raise HTTPException(status_code=404,
                                detail=f"trigger {trigger_id} not found")
        cid = trig["chat_id"]
        if not await is_allowed(int(cid)):
            raise HTTPException(status_code=400, detail="chat not registered")
        addressed = bool(trig.get("addressed"))
        target = None
        if addressed:
            target = await get_random_member(int(cid))
            if not target:
                addressed = False
        kind = trig.get("kind") or "custom"
        await dispatch_initiation(
            int(cid), slot=kind, addressed=addressed, target=target,
            custom_prompt=trig.get("prompt"),
        )
        if addressed and target:
            asyncio.create_task(_sadness_watch(int(cid), target))
        return DebugTriggerResponse(
            ok=True, action=f"trigger:{trigger_id}",
            detail=(f"fired trigger {trigger_id} in chat {cid} "
                    f"(kind={kind}, addressed={addressed}, "
                    f"prompt={(trig.get('prompt') or '')[:60]!r})"))

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

    # Агент отдаёт финальный ответ в Markdown → конвертим в Telegram-HTML.
    # Только для parse_mode=HTML (дефолт); если провайдер прислал plain — не трогаем.
    if (req.parse_mode or "").upper() == "HTML":
        render_text = md_to_telegram_html(req.text)
    else:
        render_text = req.text

    chunks = split_message_safely(render_text)
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
        # Связка tg_message_id -> agent_message_id + текст (для реакций).
        # Кладём только если есть agent_message_id — чтобы при реакции найти запись в БД.
        try:
            log.info(f"[msgmap] push chat={req.chat_id} aid={req.agent_message_id!r} sent_ids={sent_ids}")
            if req.agent_message_id and sent_ids:
                r = await get_redis()
                snippet = (req.text or "")[:400]
                payload = json.dumps({"aid": req.agent_message_id, "text": snippet})
                for _mid in sent_ids:
                    await r.set(_k_msgmap(req.chat_id, _mid), payload, ex=86400)
                    log.info(f"[msgmap] SET key={_k_msgmap(req.chat_id, _mid)}")
            elif not req.agent_message_id:
                log.warning(f"[msgmap] NO agent_message_id -> reactions won't map, chat={req.chat_id} sent_ids={sent_ids}")
        except Exception as e:
            log.warning(f"msgmap save fail chat={req.chat_id}: {e}")
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
    """Файл из ответа агента. Поддерживаем file_b64, локальный path и file_url.

    file_url — путь для скачивания с agent_service (/web/file?path=...&chat_id=...).
    Крупные файлы (напр. pptx 6 МБ) не влезают в Kafka как base64
    (MessageSizeTooLargeError), поэтому агент кладёт только file_url, а бот
    (host-режим, порт 9001 проброшен) дочитывает бинарник HTTP-запросом.
    Файл лежит на диске агента или восстанавливается им из OBS.
    """
    from io import BytesIO
    raw = None
    if f.get("file_b64"):
        raw = base64.b64decode(f["file_b64"])
    elif f.get("path") and os.path.exists(f["path"]):
        raw = pathlib.Path(f["path"]).read_bytes()
    elif f.get("file_url"):
        url = f["file_url"]
        if url.startswith("/"):
            url = AGENT_SERVICE_URL.rstrip("/") + url
        try:
            async with httpx.AsyncClient(timeout=120.0) as _c:
                _r = await _c.get(url)
                if _r.status_code == 200:
                    raw = _r.content
                else:
                    logging.warning(f"[tg-file] pull {url} -> HTTP {_r.status_code}")
        except Exception as _pull_err:
            logging.error(f"[tg-file] не удалось скачать файл по file_url={url}: {_pull_err}")
    if not raw:
        return
    name = f.get("name") or "file"
    # Делегируем выбор метода отправки (voice/audio/video/photo/document)
    # в отдельный модуль media_voice. kind — явная подсказка от агента
    # (kind="voice" для generate_audio OGG/Opus), иначе решает расширение.
    from media_voice import send_agent_media
    await send_agent_media(
        _bot_app.bot, chat_id, raw, name,
        kind=f.get("kind"), caption=f.get("caption", ""),
    )


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


# Короткие «сигнальные» реплики: тег с таким текстом трактуется как
# «сейчас пришлю форвард» и ждёт форвард (обратный порядок), а не отвечает
# сразу. Длинные самодостаточные вопросы (> порога и без паттерна) —
# обрабатываются немедленно, чтобы не тормозить обычные обращения.
TG_SIGNAL_MAXLEN = int(os.environ.get("TG_SIGNAL_MAXLEN", "40"))
_SIGNAL_PATTERNS = (
    "вот", "теперь", "это", "гля", "глянь", "смотри", "мнение",
    "что думаешь", "что скажешь", "как тебе", "оцени", "разбер", "инфо",
    "а ты", "а вы", "прокоммент", "коммент",
)


def _is_signal_tag(stripped_tag: str) -> bool:
    """True, если текст после снятия @-тега — короткая сигнальная фраза,
    после которой ожидается форвард (обратный порядок связывания)."""
    t = (stripped_tag or "").strip().lower()
    if not t:
        return True  # голый тег — всегда ждёт форвард
    if len(t) <= TG_SIGNAL_MAXLEN:
        return True  # короткая реплика вроде «вот инфо», «а теперь?»
    # длинный текст — сигналим только при явном паттерне-обёртке
    return any(p in t for p in _SIGNAL_PATTERNS)


def _registration_gate_text(chat_id: int) -> str:
    """Единый текст отказа для незарегистрированного чата (/start + on_text_message)."""
    return (
        "У вас нет доступа к агенту в этом чате.\n\n"
        f"Регистрация чатов доступна только через интерфейс CatchPM: {CATCHPM_URL}\n\n"
        f"Передайте администратору chat_id этого чата: {chat_id}"
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
# Админская категория тулов (платный RouterAPI: видео/аудио). Видна только админу.
TG_ADMIN_TOOL_CATEGORIES = ["media_gen"]
TG_TOOL_CATEGORIES = ["pm_research", "internet", "content", "sessions", "skills"]


def _parse_tg_admin_ids():
    """tg_id админов из того же ADMINS_INFO (поле tg_id). Без дублирования записей."""
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
        tg = item.get("tg_id")
        if tg is None:
            continue
        try:
            ids.add(int(tg))
        except (ValueError, TypeError):
            continue
    return ids


TG_ADMIN_IDS = _parse_tg_admin_ids()


def _is_tg_admin(uid) -> bool:
    try:
        return int(uid) in TG_ADMIN_IDS
    except (ValueError, TypeError):
        return False

TG_TRIGGERS = ["mention", "orchestrator"]


def _settings_summary(settings: dict) -> str:
    model = settings.get("agent_model") or "coder"
    mode = settings.get("trigger_mode") or "orchestrator"
    tools = settings.get("selected_tools") or []
    tools_str = ", ".join(tools) if tools else "нет (только базовые)"
    name = settings.get("agent_name")
    name_str = name.strip() if (name and name.strip()) else "по умолчанию"
    return (
        "<b>Настройки агента для этого чата</b>\n\n"
        f"Имя: <code>{name_str}</code>\n"
        f"Модель: <code>{model}</code>\n"
        f"Триггер: <code>{mode}</code>\n"
        f"Инструменты: <code>{tools_str}</code>"
    )


def _menu_root(settings: dict | None = None) -> InlineKeyboardMarkup:
    settings = settings or {}
    proactive = settings.get("proactive_enabled")
    if proactive is None:
        proactive = True
    # Будильники (завтрак/обед/ужин + poke)
    poke_label = ("\U0001f514 Будильники: вкл" if proactive
                  else "\U0001f515 Будильники: выкл")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Имя агента", callback_data="tg:menu:name")],
        [InlineKeyboardButton("Модель", callback_data="tg:menu:model")],
        [InlineKeyboardButton("Инструменты", callback_data="tg:menu:tools")],
        [InlineKeyboardButton("Триггер", callback_data="tg:menu:trigger")],
        [InlineKeyboardButton("Будильники", callback_data="tg:menu:alarms")],
        [InlineKeyboardButton(poke_label, callback_data="tg:toggle:proactive")],
        [InlineKeyboardButton("Закрыть", callback_data="tg:close")],
    ])


def _menu_model(current) -> InlineKeyboardMarkup:
    cur = current or "coder"
    rows = []
    for m in TG_MODELS:
        mark = "\u2713 " if m == cur else ""
        rows.append([InlineKeyboardButton(f"{mark}{m}", callback_data=f"tg:set:model:{m}")])
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


def _menu_tools(selected, is_admin: bool = False) -> InlineKeyboardMarkup:
    sel = set(selected or [])
    rows = []
    cats = list(TG_TOOL_CATEGORIES)
    if is_admin:
        cats = cats + list(TG_ADMIN_TOOL_CATEGORIES)
    for c in cats:
        mark = "\u2611 " if c in sel else "\u2610 "
        label = f"{mark}{c}"
        if c in TG_ADMIN_TOOL_CATEGORIES:
            label = f"{mark}\U0001f512 {c}"  # замок: админ-категория
        rows.append([InlineKeyboardButton(label, callback_data=f"tg:tool:{c}")])
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


def _menu_trigger(current) -> InlineKeyboardMarkup:
    cur = current or "orchestrator"
    rows = []
    for t in TG_TRIGGERS:
        mark = "\u2713 " if t == cur else ""
        rows.append([InlineKeyboardButton(f"{mark}{t}", callback_data=f"tg:set:trigger:{t}")])
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


ALARM_PRESETS = [
    ("breakfast", "Завтрак", "09:00"),
    ("lunch", "Обед", "13:00"),
    ("dinner", "Ужин", "18:00"),
]
ALARM_TITLES = {k: t for k, t, _ in ALARM_PRESETS}

async def fetch_chat_triggers(chat_id: int) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CHATS_SERVICE_URL}/tg/triggers/{chat_id}")
            resp.raise_for_status()
            return resp.json().get("triggers", []) or []
    except Exception as e:
        log.error(f"fetch_chat_triggers fail chat={chat_id}: {e}")
        return []


def _find_alarm_trigger(triggers: list[dict], preset_key: str) -> dict | None:
    for t in triggers or []:
        if t.get("preset_key") == preset_key:
            return t
    return None


async def ensure_alarm_trigger(chat_id: int, uid: int, preset_key: str) -> dict | None:
    triggers = await fetch_chat_triggers(chat_id)
    hit = _find_alarm_trigger(triggers, preset_key)
    if hit:
        return hit
    title = ALARM_TITLES.get(preset_key, preset_key)
    default_time = next((tm for k, _, tm in ALARM_PRESETS if k == preset_key), "09:00")
    body = {
        "kind": "recurring",
        "title": title,
        "time_msk": default_time,
        "days_of_week": [],
        "prompt": None,
        "preset_key": preset_key,
        "addressed": False,
        "added_by_user_id": uid,
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(f"{CHATS_SERVICE_URL}/tg/triggers/{chat_id}", json=body)
            resp.raise_for_status()
            return resp.json().get("trigger")
    except Exception as e:
        log.error(f"ensure_alarm_trigger fail chat={chat_id} preset={preset_key}: {e}")
        return None


async def patch_trigger(chat_id: int, trigger_id: int, patch: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.patch(f"{CHATS_SERVICE_URL}/tg/triggers/{chat_id}/{trigger_id}", json=patch)
            resp.raise_for_status()
            return resp.json().get("trigger")
    except Exception as e:
        log.error(f"patch_trigger fail chat={chat_id} trigger={trigger_id}: {e}")
        return None


def _menu_alarms(settings: dict | None, triggers: list[dict]) -> InlineKeyboardMarkup:
    settings = settings or {}
    enabled = settings.get("proactive_enabled")
    if enabled is None:
        enabled = True
    rows = []
    rows.append([InlineKeyboardButton(("🔔 Будильники: вкл" if enabled else "🔕 Будильники: выкл"), callback_data="tg:toggle:proactive")])
    rows.append([InlineKeyboardButton(f"TZ: {(settings.get('timezone_name') or TG_TZ)}", callback_data="tg:alarm:tz")])
    for preset_key, title, default_time in ALARM_PRESETS:
        trig = _find_alarm_trigger(triggers, preset_key)
        active = bool(trig and trig.get("is_active", True))
        hhmm = (trig or {}).get("time_msk") or default_time
        label = f"{'✅' if active else '⏸'} {title}: {hhmm}"
        rows.append([InlineKeyboardButton(label, callback_data=f"tg:alarm:open:{preset_key}")])
    rows.append([InlineKeyboardButton("‹ Назад", callback_data="tg:menu:root")])
    return InlineKeyboardMarkup(rows)


def _menu_alarm_detail(preset_key: str, trig: dict | None) -> InlineKeyboardMarkup:
    title = ALARM_TITLES.get(preset_key, preset_key)
    active = bool((trig or {}).get("is_active", True))
    rows = [
        [InlineKeyboardButton(f"Время: {((trig or {}).get('time_msk') or '09:00')}", callback_data=f"tg:alarm:edit_time:{preset_key}")],
        [InlineKeyboardButton("Промпт", callback_data=f"tg:alarm:edit_prompt:{preset_key}")],
        [InlineKeyboardButton(("Выключить" if active else "Включить"), callback_data=f"tg:alarm:toggle:{preset_key}")],
        [InlineKeyboardButton("Сбросить промпт", callback_data=f"tg:alarm:clear_prompt:{preset_key}")],
        [InlineKeyboardButton(f"‹ К будильникам ({title})", callback_data="tg:menu:alarms")],
    ]
    return InlineKeyboardMarkup(rows)


async def _refresh_alarms(query, chat_id: int):
    settings = await get_chat_settings(chat_id)
    triggers = await fetch_chat_triggers(chat_id)
    try:
        await query.edit_message_text(
            _settings_summary(settings),
            parse_mode=ParseMode.HTML,
            reply_markup=_menu_alarms(settings, triggers),
        )
    except Exception:
        pass


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
        await query.answer("Чат не зарегистрирован.", show_alert=True)
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
            ("Будильники " + ("выкл" if cur else "вкл"))
            if ok else "Ошибка"
        )
        await _refresh(lambda s: _menu_root(s))
        return
    if data == "tg:menu:alarms":
        await query.answer()
        await _refresh_alarms(query, chat_id)
        return

    if data == "tg:alarm:tz":
        uid = query.from_user.id if query.from_user else 0
        try:
            r = await get_redis()
            await r.set(_k_alarmedit(chat_id, uid), "tz", ex=300)
        except Exception:
            pass
        await query.answer()
        try:
            await query.edit_message_text(
                "Отправь timezone чата в формате IANA, например: <code>Europe/Moscow</code> или <code>Europe/Samara</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ К будильникам", callback_data="tg:menu:alarms")]]),
            )
        except Exception:
            pass
        return

    if data.startswith("tg:alarm:open:"):
        preset_key = data.rsplit(":", 1)[-1]
        uid = query.from_user.id if query.from_user else 0
        trig = await ensure_alarm_trigger(chat_id, uid, preset_key)
        await query.answer()
        try:
            await query.edit_message_text(
                _settings_summary(await get_chat_settings(chat_id)),
                parse_mode=ParseMode.HTML,
                reply_markup=_menu_alarm_detail(preset_key, trig),
            )
        except Exception:
            pass
        return

    if data.startswith("tg:alarm:edit_time:"):
        preset_key = data.rsplit(":", 1)[-1]
        uid = query.from_user.id if query.from_user else 0
        try:
            r = await get_redis()
            await r.set(_k_alarmedit(chat_id, uid), f"time:{preset_key}", ex=300)
        except Exception:
            pass
        await query.answer()
        try:
            await query.edit_message_text(
                f"Отправь новое время для {ALARM_TITLES.get(preset_key, preset_key)} в формате <code>HH:MM</code> по локальной timezone чата.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data=f"tg:alarm:open:{preset_key}")]]),
            )
        except Exception:
            pass
        return

    if data.startswith("tg:alarm:edit_prompt:"):
        preset_key = data.rsplit(":", 1)[-1]
        uid = query.from_user.id if query.from_user else 0
        try:
            r = await get_redis()
            await r.set(_k_alarmedit(chat_id, uid), f"prompt:{preset_key}", ex=300)
        except Exception:
            pass
        await query.answer()
        try:
            await query.edit_message_text(
                f"Отправь новый промпт для {ALARM_TITLES.get(preset_key, preset_key)}. Он будет выполнен как custom_prompt триггера.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("‹ Назад", callback_data=f"tg:alarm:open:{preset_key}")]]),
            )
        except Exception:
            pass
        return

    if data.startswith("tg:alarm:toggle:"):
        preset_key = data.rsplit(":", 1)[-1]
        uid = query.from_user.id if query.from_user else 0
        trig = await ensure_alarm_trigger(chat_id, uid, preset_key)
        cur = bool((trig or {}).get("is_active", True))
        ok = trig and await patch_trigger(chat_id, int(trig["id"]), {"is_active": (not cur)})
        await query.answer("Сохранено" if ok else "Ошибка")
        trig = await ensure_alarm_trigger(chat_id, uid, preset_key)
        try:
            await query.edit_message_text(
                _settings_summary(await get_chat_settings(chat_id)),
                parse_mode=ParseMode.HTML,
                reply_markup=_menu_alarm_detail(preset_key, trig),
            )
        except Exception:
            pass
        return

    if data.startswith("tg:alarm:clear_prompt:"):
        preset_key = data.rsplit(":", 1)[-1]
        uid = query.from_user.id if query.from_user else 0
        trig = await ensure_alarm_trigger(chat_id, uid, preset_key)
        ok = trig and await patch_trigger(chat_id, int(trig["id"]), {"prompt": ""})
        await query.answer("Промпт очищен" if ok else "Ошибка")
        trig = await ensure_alarm_trigger(chat_id, uid, preset_key)
        try:
            await query.edit_message_text(
                _settings_summary(await get_chat_settings(chat_id)),
                parse_mode=ParseMode.HTML,
                reply_markup=_menu_alarm_detail(preset_key, trig),
            )
        except Exception:
            pass
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
                "Отправьте следующим сообщением имя агента для этого чата.\n"
                "Например: <code>Макс</code> или <code>Ассистент</code>.\n"
                "Чтобы сбросить имя — отправьте <code>-</code>.",
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
        _uid_t = query.from_user.id if query.from_user else 0
        _adm_t = _is_tg_admin(_uid_t)
        await _refresh(lambda s: _menu_tools(s.get("selected_tools"), is_admin=_adm_t))
        return
    if data == "tg:menu:trigger":
        await query.answer()
        await _refresh(lambda s: _menu_trigger(s.get("trigger_mode")))
        return

    if data.startswith("tg:set:model:"):
        model = data.split(":", 3)[3]
        if model not in TG_MODELS:
            await query.answer("Неизвестная модель", show_alert=True)
            return
        ok = await save_chat_settings(chat_id, {"agent_model": model})
        await query.answer("Сохранено" if ok else "Ошибка")
        await _refresh(lambda s: _menu_model(s.get("agent_model")))
        return

    if data.startswith("tg:set:trigger:"):
        trig = data.split(":", 3)[3]
        if trig not in TG_TRIGGERS:
            await query.answer("Неизвестный триггер", show_alert=True)
            return
        ok = await save_chat_settings(chat_id, {"trigger_mode": trig})
        await query.answer("Сохранено" if ok else "Ошибка")
        await _refresh(lambda s: _menu_trigger(s.get("trigger_mode")))
        return

    if data.startswith("tg:tool:"):
        cat = data.split(":", 2)[2]
        _uid_t = query.from_user.id if query.from_user else 0
        _adm_t = _is_tg_admin(_uid_t)
        _allowed_cats = set(TG_TOOL_CATEGORIES)
        if _adm_t:
            _allowed_cats |= set(TG_ADMIN_TOOL_CATEGORIES)
        if cat not in _allowed_cats:
            # админская категория для не-админа либо вообще неизвестная
            await query.answer("Эта категория доступна только админу", show_alert=True)
            return
        settings = await get_chat_settings(chat_id)
        sel = set(settings.get("selected_tools") or [])
        if cat in sel:
            sel.discard(cat)
        else:
            sel.add(cat)
        ok = await save_chat_settings(chat_id, {"selected_tools": sorted(sel)})
        await query.answer("Сохранено" if ok else "Ошибка")
        await _refresh(lambda s: _menu_tools(s.get("selected_tools"), is_admin=_adm_t))
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
    # Атомарный GETDEL — защита от дубля update (см. комментарий у alarmedit).
    try:
        r = await get_redis()
        try:
            awaiting_name = await r.execute_command("GETDEL", _k_setname(chat_id, user.id))
        except Exception:
            awaiting_name = await r.get(_k_setname(chat_id, user.id))
            if awaiting_name:
                await r.delete(_k_setname(chat_id, user.id))
    except Exception:
        awaiting_name = None
    if awaiting_name:
        if isinstance(awaiting_name, bytes):
            awaiting_name = awaiting_name.decode()
        try:
            await post_already_dispatched(chat_id, msg)
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

    # ── перехват ввода настроек будильника (после кнопок Время/Промпт/TZ) ──
    # ВАЖНО: берём СВЕЖИЙ redis-инстанс (r из блока awaiting_name мог быть
    # невалиден/закрыт после сетевых await'ов), иначе get упадёт в except,
    # alarm_flag=None и ввод «08:00» уедет в агента вместо будильника.
    #
    # АТОМАРНО: читаем И удаляем флаг одной командой GETDEL. Это защищает от
    # дубля update от Telegram (два параллельных on_text_message на одно
    # сообщение): непустое значение получит РОВНО ОДИН заход — он и обработает
    # будильник + сделает return. Второй заход получит None и НЕ уйдёт в
    # агента, потому что мы дополнительно помечаем этот message_id как
    # «съеденный будильником» через post_already_dispatched.
    try:
        r = await get_redis()
        alarm_key = _k_alarmedit(chat_id, user.id)
        try:
            alarm_flag = await r.execute_command("GETDEL", alarm_key)
        except Exception:
            # старый redis без GETDEL — фолбэк на get+delete
            alarm_flag = await r.get(alarm_key)
            if alarm_flag:
                await r.delete(alarm_key)
    except Exception as e:
        log.warning(f"[alarmedit] get fail chat={chat_id} uid={user.id}: {e}")
        alarm_flag = None
    log.info(f"[alarmedit GETDEL] key={_k_alarmedit(chat_id, user.id)} val={alarm_flag!r}")
    if alarm_flag:
        if isinstance(alarm_flag, bytes):
            alarm_flag = alarm_flag.decode()
        log.info(f"[alarmedit HIT] chat={chat_id} uid={user.id} flag={alarm_flag!r} raw={raw_text!r}")
        # Помечаем пост как обработанный будильником, чтобы дублирующий update
        # по тому же message_id (если он проскочит) не ушёл в dispatch.
        try:
            await post_already_dispatched(chat_id, msg)
        except Exception:
            pass
        value = (raw_text or "").strip()
        # TZ
        if alarm_flag == "tz":
            try:
                ZoneInfo(value)
                tz_ok = True
            except Exception:
                tz_ok = False
            if not tz_ok:
                await msg.reply_text("Неизвестный часовой пояс. Пример: Europe/Moscow или Europe/Samara.")
            else:
                ok = await save_chat_settings(chat_id, {"timezone_name": value})
                await msg.reply_text(("Timezone: " + value) if ok else "Ошибка сохранения.")
            return
        # time:<preset> / prompt:<preset>
        kind_flag, _, preset_key = alarm_flag.partition(":")
        if kind_flag == "time":
            if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", value):
                await msg.reply_text("Формат времени HH:MM (например 08:00).")
                return
            hh, mm = value.split(":")
            norm = f"{int(hh):02d}:{mm}"
            trig = await ensure_alarm_trigger(chat_id, user.id, preset_key)
            ok = trig and await patch_trigger(chat_id, int(trig["id"]), {"time_msk": norm})
            await msg.reply_text((ALARM_TITLES.get(preset_key, preset_key) + ": " + norm) if ok else "Ошибка сохранения.")
            return
        if kind_flag == "prompt":
            new_prompt = value[:2000]
            trig = await ensure_alarm_trigger(chat_id, user.id, preset_key)
            ok = trig and await patch_trigger(chat_id, int(trig["id"]), {"prompt": new_prompt})
            await msg.reply_text(("Промпт для " + ALARM_TITLES.get(preset_key, preset_key) + " сохранён.") if ok else "Ошибка сохранения.")
            return
        # неизвестный флаг — молча выходим, не шлём в агента
        return

    # разбор упоминания/реплая
    is_group = _is_group(chat_id)
    approved = False
    text_for_agent = raw_text
    tagged = False
    reply_to_bot = False

    if is_group:
        stripped, tagged = _strip_bot_tag(raw_text, bot_username)
        reply_to_bot = _is_reply_to_bot(msg, bot_username)
        approved = tagged or reply_to_bot
        if approved:
            text_for_agent = stripped or raw_text
    else:
        # личка — всегда обращение к агенту
        approved = True

    # ── форвард из другого чата/канала ──────────────────────────────────────
    # Если это ПЕРЕСЛАННОЕ сообщение и оно НЕ является прямым обращением
    # (нет тега/reply на бота) — просто откладываем его контент в stash и
    # выходим. Подхватим при следующем тег-сообщении.
    is_forward = bool(msg.forward_origin)
    if is_forward and not approved:
        # Если перед этим был голый тег, ждущий форвард (обратный
        # порядок) — свяжем и шлём агенту. Иначе отложим в stash.
        if raw_text.strip():
            await buffer_push(chat_id, author, raw_text.strip())
        await handle_incoming_forward(chat_id, msg)
        return

    # обогащение ссылками/форвардом/реплаем
    text_for_agent = _enrich_text(msg, text_for_agent)

    # Тег-сообщение подхватывает отложенный форвард, если у него самого
    # нет собственного смыслового контента (только обращение к боту).
    fwd_file_b64 = None
    fwd_file_name = None
    tag_waits_forward = False
    if approved and not is_forward:
        stashed = await pop_forward(chat_id)
        if not stashed:
            # Форварда ещё нет. Если тег НЕ несёт собственного
            # смыслового контента (голый @-тег/обращение по имени) —
            # откладываем его на короткое окно: вдруг форвард
            # прилетит следующим сообщением (обратный порядок).
            stripped_tag = _strip_bot_tag(raw_text, bot_username)[0].strip()
            # Ждём форвард при @-упоминании с ПУСТЫМ или коротким «сигнальным»
            # текстом («вот инфо», «а теперь?», «что думаешь?»). Длинный
            # самодостаточный вопрос обрабатываем сразу (не тормозим).
            # Reply-to-bot без тега — продолжение диалога, отвечаем сразу.
            if tagged and not reply_to_bot and _is_signal_tag(stripped_tag):
                await stash_tag(chat_id, user.id, author, text_for_agent)
                tag_waits_forward = True
                # ВАРИАНТ 2: планируем отложенный ответ на сам тег-вопрос.
                # Если форвард прилетит за TG_TAG_WAIT сек — он выдернет тег
                # (pop_tag) и ответит связкой, а наш таймер получит None и
                # тихо выйдет. Иначе через TG_TAG_WAIT отвечаем на исходный
                # тег — тег больше не теряется.
                asyncio.create_task(_deferred_tag_answer(
                    chat_id, user.id, author,
                    _strip_bot_tag(raw_text, bot_username)[0].strip()))
        if stashed:
            f_author = stashed.get("author") or "источник"
            f_text = (stashed.get("text") or "").strip()
            if f_text:
                text_for_agent = (
                    f"{text_for_agent}\n[Переслано от {f_author}]: {f_text}"
                ).strip()
            else:
                text_for_agent = (
                    f"{text_for_agent}\n[Переслано от {f_author}: картинка]"
                ).strip()
            if stashed.get("file_b64"):
                fwd_file_b64 = stashed["file_b64"]
                fwd_file_name = stashed.get("file_name") or "forward.jpg"
            log.info(f"[fwd] merged forward into tagged msg chat={chat_id} "
                     f"img={'yes' if fwd_file_b64 else 'no'}")

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

    # Голый/сигнальный тег без форварда — отложенный ответ (ВАРИАНТ 2).
    # Мы уже запланировали _deferred_tag_answer: если форвард прилетит за
    # TG_TAG_WAIT сек — он свяжет тег и ответит связкой; иначе таймер
    # ответит на сам тег-вопрос. В любом случае тег НЕ теряется.
    if tag_waits_forward:
        return

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

    # Если подхватили форвард — text_for_agent уже содержит блок [Переслано...],
    # но buffer_flush мог его перекрыть склейкой буфера. Дописываем форвард-блок
    # к merged_text, если его там ещё нет.
    if (fwd_file_b64 or "[Переслано от" in text_for_agent) and \
            "[Переслано от" not in merged_text:
        merged_text = f"{merged_text}\n{text_for_agent}".strip()

    # Подмешиваем обратную связь по реакциям (копились с прошлого ответа бота).
    # Буфер очищается ТОЛЬКО здесь (после next-сообщения), без TTL.
    try:
        react_fb = await reactions_feedback_flush(chat_id)
        if react_fb:
            merged_text = f"{react_fb}\n\n{merged_text}".strip()
    except Exception as e:
        log.warning(f"reactions feedback merge fail chat={chat_id}: {e}")

    # Единый guard поста ДО плашки «Думаю...»: если по этому посту (например
    # альбом с caption-тегом, кадры которого параллельно идут в on_photo)
    # агент уже вызван из другой точки входа — молча выходим, не оставляя
    # висящую плашку и не дублируя ответ.
    if await post_already_dispatched(chat_id, msg):
        log.info(f"[post] skip duplicate dispatch (on_text) chat={chat_id} mid={msg.message_id} gid={msg.media_group_id}")
        return

    thinking = await msg.reply_text("Думаю...")
    _thinking_messages[chat_id] = thinking.message_id

    try:
        await dispatch_to_agent(chat_id, user.id, author, merged_text,
                                file_b64=fwd_file_b64, file_name=fwd_file_name)
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


async def _flush_album(gid: str):
    """Флаш буфера альбома после дебаунс-окна: один dispatch со всеми кадрами."""
    try:
        await asyncio.sleep(TG_ALBUM_DEBOUNCE)
    except asyncio.CancelledError:
        return
    buf = _album_buffers.pop(gid, None)
    if not buf or not buf.get("items"):
        return
    items = buf["items"][:TG_ALBUM_MAX]
    chat_id = buf["chat_id"]
    user_id = buf["user_id"]
    author = buf["author"]
    raw_text = buf.get("text") or ""
    n = len(items)
    if raw_text:
        merged_text = f"{author}: {raw_text}".strip()
    else:
        merged_text = f"{author}: [альбом из {n} картинок]"
    log.info(f"[album] flush chat={chat_id} gid={gid} frames={n}")
    try:
        await dispatch_to_agent(chat_id, user_id, author, merged_text, files=items)
    except Exception as e:
        log.error(f"[album] dispatch fail chat={chat_id} gid={gid}: {e}")


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Фото с подписью: Telegram шлёт его как photo-сообщение (не text/document),
    текст лежит в caption. Гейт и триггер как в on_text/on_document,
    картинка уходит в vision через _upload_files_to_agent (/web/upload_files).
    """
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
            # Вариант Б (склейка тег↔форвард-альбом раздельными апдейтами):
            # если ранее пришёл голый тег и ждёт форвард (tg:lasttag жив), то
            # это фото-сообщение — тот самый форвард (пересланный альбом ИЛИ
            # обычное фото, отправленное следом за тегом). Подхватываем через
            # handle_incoming_forward, который свяжет тег и вызовет агента ОДИН
            # раз (per-post guard внутри отсекает лишние кадры альбома).
            has_waiting_tag = False
            try:
                r = await get_redis()
                has_waiting_tag = bool(await r.get(_k_lasttag(chat_id)))
            except Exception:
                has_waiting_tag = False
            if msg.forward_origin or has_waiting_tag:
                await handle_incoming_forward(chat_id, msg)
            return

    photo = msg.photo[-1] if msg.photo else None  # самый большой размер
    if not photo:
        return
    if photo.file_size and photo.file_size > MAX_FILE_SIZE:
        await msg.reply_text("Картинка слишком большая (лимит 20 MB).")
        return

    fname = f"photo_{photo.file_unique_id}.jpg"
    tg_file = await photo.get_file()
    file_bytearray = await tg_file.download_as_bytearray()
    file_b64 = base64.b64encode(file_bytearray).decode("utf-8")
    item = {"file_b64": file_b64, "file_name": fname}

    gid = msg.media_group_id
    if gid:
        # АЛЬБОМ: TG шлёт кадры N отдельными photo-апдейтами с общим
        # media_group_id. Копим все кадры в буфер и одним dispatch'ем отправляем
        # после дебаунс-окна (тишины). caption есть только у одного кадра.
        key = str(gid)
        buf = _album_buffers.get(key)
        if buf is None:
            thinking = await msg.reply_text("Смотрю картинки...")
            _thinking_messages[chat_id] = thinking.message_id
            buf = {
                "items": [], "chat_id": chat_id, "user_id": user.id,
                "author": author, "text": raw_text, "task": None,
            }
            _album_buffers[key] = buf
        if len(buf["items"]) < TG_ALBUM_MAX:
            buf["items"].append(item)
        if raw_text and not buf.get("text"):
            buf["text"] = raw_text
        # перезапускаем дебаунс-таймер на каждый новый кадр
        old_task = buf.get("task")
        if old_task and not old_task.done():
            old_task.cancel()
        buf["task"] = asyncio.create_task(_flush_album(key))
        return

    # ОДИНОЧНОЕ фото: единый guard поста (вариант Б) — мог параллельно
    # прийти в on_text; первый дошедший ставит ключ, дубли молча выходят.
    if await post_already_dispatched(chat_id, msg):
        log.info(f"[post] skip duplicate dispatch (on_photo) chat={chat_id} mid={msg.message_id}")
        return

    thinking = await msg.reply_text("Смотрю картинку...")
    _thinking_messages[chat_id] = thinking.message_id

    merged_text = f"{author}: {raw_text}".strip() if raw_text else f"{author}: [картинка]"
    try:
        await dispatch_to_agent(chat_id, user.id, author, merged_text, files=[item])
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
    application.add_handler(MessageHandler(filters.PHOTO, on_photo))
    application.add_handler(MessageReactionHandler(on_message_reaction))
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
