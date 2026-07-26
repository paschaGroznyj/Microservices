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
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{AGENT_SERVICE_URL}/api/tg/message", json=payload)
            resp.raise_for_status()
            return resp.json().get("reply", "Агент не вернул ответ.")
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
        print(full_text)
        await msg.reply_text("⏳ Думаю...")

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

        if len(reply) > 4000:
            reply = reply[:4000] + "\n\n... (обрезано)"
        try:
            await msg.reply_text(reply, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.info(f'Проблема с парсинго сообщения от агента, {e}')
            await msg.reply_text(f'Ошибка парсинга сообщения от агента: {str(e)[:300]}')
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
    await update.message.reply_text("⏳ Думаю...")

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
    await update.message.reply_text(reply, parse_mode=ParseMode.HTML)


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error: %s", context.error, exc_info=context.error)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_error_handler(_on_error)

    log.info("tg_bot запущен. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()