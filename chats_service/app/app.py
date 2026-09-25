import json
import logging
from fastapi import FastAPI, Header
from kafka import KafkaProducer
from pydantic import BaseModel
from app.db.chats_tables import DataBaseChats
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from app.core.config import SECRET_KEY, ALGORITHM, KAFKA_BROKER
from app.db.group_chat_db import GroupChatDB
from app import group_chat as group_chat_module
from app.db.telegram_db import TelegramDB
import time

WATCHDOG_INTERVAL = 10      # как часто проверяем
MESSAGE_TIMEOUT = 20        # сколько секунд ждём ответ от бота


db = DataBaseChats()  # ← один раз для всего приложения
service_message_alive = False
producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    api_version=(4,1,0),
    key_serializer=lambda k: str(k).encode('utf-8'),
    value_serializer=lambda m: json.dumps(m).encode('utf-8'))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="Chats Service")

# Общий/групповой чат: отдельный слой БД + роутер (не раздуваем этот app.py)
group_db = GroupChatDB(None)  # pool проставится на startup
app.include_router(group_chat_module.router)

# Telegram-интеграция: отдельный слой БД (allow-list + per-chat настройки).
tg_db = TelegramDB(None)  # pool проставится на startup

# async def bot_response_watchdog():
#     logging.info("🤖 Bot watchdog started")
#
#     while True:
#         try:
#             # 1. Получаем зависшие сообщения
#             stuck_messages = await db.get_stuck_messages(
#                 older_than_seconds=MESSAGE_TIMEOUT
#             )
#
#             for msg in stuck_messages:
#                 logging.warning(
#                     f"LLM timeout for message id={msg['id']} chat={msg['chat_id']}"
#                 )
#
#                 # 2. Помечаем сообщение как FAILED
#                 await db.mark_message_failed(msg["id"])
#
#                 # 3. Формируем system/bot сообщение
#                 bot_message = {
#                     "chat_id": msg["chat_id"],
#                     "message": "🤖 Ассистент временно недоступен. Попробуйте позже.",
#                     "is_from_bot": True,
#                 }
#
#                 # 4. Сохраняем в БД
#                 await db.add_message(
#                     username=msg["username"],
#                     message=bot_message["message"],
#                     chat_id=msg["chat_id"],
#                     is_from_bot=True
#                 )
#
#                 # 5. Отправляем в WebSocket
#                 await broadcast(msg["chat_id"], bot_message)
#
#         except Exception as e:
#             logging.error(f"Watchdog error: {e}")
#
#         await asyncio.sleep(WATCHDOG_INTERVAL)



def get_auth_data():
    return {"secret_key": SECRET_KEY, "algorithm": ALGORITHM}

def get_token(request: Request):
    token = request.cookies.get('users_access_token')
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token not found')
    return token


async def get_current_user_from_cookie(token: str = Depends(get_token)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail='Invalid token')

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail='Missing username')

    return username


class ChatMessage(BaseModel):
    user_id: int | None
    username: str
    tb_name: str | None = None
    message: str
    chat_id: str
    type_agent: str
    request_id: str
    selected_tools: list = []
    context_size: str = "medium"
    message_id: str
    has_files: bool = False
    files: list = []
    agent_model: str | None = None
    gateway_target: str = "main"
    # Проактивная инициация из telegram_service (будильники/poke/грусть).
    initiated: bool = False
    slot: str | None = None
    addressed: bool = False
    target_username: str | None = None
    sad_followup: bool = False
    

@app.on_event("startup")
async def startup():
    global service_message_alive
    service_message_alive = True
    await db.init_db_pool()

    # Групповой чат: переиспользуем тот же пул asyncpg, инициализируем схему
    # (таблицы group_rooms/group_messages/group_room_members + общая комната).
    group_db.pool = db.pool
    await group_db.init_schema()
    group_chat_module.bind(group_db, producer)

    # Telegram: переиспользуем тот же пул, создаём таблицы telegram_*.
    tg_db.pool = db.pool
    await tg_db.init_db_pool()

    # # 🚀 старт watchdog-а
    # asyncio.create_task(bot_response_watchdog())


@app.on_event("shutdown")
async def shutdown():
    global service_message_alive
    service_message_alive = False
    await db.pool.close()

@app.post("/send_message")
async def send_message(chat: ChatMessage):
    message = {
        "user_id": chat.user_id,
        "tb_name": chat.tb_name,
        "username": chat.username,
        "chat_id": chat.chat_id,
        "message": chat.message,
        "type_agent": chat.type_agent,
        "is_from_bot": False,
        "request_id": chat.request_id,
        "selected_tools": chat.selected_tools,
        "context_size": chat.context_size,
        "message_id": chat.message_id,
        "has_files": chat.has_files,
        "files": chat.files,
        "agent_model": chat.agent_model,
        "gateway_target": chat.gateway_target,
        "initiated": chat.initiated,
        "slot": chat.slot,
        "addressed": chat.addressed,
        "target_username": chat.target_username,
        "sad_followup": chat.sad_followup,
    }
    logging.info(message)
    start = time.perf_counter()
    # отправка в Kafka
    producer.send("chat-messages", key=chat.user_id, value=message)
    producer.flush()
    elapsed = time.perf_counter() - start
    logging.info(f"Message Service. Время на отправку в сокет и на перекладку в кафку = {elapsed:.4f}s")
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM-ИНТЕГРАЦИЯ: allow-list чатов + per-chat настройки.
# Встраивание чата — только через CatchPM UI (залогиненный юзер).
# telegram_service (host-режим) читает эти эндпоинты по 127.0.0.1:<port>.
# ══════════════════════════════════════════════════════════════════════════════
class TgChatIn(BaseModel):
    chat_id: str
    added_by_user_id: int | None = None
    # Опционально: настройки, применяемые сразу при встраивании (из CatchPM UI).
    system_prompt: str | None = None
    agent_model: str | None = None
    selected_tools: list | None = None
    trigger_mode: str | None = None
    orchestrator_context_n: int | None = None
    proactive_enabled: bool | None = None


class TgSettingsIn(BaseModel):
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_model: str | None = None
    selected_tools: list | None = None
    trigger_mode: str | None = None
    orchestrator_context_n: int | None = None
    proactive_enabled: bool | None = None


@app.get("/tg/chats")
async def tg_list_chats():
    """Список встроенных активных чатов вместе с настройками — для CatchPM UI."""
    return {"chats": await tg_db.list_chats_with_settings()}


@app.post("/tg/chats")
async def tg_add_chat(body: TgChatIn):
    """Встроить телеграм-чат в allow-list (из CatchPM UI). Тип по знаку chat_id."""
    row = await tg_db.upsert_chat(body.chat_id, body.added_by_user_id)
    # Если вместе с чатом переданы настройки — применяем их сразу.
    settings_fields = {
        k: v for k, v in {
            "system_prompt": body.system_prompt,
            "agent_model": body.agent_model,
            "selected_tools": body.selected_tools,
            "trigger_mode": body.trigger_mode,
            "orchestrator_context_n": body.orchestrator_context_n,
            "proactive_enabled": body.proactive_enabled,
        }.items() if v is not None
    }
    settings = None
    if settings_fields:
        settings = await tg_db.update_settings(body.chat_id, **settings_fields)
    else:
        settings = await tg_db.get_settings(body.chat_id)
    return {"status": "ok", "chat": row, "settings": settings}


@app.delete("/tg/chats/{chat_id}")
async def tg_remove_chat(chat_id: str):
    ok = await tg_db.deactivate_chat(chat_id)
    return {"status": "ok" if ok else "not_found"}


@app.get("/tg/allowed")
async def tg_allowed():
    """Список активных chat_id — telegram_service кэширует в Redis."""
    return {"chat_ids": await tg_db.list_active_chat_ids()}


@app.get("/tg/settings/{chat_id}")
async def tg_get_settings(chat_id: str):
    s = await tg_db.get_settings(chat_id)
    return {"settings": s}


@app.post("/tg/settings/{chat_id}")
async def tg_set_settings(chat_id: str, body: TgSettingsIn):
    """Точечное обновление настроек (из /start бота). Админ-тулы режутся ботом/агентом."""
    s = await tg_db.update_settings(
        chat_id,
        **{k: v for k, v in body.dict().items() if v is not None},
    )
    return {"status": "ok", "settings": s}


class TgMemberIn(BaseModel):
    chat_id: str
    user_id: int
    username: str | None = None
    first_name: str | None = None
    is_bot: bool = False


@app.post("/tg/members")
async def tg_upsert_member(body: TgMemberIn):
    """Пассивный сбор участников: бот шлёт каждого написавшего."""
    await tg_db.upsert_member(
        body.chat_id, body.user_id, body.username, body.first_name, body.is_bot
    )
    return {"status": "ok"}


@app.get("/tg/members/{chat_id}/random")
async def tg_random_member(chat_id: str):
    """Рандомный участник — для проактивной инициации."""
    m = await tg_db.get_random_member(chat_id)
    return {"member": m}


@app.get("/tg/proactive_chats")
async def tg_proactive_chats():
    """Активные чаты с включёнными будильниками — для планировщика бота."""
    return {"chats": await tg_db.list_proactive_chats()}


class TgLogIn(BaseModel):
    chat_id: str
    user_id: int | None = None
    username: str
    message: str
    is_from_bot: bool = False


@app.post("/tg/log_message")
async def tg_log_message(body: TgLogIn):
    """
    Пишет в chats ТОЛЬКО одобренные сообщения группы (те, на которые агент
    реагирует). Молчаливый поток группы сюда не попадает — память не засоряется.
    Идёт мимо Kafka (без повторного триггера агента).
    """
    await db.add_message(
        user_id=body.user_id,
        message=body.message,
        chat_id=body.chat_id,
        is_from_bot=body.is_from_bot,
    )
    return {"status": "ok"}



@app.get("/chat/{chat_id}/messages")
async def get_chat_messages(chat_id: str, limit: int = 50, offset: int = 0,  x_user_name: str = Header(..., alias="X-User-Name")):
    try:
        # Страховка от неадекватных значений с фронта
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        result = await db.get_chat_messages(chat_id, x_user_name, limit=limit, offset=offset)
        messages = result["messages"]
        logging.info(
            f"🦧Чат {chat_id}: отдано {len(messages)} (offset={offset}, "
            f"total={result['total_count']}, has_more={result['has_more']})"
        )
        return {
            "chat_id": chat_id,
            "total": len(messages),
            "total_count": result["total_count"],
            "has_more": result["has_more"],
            "limit": limit,
            "offset": offset,
            "messages": messages,
            "username": x_user_name
        }
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")

@app.get("/user/all/messages")
async def get_all_user_messages(limit: int = 50, offset: int = 0,  x_user_name: str = Header(..., alias="X-User-Name")):
    try:
        logging.info(f"Лимит по сообщениям для пользователя {limit}")
        messages = await db.get_all_user_messages(x_user_name, limit=limit, offset=offset)
        return {"messages": messages}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")


@app.get("/user/messages/search")
async def search_messages(q: str = "", limit: int = 30, x_user_name: str = Header(..., alias="X-User-Name")):
    """Живой поиск по сообщениям пользователя. Требует минимум 2 символа в q."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "query": query}
    try:
        limit = max(1, min(limit, 50))
        results = await db.search_user_messages(x_user_name, query, limit=limit)
        return {"results": results, "query": query}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте search_messages: {e}")
        raise HTTPException(status_code=500, detail="Не удалось выполнить поиск по сообщениям")


class ChatSidebarAction(BaseModel):
    chat_id: str
    user_id: int


class ChatRenameAction(BaseModel):
    chat_id: str
    user_id: int
    new_title: str


class ChatFavoriteAction(BaseModel):
    chat_id: str
    user_id: int
    is_favorite: bool


@app.get("/user/chats")
async def get_user_chats(x_user_name: str = Header(..., alias="X-User-Name")):
    try:
        chats = await db.get_user_chats_summary(x_user_name)
        sorted_chats = sorted(
            chats,
            key=lambda x: x["timestamp"],
            reverse=True
        )
        return {"chats": sorted_chats}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте get_user_chats: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить список чатов")


@app.post("/chat/delete")
async def delete_chat(action: ChatSidebarAction):
    try:
        await db.mark_chat_deleted(action.chat_id, action.user_id)
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка удаления чата {action.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось пометить чат на удаление")


@app.post("/chat/rename")
async def rename_chat(action: ChatRenameAction):
    try:
        new_title = (action.new_title or "").strip()
        if not new_title:
            raise HTTPException(status_code=400, detail="new_title required")
        await db.rename_chat(action.chat_id, action.user_id, new_title)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка переименования чата {action.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось переименовать чат")


@app.post("/chat/favorite")
async def favorite_chat(action: ChatFavoriteAction):
    try:
        await db.set_chat_favorite(action.chat_id, action.user_id, action.is_favorite)
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка избранного для чата {action.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось обновить избранное")


@app.post("/chat/new")
async def create_chat(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    type_agent = data.get("type_agent", "pm")

    # Для ouroboros — один чат на пользователя
    if type_agent == "ouroboros":
        existing = await db.find_chat_by_type(user_id, "ouroboros")
        if existing:
            return {"chat_id": existing}

    chat_id = await db.create_new_chat()

    # Сохраняем тип чата
    await db.set_chat_type(chat_id, user_id, type_agent)

    # (опционально) создаём первое сообщение
    await db.add_message(
        user_id=user_id,
        message="Чат создан",
        chat_id=chat_id,
        is_from_bot=True
    )

    return {"chat_id": chat_id}


@app.get("/chat/by-type/{type_agent}")
async def find_chat_by_type(type_agent: str, x_user_id: str = Header(..., alias="X-User-Id")):
    """Найти существующий чат пользователя по типу агента (ouroboros/pm)."""
    chat_id = await db.find_chat_by_type(int(x_user_id), type_agent)
    logging.info(f"Пытались найти чат, для {x_user_id}, {type_agent}, {chat_id}")
    if chat_id:
        return {"chat_id": chat_id, "exists": True}
    return {"chat_id": None, "exists": False}


@app.get("/internal/resolve_file")
async def resolve_file(chat_id: str, message_id: str, file_name: str):
    """Только для gateway. Отдаёт путь на диске agent_service для скачивания."""
    row = await db.pool.fetchrow("""
        SELECT file_path, source
        FROM chat_files
        WHERE chat_id = $1 AND message_id = $2 AND file_name = $3
        LIMIT 1
    """, chat_id, message_id, file_name)

    if not row or row["source"] != "agent" or not row["file_path"]:
        raise HTTPException(status_code=404, detail="File not found")

    return {"path": row["file_path"]}


@app.get("/internal/stats/overlay")
async def overlay_stats(gw: str = "main", top_n: int = 3):
    """Только для gateway. SQL-агрегаты для оверлея онлайна.
    Гейтвей сам подмешивает online/peak из Redis. gw пока не фильтрует
    выборку (топ — общий по системе), принимается для совместимости."""
    try:
        top_n = max(1, min(top_n, 10))
        stats = await db.get_overlay_stats(top_n=top_n)
        return stats
    except Exception as e:
        logging.error(f"Ошибка в overlay_stats: {e}")
        raise HTTPException(status_code=500, detail="overlay stats failed")


@app.get("/health")
async def health():
    status = "ok" if service_message_alive else "error"
    return {"service": "chats_service", "status": status}


# import uvicorn
# from utils.config import PORT_CHATS_SERVICE
#
# if __name__ == "__main__":
#     uvicorn.run("chats_service.app.app:app", host="127.0.0.1", port=int(PORT_CHATS_SERVICE), reload=True)


