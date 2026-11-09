# gateway/app.py
from fastapi import FastAPI, HTTPException, Request, Depends, Form, Cookie, Response, File, UploadFile
from fastapi.websockets import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import httpx
import asyncio
import logging
import json
import uuid
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timedelta
from jose import JWTError, jwt
from pydantic import BaseModel
from typing import Optional
import time

from config import (
    PORT_PM_AGENT, PORT_DB_CONSUMER,
    PORT_AUTH_SERVICE, PORT_CHATS_SERVICE,
    PORT_CRB_SERVICE, SECRET_KEY, ALGORITHM,
    PORT_GATEWAY_PORT, PORT_PARSERS_SERVICE,
    ADMIN_USERNAMES
)
import presence
import run_status
import cache_bust
import group_chat_gw
import reactions_bus
import reads_bus
import editor_gw
import os
import max_chats_gw
import file_parsers

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

    # Общий чат: прокидываем зависимости в роутер
    group_chat_gw.bind(
        http_client=app.state.http_client,
        chats_url=SERVICES['chats']['url'],
        get_current_user=get_current_user,
        admin_usernames=ADMIN_USERNAMES,
    )

    # Реакции общего чата: Redis-шина с дебаунсом 1 с
    reactions_bus.bind(app.state.http_client, SERVICES['chats']['url'])
    reactions_bus.start()
    reads_bus.bind(app.state.http_client, SERVICES['chats']['url'])
    reads_bus.start()

    editor_gw.bind(
        http_client=app.state.http_client,
        chats_url=SERVICES['chats']['url'],
        sandbox_url=SERVICES['sandbox']['url'],
        get_current_user=get_current_user,
        templates=templates,
    )

    # MAX-чаты: прокси-роутер /api/max/*
    max_chats_gw.bind(
        http_client=app.state.http_client,
        chats_url=SERVICES['chats']['url'],
        get_current_user=get_current_user,
    )

    yield

    # Shutdown
    logger.info("Shutting down API Gateway...")
    await reactions_bus.stop()
    await reads_bus.stop()
    await app.state.http_client.aclose()
app = FastAPI(title="API Gateway", lifespan=lifespan, docs_url=None)
app.include_router(group_chat_gw.router)

app.include_router(editor_gw.router)
app.include_router(max_chats_gw.router)
# app = FastAPI(title="API Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


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
    },
    "sandbox": {
        "url": os.getenv("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005"),
        "health_endpoint": "/health",
    }
}

BASE_DIR = Path(__file__).resolve().parent
# Статика и шаблоны
app.middleware("http")(cache_bust.cache_bust_middleware)
# Monaco из образа — до /static (см. cache_bust.mount_vendor_monaco).
cache_bust.mount_vendor_monaco(app)
app.mount("/static", cache_bust.CacheBustStaticFiles(directory=BASE_DIR / "static"), name="static")
templates = cache_bust.CacheBustTemplates(directory=BASE_DIR / "templates")


# Модели данных
class ChatMessage(BaseModel):
    user_id: Optional[int] = None
    message: str
    chat_id: str


class UserRegister(BaseModel):
    username: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str

class BotMessage(BaseModel):
    chat_id: str
    message: dict

class BotStatus(BaseModel):
    request_id: str
    status: str

class BotStream(BaseModel):
    request_id: str
    chunk: str
    id: str | None = None
    # Тип чанка: "live" — живой текст (печатается в текущем блоке),
    # "reasoning_end" — маркер сворачивания живого блока в reasoning,
    # "final" / "final_commit" — фиксация финального ответа.
    kind: str = "final"

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


# Другой коннектор для отправки релевантных статей на фронт
user_connections: dict[str, WebSocket] = {}

@app.websocket("/ws/user/{username}")
async def websocket_user(websocket: WebSocket, username: str):
    await websocket.accept()
    user_connections[username] = websocket
    await presence.touch("main", username)
    try:
        while True:
            await websocket.receive_text()
            await presence.touch("main", username)
    except WebSocketDisconnect:
        user_connections.pop(username, None)

connections: dict[int, list[WebSocket]] = {}
@app.websocket("/ws/chat/{chat_id}")
async def websocket_chat(websocket: WebSocket, chat_id: str):
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
async def broadcast(chat_id: str, message: dict):
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

@app.post("/internal/bot_stream")
async def bot_stream(payload: BotStream):
    """Прямой канал стриминга финального ответа модели (минуя Kafka).
    Резолвит chat_id по request_id и шлёт чанк на фронт по ws."""
    chat_id = pending_requests.get(payload.request_id)

    if not chat_id:
        logger.warning(f"[stream] Неизвестный request_id: {payload.request_id}")
        raise HTTPException(status_code=404, detail="request_id not found")

    active_requests = await run_status.append_reasoning_chunk(
        chat_id, payload.chunk, payload.kind, payload.id
    )

    await broadcast(chat_id, {
        "is_from_bot": True,
        "chunk": payload.chunk,
        "id": payload.id,
        "kind": payload.kind,
        "active_requests": active_requests,
    })

    return {"ok": True}

@app.post("/internal/bot_message")
async def receive_bot_message(bot_msg: BotMessage):
    """Получаем сообщения от бота через DB Consumer и отправляем через WebSocket"""
    logging.info(f"Получено сообщение от бота для chat_id={bot_msg.chat_id}: {bot_msg.message}")
    start = time.perf_counter()

    # Отправляем сообщение через WebSocket всем клиентам в чате
    await broadcast(bot_msg.chat_id, bot_msg.message)

    # Финал пришёл — снимаем Redis-флаг «агент в работе».
    await run_status.clear_running(bot_msg.chat_id)

    # Автогенерированный заголовок чата: отдельное событие title_update,
    # чтобы фронт обновил строку чата в сайдбаре без перезапроса списка.
    try:
        msg = bot_msg.message if isinstance(bot_msg.message, dict) else {}
        auto_title = msg.get("auto_title")
        if auto_title:
            await broadcast(bot_msg.chat_id, {
                "type": "title_update",
                "chat_id": bot_msg.chat_id,
                "auto_title": auto_title,
            })
    except Exception as e:
        logging.warning(f"title_update broadcast не удался: {e}")

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
    authentik_user = request.headers.get("X-Authentik-Username")
    if authentik_user:
        # Автоматически создаём/находим юзера в БД
        logging.info(authentik_user)
        # user = await db.get_or_create_user(authentik_user)
        auth_url = SERVICES['auth']['url']
        try:
            response = await app.state.http_client.post(
                f"{auth_url}/authentik",
                json={"username": authentik_user}
            )
            if response.status_code == 200:
                data = response.json()
                user_id = data.get("user_id")
                return {
                    "username": authentik_user,
                    "user_id": user_id,
                    "authenticated": True,
                    "source": "authentik"}
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Auth service unreachable: {str(e)}")
        # return user

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
            "authenticated": True,
            "source": "internal"
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
    if request.headers.get("X-Authentik-Username") or await get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "message": None
    })

@app.get("/register-page", response_class=HTMLResponse)
async def register_page(request: Request):
    """Страница регистрации"""
    if request.headers.get("X-Authentik-Username") or await get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
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


# ────────────────────────────────────────────────────────
# ONLINE PRESENCE (Вариант А: гейтвей не ходит в БД).
# Счётчик — суммарно main + sigma из Redis. Оверлей — Redis-часть
# склеивается с SQL-агрегатами от chats_service.
# ────────────────────────────────────────────────────────
@app.post("/api/presence/ping")
async def presence_ping(request: Request):
    """Heartbeat main-присутствия. Фронт дёргает его по таймеру (каждые 15с)."""
    user = await get_current_user(request)
    if user:
        await presence.touch("main", user["username"])
    return Response(status_code=204)

@app.post("/api/presence/leave")
async def presence_leave(request: Request):
    """Немедленный выход из онлайна при закрытии вкладки."""
    user = await get_current_user(request)
    if user:
        await presence.leave("main", user["username"])
    return Response(status_code=204)

@app.get("/api/presence/count")
async def presence_count(request: Request, ping: int = 0):
    """Лёгкий публичный счётчик онлайн (суммарно main+sigma) + живые запросы к агенту. Redis-only."""
    if ping:
        user = await get_current_user(request)
        if user:
            await presence.touch("main", user["username"])
    m = await presence.count_online("main")
    sg = await presence.count_online("sigma")
    active_req = await run_status.count_active_requests()
    return {"online": m + sg, "main": m, "sigma": sg, "active_requests": active_req,
        "build_id": cache_bust.get_build_id(), "build_id": cache_bust.get_build_id()}


@app.get("/api/presence/overlay")
async def presence_overlay(request: Request):
    """Подробный оверлей по клику. Гибрид: Redis (online/peak/имена) +
    SQL-агрегаты из chats_service (топ-3 по сообщениям). Redis-данные не покидают гейтвей."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    m = await presence.count_online("main")
    sg = await presence.count_online("sigma")
    peak_m = await presence.peak_today("main")
    peak_sg = await presence.peak_today("sigma")
    names_m = await presence.online_usernames("main")
    names_sg = await presence.online_usernames("sigma")
    online_names = sorted(set(names_m) | set(names_sg))

    top_users = []
    messages_today = 0
    try:
        resp = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/internal/stats/overlay",
            params={"gw": "main", "top_n": 3},
        )
        if resp.status_code == 200:
            data = resp.json()
            top_users = data.get("top_users", [])
            messages_today = data.get("messages_today", 0)
    except Exception as e:
        logging.error(f"presence_overlay: chats_service stats failed: {e}")

    active_req = await run_status.count_active_requests()
    return {
        "online": m + sg,
        "by_gateway": {"main": m, "sigma": sg},
        "peak_today": peak_m + peak_sg,
        "online_usernames": online_names,
        "top_users": top_users,
        "messages_today": messages_today,
        "active_requests": active_req,
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
import json
from fastapi.responses import Response

@app.post("/api/profile/me")
async def me(request: Request):
    user = await get_current_user(request)

    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/profile/me",
            json={"username": user['username']},
            timeout=10.0
        )
        if response.status_code == 200:
            logging.info(f'Статистика пользователя: {type(response.content)}')
            data = response.json()
            data["name"] = await get_first_last_name(request)
            return JSONResponse(content=data, status_code=200)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")


@app.get("/api/auth/permissions")
async def get_permissions(request: Request):
    """
    Возвращает права текущего пользователя.
    Вызывается из tools-panel.js после checkAuth().
    Ответ: { "is_admin": bool }
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return JSONResponse(
        content={"is_admin": user["username"] in ADMIN_USERNAMES},
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma":        "no-cache",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Telegram-чаты: встраивание из CatchPM UI (раздел в профиле, любой юзер видит свои чаты).
# Прокси в chats_service /tg/*. added_by_user_id проставляется из текущей сессии.
# ─────────────────────────────────────────────────────────────────────────────
class TgChatUpsert(BaseModel):
    chat_id: str
    system_prompt: Optional[str] = None
    agent_name: Optional[str] = None
    agent_model: Optional[str] = None
    selected_tools: Optional[list] = None
    trigger_mode: Optional[str] = None
    orchestrator_context_n: Optional[int] = None
    proactive_enabled: Optional[bool] = None


class TgSettingsUpdate(BaseModel):
    system_prompt: Optional[str] = None
    agent_name: Optional[str] = None
    agent_model: Optional[str] = None
    selected_tools: Optional[list] = None
    trigger_mode: Optional[str] = None
    orchestrator_context_n: Optional[int] = None
    proactive_enabled: Optional[bool] = None


class TgTriggerUpsert(BaseModel):
    kind: str = "recurring"            # 'recurring' | 'once'
    title: Optional[str] = None
    time_msk: str = "09:00"            # 'HH:MM'
    days_of_week: Optional[list] = None  # [1..7] пн..вс; None/[] = все дни
    trigger_date: Optional[str] = None   # 'YYYY-MM-DD' для kind=once
    prompt: Optional[str] = None
    addressed: Optional[bool] = False
    is_active: Optional[bool] = True


class TgTriggerPatchIn(BaseModel):
    kind: Optional[str] = None
    title: Optional[str] = None
    time_msk: Optional[str] = None
    days_of_week: Optional[list] = None
    trigger_date: Optional[str] = None
    prompt: Optional[str] = None
    addressed: Optional[bool] = None
    is_active: Optional[bool] = None


async def _require_auth(request: Request):
    """Гейт: любой аутентифицированный юзер. Иначе 401."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/api/tg/chats")
async def api_tg_list_chats(request: Request):
    user = await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.get(
            f"{chats_url}/tg/chats", params={"added_by_user_id": user["user_id"]}, timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.post("/api/tg/chats")
async def api_tg_add_chat(request: Request, body: TgChatUpsert):
    user = await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    payload = body.dict()
    payload["added_by_user_id"] = user["user_id"]
    try:
        resp = await app.state.http_client.post(
            f"{chats_url}/tg/chats", json=payload, timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.delete("/api/tg/chats/{chat_id}")
async def api_tg_remove_chat(request: Request, chat_id: str):
    await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.delete(
            f"{chats_url}/tg/chats/{quote(chat_id, safe='')}", timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


# --- Telegram triggers (per-chat, edited by owner) ---
@app.get("/api/tg/triggers/{chat_id}")
async def api_tg_list_triggers(request: Request, chat_id: str):
    await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.get(
            f"{chats_url}/tg/triggers/{quote(chat_id, safe='')}", timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.post("/api/tg/triggers/{chat_id}")
async def api_tg_create_trigger(request: Request, chat_id: str, body: TgTriggerUpsert):
    user = await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    payload = body.dict()
    payload["added_by_user_id"] = user["user_id"]
    try:
        resp = await app.state.http_client.post(
            f"{chats_url}/tg/triggers/{quote(chat_id, safe='')}", json=payload, timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.patch("/api/tg/triggers/{chat_id}/{trigger_id}")
async def api_tg_update_trigger(request: Request, chat_id: str, trigger_id: int, body: TgTriggerPatchIn):
    await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    payload = {k: v for k, v in body.dict().items() if v is not None}
    try:
        resp = await app.state.http_client.patch(
            f"{chats_url}/tg/triggers/{quote(chat_id, safe='')}/{trigger_id}", json=payload, timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.delete("/api/tg/triggers/{chat_id}/{trigger_id}")
async def api_tg_delete_trigger(request: Request, chat_id: str, trigger_id: int):
    await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.delete(
            f"{chats_url}/tg/triggers/{quote(chat_id, safe='')}/{trigger_id}", timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.get("/api/tg/settings/{chat_id}")
async def api_tg_get_settings(request: Request, chat_id: str):
    await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.get(
            f"{chats_url}/tg/settings/{quote(chat_id, safe='')}", timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


@app.post("/api/tg/settings/{chat_id}")
async def api_tg_set_settings(request: Request, chat_id: str, body: TgSettingsUpdate):
    await _require_auth(request)
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.post(
            f"{chats_url}/tg/settings/{quote(chat_id, safe='')}",
            json=body.dict(), timeout=10.0
        )
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service dead: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# What's New (обновления приложения) + Onboarding (демо-модалка).
# Аутентификация — на стороне gateway (get_current_user по куке), в auth_service
# проксируем уже готовый user_id в теле (как /profile/me).
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/whatsnew/pending")
async def whatsnew_pending(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/whatsnew/pending",
            json={"user_id": user["user_id"]},
            timeout=10.0
        )
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")


@app.post("/api/whatsnew/demo/seen")
async def whatsnew_demo_seen(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/whatsnew/demo/seen",
            json={"user_id": user["user_id"]},
            timeout=10.0
        )
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")


@app.post("/api/whatsnew/updates/seen")
async def whatsnew_update_seen(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    update_id = data.get("update_id")
    update_ids = data.get("update_ids")
    if update_id is None and not update_ids:
        raise HTTPException(status_code=400, detail="update_id или update_ids обязателен")

    payload = {"user_id": user["user_id"]}
    if update_id is not None:
        payload["update_id"] = update_id
    if isinstance(update_ids, list):
        payload["update_ids"] = update_ids

    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/whatsnew/updates/seen",
            json=payload,
            timeout=10.0
        )
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")


@app.post("/api/profile/token")
async def token(request: Request):
    """Получение Scholar токена с сохранение в БД"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_url = SERVICES['auth']['url']
    data = await request.json()
    logging.info(f"Прилет токена")
    if len(data["token"]) > 50:
        raise HTTPException(status_code=400, detail="Некорректная длина токена")
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/profile/token",
            json={"username": user['username'], "token": data["token"]},
            timeout=10.0
        )
        if response.status_code == 200:
            logging.info(f'Статистика пользователя: {response.text}')
            return Response(
                content=response.content,
                status_code=200,
                media_type="application/json"
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")

@app.get("/api/preferences")
async def get_preferences(request: Request):
    """Персональные настройки текущего юзера (палитра и т.п.)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/profile/preferences/get",
            json={"user_id": user["user_id"]},
            timeout=10.0
        )
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")


@app.put("/api/preferences")
async def put_preferences(request: Request):
    """Сохранить палитру/настройки текущего юзера."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    payload = {"user_id": user["user_id"]}
    if "palette" in data:
        payload["palette"] = data["palette"]
    if "extra" in data:
        payload["extra"] = data["extra"]

    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/profile/preferences/set",
            json=payload,
            timeout=10.0
        )
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service dead: {str(e)}")


async def get_first_last_name(request: Request):
    raw_name = request.headers.get("X-Authentik-Name")
    name = "Unknown"
    if raw_name:
        try:
            name = raw_name.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            name = raw_name
    return name

@app.get("/api/auth/me")
async def get_current_user_info(request: Request):
    """
    Получение информации о текущем пользователе.
    Данные берутся из расшифрованного JWT токена
    """
    user = await get_current_user(request)

    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    name = await get_first_last_name(request)

    response_data = {
        "username": user["username"],
        "user_id": user["user_id"],
        "authenticated": True,
        "name": name,
        "source": user.get("source", "internal")
    }

    return Response(
        content=json.dumps(response_data),
        status_code=200,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache"
        }
    )
pending_requests: dict[str, int] = {}

class StopMessageRequest(BaseModel):
    request_id: str

async def notify_user_error(username: str, error_type: str = "error_parser"):
    ws = user_connections.get(username)
    if ws:
        await ws.send_json({"type": error_type})
    else:
        logging.warning(f"Нет WS соединения для {username}")

async def notify_user_message(username: str, type_message: str, message: str, source: str = None, title: str = None):
    ws = user_connections.get(username)
    if ws:
        logging.info(f"Отправили по вебсокету сообщения: {type_message}, {message}")
        await ws.send_json({"type": type_message, "message": message, "source": source, "title": title})
    else:
        logging.warning(f"Нет WS соединения для {username}")


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


# ========== ПРОКСИ ДЛЯ ЧАТОВ ==========
@app.get("/api/chats/workspaces")
async def api_chats_workspaces(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    chat_id = request.query_params.get("chat_id") or None
    r = await app.state.http_client.get(
        f"{SERVICES['chats']['url']}/editor/workspaces",
        params={"user_id": user["user_id"], **({"chat_id": chat_id} if chat_id else {})},
    )
    if r.status_code != 200:
        return {"workspaces": []}
    return r.json()


@app.post("/api/chats/send_message")
async def send_message(request: Request):
    """Отправка сообщения (для SPA)"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Получаем данные из запроса
    data = await request.json()
    logging.info(data)
    raw_cmd = (data.get("message") or "").strip().lower()
    if raw_cmd in ("/chat-id", "/workspaces"):
        if raw_cmd == "/workspaces":
            r = await app.state.http_client.get(
                f"{SERVICES['chats']['url']}/editor/workspaces",
                params={"user_id": user["user_id"]},
            )
            ws_list = r.json().get("workspaces", []) if r.status_code == 200 else []
            return JSONResponse(content={"slash_command": "/workspaces", "workspaces": ws_list})
        return JSONResponse(content={"slash_command": "/chat-id", "chat_id": data.get("chat_id")})
    request_id = str(uuid.uuid4()) # Генерация id для передачи его агенту с последующей реализацией ОС о его действиях
    # Запоминаем связь ДО отправки агенту
    pending_requests[request_id] = data["chat_id"]

    # Самоцикл (loop) — только для админов. Если не-админ подделал POST
    # с loop=true — молча срезаем все loop-поля (обычная отправка не ломается).
    if data.get("loop"):
        if user["username"] not in ADMIN_USERNAMES:
            logger.warning(
                f"[self-loop] не-админ {user.get('username')} пытался включить loop — срезано"
            )
            data.pop("loop", None)
            data.pop("loop_instruction", None)
            data.pop("loop_tick", None)
        else:
            # старт петли с фронта — это всегда начальный вход, не служебный тик
            data["loop_tick"] = False

    data["user_id"] = user["user_id"]
    data["tb_name"] = request.headers.get("X-Authentik-Username")
    data["username"] = request.headers.get("X-Authentik-Name").encode('latin-1').decode('utf-8')
    data["type_agent"] = data.get("type_agent", "pm")
    data["request_id"] = request_id
    data["gateway_target"] = "main"
    data["timezone_name"] = (data.get("timezone_name") or request.headers.get("X-Timezone") or "Europe/Moscow").strip()
    logger.info(f"Что летит при отправке сообщений {data['chat_id']}, {data['message']}")

    message = {
        "user_id": data["user_id"],
        "chat_id": data["chat_id"],
        "message": data["message"],
        "message_id": data.get("message_id"),
        "poll_answer": data.get("poll_answer"),
        "is_from_bot": False
    }
    print("Перед отправкой сообщения", data)
    await broadcast(data["chat_id"], message) # Отравим сразу по вебсокету
    # Флаг «агент в работе» в Redis: переживает F5/переключение чата,
    # фронт по нему восстанавливает стоп-кнопку и индикатор работы.
    await run_status.mark_running(
        data["chat_id"],
        request_id,
        data.get("type_agent"),
        user_message=data.get("message"),
        user_message_id=data.get("message_id"),
    )
    # Старт самоцикла (реальный, не служебный тик): ставим стабильный loop-флаг,
    # чтобы фронт после F5/переключения чата знал, что работа идёт в цикле.
    if data.get("loop") and not data.get("loop_tick"):
        await run_status.mark_loop(data["chat_id"])
    try:
        start = time.perf_counter()
        response = await app.state.http_client.post(
            f"{SERVICES['chats']['url']}/send_message",
            json=data
        )
        elapsed = time.perf_counter() - start
        logging.info(f"Время на отправку сообщения пользователя в микросервис = {elapsed:.4f}s")
        body = {}
        try:
            body = response.json()
        except Exception:
            body = {}
        body["request_id"] = request_id
        body["active_requests"] = await run_status.count_active_requests()
        return JSONResponse(content=body, status_code=response.status_code)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")


@app.post("/api/chats/stop_message")
async def stop_message(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    request_id = data.get("request_id")
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id required")

    # Стоп: снимаем Redis-флаг работы (chat_id знаем по pending_requests).
    stop_chat_id = pending_requests.get(request_id) or data.get("chat_id")
    if stop_chat_id is not None:
        await run_status.clear_running(stop_chat_id)
        # Гасим и флаг самоцикла — петля останавливается по стоп-кнопке.
        await run_status.clear_loop(stop_chat_id)

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['agent_service']['url']}/internal/stop_message",
            json={
                "request_id": request_id,
                "user_id": user["user_id"],
                # chat_id нужен, чтобы погасить самоцикл (deactivate_loop_task).
                # Петля живёт на уровне чата, а не request_id (каждый тик — свой id).
                "chat_id": data.get("chat_id"),
            },
            timeout=10.0
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {str(e)}")

@app.get("/api/chats/{chat_id}/run_status")
async def chat_run_status(chat_id: str, request: Request):
    """Идёт ли сейчас генерация по этому чату (для восстановления UI
    после F5/переключения чата). Возвращает:
      {"running": true, "request_id": "...", "model": "...", "started_at": ...}
      {"running": false}
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    info = await run_status.get_running(chat_id)
    loop_active = await run_status.is_loop_active(chat_id)
    if not info:
        last_reasoning = await run_status.get_reasoning_state(chat_id)
        active_requests = await run_status.count_active_requests()
        return JSONResponse(content={
            "running": loop_active,
            "loop_active": loop_active,
            "reasoning": last_reasoning,
            "active_requests": active_requests,
        })
    return JSONResponse(content={"running": True, "loop_active": loop_active, **info})


@app.post("/api/chats/upload_files")
async def upload_files(
    request: Request,
    chat_id: str = Form(...),
    message_id: str = Form(...),
    files: list[UploadFile] = File(...),
    force_file: list[str] = Form(default=[]),
):
    """Приём вложений с фронта и решение «текстом в промпт или файлом».

    Gateway сам режет размер (не больше 50 МБ на файл) и по file_parsers.classify
    выставляет каждому файлу режим inline/file. В agent_service уходят байты
    и список file_modes той же длины; агент ничего не парсит.
    force_file — имена файлов, которые пользователь явно попросил отправить
    файлом (кнопка «В файл» на чипе вставленного текста).
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    forced = {str(n) for n in (force_file or []) if n}
    limit_mb = file_parsers.MAX_UPLOAD_BYTES // (1024 * 1024)
    files_payload = []
    file_modes: list[str] = []
    for f in files:
        size = getattr(f, "size", None)
        if size is not None and size > file_parsers.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Файл «{f.filename}» больше {limit_mb} МБ",
            )
        # Читаем на байт больше лимита: если дочитали — файл слишком большой.
        content = await f.read(file_parsers.MAX_UPLOAD_BYTES + 1)
        if len(content) > file_parsers.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Файл «{f.filename}» больше {limit_mb} МБ",
            )
        mode, _chars = file_parsers.classify(
            f.filename or "", content, force_file=(f.filename in forced)
        )
        file_modes.append(mode)
        files_payload.append(("files", (f.filename, content, f.content_type)))

    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "user_id": str(user["user_id"]),
        "file_modes": file_modes,
    }

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['agent_service']['url']}/web/upload_files",  # прямой урл сервиса агента
            data=data,
            files=files_payload,
            timeout=120.0,
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json",
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {str(e)}")

@app.get("/api/chats/{chat_id}/image/{message_id}/{file_name}")
async def get_chat_db_image(chat_id: str, message_id: str, file_name: str, request: Request, image_key: str | None = None):
    """Отдаёт сгенерированную картинку из БД (generated_images) для превью в чате.

    В отличие от /files/... (который резолвит путь и стримит файл с диска —
    а он живёт лишь ~30 мин), этот эндпоинт проксирует /web/db_image
    agent_service и берёт бинарник напрямую из таблицы. Привязка — по
    message_id (последняя по времени картинка сообщения); file_name в поиске
    НЕ участвует (он всегда generated_0.jpg). file_name остаётся в пути роута
    лишь ради обратной совместимости старых URL, в agent_service не передаётся.

    Уникальный image_key приходит из agent_service в заголовке X-Image-Key —
    пробрасываем его наружу, фронт строит из него имя файла для скачивания."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        agent_resp = await app.state.http_client.get(
            f"{SERVICES['agent_service']['url']}/web/db_image",
            # file_name нужен для диск-фолбэка на стороне agent_service:
            # если картинки нет в generated_images, она резолвится по имени
            # СТРОГО в whitelist-корнях (/app/generated_files, /sandbox).
            params={"message_id": message_id, "image_key": image_key,
                    "file_name": file_name, "chat_id": chat_id},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {e}")

    if agent_resp.status_code != 200:
        try:
            resolve_resp = await app.state.http_client.get(
                f"{SERVICES['chats']['url']}/internal/resolve_file",
                params={"chat_id": chat_id, "message_id": message_id, "file_name": file_name},
            )
            if resolve_resp.status_code == 200:
                path = (resolve_resp.json() or {}).get("path")
                if path:
                    file_resp = await app.state.http_client.get(
                        f"{SERVICES['agent_service']['url']}/web/file",
                        params={"path": path, "chat_id": chat_id, "download": 0},
                    )
                    if file_resp.status_code in (200, 206):
                        return Response(
                            content=file_resp.content,
                            media_type=file_resp.headers.get("content-type", "image/png"),
                            headers={
                                "Cache-Control": "public, max-age=86400",
                                "X-Image-Source": "file_fallback",
                                "Access-Control-Expose-Headers": "X-Image-Key, X-Image-Source",
                            },
                        )
        except httpx.RequestError:
            pass
        raise HTTPException(status_code=agent_resp.status_code, detail="Image not found")

    return Response(
        content=agent_resp.content,
        media_type=agent_resp.headers.get("content-type", "image/png"),
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Image-Key": agent_resp.headers.get("X-Image-Key", ""),
            "X-Image-Source": agent_resp.headers.get("X-Image-Source", "db"),
            "Access-Control-Expose-Headers": "X-Image-Key, X-Image-Source",
        },
    )


@app.get("/api/chats/{chat_id}/files/{message_id}/{file_name}")
async def download_chat_file(chat_id: str, message_id: str, file_name: str, request: Request, download: int = 0):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # 1. Резолвим путь через chats_service
    try:
        resolve_resp = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/internal/resolve_file",
            params={"chat_id": chat_id, "message_id": message_id, "file_name": file_name},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {e}")

    if resolve_resp.status_code != 200:
        raise HTTPException(status_code=404, detail="File not found")

    path = resolve_resp.json()["path"]

    # 2. Стримим файл с agent_service (с поддержкой HTTP Range 206 для видео)
    fwd_headers = {}
    if request.headers.get("range"):
        fwd_headers["Range"] = request.headers["range"]
    try:
        agent_resp = await app.state.http_client.get(
            f"{SERVICES['agent_service']['url']}/web/file",
            params={"path": path, "chat_id": chat_id, "download": download},
            headers=fwd_headers,
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {e}")

    if agent_resp.status_code == 416:
        out_hdrs = {}
        if agent_resp.headers.get("content-range"):
            out_hdrs["Content-Range"] = agent_resp.headers["content-range"]
        return Response(content=b"", status_code=416, headers=out_hdrs)

    if agent_resp.status_code not in (200, 206):
        raise HTTPException(status_code=agent_resp.status_code, detail="Failed to fetch file")

    out_headers = {
        "Content-Disposition": agent_resp.headers.get(
            "content-disposition",
            "attachment; "
            f'filename="{file_name.encode("ascii", "replace").decode("ascii")}"; '
            f"filename*=UTF-8''{quote(file_name, safe='')}",
        ),
        "Accept-Ranges": agent_resp.headers.get("accept-ranges", "bytes"),
        "Cache-Control": agent_resp.headers.get("cache-control", "public, max-age=3600"),
    }
    if agent_resp.headers.get("content-range"):
        out_headers["Content-Range"] = agent_resp.headers["content-range"]
    if agent_resp.headers.get("content-length"):
        out_headers["Content-Length"] = agent_resp.headers["content-length"]

    return Response(
        content=agent_resp.content,
        status_code=agent_resp.status_code,
        media_type=agent_resp.headers.get("content-type", "application/octet-stream"),
        headers=out_headers,
    )

@app.post("/api/chats/{chat_id}/delete")
async def delete_chat(chat_id: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['chats']['url']}/chat/delete",
            json={"chat_id": chat_id, "user_id": user["user_id"]}
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")


@app.post("/api/chats/{chat_id}/rename")
async def rename_chat(chat_id: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    new_title = (data.get("new_title") or "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="new_title required")

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['chats']['url']}/chat/rename",
            json={"chat_id": chat_id, "user_id": user["user_id"], "new_title": new_title}
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")


@app.post("/api/chats/{chat_id}/favorite")
async def favorite_chat(chat_id: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
    is_favorite = bool(data.get("is_favorite", True))

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['chats']['url']}/chat/favorite",
            json={"chat_id": chat_id, "user_id": user["user_id"], "is_favorite": is_favorite}
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

    try:
        start = time.perf_counter()
        headers = {"X-User-Name": user["username"]}

        response = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/user/chats",
            cookies=request.cookies,
            headers=headers
        )
        
        elapsed = time.perf_counter() - start
        logging.info(f"Время подгрузки чатов = {elapsed:.4f}s")

        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Chats service error")

        data = response.json()
        return data["chats"]

    except Exception as e:
        logging.error(f"Ошибка получения чатов: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch chats")


@app.get("/api/chats/search")
async def search_chats(request: Request, q: str = "", limit: int = 30):
    """Живой поиск по сообщениям пользователя. Прокси на chats_service.
    Требует минимум 2 символа — иначе сразу пустой результат без похода в сервис."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "query": query}

    try:
        headers = {"X-User-Name": user["username"]}
        response = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/user/messages/search",
            params={"q": query, "limit": max(1, min(limit, 50))},
            cookies=request.cookies,
            headers=headers,
        )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Chats service error")
        return response.json()

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка поиска по чатам: {e}")
        raise HTTPException(status_code=500, detail="Failed to search chats")


@app.post("/api/chats/{chat_id}/reaction")
async def post_chat_message_reaction(chat_id: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = await request.json()
    message_id = data.get("message_id")
    db_id = data.get("id")
    reaction = data.get("reaction")
    if reaction not in ("like", "dislike", None, ""):
        raise HTTPException(status_code=400, detail="Invalid reaction")
    norm_reaction = reaction if reaction in ("like", "dislike") else None
    ok = await run_status.push_message_reaction(
        chat_id=chat_id,
        message_id=message_id,
        reaction=norm_reaction,
        user_id=user.get("user_id"),
        db_id=db_id,
    )
    return {"ok": ok, "chat_id": str(chat_id), "message_id": message_id, "id": db_id, "reaction": norm_reaction}


@app.post("/api/chats/reaction")
async def post_chat_message_reaction_root(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = await request.json()
    chat_id = str(data.get("chat_id") or "").strip()
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id required")
    message_id = data.get("message_id")
    db_id = data.get("id")
    reaction = data.get("reaction")
    if reaction not in ("like", "dislike", None, ""):
        raise HTTPException(status_code=400, detail="Invalid reaction")
    norm_reaction = reaction if reaction in ("like", "dislike") else None
    ok = await run_status.push_message_reaction(
        chat_id=chat_id,
        message_id=message_id,
        reaction=norm_reaction,
        user_id=user.get("user_id"),
        db_id=db_id,
    )
    return {"ok": ok, "chat_id": chat_id, "message_id": message_id, "id": db_id, "reaction": norm_reaction}


@app.get("/api/user/settings")
async def get_user_settings_gw(request: Request):
    user = await get_current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    uid = int(user["user_id"])
    try:
        resp = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/user/settings",
            headers={"X-User-Id": str(uid)},
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logging.warning(f"get_user_settings_gw fallback error: {e}")
    return {
        "user_id": uid,
        "selected_tools": [],
        "selected_model": "gpt",
        "active_chat_id": None,
    }


@app.post("/api/user/settings")
async def post_user_settings_gw(request: Request):
    user = await get_current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    uid = int(user["user_id"])
    data = await request.json()
    has_tools = "selected_tools" in data and isinstance(data.get("selected_tools"), list)
    has_model = "selected_model" in data and data.get("selected_model") is not None
    has_chat = "active_chat_id" in data

    selected_tools = [str(t).strip() for t in data["selected_tools"] if str(t).strip()] if has_tools else None
    selected_model = run_status.normalize_setting_model(data["selected_model"]) if has_model else None
    active_chat_id = (str(data.get("active_chat_id") or "").strip() or None) if has_chat else None

    ok = await run_status.push_user_settings(
        user_id=uid,
        selected_tools=selected_tools,
        selected_model=selected_model,
        active_chat_id=active_chat_id,
        has_active_chat=has_chat,
    )
    if not ok:
        try:
            payload = {}
            if has_tools:
                payload["selected_tools"] = selected_tools
            if has_model:
                payload["selected_model"] = selected_model
            if has_chat:
                payload["active_chat_id"] = active_chat_id
            await app.state.http_client.post(
                f"{SERVICES['chats']['url']}/user/settings",
                headers={"X-User-Id": str(uid)},
                json=payload,
                timeout=5.0,
            )
            ok = True
        except Exception as e:
            logging.warning(f"post_user_settings_gw fallback failed: {e}")
    return {
        "ok": ok,
        "user_id": uid,
        "selected_tools": selected_tools,
        "selected_model": selected_model,
        "active_chat_id": active_chat_id,
    }


@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, request: Request):
    """Получение сообщений чата (для SPA)"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:

        headers = {
            "X-User-Name": user["username"]
        }
        response = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/chat/{chat_id}/messages",
            cookies=request.cookies,
            params=dict(request.query_params),
            headers=headers
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
        data = await request.json()
        logger.info(f"Тело с фронта {data}")
    except Exception:
        data = {}

    type_agent = data.get("type_agent", "pm")

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['chats']['url']}/chat/new",
            json={"user_id": user["user_id"], "type_agent": type_agent}
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

@app.get("/api/chats/{chat_id}/poll_answers")
async def get_chat_poll_answers(chat_id: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        response = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/chats/{chat_id}/poll_answers"
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")

# ========= Найти чат по типу ========
@app.get("/api/chats/by-type/{type_agent}")
async def find_chat_by_type(type_agent: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        response = await app.state.http_client.get(
            f"{SERVICES['chats']['url']}/chat/by-type/{type_agent}",
            headers={"X-User-Id": str(user["user_id"])}
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {str(e)}")

# GET /api/feedback — история
@app.get("/api/feedback")
async def get_feedbacks(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        response = await app.state.http_client.get(
            f"{SERVICES['auth']['url']}/feedback"
        )
        logging.info(response.json())
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Auth service unreachable: {str(e)}")


# POST /api/feedback — новый отзыв
@app.post("/api/feedback")
async def post_feedback(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await get_current_user(request)
    data = await request.json()
    data["user_id"] = user["user_id"]
    data["username"] = user["username"]
    auth_url = SERVICES['auth']['url']
    try:
        response = await app.state.http_client.post(
            f"{auth_url}/feedback",
            json=data,
            timeout=30.0
        )
        if response.status_code == 200:
            body = response.json()
            for ws in user_connections.values():
                if ws:
                    # Формируем правильную обертку для фронтенда
                    ws_payload = {
                        "type": "feedback_new",
                        "feedback": body
                    }
                    try:
                        await ws.send_json(ws_payload)
                    except Exception as e:
                        logging.error(f"WS send error: {e}")
                        # Если ошибка отправки, возможно сокет мертв, удаляем его
                        user_connections.pop(user["username"], None)

            return {"ok": True}
    except httpx.RequestError as e:
        return {"ok": False}

@app.get("/api/graph")
async def get_graph_nodes(request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get( 
            f"{agent_url}/internal/graph",
            timeout=30.0
        )
        return resp.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Graph data error")


@app.get("/api/models")
async def get_agent_models(request: Request):
    """Каталог моделей агента с описаниями и SVG (для ModelPicker) + проверка доступности agent_service.

    Ходит напрямую в /internal/models сервиса agent_service.
    Если agent_service упал или недоступен, возвращает agent_available=False,
    чтобы фронтенд (app.js) заблокировал отправку запросов."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/internal/models",
            timeout=5.0,
        )
        if resp.status_code != 200:
            return {"agent_available": False, "default_model": "coder", "models": []}
        data = resp.json()
        if not isinstance(data, dict):
            return {"agent_available": False, "default_model": "coder", "models": []}
        models = data.get("models")
        if not isinstance(models, list) or not models:
            return {
                "agent_available": False,
                "default_model": data.get("default_model") or "coder",
                "models": [],
            }
        run_status.register_setting_models(models)
        return {
            "agent_available": bool(data.get("agent_available", True)),
            "default_model": data.get("default_model") or models[0].get("id") or "coder",
            "models": models,
        }
    except (httpx.RequestError, ValueError):
        return {"agent_available": False, "default_model": "coder", "models": []}


@app.get("/api/providers/status/stream")
async def get_providers_status_stream(request: Request, force: bool = False):
    """SSE-поток доступности LLM-провайдеров: каждая модель летит по мере ответа."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    agent_url = SERVICES['agent_service']['url']

    async def _proxy_stream():
        try:
            async with app.state.http_client.stream(
                "GET",
                f"{agent_url}/internal/providers_status/stream",
                params={"force": "1"} if force else None,
                timeout=25.0,
            ) as resp:
                if resp.status_code != 200:
                    yield 'event: error\ndata: {"providers": null}\n\n'
                    return
                async for chunk in resp.aiter_text():
                    if chunk:
                        yield chunk
        except httpx.RequestError:
            yield 'event: error\ndata: {"providers": null}\n\n'

    return StreamingResponse(
        _proxy_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/providers/status")
async def get_providers_status(
    request: Request,
    model: Optional[str] = None,
    stream: bool = False,
    force: bool = False,
):
    """Доступность LLM-провайдеров под моделями тоггла (для ModelPicker).

    Проксирует /internal/providers_status agent_service (включая одиночный ?model=<id>
    и потоковый ?stream=1). При недоступности самого агента отдаёт мягкий 200 с
    providers=null, чтобы фронт показал нейтральное состояние, а не рушился."""
    if stream:
        return await get_providers_status_stream(request, force=force)
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    agent_url = SERVICES['agent_service']['url']
    params = {}
    if model:
        params["model"] = model
    if force:
        params["force"] = "1"
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/internal/providers_status",
            params=params or None,
            timeout=22.0,
        )
        return resp.json()
    except httpx.RequestError:
        return {"providers": None, "cached_at": None}



@app.get("/api/skills")
async def api_list_skills(request: Request, query: str = "", sort_by: str = "new", filter_by: str = "all"):
    """Прокси списка скиллов из agent_service (с поддержкой фильтрации liked/selected)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = int(user.get("user_id") or 0)
    chats_url = SERVICES['chats']['url']
    if user_id > 0 and filter_by in ("liked", "selected"):
        ep = "/skills/liked" if filter_by == "liked" else "/skills/selected"
        try:
            await app.state.http_client.get(
                f"{chats_url}{ep}",
                params={"user_id": user_id},
                headers={"X-User-Id": str(user_id)},
                timeout=8.0,
            )
        except httpx.RequestError:
            pass
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/api/skills",
            params={"query": query, "sort_by": sort_by, "filter_by": filter_by, "user_id": user_id},
            timeout=20.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.post("/api/skills")
async def api_create_skill(request: Request):
    """Прокси создания скилла (публикация агентом)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = await request.json()
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.post(
            f"{agent_url}/api/skills",
            json=payload,
            timeout=30.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.get("/api/skills/selected")
async def api_get_selected_skills(request: Request):
    """Текущие выбранные скиллы пользователя (через chats_service с дренажем Redis)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = user.get("user_id")
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.get(
            f"{chats_url}/skills/selected",
            params={"user_id": user_id},
            headers={"X-User-Id": str(user_id)},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return JSONResponse(status_code=200, content=resp.json())
    except httpx.RequestError:
        pass

    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/api/skills/selected",
            params={"user_id": user_id},
            timeout=20.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.get("/api/skills/liked")
async def api_get_liked_skills(request: Request):
    user = await get_current_user(request)
    if not user: raise HTTPException(status_code=401)
    user_id = user.get("user_id")
    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.get(
            f"{chats_url}/skills/liked",
            params={"user_id": user_id},
            headers={"X-User-Id": str(user_id)},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return JSONResponse(status_code=200, content=resp.json())
    except httpx.RequestError: pass

    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/api/skills/liked",
            params={"user_id": user_id},
            timeout=20.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.get("/api/skills/{skill_id}")
async def api_get_skill_detail(skill_id: int, request: Request):
    """Полный текст и метаданные конкретного скилла из agent_service."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = int(user.get("user_id") or 0)
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/api/skills/{skill_id}",
            params={"user_id": user_id},
            timeout=20.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.post("/api/skills/{skill_id}/select")
async def api_toggle_selected_skill(skill_id: int, request: Request):
    """Тоггл выбранного скилла через Redis -> chats_service (лимит 5 на пользователя)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    uid = int(user.get("user_id") or 0)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    payload["user_id"] = uid

    res = await run_status.push_skill_select(
        user_id=uid,
        skill_id=skill_id,
        selected=payload.get("selected"),
        selected_ids=payload.get("selected_ids"),
        max_selected=5,
    )
    if res.get("ok"):
        return JSONResponse(status_code=200, content=res)
    if res.get("reason") == "limit":
        raise HTTPException(status_code=400, detail="Max 5 selected skills per user")

    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.post(
            f"{chats_url}/skills/{skill_id}/select",
            headers={"X-User-Id": str(uid)},
            json=payload,
            timeout=10.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {e}")


@app.post("/api/skills/{skill_id}/like")
async def api_like_skill(skill_id: int, request: Request):
    user = await get_current_user(request)
    if not user: raise HTTPException(status_code=401)
    uid = int(user.get("user_id") or 0)
    try: payload = await request.json()
    except Exception: payload = {}
    payload["user_id"] = uid

    res = await run_status.push_skill_like(
        user_id=uid,
        skill_id=skill_id,
        liked=payload.get("liked"),
        liked_ids=payload.get("liked_ids"),
        likes_count=payload.get("likes_count"),
    )
    if res.get("ok"):
        return JSONResponse(status_code=200, content=res)

    chats_url = SERVICES['chats']['url']
    try:
        resp = await app.state.http_client.post(
            f"{chats_url}/skills/{skill_id}/like",
            headers={"X-User-Id": str(uid)},
            json=payload,
            timeout=10.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Chats service unreachable: {e}")


@app.get("/api/skills/{skill_id}/prompt")
async def api_skill_prompt(skill_id: int, request: Request):
    """Прокси экспорта промпта скилла."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/api/skills/{skill_id}/prompt",
            timeout=20.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.put("/api/skills/{skill_id}")
async def api_update_skill(skill_id: int, request: Request):
    """Прокси редактирования скилла. Разрешено только автору скилла."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = await request.json()
    payload["user_id"] = user.get("user_id")

    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.put(
            f"{agent_url}/api/skills/{skill_id}",
            json=payload,
            timeout=30.0,
        )
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")


@app.delete("/api/skills/{skill_id}")
async def api_delete_skill(skill_id: int, request: Request):
    """Удаление скилла. Проверка прав только на сервере (ADMIN_USERNAMES)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("username") not in ADMIN_USERNAMES:
        raise HTTPException(status_code=403, detail="Only admin can delete skill")

    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.delete(
            f"{agent_url}/api/skills/{skill_id}",
            timeout=20.0,
        )
        if resp.status_code == 200:
            await run_status.evict_deleted_skill(skill_id)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"PM Agent unreachable: {e}")

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


# ── ЗАДАЧНИК (task_gw.py): карточки фоновых задач по WS и служебный вход агента ──
import task_gw as _task_gw


def _task_register(request_id, chat_id):
    pending_requests[request_id] = chat_id


async def _task_mark_running(chat_id, request_id):
    await run_status.mark_running(chat_id, request_id)


_task_gw.bind(push=broadcast, register=_task_register, mark_running=_task_mark_running)
app.include_router(_task_gw.router)
