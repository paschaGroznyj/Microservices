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
import re
import time
import uuid

import httpx
import redis.asyncio as aioredis
from telegram import Update, Message, MessageEntity
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
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
ALLOWED_TTL        = int(os.environ.get("ALLOWED_TTL", "60"))

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
def _k_orch_ts(chat_id: int) -> str:    return f"tg:orch_ts:{chat_id}"


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


async def buffer_flush(chat_id: int) -> list[str]:
    """
    Забирает накопленные реплики «с последнего ответа агента» и очищает буфер.
    Возврат — список строк "Ник: текст". После флаша буфер пуст → маркер
    сдвигается естественно (следующая порция копится заново).
    """
    r = await get_redis()
    try:
        # atomic: прочитать всё и удалить
        async with r.pipeline(transaction=True) as pipe:
            pipe.lrange(_k_buf(chat_id), 0, -1)
            pipe.delete(_k_buf(chat_id))
            res = await pipe.execute()
        return res[0] or []
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start внутри целевой группы — заглушка меню настроек (Коммит 2 добавит
    инлайн-кнопки: модель / тулы / триггер). Настройки строго per-chat.
    """
    chat_id = update.effective_chat.id
    if not await is_allowed(chat_id):
        await update.message.reply_text("Этот чат не подключён к агенту. Обратитесь к администратору CatchPM.")
        return
    settings = await get_chat_settings(chat_id)
    model = settings.get("agent_model") or "coder (по умолчанию)"
    mode = settings.get("trigger_mode") or "orchestrator"
    await update.message.reply_text(
        "Агент CatchPM подключён к этому чату.\n\n"
        f"Модель: {model}\n"
        f"Триггер: {mode}\n\n"
        "Меню настроек (модель, инструменты, триггер) появится здесь в следующем обновлении.",
    )


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not await is_allowed(chat_id):
        return

    msg = update.message
    raw_text = msg.text or msg.caption or ""
    user = update.effective_user
    author = user.full_name or user.username or "Аноним"
    bot_username = context.bot.username

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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_error_handler(_on_error)
    _bot_app = application

    config = uvicorn.Config(api, host="0.0.0.0", port=TG_PUSH_PORT, log_level="info")
    server = uvicorn.Server(config)

    async with application:
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info(f"tg_bot запущен: polling + push API на :{TG_PUSH_PORT}")
        try:
            await server.serve()
        finally:
            await application.updater.stop()
            await application.stop()


def main():
    asyncio.run(run_both())


if __name__ == "__main__":
    main()
