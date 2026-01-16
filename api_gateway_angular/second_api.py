# gateway/app.py
from fastapi import FastAPI, HTTPException, Request, Depends, Form, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
import logging
import json
from datetime import datetime, timedelta
from jose import JWTError, jwt
from pydantic import BaseModel
from typing import Optional

from utils.config import (
    PORT_LLM_CONSUMER, PORT_DB_CONSUMER,
    PORT_AUTH_SERVICE, PORT_CHATS_SERVICE,
    PORT_CRB_SERVICE, SECRET_KEY, ALGORITHM,
    PORT_GATEWAY_PORT
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="API Gateway")

# Service Discovery
SERVICES = {
    "auth": {
        "url": f"http://localhost:{PORT_AUTH_SERVICE}",
        "health_endpoint": "/health",
        "status": "unknown"
    },
    "chats": {
        "url": f"http://localhost:{PORT_CHATS_SERVICE}",
        "health_endpoint": "/health",
        "status": "unknown"
    },
    "llm_bot": {
        "url": f"http://localhost:{PORT_LLM_CONSUMER}",
        "health_endpoint": "/health",
        "status": "unknown"
    },
    "crb": {
        "url": f"http://localhost:{PORT_CRB_SERVICE}",
        "health_endpoint": "/health",
        "status": "unknown"
    }
}

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4000"],  # фронт-сервис
    allow_credentials=True,
    allow_methods=["*"],  # OPTIONS, GET, POST, PUT, DELETE
    allow_headers=["*"],  # Content-Type и т.д.
)

# Статика и шаблоны
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory=r"C:\Users\ivans\PycharmProjects\Micro_Services\api_gateway\tempates")


# Модели данных
class ChatMessage(BaseModel):
    user_id: Optional[int] = None
    message: str
    chat_id: int


class UserRegister(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


# Service Discovery функция
async def discover_services():
    """Проверяет доступность сервисов"""
    for service_name, service_info in SERVICES.items():
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                health_url = f"{service_info['url']}{service_info['health_endpoint']}"
                response = await client.get(health_url)
                if response.status_code == 200:
                    SERVICES[service_name]["status"] = "healthy"
                else:
                    SERVICES[service_name]["status"] = "unhealthy"
        except Exception as e:
            SERVICES[service_name]["status"] = "down"
            logger.warning(f"Service {service_name} is down: {e}")


# Аутентификация через Gateway
async def get_current_user(request: Request):
    """Получение текущего пользователя из cookie"""
    token = request.cookies.get("users_access_token")

    if not token:
        logger.info("Токен не найден в cookies")
        return None

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        user_id = payload.get("user_id")

        if not username:
            logger.warning("Токен не содержит username (sub)")
            return None
        logger.info(f"Декодирован токен для пользователя: {username}, user_id: {user_id}")
        return {
            "username": username,
            "user_id": user_id,
            "authenticated": True
        }
    except JWTError as e:
        logger.warning(f"Ошибка декодирования токена: {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка в get_current_user: {e}")
        return None



# ========== ОБРАБОТКА ФОРМ ==========
@app.post("/api/auth/login")
async def login_api(request: Request):
    data = await request.json()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{SERVICES['auth']['url']}/login",
            data=data
        )

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = response.json()["access_token"]

    resp = Response(content=json.dumps({"status": "ok"}), media_type="application/json")
    resp.set_cookie(
        key="users_access_token",
        value=token,
        httponly=True,
        max_age=3600
    )
    return resp

@app.post("/api/auth/register")
async def register_api(request: Request):
    data = await request.json()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{SERVICES['auth']['url']}/register",
            data=data
        )

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type="application/json"
    )

# Health check с discovery
@app.get("/health")
async def gateway_health():
    await discover_services()

    return {
        "gateway": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            name: info["status"]
            for name, info in SERVICES.items()
        }
    }


# Выход из системы
@app.post("/logout-web", response_class=RedirectResponse)
async def logout_web(request: Request):
    """Выход из системы (обработка HTML формы)"""
    response = RedirectResponse(
        url="/?message=exit",
        status_code=303
    )

    # Удаляем cookie с токеном
    response.delete_cookie(
        key="users_access_token",
        path="/"
    )

    return response

# Дополнительный endpoint для проверки текущего пользователя
@app.get("/api/auth/me")
async def get_current_user_info(request: Request):
    """Получение информации о текущем пользователе через Gateway"""
    # Просто проксируем запрос к auth сервису
    token = request.cookies.get("users_access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with httpx.AsyncClient() as client:
        try:
            # Нужно добавить endpoint /me в auth сервис
            response = await client.get(
                f"{SERVICES['auth']['url']}/me",
                cookies={"users_access_token": token}
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Auth service unreachable: {str(e)}")


# ========== ПРОКСИ ДЛЯ ЧАТОВ ==========
@app.post("/api/chats/send_message")
async def send_message(request: Request):
    """Отправка сообщения (для SPA)"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Получаем данные из запроса
    data = await request.json()
    data["user_id"] = user["user_id"]
    logger.info(f"Что летит при отправке сообщений {data}")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{SERVICES['chats']['url']}/send_message",
                json=data
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")


@app.get("/api/chats")
async def get_chats(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{SERVICES['chats']['url']}/user/all/messages",
                cookies=request.cookies,
                params={"limit": 500}
            )

            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="Chats service error")

            data = response.json()
            messages = data["messages"]
            logger.info(messages)

            chats = {}
            for msg in messages:
                chat_id = msg["chat_id"]
                if chat_id not in chats:
                    chats[chat_id] = {
                        "id": chat_id,
                        "last_message": msg["message"],
                        "timestamp": msg["timestamp"]
                    }
                else:
                    # сообщения уже отсортированы ASC
                    chats[chat_id]["last_message"] = msg["message"]
                    chats[chat_id]["timestamp"] = msg["timestamp"]

            return list(chats.values())

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Chats service unreachable: {str(e)}"
            )

@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: int, request: Request):
    """Получение сообщений чата (для SPA)"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{SERVICES['chats']['url']}/chat/{chat_id}/messages",
                cookies=request.cookies,
                params=dict(request.query_params)
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")

# ========= Создание нового чата ========
@app.post("/api/chats/new")
async def create_new_chat(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    logger.info(f'{user["user_id"]} текущий пользователь')
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{SERVICES['chats']['url']}/chat/new",
                json={"user_id": user["user_id"]}
            )

            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type="application/json"
            )

        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Chats service unreachable: {str(e)}"
            )

# # WebSocket проксирование (важно!)
# from fastapi import WebSocket, WebSocketDisconnect
# import httpx
# import asyncio
#
# async def get_current_user_ws(websocket: WebSocket):
#     token = websocket.cookies.get("users_access_token")
#     if not token:
#         return None
#
#     try:
#         payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
#         return payload.get("sub")
#     except JWTError:
#         return None

# ========== SERVICE DISCOVERY ==========
@app.on_event("startup")
async def startup_event():
    """Запуск Service Discovery при старте"""
    logger.info("Starting API Gateway...")
    await discover_services()
    logger.info(f"Services status: { {name: info['status'] for name, info in SERVICES.items()} }")


# ========== СТАТИЧЕСКИЕ ФАЙЛЫ ==========
@app.get("/status")
async def status_page(request: Request):
    """Страница статуса сервисов"""
    await discover_services()
    return templates.TemplateResponse("status.html", {
        "request": request,
        "services": SERVICES,
        "timestamp": datetime.now()
    })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_gateway_angular.second_api:app",
        host="127.0.0.1",
        port=int(PORT_GATEWAY_PORT),
        reload=True
    )