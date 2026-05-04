# gateway/app.py
from fastapi import FastAPI, HTTPException, Request, Depends, Form, Cookie, Response
from fastapi.websockets import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import httpx
import asyncio
import logging
import json
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from jose import JWTError, jwt
from pydantic import BaseModel
from typing import Optional
import time

from config import (
    PORT_PM_AGENT, PORT_DB_CONSUMER,
    PORT_AUTH_SERVICE, PORT_CHATS_SERVICE,
    PORT_CRB_SERVICE, SECRET_KEY, ALGORITHM,
    PORT_GATEWAY_PORT, PORT_PARSERS_SERVICE
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up API Gateway...")

    app.state.http_client = httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(
            max_keepalive_connections=20, # количество idle соединений
            max_connections=100, # максимум одновременных соединений
            keepalive_expiry=60.0 # idle соединение закрывается
        ),
        follow_redirects=True
    )

    yield

    # Shutdown
    logger.info("Shutting down API Gateway...")
    await app.state.http_client.aclose()
# app = FastAPI(title="API Gateway", lifespan=lifespan)
app = FastAPI(title="API Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# Service Discovery
# SERVICES = {
#     "auth": {
#         "url": f"http://auth_service:{PORT_AUTH_SERVICE}",
#         "health_endpoint": "/health",
#     },
#     "chats": {
#         "url": f"http://chats_service:{PORT_CHATS_SERVICE}",
#         "health_endpoint": "/health",
#     },
#     "parsers": {
#              "url": f"http://parsers_service:{PORT_PARSERS_SERVICE}",
#              "health_endpoint": "/health",
#          },
#     "agent_service": {
#         "url": f"http://agent_service:{PORT_LLM_CONSUMER}",
#         "health_endpoint": "/health",
#     }
# }

SERVICES = {
    "auth": {
        "url": f"http://auth_service:{PORT_AUTH_SERVICE}",
        "health_endpoint": "/health",
    },
    "chats": {
        "url": f"http://chats_service:{PORT_CHATS_SERVICE}",
        "health_endpoint": "/health",
    },
    "parsers": {
             "url": f"http://parsers_service:{PORT_PARSERS_SERVICE}",
             "health_endpoint": "/health",
         },
    "agent_service": {
        "url": f"http://agent_service:{PORT_PM_AGENT}",
        "health_endpoint": "/health",
    }
}

BASE_DIR = Path(__file__).resolve().parent
# Статика и шаблоны
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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

class BotMessage(BaseModel):
    chat_id: int
    message: dict

class BotStatus(BaseModel):
    request_id: str
    status: str

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Хранилище очередей — username → Queue
sse_queues: dict[str, asyncio.Queue] = {}

async def push_sse(username: str, data: dict):
    if username not in sse_queues:
        sse_queues[username] = asyncio.Queue()
    await sse_queues[username].put(data)

@app.get("/some-route")
async def some_route(request: Request):
    authentik_user = request.headers.get("X-Authentik-Username")
    logger.info(authentik_user)

# Другой коннектор для отправки релевантных статей на фронт
user_connections: dict[str, WebSocket] = {}

@app.websocket("/ws/user/{username}")
async def websocket_user(websocket: WebSocket, username: str):
    await websocket.accept()
    user_connections[username] = websocket
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        user_connections.pop(username, None)

connections: dict[int, list[WebSocket]] = {}
@app.websocket("/ws/chat/{chat_id}")
async def websocket_chat(websocket: WebSocket, chat_id: int):
    await websocket.accept()

    if chat_id not in connections:
        connections[chat_id] = []

    connections[chat_id].append(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        connections[chat_id].remove(websocket)

# Функция для отправки события в WebSocket
async def broadcast(chat_id: int, message: dict):
    for ws in connections.get(chat_id, []):
        logging.info(f"Подключение {ws}, сообщение {message}")
        await ws.send_json(message)

@app.post("/internal/bot_status")
async def bot_status(payload: BotStatus):
    chat_id = pending_requests.get(payload.request_id)

    if not chat_id:
        logger.warning(f"Неизвестный request_id: {payload.request_id}")
        raise HTTPException(status_code=404, detail="request_id not found")

    logger.info(f"Статус агента для chat_id={chat_id}: {payload.status}")

    await broadcast(chat_id, {
        "type": "agent_status",  # прописали проверку на фронте
        "status": payload.status
    })

    return {"ok": True}

@app.post("/internal/bot_message")
async def receive_bot_message(bot_msg: BotMessage):
    """Получаем сообщения от бота через DB Consumer и отправляем через WebSocket"""
    logging.info(f"Получено сообщение от бота для chat_id={bot_msg.chat_id}: {bot_msg.message}")
    start = time.perf_counter()

    # Отправляем сообщение через WebSocket всем клиентам в чате
    await broadcast(bot_msg.chat_id, bot_msg.message)
    elapsed = time.perf_counter() - start
    logging.info(f"Message Service. Время на отправку в сокет и сообщение от бота = {elapsed:.4f}s")


# Service Discovery функция
async def discover_services():
    """Проверяет доступность сервисов"""
    for service_name, service_info in SERVICES.items():
        try:
            health_url = f"{service_info['url']}{service_info['health_endpoint']}"
            response = await app.state.http_client.get(health_url)
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

# Главная страница (SPA)
# ========== HTML СТРАНИЦЫ ==========
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    """Главная страница - проверяем авторизацию и показываем соответствующую страницу"""
    user = await get_current_user(request)

    if not user:
        # Не авторизован - показываем приветственную страницу
        return templates.TemplateResponse("welcome.html", {
            "request": request,
            "user": None
        })

    # Авторизован - показываем SPA чата
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user
    })

@app.get("/login-page", response_class=HTMLResponse)
async def login_page(request: Request):
    """Страница логина"""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "message": None
    })

@app.get("/register-page", response_class=HTMLResponse)
async def register_page(request: Request):
    """Страница регистрации"""
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": None
    })


@app.get("/without-register-page", response_class=RedirectResponse)
async def without_register_page(request: Request):
    """Сразу перенаправляем на гостевой вход, форма не нужна"""
    return RedirectResponse(url="/without-register-web", status_code=303)

# ========== ОБРАБОТКА ФОРМ ==========

@app.post("/without-register-web")
async def without_register_web(request: Request):
    """Вход без регистрации — создаём гостевой аккаунт"""
    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/guest-login"
        )
        if response.status_code == 200:
            data = response.json()
            token = data.get("access_token")

            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(
                key="users_access_token",
                value=token,
                httponly=True,
                max_age=3600,
                secure=False
            )
            return resp
        else:
            error_detail = response.json().get("detail", "Ошибка гостевого входа")
            return templates.TemplateResponse(
                "welcome.html",
                {"request": request, "error": error_detail},
                status_code=400
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service unreachable: {str(e)}")


# ========== ОБРАБОТКА ФОРМ ==========
@app.post("/login-web")
async def login_web(
        request: Request,
        username: str = Form(...),
        password: str = Form(...)
):
    auth_url = SERVICES['auth']['url']
    """Обработка HTML формы логина"""
    try:
        start = time.perf_counter()
        response = await app.state.http_client.post(
            f"{auth_url}/login",
            data={"username": username, "password": password},
            timeout=30.0
        )
        elapsed = time.perf_counter() - start
        logging.info(f"AUTH latency = {elapsed:.4f}s")
        if response.status_code == 200:
            login_data = response.json()
            token = login_data.get("access_token")

            # Редирект на главную страницу
            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(
                key="users_access_token",
                value=token,
                httponly=True,
                max_age=3600,
                secure=False
            )
            return resp
        else:
            # Показываем ошибку
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Неверный логин или пароль",
                    "message": None
                },
                status_code=401
            )

    except httpx.RequestError:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Сервис авторизации недоступен",
                "message": None
            },
            status_code=502
        )


@app.post("/register-web")
async def register_web(
        request: Request,
        username: str = Form(...),
        password: str = Form(...)
):
    auth_url = SERVICES['auth']['url']

    # try:
    response = await app.state.http_client.post(
        f"{auth_url}/register-and-login",
        data={"username": username, "password": password}
    )

    if response.status_code == 200:
        data = response.json()
        token = data.get("access_token")

        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie(
            key="users_access_token",
            value=token,
            httponly=True,
            max_age=3600,
            secure=False
        )
        return resp
    else:
        error_detail = response.json().get("detail", "Ошибка регистрации")
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": error_detail},
            status_code=400
        )

    # except Exception as e:
    #     logger.error(f"Ошибка: {e}")
    #     return templates.TemplateResponse(
    #         "register.html",
    #         {"request": request, "error": "Сервис недоступен"},
    #         status_code=500
    #     )

# @app.post("/register-web")
# async def register_web(
#         request: Request,
#         username: str = Form(...),
#         password: str = Form(...)
# ):
#     auth_url = SERVICES['auth']['url']
#     async with httpx.AsyncClient(timeout=30.0) as client:
#         try:
#             # Регистрация
#             response = await client.post(
#                 f"{auth_url}/register",
#                 data={"username": username, "password": password},
#                 timeout=30.0
#             )
#             if response.status_code == 200:
#                 # После регистрации сразу логиним
#                 login_response = await client.post(
#                     f"{auth_url}/login",
#                     data={"username": username, "password": password},
#                     timeout=30.0
#                 )
#                 if login_response.status_code == 200:
#                     login_data = login_response.json()
#                     token = login_data.get("access_token")
#                     resp = RedirectResponse(url="/", status_code=303)
#                     resp.set_cookie(
#                         key="users_access_token",
#                         value=token,
#                         httponly=True,
#                         max_age=3600,
#                         secure=False
#                     )
#                     return resp
#
#             # Ошибка регистрации
#             try:
#                 error_data = response.json()
#                 error_msg = error_data.get("detail", "Ошибка регистрации")
#             except:
#                 error_msg = response.text or "Ошибка регистрации"
#
#             logger.error(f"Ошибка регистрации: {error_msg}")
#
#             return templates.TemplateResponse(
#                 "register.html",
#                 {
#                     "request": request,
#                     "error": error_msg
#                 },
#                 status_code=400
#             )
#
#         except Exception as e:
#             logger.error(f"Неожиданная ошибка: {type(e).__name__}: {e}")
#             import traceback
#             logger.error(f"Traceback: {traceback.format_exc()}")
#             return templates.TemplateResponse(
#                 "register.html",
#                 {
#                     "request": request,
#                     "error": f"Внутренняя ошибка сервера: {type(e).__name__}"
#                 },
#                 status_code=500
#             )


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

    try:
        # Нужно добавить endpoint /me в auth сервис
        response = await app.state.http_client.get(
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

pending_requests: dict[str, int] = {}

@app.post("/api/favorites")
async def favorites(request: Request):
    """Обработка звездочек избранного"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    logging.info(f"ИЗБРАННОЕ {data}")
    parser_url = SERVICES['parsers']['url']
    try:
        response = await app.state.http_client.post(
            f"{parser_url}/favorites",
            json=data,
            timeout=30.0
        )
        if response.status_code == 200:
            return Response(
                content=response.content,
                status_code=200,
                media_type="application/json"
            )
        else:
            raise HTTPException(status_code=response.status_code, detail="Parser error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Parsers service unreachable: {str(e)}")

@app.post("/api/favorites/select")
async def favorites_select(request: Request):
    """Обработка звездочек избранного"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    username = {'username': user['username']}
    parser_url = SERVICES['parsers']['url']
    try:
        response = await app.state.http_client.post(
            f"{parser_url}/favorites/select",
            json=username,
            timeout=30.0
        )
        if response.status_code == 200:
            return Response(
                content=response.content,
                status_code=200,
                media_type="application/json"
            )
        else:
            raise HTTPException(status_code=response.status_code, detail="Parser error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Parsers service unreachable: {str(e)}")

async def notify_user_error(username: str, error_type: str = "error_parser"):
    ws = user_connections.get(username)
    if ws:
        await ws.send_json({"type": error_type})
    else:
        logging.warning(f"Нет WS соединения для {username}")

async def notify_user_message(username: str, type_message: str, message: str):
    ws = user_connections.get(username)
    if ws:
        await ws.send_json({"type": type_message, "message": message})
    else:
        logging.warning(f"Нет WS соединения для {username}")

@app.post("/api/search")
async def searching(request: Request):
    """Направить в агента"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    post_body = {
        "user_name": data['username'],
        "query": data['query']
    }
    agent_url = SERVICES['agent_service']['url']
    # Отправляем дефолтные словосочетания с фронта
    try:
        response = await app.state.http_client.post(
            f"{agent_url}/api/search",
            json=post_body,
            timeout=30.0
        )
        """
        С парсера
        {   
            'type': 'parser'
            'request_id': request_id,
            'user_name': user_name,
            'user_query': user_query,
            'count': len(saved_articles),
            'articles': saved_articles,
        }
        С агента, если запрос не релевантный
        {
            'type': 'agent'
            'message': text
        }
        
        """
        logging.info(response.content)
        if response.status_code == 200:
            result = response.json()

            if result.get("type") == "agent":
                await notify_user_message(data['username'], type_message=result.get("type"), message=result['message'])
            # Агент вернул ошибку парсера внутри 200
            if result.get("type") == "arxiv_error":
                await notify_user_message(data['username'], type_message=result.get("type"), message=result['message'])
            if result.get("type") == "error_parser":
                await notify_user_error(data['username'])
                raise HTTPException(status_code=502, detail="Parser internal error")

            return Response(
                content=response.content,
                status_code=200,
                media_type="application/json"
            )
        else:
            # Агент вернул не 200 — шлём WS и пробрасываем ошибку
            await notify_user_error(data['username'])
            raise HTTPException(status_code=response.status_code, detail="Agent error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {str(e)}")


@app.post("/api/translate")
async def translate(request: Request):
    """Направить в агента"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    post_body = {
        "username": user['username'],
        "article_id": data['article_id']
    }
    logging.info(f"Это перевод для {post_body}")
    agent_url = SERVICES['agent_service']['url']
    # Отправляем дефолтные словосочетания с фронта
    try:
        response = await app.state.http_client.post(
            f"{agent_url}/api/translate",
            json=post_body,
            timeout=30.0
        )
        logging.info(response.content)
        if response.status_code == 200:
            return Response(
                content=response.content,
                status_code=200,
                media_type="application/json"
            )
        else:
            # Агент вернул не 200 — шлём WS и пробрасываем ошибку
            await notify_user_message(user['username'], type_message='translate', message="Не удалось перевести аннотацию")
            raise HTTPException(status_code=response.status_code, detail="Agent error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {str(e)}")


@app.post("/internal/sse-push")
async def internal_sse_push(request: Request):
    """Агент сообщает что эмбеддинги готовы"""
    data = await request.json()
    username = data["username"]
    logging.info(f"ОТВЕТ АГЕНТА ПО ЭМБЕДДИНГАМ: {data}")
    ws = user_connections.get(username)
    logging.info(user_connections)
    if ws:
        await ws.send_json(data["data"])
    else:
        logging.warning(f"Нет WS соединения для {username}")

    return {"ok": True}


@app.post("/api/search/default")
async def searching_default(request: Request):
    """Направить сразу в парсер"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()

    post_body = {
        "user_name": data['username'],
        "query": "default",
        "params": {
            "keywords": [data['key_word']]
        },
        "request_id": None
    }

    logging.info(f"ДЕФОЛТ ДАННЫЕ с вкладки ПОИСКА {data}")
    parser_url = SERVICES['parsers']['url']
    # Отправляем дефолтные словосочетания с фронта
    try:
        response = await app.state.http_client.post(
            f"{parser_url}/search/articles",
            json=post_body,
            timeout=30.0
        )
        if response.status_code == 200:
            # logging.info(f"Смотрим ЧТО прилетело с парсера: {response.text}")
            return Response(
                content=response.content,
                status_code=200,
                media_type="application/json"
            )
        else:
            raise HTTPException(status_code=response.status_code, detail="Parser error")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Parsers service unreachable: {str(e)}")

# ========== ПРОКСИ ДЛЯ ЧАТОВ ==========
@app.post("/api/chats/send_message")
async def send_message(request: Request):
    """Отправка сообщения (для SPA)"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Получаем данные из запроса
    data = await request.json()

    request_id = str(uuid.uuid4()) # Генерация id для передачи его агенту с последующей реализацией ОС о его действиях
    # Запоминаем связь ДО отправки агенту
    pending_requests[request_id] = data["chat_id"]

    data["user_id"] = user["user_id"]
    data["type_agent"] = "pm" # О Великий gateway
    data["request_id"] = request_id
    logger.info(f"Что летит при отправке сообщений {data['chat_id']}, {data['message']}")

    message = {
        "user_id": data["user_id"],
        "chat_id": data["chat_id"],
        "message": data["message"],
        "is_from_bot": False
    }

    await broadcast(data["chat_id"], message) # Отравим сразу по вебсокету
    try:
        start = time.perf_counter()
        response = await app.state.http_client.post(
            f"{SERVICES['chats']['url']}/send_message",
            json=data
        )
        elapsed = time.perf_counter() - start
        logging.info(f"Время на отправку сообщения пользователя в микросервис = {elapsed:.4f}s")
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

    try:
        start = time.perf_counter()
        response = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/user/all/messages",
            cookies=request.cookies,
            params={"limit": 500}
        )
        elapsed = time.perf_counter() - start
        logging.info(f"Время подгрузки чатов = {elapsed:.4f}s")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Chats service error")

        data = response.json()
        messages = data["messages"]

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

    try:
        response = await app.state.http_client.get(
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
    try:
        response = await app.state.http_client.post(
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


# if __name__ == "__main__":
#     import uvicorn
#
#     uvicorn.run(
#         "api_gateway.app:app",
#         host="127.0.0.1",
#         port=int(PORT_GATEWAY_PORT),
#         reload=True
#     )
