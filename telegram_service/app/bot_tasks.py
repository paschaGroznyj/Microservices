"""
bot_tasks.py — расширенный Telegram-бот (поверх bot_async.py)
═══════════════════════════════════════════════════════════════
Стек: aiogram 3.x + FastAPI (uvicorn в daemon-thread) — тот же что в bot_async.py.

НОВОЕ (поверх оригинала):
  /start   — расширенный экран с картой команд
  /task    — создать задачу (агент сохраняет в БД)
  /tasks   — список открытых задач
  /done    — завершить задачу: /done <id>
  /assign  — назначить: /assign <id> @username
  /graph   — граф задач (агент генерирует HTML vis.js, шлёт файлом)
  Callback-кнопки: ✅ Выполнено / 📋 Детали / 👤 Назначить / 📊 Граф

АРХИТЕКТУРА (не изменилась):
  Bot ─── POST /api/tg/message ──► agent_service (202 Accepted)
                                          │ fire-and-forget
  Bot ◄── POST /bot/reply  ◄─────────── agent пушит ответ

Env-переменные (без изменений):
  BOT_TOKEN          — токен от @BotFather
  AGENT_URL          — http://agent_service:9001  (app.py)
  BOT_CALLBACK_PORT  — порт FastAPI-callback (default: 9002)
  BOT_CALLBACK_URL   — http://tg_bot:9002 (как agent_service видит бота)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import threading
from typing import Optional

import httpx
import uvicorn
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────────

BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN")
AGENT_URL         = os.getenv("AGENT_SERVICE_URL", "http://agent_service:9001")
BOT_CALLBACK_PORT = int(os.getenv("BOT_CALLBACK_PORT", "9002"))
BOT_CALLBACK_URL  = os.getenv("BOT_CALLBACK_URL", f"http://tg_bot:{BOT_CALLBACK_PORT}")
HTTP_TIMEOUT      = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tg_bot")

# ── FastAPI callback-сервер ────────────────────────────────────────────────────

callback_app = FastAPI(title="TG Bot Callback Server")

# chat_id → message_id (placeholder "⏳ Думаю...")
pending_messages: dict[int, int] = {}


class CallbackReply(BaseModel):
    chat_id:    int
    reply:      str
    parse_mode: str = "HTML"


@callback_app.post("/bot/reply")
async def bot_reply(payload: CallbackReply):
    """Агент пушит сюда ответ — бот редактирует placeholder или шлёт новое."""
    chat_id    = payload.chat_id
    text       = payload.reply
    parse_mode = payload.parse_mode or None

    placeholder_id = pending_messages.pop(chat_id, None)
    try:
        if placeholder_id:
            try:
                await bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=placeholder_id,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
                return JSONResponse({"status": "edited"})
            except Exception:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=placeholder_id)
                except Exception:
                    pass

        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        return JSONResponse({"status": "sent"})

    except Exception as e:
        log.error("bot_reply error for chat %s: %s", chat_id, e)
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


@callback_app.get("/health")
async def health():
    return {"status": "ok", "pending": len(pending_messages)}


# ── Bot + Dispatcher ───────────────────────────────────────────────────────────

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# ── Util: отправка к агенту ────────────────────────────────────────────────────

async def ask_agent(
    *,
    text:      str,
    message:   Message,
    file_b64:  str | None = None,
    file_name: str | None = None,
) -> None:
    """
    Показывает placeholder «⏳», затем fire-and-forget POST к agent_service.
    Ответ придёт через /bot/reply.
    """
    chat_id = message.chat.id
    user    = message.from_user

    placeholder = await message.answer("⏳ Думаю...")
    pending_messages[chat_id] = placeholder.message_id

    payload = {
        "message":      text,
        "user_id":      user.id,
        "username":     user.full_name,
        "user_tag":     f"@{user.username}" if user.username else user.full_name,
        "chat_id":      chat_id,
        "chat_type":    "private" if message.chat.type == "private" else "group",
        "file_b64":     file_b64,
        "file_name":    file_name,
        "callback_url": f"{BOT_CALLBACK_URL}/bot/reply",
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(f"{AGENT_URL}/api/tg/message", json=payload)
        if resp.status_code == 200:
            # sync-fallback: агент ответил сразу
            data = resp.json()
            reply_text = data.get("reply", "Агент не вернул ответ.")
            pending_messages.pop(chat_id, None)
            try:
                await placeholder.edit_text(reply_text, disable_web_page_preview=True)
            except Exception:
                await bot.send_message(chat_id=chat_id, text=reply_text,
                                       disable_web_page_preview=True)
    except httpx.TimeoutException:
        pending_messages.pop(chat_id, None)
        try:
            await placeholder.edit_text("❌ Не удалось связаться с агентом. Попробуй позже.")
        except Exception:
            await bot.send_message(chat_id=chat_id,
                                   text="❌ Не удалось связаться с агентом. Попробуй позже.")
    except Exception as e:
        log.error("ask_agent failed: %s", e)
        pending_messages.pop(chat_id, None)
        try:
            await placeholder.edit_text(f"❌ Ошибка: {e}")
        except Exception:
            await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка: {e}")


# ── Inline-keyboard для задач ──────────────────────────────────────────────────

def _task_kb(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Выполнено",  callback_data=f"task:done:{task_id}"),
            InlineKeyboardButton(text="📋 Детали",     callback_data=f"task:detail:{task_id}"),
        ],
        [
            InlineKeyboardButton(text="👤 Назначить", callback_data=f"task:assign:{task_id}"),
            InlineKeyboardButton(text="📊 Граф",      callback_data=f"task:graph:{task_id}"),
        ],
    ])


# ── /start ─────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "<b>PM Agent</b>\n\n"
        "Управляю задачами и базой знаний Process Mining.\n\n"
        "<b>Задачи</b>\n"
        "• /task <i>название</i> — создать задачу\n"
        "• /tasks — список открытых задач\n"
        "• /done <i>id</i> — завершить задачу\n"
        "• /assign <i>id @user</i> — назначить исполнителя\n"
        "• /graph — граф задач\n\n"
        "<b>База знаний</b>\n"
        "• Задай вопрос — отвечу из базы статей\n"
        "• Прикрепи документ — обработаю\n\n"
        "<b>Граф задач</b> строится на vis.js — так же, как граф статей в CatchPM.\n"
        "Узлы = задачи, рёбра = зависимости / исполнители."
    )


# ── /task ──────────────────────────────────────────────────────────────────────

@dp.message(Command("task"))
async def cmd_task(message: Message):
    args = (message.text or "").split(maxsplit=1)
    title = args[1].strip() if len(args) > 1 else ""
    if not title:
        await message.answer(
            "Укажи название задачи:\n"
            "<code>/task Разработать модуль X</code>"
        )
        return

    user = message.from_user
    await ask_agent(
        text=(
            f"Создай задачу с названием: «{title}».\n"
            f"Автор: {user.full_name} (@{user.username or '-'}).\n"
            "Сохрани в БД, верни ID задачи и краткое подтверждение. "
            "После ответа добавь инлайн-кнопки (✅ Выполнено, 📊 Граф)."
        ),
        message=message,
    )


# ── /tasks ─────────────────────────────────────────────────────────────────────

@dp.message(Command("tasks"))
async def cmd_tasks(message: Message):
    await ask_agent(
        text=(
            "Покажи список всех открытых задач. "
            "Формат каждой строки: <code>#ID</code> — Название — Исполнитель — Статус — Дата создания. "
            "Если задач нет — скажи об этом явно."
        ),
        message=message,
    )


# ── /done ──────────────────────────────────────────────────────────────────────

@dp.message(Command("done"))
async def cmd_done(message: Message):
    args = (message.text or "").split(maxsplit=1)
    task_id = args[1].strip() if len(args) > 1 else ""
    if not task_id:
        await message.answer("Укажи ID задачи:\n<code>/done 42</code>")
        return

    user = message.from_user
    await ask_agent(
        text=f"Отметь задачу #{task_id} как выполненную. Выполнил: {user.full_name} (@{user.username or '-'}).",
        message=message,
    )


# ── /assign ────────────────────────────────────────────────────────────────────

@dp.message(Command("assign"))
async def cmd_assign(message: Message):
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Формат: <code>/assign &lt;id&gt; @username</code>\n"
            "Пример: <code>/assign 42 @ivanov</code>"
        )
        return

    task_id  = parts[1].strip()
    assignee = parts[2].strip().lstrip("@")
    user     = message.from_user
    await ask_agent(
        text=(
            f"Назначь задачу #{task_id} исполнителю @{assignee}. "
            f"Назначил: {user.full_name}."
        ),
        message=message,
    )


# ── /graph ─────────────────────────────────────────────────────────────────────

@dp.message(Command("graph"))
async def cmd_graph(message: Message):
    await ask_agent(
        text=(
            "Построй граф задач в стиле графа базы знаний CatchPM.\n"
            "Узлы — задачи, рёбра — зависимости и связи исполнителей.\n"
            "Используй vis.js (Network). Сгенерируй HTML-файл и пришли мне как документ."
        ),
        message=message,
    )


# ── Callback-кнопки ────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("task:"))
async def on_task_callback(query: CallbackQuery):
    await query.answer()
    parts = (query.data or "").split(":", 2)
    if len(parts) != 3:
        return

    _, action, task_id = parts
    user = query.from_user

    prompts: dict[str, str] = {
        "done": (
            f"Отметь задачу #{task_id} как выполненную. "
            f"Выполнил: {user.full_name} (@{user.username or '-'})."
        ),
        "detail": (
            f"Покажи подробности задачи #{task_id}: "
            "описание, исполнитель, автор, дата создания, история изменений, статус."
        ),
        "assign": (
            f"Нужно назначить исполнителя задаче #{task_id}. "
            "Попроси у пользователя написать @username исполнителя и жди его ответа."
        ),
        "graph": (
            f"Построй граф зависимостей для задачи #{task_id} и связанных задач. "
            "vis.js (Network), HTML-файл — пришли как документ."
        ),
    }

    prompt = prompts.get(action)
    if not prompt:
        return

    # Создаём псевдо-сообщение для ask_agent через query.message
    msg = query.message
    placeholder = await msg.answer("⏳ Обрабатываю...")
    pending_messages[msg.chat.id] = placeholder.message_id

    payload = {
        "message":      prompt,
        "user_id":      user.id,
        "username":     user.full_name,
        "user_tag":     f"@{user.username}" if user.username else user.full_name,
        "chat_id":      msg.chat.id,
        "chat_type":    "group" if msg.chat.type != "private" else "private",
        "callback_url": f"{BOT_CALLBACK_URL}/bot/reply",
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            await client.post(f"{AGENT_URL}/api/tg/message", json=payload)
    except Exception as e:
        log.error("callback ask_agent error: %s", e)
        pending_messages.pop(msg.chat.id, None)
        try:
            await placeholder.edit_text(f"❌ Ошибка: {e}")
        except Exception:
            pass


# ── Text и Document (из оригинального bot_async.py без изменений) ──────────────

@dp.message(F.text | F.document)
async def handle_message(message: Message):
    # Пропускаем если это команда (обработается выше)
    if message.text and message.text.startswith("/"):
        return

    file_b64  = None
    file_name = None

    if message.document:
        try:
            file_obj  = await bot.get_file(message.document.file_id)
            file_bytes = await bot.download_file(file_obj.file_path)
            file_b64  = base64.b64encode(file_bytes.read()).decode("utf-8")
            file_name = message.document.file_name or "document"
        except Exception as e:
            log.error("File download error: %s", e)

    asyncio.create_task(
        ask_agent(
            text=message.text or message.caption or "",
            message=message,
            file_b64=file_b64,
            file_name=file_name,
        )
    )


# ── FastAPI в daemon-thread ────────────────────────────────────────────────────

def run_fastapi() -> None:
    config = uvicorn.Config(
        callback_app,
        host="0.0.0.0",
        port=BOT_CALLBACK_PORT,
        log_level="info",
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


# ── Меню команд ───────────────────────────────────────────────────────────────

BOT_COMMANDS = [
    BotCommand(command="start",  description="Стартовый экран"),
    BotCommand(command="task",   description="Создать задачу: /task Название"),
    BotCommand(command="tasks",  description="Список открытых задач"),
    BotCommand(command="done",   description="Завершить: /done ID"),
    BotCommand(command="assign", description="Назначить: /assign ID @user"),
    BotCommand(command="graph",  description="Граф задач"),
]


# ── main ───────────────────────────────────────────────────────────────────────

async def main():
    # Регистрируем меню команд в Telegram
    await bot.set_my_commands(BOT_COMMANDS)

    # FastAPI-callback в daemon-thread
    t = threading.Thread(target=run_fastapi, daemon=True)
    t.start()
    log.info("FastAPI callback server started on port %s", BOT_CALLBACK_PORT)

    log.info("Telegram polling started")
    await dp.start_polling(bot, skip_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
