# app.py (основной сервис)
import json
import logging
from fastapi import FastAPI, HTTPException, Depends
from utils.config import KAFKA_BROKER
from kafka import KafkaProducer
from pydantic import BaseModel
from db_scripts.chats_tables import DataBaseChats
from fastapi.websockets import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from utils.config import SECRET_KEY, ALGORITHM
from datetime import datetime, timedelta, timezone

db = DataBaseChats()
producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    api_version=(4, 1, 0),
    key_serializer=lambda k: str(k).encode('utf-8'),
    value_serializer=lambda m: json.dumps(m).encode('utf-8'))

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Chats Service")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

connections: dict[int, list[WebSocket]] = {}




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

# WebSocket endpoint
@app.websocket("/ws/chat/{chat_id}")
async def websocket_chat(websocket: WebSocket, chat_id: int):
    await websocket.accept()

    if chat_id not in connections:
        connections[chat_id] = []

    connections[chat_id].append(websocket)

    try:
        while True:
            # Принимаем сообщения от клиента
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                # Отправляем в Kafka
                producer.send("chat-messages", key=str(message.get("user_id")), value=message)
                producer.flush()

                # Немедленная отправка отправителю
                await websocket.send_json({
                    **message,
                    "timestamp": datetime.now().isoformat()
                })
            except json.JSONDecodeError:
                logging.error(f"Invalid JSON: {data}")
    except WebSocketDisconnect:
        if chat_id in connections and websocket in connections[chat_id]:
            connections[chat_id].remove(websocket)


# Функция для отправки в WebSocket (используется при получении из Kafka)
async def broadcast(chat_id: int, message: dict):
    if chat_id in connections:
        for ws in connections[chat_id]:
            try:
                await ws.send_json(message)
            except:
                pass

class BotMessage(BaseModel):
    chat_id: int
    message: dict

# ВНУТРЕННИЙ endpoint для получения сообщений от бота из DB Consumer
@app.post("/internal/bot_message")
async def receive_bot_message(bot_msg: BotMessage):
    """Получаем сообщения от бота через DB Consumer и отправляем через WebSocket"""
    logging.info(f"Получено сообщение от бота для chat_id={bot_msg.chat_id}: {bot_msg.message}")

    # Отправляем сообщение через WebSocket всем клиентам в чате
    await broadcast(bot_msg.chat_id, bot_msg.message)

    return {"status": "ok", "detail": "Bot message broadcasted"}

# Модель для REST API

class ChatMessage(BaseModel):
    user_id: int
    message: str
    chat_id: int

# REST endpoint для отправки сообщений
@app.post("/send_message")
async def send_message(chat: ChatMessage):
    message = {
        "user_id": chat.user_id,
        "chat_id": chat.chat_id,
        "message": chat.message,
        "is_from_bot": False,
        "timestamp": datetime.now().isoformat()
    }

    # Отправляем в Kafka
    producer.send("chat-messages", key=str(chat.user_id), value=message)
    producer.flush()

    # Немедленная отправка через WebSocket
    await broadcast(chat.chat_id, message)

    return {"status": "ok"}

@app.on_event("startup")
async def startup():
    await db.init_db_pool()

@app.on_event("shutdown")
async def shutdown():
    await db.pool.close()

@app.get("/chat/{chat_id}/messages")
async def get_chat_messages(chat_id: int, limit: int = 50, offset: int = 0,  username: str = Depends(get_current_user_from_cookie)):
    try:
        messages = await db.get_chat_messages(chat_id, limit=limit, offset=offset)
        return {
            "chat_id": chat_id,
            "total": len(messages),
            "messages": messages,
            "username": username
        }
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")





import uvicorn
from utils.config import PORT_CHATS_SERVICE

if __name__ == "__main__":
    uvicorn.run("chats_service.endpoint_producer:app", host="127.0.0.1", port=int(PORT_CHATS_SERVICE), reload=True)