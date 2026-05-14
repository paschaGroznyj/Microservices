import json
import logging
from fastapi import FastAPI
from kafka import KafkaProducer
from pydantic import BaseModel
from app.db.chats_tables import DataBaseChats
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from app.core.config import SECRET_KEY, ALGORITHM, KAFKA_BROKER
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
    user_id: int
    message: str
    chat_id: int
    type_agent: str
    request_id: str

@app.on_event("startup")
async def startup():
    global service_message_alive
    service_message_alive = True
    await db.init_db_pool()

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
        "chat_id": chat.chat_id,
        "message": chat.message,
        "type_agent": chat.type_agent,
        "is_from_bot": False,
        "request_id": chat.request_id
    }
    logging.info(message)
    start = time.perf_counter()
    # отправка в Kafka
    producer.send("chat-messages", key=chat.user_id, value=message)
    producer.flush()
    elapsed = time.perf_counter() - start
    logging.info(f"Message Service. Время на отправку в сокет и на перекладку в кафку = {elapsed:.4f}s")
    return {"status": "ok"}



@app.get("/chat/{chat_id}/messages")
async def get_chat_messages(chat_id: int, limit: int = 1000, offset: int = 0,  username: str = Depends(get_current_user_from_cookie)):
    try:
        messages = await db.get_chat_messages(chat_id, username, limit=limit, offset=offset)
        return {
            "chat_id": chat_id,
            "total": len(messages),
            "messages": messages,
            "username": username
        }
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")

@app.get("/user/all/messages")
async def get_all_user_messages(limit: int = 50, offset: int = 0,  username: str = Depends(get_current_user_from_cookie)):
    try:
        logging.info(f"Лимит по сообщениям для пользователя {limit}")
        messages = await db.get_all_user_messages(username, limit=limit, offset=offset)
        return {"messages": messages}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")


@app.post("/chat/new")
async def create_chat(data: dict):
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    chat_id = await db.create_new_chat()

    # (опционально) создаём первое сообщение
    await db.add_message(
        username=user_id,
        message="Чат создан",
        chat_id=chat_id,
        is_from_bot=True
    )

    return {"chat_id": chat_id}



@app.get("/health")
async def health():
    status = "ok" if service_message_alive else "error"
    return {"service": "chats_service", "status": status}


# import uvicorn
# from utils.config import PORT_CHATS_SERVICE
#
# if __name__ == "__main__":
#     uvicorn.run("chats_service.app.app:app", host="127.0.0.1", port=int(PORT_CHATS_SERVICE), reload=True)


