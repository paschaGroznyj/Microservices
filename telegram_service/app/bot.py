"""
tg_bot/bot.py
─────────────
Минимальный бот: текст и файлы → POST /api/tg/message → agent_service → reply.
Никакой Kafka, никакой БД, никакого голоса.

Env:
  TELEGRAM_BOT_TOKEN  — токен от @BotFather
  TELEGRAM_CHAT_ID    — личка (int)
  TELEGRAM_GROUPS_IDS — comma-separated ids групп, например: -100123,-100456
  AGENT_SERVICE_URL   — http://agent_service:9001
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import pathlib
import base64

import httpx
from telegram import Update, Message, MessageEntity
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_CHAT_IDS   = [
    int(x) for x in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()
]
TELEGRAM_GROUPS_IDS = [
    int(x) for x in os.environ.get("TELEGRAM_GROUPS_IDS", "").split(",") if x.strip()
]
AGENT_SERVICE_URL   = os.environ.get("AGENT_SERVICE_URL", "http://agent_service:9001")

RECEIVED_DIR = pathlib.Path("/tmp/received_files")
RECEIVED_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 20 * 1024 * 1024
TEXT_EXTS = {
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv",
    ".xml", ".html", ".yaml", ".yml", ".sql", ".sh", ".log",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tg_bot")


api = FastAPI(title="TG Bot Push API")

# Глобальная ссылка на bot instance — заполняется в main() после старта Application
_bot_app: Application | None = None

# Храним message_id последнего "Думаю..." сообщения по chat_id,
# чтобы уметь его редактировать вместо отправки нового
_thinking_messages: dict[int, int] = {}


class PushMessageRequest(BaseModel):
    chat_id: int
    text: str
    parse_mode: str = "HTML"
    edit_thinking: bool = False  # если True — редактирует последнее "Думаю..." вместо нового сообщения


class PushMessageResponse(BaseModel):
    ok: bool
    message_ids: list[int] = []
    error: str | None = None


import re

TELEGRAM_MAX_LEN = 4000  # чуть меньше лимита 4096, с запасом на HTML-разметку

def split_message_safely(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """
    Делит текст на чанки, не превышая max_len, стараясь резать по границам
    абзацев/строк, а не посередине HTML-тега.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_len:
        # Ищем лучшую точку разреза в пределах max_len:
        # сначала пробуем по двойному переносу (абзац), потом по одинарному,
        # потом просто по пробелу — но НЕ разрешаем резать внутри тега
        cut = _find_safe_cut(remaining, max_len)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        chunks.append(remaining)

    return _fix_unclosed_tags(chunks)


def _find_safe_cut(text: str, max_len: int) -> int:
    window = text[:max_len]

    # Не резать посередине открытого тега вида "<b" без закрывающего ">"
    last_lt = window.rfind("<")
    last_gt = window.rfind(">")
    if last_lt > last_gt:
        # внутри окна есть незакрытый "<" — обрезаем до него
        window = window[:last_lt]

    for separator in ["\n\n", "\n", ". ", " "]:
        idx = window.rfind(separator)
        if idx > max_len * 0.5:  # не резать слишком рано, если разделитель есть хоть где-то приемлемо
            return idx + len(separator)

    return len(window) if window else max_len  # крайний случай — жёсткий разрез


def _fix_unclosed_tags(chunks: list[str]) -> list[str]:
    """
    Простая защита: если в чанке остались незакрытые парные HTML-теги
    (b, i, u, s, code, pre), закрываем их в конце чанка и открываем заново
    в начале следующего — чтобы Telegram не падал на parse_mode=HTML.
    """
    SUPPORTED_TAGS = ["b", "i", "u", "s", "code", "pre"]
    fixed = []
    open_tags: list[str] = []

    for chunk in chunks:
        # закрываем то, что было открыто в предыдущем чанке
        prefix = "".join(f"<{t}>" for t in open_tags)
        chunk_with_prefix = prefix + chunk

        # находим все теги в текущем чанке, обновляем стек открытых
        for tag in SUPPORTED_TAGS:
            opens = len(re.findall(f"<{tag}>", chunk_with_prefix))
            closes = len(re.findall(f"</{tag}>", chunk_with_prefix))
            if opens > closes and tag not in open_tags:
                open_tags.append(tag)
            elif closes >= opens and tag in open_tags:
                open_tags.remove(tag)

        suffix = "".join(f"</{t}>" for t in reversed(open_tags))
        fixed.append(chunk_with_prefix + suffix)

    return fixed

def telegram_role(message, chat_id) -> dict:
    return {
        "text": message, 
        "chat_id": chat_id, 
        "user_name": "Telegram bot", 
        "user_tag": "", 
        "user_id": -2
    }

@api.post("/push_message", response_model=PushMessageResponse)
async def push_message(req: PushMessageRequest):
    if _bot_app is None:
        raise HTTPException(status_code=503, detail="Bot ещё не инициализирован")

    if not req.text:
        text = "Это сервис Телеграм-БОТ, ты вернул пустой ответ пользователю, продолжи свою мысль"
        role = telegram_role(text, req.chat_id)

        await ask_pm_agent(
            text=text,
            user_id=role["user_id"], 
            user_name=role["user_name"], 
            user_tag=role["user_tag"],
            chat_id=role["chat_id"],
            file_b64='',    
            file_name='' 
        )
        return PushMessageResponse(ok=False, error="Empty text, agent re-prompted")
        
    chunks = split_message_safely(req.text)
    

    sent_ids = []

    try:
        if req.edit_thinking and chat_id_has_thinking(req.chat_id):
            # Первый чанк — редактируем существующее сообщение
            thinking_id = _thinking_messages.pop(req.chat_id)
            try:
                await _bot_app.bot.edit_message_text(
                    chat_id=req.chat_id,
                    message_id=thinking_id,
                    text=chunks[0],
                    parse_mode=req.parse_mode,
                )
                sent_ids.append(thinking_id)
                chunks = chunks[1:]  # остальные чанки шлём как новые сообщения
            except Exception as e:
                log.warning(f"Не удалось отредактировать thinking-сообщение: {e}, отправляю новое")

        for chunk in chunks:
            msg = await _bot_app.bot.send_message(
                chat_id=req.chat_id,
                text=chunk,
                parse_mode=req.parse_mode,
            )
            sent_ids.append(msg.message_id)

        return PushMessageResponse(ok=True, message_ids=sent_ids)

    except Exception as e:
        log.error(f"push_message failed для chat_id={req.chat_id}: {e}", exc_info=True)
        text = f"Это сервис Телеграм-БОТ, ты вернул сообщение с ошибкой. Исправь. Ошибка: {str(e)[:300]}"
        role = telegram_role(text, req.chat_id)
        
        # 3. Исправлен доступ к словарю через []
        await ask_pm_agent(
            text=text,
            user_id=role["user_id"], 
            user_name=role["user_name"], 
            user_tag=role["user_tag"],
            chat_id=role["chat_id"],
            file_b64='',    
            file_name='' 
        )
        return PushMessageResponse(ok=False, error=str(e))

class PushFileRequest(BaseModel):
    chat_id: int
    file_b64: str
    filename: str
    caption: str = ""


@api.post("/push_file")
async def push_file(req: PushFileRequest):
    if _bot_app is None:
        raise HTTPException(status_code=503, detail="Bot ещё не инициализирован")

    try:
        raw = base64.b64decode(req.file_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Некорректный base64: {e}")

    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"Файл превышает лимит {MAX_FILE_SIZE} байт")

    try:
        from io import BytesIO
        bio = BytesIO(raw)
        bio.name = req.filename

        ext = pathlib.Path(req.filename).suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            await _bot_app.bot.send_photo(chat_id=req.chat_id, photo=bio, caption=req.caption)
        else:
            await _bot_app.bot.send_document(chat_id=req.chat_id, document=bio, filename=req.filename, caption=req.caption)

        return {"ok": True}

    except Exception as e:
        log.error(f"push_file failed для chat_id={req.chat_id}: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}

def chat_id_has_thinking(chat_id: int) -> bool:
    return chat_id in _thinking_messages

@api.get("/health")
async def health():
    return {"status": "ok", "bot_initialized": _bot_app is not None}

# ── Access helpers ─────────────────────────────────────────────────────────────

def _allowed(chat_id: int) -> bool:
    # Проверяем вхождение и в личные, и в групповые чаты
    return chat_id in TELEGRAM_CHAT_IDS or chat_id in TELEGRAM_GROUPS_IDS

def _is_group(chat_id: int) -> bool:
    return chat_id in TELEGRAM_GROUPS_IDS

def _strip_bot_tag(text: str, bot_username: str) -> tuple[str, bool]:
    tag = f"@{bot_username}"
    if tag in text:
        return text.replace(tag, "").strip(), True
    return text, False




# ── Agent Service call ─────────────────────────────────────────────────────────

async def ask_pm_agent(text: str, 
    user_id: int, 
    user_name: str, 
    user_tag: str, 
    chat_id: int, 
    file_b64: str,    
    file_name: str,
    chat_type: str = "private") -> str:

    payload = {
        "message":   text,
        "user_id":   user_id,
        "username":  user_name,
        "user_tag": user_tag,
        "chat_type": chat_type,
        "chat_id": chat_id,
        "file_b64": file_b64,
        "file_name": file_name
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{AGENT_SERVICE_URL}/api/tg/message", json=payload)
            resp.raise_for_status()
            # return resp.json().get("reply", "Агент не вернул ответ.")
    except httpx.HTTPStatusError as e:
        log.error("agent_service HTTP %s: %s", e.response.status_code, e.response.text)
        return f"❌ Агент вернул ошибку {e.response.status_code}."
    except Exception as e:
        log.error("ask_pm_agent failed: %s", e)
        return f"❌ Не удалось связаться с агентом: {e}"


# ── Handlers ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 PM Agent бот\n\n"
        "Напишите любое сообщение или отправьте файл — перенаправлю в агента.\n\n"
        "/start — это сообщение"
    )

def extract_urls_from_message(msg: Message) -> list[str]:
    """Извлекает явные и скрытые (hyperlink) ссылки из сообщения."""
    urls = []

    has_text    = bool(msg.text)
    has_caption = bool(msg.caption)

    entities = msg.entities or msg.caption_entities or []

    for entity in entities:
        try:
            if entity.type == MessageEntity.TEXT_LINK:
                label = (
                    msg.parse_entity(entity)
                    if has_text
                    else msg.parse_caption_entity(entity)
                    if has_caption
                    else ""
                )
                urls.append(f"{label} -> {entity.url}")

            elif entity.type == MessageEntity.URL:
                extracted = (
                    msg.parse_entity(entity)
                    if has_text
                    else msg.parse_caption_entity(entity)
                    if has_caption
                    else ""
                )
                if extracted:
                    urls.append(extracted)

        except Exception:
            continue

    return urls


async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _allowed(chat_id):
        await update.message.reply_text("⛔ Бот приватный.")
        return

    msg = update.message
    raw_text = msg.text or msg.caption or ""

    if _is_group(chat_id):
        raw_text, tagged = _strip_bot_tag(raw_text, context.bot.username)
        if not tagged:
            return
        if not raw_text and not (msg.forward_origin or msg.reply_to_message):
            await update.message.reply_text("🤖 Напишите задачу вместе с тегом.")
            return

    user_comment = ""
    forward_text = ""
    reply_text = ""

    # === ИЗВЛЕЧЕНИЕ ССЫЛОК ИЗ ОСНОВНОГО СООБЩЕНИЯ ===
    try:
        main_urls = extract_urls_from_message(msg)
        urls_str = ("\n[Найденные ссылки:\n" + "\n".join(main_urls) + "]") if main_urls else ""

        # 1. Если сообщение ПЕРЕСЛАННОЕ
        if msg.forward_origin:
            origin = msg.forward_origin
            if origin.type == "user":
                f_author = origin.sender_user.full_name
            elif origin.type == "hidden_user":
                f_author = origin.sender_user_name
            elif origin.type in ("chat", "channel"):
                f_author = origin.chat.title
            else:
                f_author = "Неизвестный источник"

            forward_text = f"--- Пересланное сообщение от {f_author} ---\n{raw_text}{urls_str}"
        else:
            user_comment = f"{raw_text}{urls_str}".strip()

        # 2. Если это ОТВЕТ (Reply) на другое сообщение
        if msg.reply_to_message:
            reply_msg = msg.reply_to_message
            r_author = reply_msg.from_user.full_name if reply_msg.from_user else "Неизвестный"
            r_content = reply_msg.text or reply_msg.caption or "[Медиа/Файл без текста]"
            
            # Ссылки из сообщения, на которое ответили
            reply_urls = extract_urls_from_message(reply_msg)
            r_urls_str = ("\n[Ссылки из ответа:\n" + "\n".join(reply_urls) + "]") if reply_urls else ""
            
            reply_text = f"--- В ответ на сообщение от {r_author} ---\n{r_content}{r_urls_str}"

        # 3. Собираем итоговый текст
        parts = [p for p in [user_comment, forward_text, reply_text] if p.strip()]
        full_text = "\n\n".join(parts).strip()

        if not full_text:
            return
        user = update.effective_user
        thinking_msg = await msg.reply_text("⏳ Думаю...")
        _thinking_messages[chat_id] = thinking_msg.message_id

        reply = await ask_pm_agent(
            text=full_text,
            user_id=user.id,
            user_name=user.full_name or "Аноним",
            user_tag=user.username or "БезТега",
            chat_type="group" if _is_group(chat_id) else "private",
            chat_id=chat_id,
            file_b64=None,    
            file_name=None 
        )

        # if len(reply) > 4000:
        #     reply = reply[:4000] + "\n\n... (обрезано)"
        # try:
        #     await msg.reply_text(reply, parse_mode=ParseMode.HTML)
        # except Exception as e:
        #     log.info(f'Проблема с парсинго сообщения от агента, {e}')
        #     await msg.reply_text(f'Ошибка парсинга сообщения от агента: {str(e)[:300]}')
    except Exception as e:
        await msg.reply_text(f'Что-то не так с сообщением: {str(e)[:300]}')
    # await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN_V2)


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not _allowed(chat_id):
        await update.message.reply_text("⛔ Бот приватный.")
        return

    msg = update.message
    caption = update.message.caption or ""
    raw_text = msg.text or msg.caption or ""
    if _is_group(chat_id):
        raw_text, tagged = _strip_bot_tag(raw_text, context.bot.username)
        if not tagged:
            return
        if not raw_text and not (msg.forward_origin or msg.reply_to_message):
            await update.message.reply_text("🤖 Напишите задачу вместе с тегом.")
            return

    doc = update.message.document
    if not doc:
        await update.message.reply_text("🤖 Вы что прислали?")
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("❌ Файл слишком большой (лимит 20 MB).")
        return

    fname = pathlib.Path(doc.file_name or f"file_{doc.file_id[:8]}").name
    await update.message.reply_text(f"📥 Обрабатываю файл {fname}...")

    # Скачиваем файл во временный буфер/байты прямо из Telegram
    tg_file = await doc.get_file()
    file_bytearray = await tg_file.download_as_bytearray()
    
    # Кодируем в base64 string
    file_b64 = base64.b64encode(file_bytearray).decode("utf-8")

    user = update.effective_user
    logging.info(f"БИНАРНИК: {file_b64[:100]}")

    thinking_msg = await msg.reply_text("⏳ Думаю...")
    _thinking_messages[chat_id] = thinking_msg.message_id

    reply = await ask_pm_agent(
        text=raw_text,
        user_id=user.id,
        user_name=user.full_name or user.username or "Аноним",
        user_tag=user.username or "БезТега",
        chat_type="group" if _is_group(chat_id) else "private",
        chat_id=chat_id,
        file_b64=file_b64,    
        file_name=fname        
    )

    if len(reply) > 4000:
        reply = reply[:4000] + "\n\n... (обрезано)"
    try:
        await msg.reply_text(reply, parse_mode=ParseMode.HTML)
    except Exception as e:
        log.info(f'Проблема с парсинго сообщения от агента, {e}')
        await msg.reply_text(f'Ошибка парсинга сообщения от агента: {str(e)[:300]}')


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error: %s", context.error, exc_info=context.error)


# ── Main ───────────────────────────────────────────────────────────────────────

async def run_both():
    global _bot_app
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_error_handler(_on_error)

    _bot_app = application

    config = uvicorn.Config(api, host="0.0.0.0", port=8010, log_level="info")
    server = uvicorn.Server(config)

    async with application:
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        log.info("tg_bot запущен: polling + push API на :8010")
        try:
            await server.serve()  # блокирует, пока не остановят
        finally:
            await application.updater.stop()
            await application.stop()


def main():
    asyncio.run(run_both())


if __name__ == "__main__":
    main()