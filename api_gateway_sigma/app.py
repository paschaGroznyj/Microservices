# gateway/app.py  (SIGMA — вариант без WebSocket, GET-polling + собственный Kafka-consumer)
from fastapi import FastAPI, HTTPException, Request, Depends, Form, Cookie, Response
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
from pydantic import BaseModel, Field
from typing import Optional
import time

from config import (
    PORT_PM_AGENT, PORT_DB_CONSUMER,
    PORT_AUTH_SERVICE, PORT_CHATS_SERVICE,
    PORT_CRB_SERVICE, SECRET_KEY, ALGORITHM,
    PORT_GATEWAY_PORT, PORT_PARSERS_SERVICE,
    ADMIN_USERNAMES
)

from sigma_status_store import status_store
import presence
import editor_gw
import os as _os_env
import run_status
import cache_bust
import reactions_bus
import reads_bus

import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Валидация логина/пароля: только латиница ──────────────────────────────
# Кириллица в username ломает работу дальше по системе, поэтому режем ещё
# на входе гейтвея (в дополнение к клиентской проверке в формах).
# username: латинские буквы, цифры, . _ -  (длина 3..50)
# password: только печатные ASCII-символы (латиница/цифры/пунктуация), 6..72
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,50}$")


def _validate_credentials(username: str, password: str) -> Optional[str]:
    """Возвращает текст ошибки, если данные невалидны, иначе None."""
    if not _USERNAME_RE.match(username or ""):
        return ("Логин может содержать только латинские буквы, цифры и символы "
                ". _ - (длина 3–50)")
    # Пароль: запрещаем любые не-ASCII (в т.ч. кириллицу)
    if not password or not password.isascii():
        return "Пароль может содержать только латинские буквы, цифры и символы"
    if not (6 <= len(password) <= 72):
        return "Пароль должен быть длиной от 6 до 72 символов"
    return None

COOKIE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up API Gateway SIGMA...")

    # Привязываем текущий event loop к store, чтобы Kafka-consumer (крутится
    # в отдельном потоке) мог взводить asyncio.Event для SSE-стримов.
    status_store.bind_loop(asyncio.get_running_loop())

    app.state.http_client = httpx.AsyncClient(
        timeout=30.0,
        limits=httpx.Limits(
            max_keepalive_connections=20, # количество idle соединений
            max_connections=100, # максимум одновременных соединений
            keepalive_expiry=60.0 # idle соединение закрывается
        ),
        follow_redirects=True
    )

    # Веб-редактор (/editor): общий роутер main gateway, но только просмотр
    # (PUT /api/editor/file -> 403) и AI-панель через SSE вместо WebSocket.
    editor_gw.bind(
        http_client=app.state.http_client,
        chats_url=SERVICES['chats']['url'],
        sandbox_url=SERVICES['sandbox']['url'],
        get_current_user=get_current_user,
        templates=templates,
        readonly=True,
        chat_transport="sse",
    )

    # Дебаунсер реакций общего чата: Redis-хеш общий с main gateway,
    # пачку забирает тот воркер, кто первым сделал RENAME.
    reactions_bus.bind(app.state.http_client, SERVICES['chats']['url'])
    reactions_bus.start()
    # отметки прочтения: zset общий с main, тот же принцип RENAME
    reads_bus.bind(app.state.http_client, SERVICES['chats']['url'])
    reads_bus.start()

    yield

    # Shutdown
    logger.info("Shutting down API Gateway SIGMA...")
    await reactions_bus.stop()
    await reads_bus.stop()
    await app.state.http_client.aclose()
app = FastAPI(title="API Gateway SIGMA", lifespan=lifespan)
app.include_router(editor_gw.router)
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
        "url": _os_env.getenv("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005"),
        "health_endpoint": "/health",
    },
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/some-route")
async def some_route(request: Request):
    logging.info(request.headers)
    authentik_user = request.headers.get("X-Authentik-Username")
    logger.info(authentik_user)

# Другой коннектор для отправки релевантных статей на фронт
# ─────────────────────────────────────────────────────────────────────────
# SIGMA: WebSocket полностью удалён. Обратный канал агент→фронт заменён на
# GET-polling. Финал ответа ловит собственный Kafka-consumer (sigma_consumer)
# и складывает в status_store; фронт опрашивает GET /api/chats/{id}/status.
#
# Внутренние HTTP-эндпоинты /internal/bot_status, /internal/bot_stream,
# /internal/bot_message СНАРУЖИ больше не нужны (их дёргал старый gateway для
# push в WS). Оставляем bot_status/bot_message как безопасные no-op заглушки
# на случай, если кто-то по ошибке настроит обратный адрес на sigma — они
# просто вернут ok и ничего не сломают. bot_stream удалён совсем (стриминга нет).
# ─────────────────────────────────────────────────────────────────────────

@app.post("/internal/bot_status")
async def bot_status(payload: BotStatus):
    """SIGMA no-op: живые статусы агента через push не доставляются.
    Фронт показывает нейтральный индикатор 'Обрабатываю…' пока status=running.
    Эндпоинт оставлен для обратной совместимости обратного канала."""
    logger.debug(f"[sigma] bot_status проигнорирован (no-op): {payload.request_id} -> {payload.status}")
    return {"ok": True, "note": "sigma-noop"}

@app.post("/internal/bot_message")
async def receive_bot_message(bot_msg: BotMessage):
    """Получаем финальное сообщение бота от chats-consumer и помечаем chat done для polling."""
    logging.info(f"[sigma] bot_message chat_id={bot_msg.chat_id}")
    status_store.mark_done(bot_msg.chat_id, bot_msg.message)
    # Финал пришёл — снимаем Redis-флаг «агент в работе» (общий для гейтвеев).
    msg_dict = bot_msg.message if isinstance(bot_msg.message, dict) else {}
    await run_status.clear_running(bot_msg.chat_id, request_id=msg_dict.get("request_id"))
    return {"ok": True}


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


_AUTHENTIK_HEADERS = (
    "X-Authentik-Username",
    "X-Authentik-Name",
    "X-Authentik-Uid",
    "X-Authentik-Email",
)


def _decode_header_val(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        return raw.encode("latin-1").decode("utf-8").strip()
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw.strip()


def _has_authentik_headers(request: Request) -> bool:
    return any(_decode_header_val(request.headers.get(h)) for h in _AUTHENTIK_HEADERS)


def _extract_authentik_username(request: Request) -> Optional[str]:
    for h in ("X-Authentik-Username", "X-Authentik-Uid", "X-Authentik-Email", "X-Authentik-Name"):
        val = _decode_header_val(request.headers.get(h))
        if val:
            return val
    return None


# Аутентификация через Gateway
async def get_current_user(request: Request):
    """Получение текущего пользователя из заголовков Authentik или cookie"""
    authentik_user = _extract_authentik_username(request)
    if authentik_user:
        logging.info(authentik_user)
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
                    "source": "authentik",
                }
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Auth service unreachable: {str(e)}")

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
        if _has_authentik_headers(request):
            return templates.TemplateResponse("chat.html", {
                "request": request,
                "user": {
                    "username": _extract_authentik_username(request) or "user",
                    "user_id": None,
                    "authenticated": True,
                    "source": "authentik",
                },
            })
        # Не авторизован - показываем приветственную страницу
        return templates.TemplateResponse("login.html", {
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
    if _has_authentik_headers(request) or await get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "message": None
    })

@app.get("/register-page", response_class=HTMLResponse)
async def register_page(request: Request):
    """Страница регистрации"""
    if _has_authentik_headers(request) or await get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": None
    })


@app.api_route("/without-register-page", methods=["GET", "POST"], response_class=RedirectResponse)
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
                max_age=COOKIE_MAX_AGE_SECONDS,
                secure=False
            )
            return resp
        else:
            error_detail = response.json().get("detail", "Ошибка гостевого входа")
            return templates.TemplateResponse(
                "login.html",
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

    # Серверная валидация: только латиница в логине и пароле
    cred_error = _validate_credentials(username, password)
    if cred_error:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": cred_error, "message": None},
            status_code=400
        )

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
                max_age=COOKIE_MAX_AGE_SECONDS,
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

    # Серверная валидация: только латиница в логине и пароле
    cred_error = _validate_credentials(username, password)
    if cred_error:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": cred_error},
            status_code=400
        )

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
            max_age=COOKIE_MAX_AGE_SECONDS,
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
# ONLINE PRESENCE (SIGMA). Счётчик суммарно main+sigma из Redis.
# Оверлей — Redis-часть + SQL-агрегаты от chats_service (gw=sigma).
# ────────────────────────────────────────────────────────
@app.post("/api/presence/ping")
async def presence_ping(request: Request):
    """Heartbeat sigma-присутствия. Фронт дёргает его по таймеру (каждые 15с),
    независимо от ожидания ответа бота (в отличие от poll_chat_status).
    Без авторизации просто no-op (204)."""
    user = await get_current_user(request)
    if user:
        await presence.touch("sigma", user["username"])
    return Response(status_code=204)


@app.post("/api/presence/leave")
async def presence_leave(request: Request):
    """Немедленный выход из онлайна sigma при закрытии вкладки.

    Фронт шлёт сюда navigator.sendBeacon на pagehide/visibilitychange.
    sendBeacon НЕ передаёт cookie кросс-доменно, но у нас same-origin —
    куки с токеном приходят, get_current_user отрабатывает. Без юзера — no-op.
    """
    user = await get_current_user(request)
    if user:
        await presence.leave("sigma", user["username"])
    return Response(status_code=204)


@app.get("/api/presence/count")
async def presence_count(request: Request, ping: int = 0):
    """Лёгкий публичный счётчик онлайн (суммарно main+sigma) + живые запросы к агенту
    + системное уведомление sys:notice (restart.sh). Redis-only."""
    if ping:
        user = await get_current_user(request)
        if user:
            await presence.touch("sigma", user["username"])
    m = await presence.count_online("main")
    sg = await presence.count_online("sigma")
    active_req = await run_status.count_active_requests()
    notice = await run_status.get_system_notice()
    return {"online": m + sg, "main": m, "sigma": sg, "active_requests": active_req,
        "build_id": cache_bust.get_build_id(), "notice": notice}


@app.get("/api/presence/overlay")
async def presence_overlay(request: Request):
    """Подробный оверлей. Гибрид: Redis + SQL от chats_service."""
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
            params={"gw": "sigma", "top_n": 3},
        )
        if resp.status_code == 200:
            data = resp.json()
            top_users = data.get("top_users", [])
            messages_today = data.get("messages_today", 0)
    except Exception as e:
        logger.error(f"presence_overlay: chats_service stats failed: {e}")

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
    """Выход из системы (обработка HTML формы).

    Куда редиректим после выхода:
      - authentik-юзер (есть заголовок X-Authentik-Username) -> в магазин
        приложений uva для SSO-выхода;
      - внутренний (парольный) юзер -> на нашу страницу логина /login-page.
    """
    authentik_user = request.headers.get("X-Authentik-Username")
    redirect_url = "https://uva-advanced.ru" if authentik_user else "/login-page"

    response = RedirectResponse(
        url=redirect_url,
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
        "is_admin": user["username"] in ADMIN_USERNAMES,
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

@app.post("/api/favorites")
async def favorites(request: Request):
    """Обработка звездочек избранного"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    data = await request.json()
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
    """SIGMA: пуш недоступен — складываем событие в status_store,
    фронт заберёт его через GET /api/chats/user_events."""
    status_store.push_user_event(username, {"type": error_type})

async def notify_user_message(username: str, type_message: str, message: str, source: str = None, title: str = None):
    """SIGMA: пуш недоступен — кладём пользовательское событие в store для polling."""
    status_store.push_user_event(username, {
        "type": type_message, "message": message, "source": source, "title": title
    })

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
            timeout=60.0
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
            logging.info(result.get("type"))
            if result.get("type") == "parser":
                meta = result.get("meta", {})
                logging.info(meta)
                warnings_list = meta.get("warnings", [])

                if warnings_list:
                    for warning in warnings_list:
                        warning_text = warning.get("message", "Неизвестная ошибка")
                        source = warning.get("source", "Неизвестный источник")
                        await notify_user_message(
                            data['username'],
                            type_message=result.get("type"),
                            message=warning_text,
                            source=source,
                            title=warning.get("title")
                        )
                else:
                    pass
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
    """Агент сообщает что эмбеддинги/релевантные статьи готовы.
    SIGMA: вместо WS кладём data в store — фронт заберёт через GET /api/chats/user_events."""
    data = await request.json()
    username = data["username"]
    logging.info(f"ОТВЕТ АГЕНТА ПО ЭМБЕДДИНГАМ: {data}")
    status_store.push_user_event(username, data["data"])
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
            "keywords": [data['key_word']],
            "limit": 10
        },
        "request_id": None,
        "search_now": False
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

    data["user_id"] = user["user_id"]

    # Поддерживаем оба режима:
    # 1) через authentik (заголовки X-Authentik-*)
    # 2) через встроенную форму логина (JWT-cookie без заголовков)
    auth_tb_name = request.headers.get("X-Authentik-Username")
    raw_auth_name = request.headers.get("X-Authentik-Name")

    data["tb_name"] = auth_tb_name or user["username"]
    if raw_auth_name:
        try:
            data["username"] = raw_auth_name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            data["username"] = raw_auth_name
    else:
        data["username"] = user["username"]
    data["type_agent"] = data.get("type_agent", "pm") # О Великий gateway
    data["request_id"] = request_id
    data["gateway_target"] = "sigma"
    data["timezone_name"] = (data.get("timezone_name") or request.headers.get("X-Timezone") or "Europe/Moscow").strip()
    logger.info(f"Что летит при отправке сообщений {data['chat_id']}, {data['message']}")

    # SIGMA: WS-эхо убрано. Помечаем в store, что для этого чата ожидается
    # ответ бота (status=running). Фронт сразу оптимистично отрисует своё
    # сообщение и начнёт поллить GET /api/chats/{id}/status.
    status_store.mark_running(data["chat_id"], request_id)
    # Redis-флаг «агент в работе» (общий для обоих гейтвеев): переживает
    # F5/переключение чата, фронт по нему восстанавливает стоп-кнопку и индикатор.
    await run_status.mark_running(
        data["chat_id"],
        request_id,
        data.get("type_agent"),
        user_message=data.get("message"),
        user_message_id=data.get("message_id"),
    )
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
        await run_status.clear_running(stop_chat_id, ok=False)

    try:
        response = await app.state.http_client.post(
            f"{SERVICES['agent_service']['url']}/internal/stop_message",
            json={"request_id": request_id, "user_id": user["user_id"]},
            timeout=10.0
        )
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type="application/json"
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {str(e)}")

# SIGMA: эндпоинт /api/chats/upload_files УДАЛЁН — загрузка файлов от пользователя
# отключена (требование ДКБ). Кнопка аплоада на фронте также вырезана.
# Отдача сгенерированных агентом файлов и превью картинок (/files, /image) сохранена.

# ─────────────────────────────────────────────────────────────────────────
# SIGMA POLLING API — замена WebSocket-канала на GET-опрос
# ─────────────────────────────────────────────────────────────────────────

@app.get("/api/chats/{chat_id}/status")
async def poll_chat_status(chat_id: str, request: Request):
    """Опрос статуса ответа бота для чата (замена WS-push).

    Фронт вызывает этот эндпоинт по таймеру после отправки сообщения.
    Ответ:
      {"status": "running"}                — агент ещё думает
      {"status": "done", "message": {...}} — финал готов (данные из Kafka-consumer)
      {"status": "idle"}                   — для чата ничего не ожидается
    Финал детектит собственный Kafka-consumer (sigma_consumer) и кладёт в store.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await presence.touch("sigma", user["username"])
    state = status_store.get_chat_state(chat_id)
    state = {**state, "tasks": sigma_chat_tasks(chat_id)}  # задачник: карточки для опроса
    return JSONResponse(content=state)


@app.get("/api/chats/user_events")
async def poll_user_events(request: Request):
    """Опрос пользовательских событий (релевантные статьи, ошибки парсера и т.п.).

    Возвращает и очищает накопленные события:
      {"events": [ {...}, ... ]}
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    events = status_store.pop_user_events(user["username"])
    return JSONResponse(content={"events": events})


@app.get("/api/chats/{chat_id}/run_status")
async def chat_run_status(chat_id: str, request: Request):
    """Идёт ли сейчас генерация по этому чату (для восстановления UI
    после F5/переключения чата). Читает общий Redis-флаг (тот же ключ,
    что и на main-гейтвее). Возвращает:
      {"running": true, "request_id": "...", "model": "...", "started_at": ...}
      {"running": false}
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    info = await run_status.get_running(chat_id)
    if not info:
        last_reasoning = await run_status.get_reasoning_state(chat_id)
        active_requests = await run_status.count_active_requests()
        return JSONResponse(content={"running": False, "reasoning": last_reasoning, "active_requests": active_requests})
    return JSONResponse(content={"running": True, **info})


@app.get("/api/chats/{chat_id}/flow")
async def chat_flow(chat_id: str, request: Request):
    """FlowMap: хронология пути последнего запроса (после F5 / переключения чата).

    events — из Redis flow:{chat_id} (ts в мс epoch), now — серверное время для пересчёта
    в таймлайн браузера без учёта расхождения часов клиента. Тексты размышлений — из
    agent:reasoning:{chat_id} (steps по порядку событий think_end, live_text — текущий живой блок)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    events = await run_status.get_flow(chat_id)
    r = await run_status.get_reasoning_state(chat_id) or {}
    running = await run_status.get_running(chat_id) is not None
    return JSONResponse(content={
        "now": int(time.time() * 1000),
        "running": running,
        "events": events,
        "reasoning": {
            "request_id": r.get("request_id"),
            "steps": r.get("steps") or [],
            "live_text": r.get("live_text") or "",
        },
    })

# ── SSE: push вместо polling ─────────────────────────────────────────────
# Заголовки, критичные для корректного SSE через прокси:
#   Cache-Control: no-cache      — не кэшировать поток
#   X-Accel-Buffering: no        — отключить буферизацию в nginx (иначе
#                                  события склеятся и придут пачкой в конце)
#   Connection: keep-alive       — держать соединение
# ── Redis pub/sub для стрима чанков от агента ────────────────────────────────
# Агент публикует reasoning/answer-чанки в канал stream:{request_id};
# SSE-стрим подписан на него и релеит фреймы браузеру. Redis уже
# используется в presence.py — тот же REDIS_URL.
import os as _os
_REDIS_URL = _os.getenv("REDIS_URL", "redis://redis:6379/0")
_sse_redis = None
_sse_redis_failed = False


async def _get_sse_redis():
    """Ленивый async Redis-клиент для подписки на stream:{request_id}.
    Fail-open: Redis лёг → None, SSE работает без чанков (только running/done)."""
    global _sse_redis, _sse_redis_failed
    if _sse_redis is not None:
        return _sse_redis
    if _sse_redis_failed:
        return None
    try:
        import redis.asyncio as aioredis
        _sse_redis = aioredis.from_url(_REDIS_URL, decode_responses=True)
        return _sse_redis
    except Exception as e:
        _sse_redis_failed = True
        logger.warning(f"[sigma-sse] Redis недоступен, стрим чанков отключён: {e}")
        return None


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Как часто слать heartbeat-комментарий, чтобы прокси/браузер не рвали
# idle-соединение, и общий потолок жизни одного стрима.
_SSE_HEARTBEAT_SEC = 15
_SSE_MAX_LIFETIME_SEC = 900


def _sse_pack(event: str, data: dict) -> str:
    """Собрать одно SSE-сообщение в формате text/event-stream."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/chats/{chat_id}/stream")
async def stream_chat_status(chat_id: str, request: Request):
    """SSE-замена polling'а GET /api/chats/{id}/status.

    Держит одно соединение и пушит событие ровно в момент готовности ответа
    (mark_done из Kafka-consumer взводит asyncio.Event в store). Никакого
    опроса store в цикле — стрим спит на wait_chat_ready до появления финала.

    События:
      event: running  data: {"status":"running"}           — при подключении, если бот думает
      event: done     data: {"status":"done","message":...} — финал готов
      event: idle     data: {"status":"idle"}               — для чата ничего не ждётся
      :heartbeat                                             — keep-alive комментарий

    Фолбэк: если фронт не смог открыть SSE, остаётся старый GET .../status.
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    await presence.touch("sigma", user["username"])

    async def event_gen():
        started = time.time()
        # Первичное состояние: если ответ уже готов/протух — отдаём сразу.
        state = status_store.get_chat_state(chat_id)
        if state.get("status") == "done":
            yield _sse_pack("done", state)
            return
        if state.get("status") == "idle":
            yield _sse_pack("idle", state)
            return
        # running — сообщаем и уходим ждать финал
        yield _sse_pack("running", {"status": "running"})

        # ── Подписка на Redis-канал чанков stream:{request_id} ──────────────
        # Агент публикует туда live/reasoning_end/final_commit-фреймы.
        # Параллельно ждём mark_done (финал через Kafka) — он главный
        # сигнал завершения; чанки — необязательный живой текст.
        request_id = status_store.get_chat_request_id(chat_id)
        pubsub = None
        chan = None
        r = await _get_sse_redis()
        if r is not None and request_id:
            try:
                pubsub = r.pubsub()
                chan = f"stream:{request_id}"
                await pubsub.subscribe(chan)
            except Exception as e:
                logger.debug(f"[sigma-sse] subscribe {request_id} не удался: {e}")
                pubsub = None

        async def _drain_chunks():
            """Слить все накопившиеся чанки из pubsub без блокировки.
            Каждый фрейм агента {chunk,id,kind} -> SSE-событие того же kind."""
            out = []
            if pubsub is None:
                return out
            while True:
                try:
                    msg = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=0.05
                    )
                except Exception:
                    break
                if not msg:
                    break
                try:
                    frame = json.loads(msg["data"])
                except Exception:
                    continue
                kind = frame.get("kind") or "live"
                if kind == "flow":
                    # FlowMap: служебное событие пути запроса, мимо reasoning-state.
                    out.append(_sse_pack("flow", {
                        "request_id": frame.get("request_id"),
                        **(frame.get("flow") or {}),
                    }))
                    continue
                active_requests = await run_status.append_reasoning_chunk(
                    chat_id, frame.get("chunk", ""), kind, frame.get("id")
                )
                out.append(_sse_pack(kind, {
                    "chunk": frame.get("chunk", ""),
                    "id": frame.get("id"),
                    "kind": kind,
                    "active_requests": active_requests,
                }))
            return out

        try:
            while True:
                if await request.is_disconnected():
                    break
                remaining = _SSE_MAX_LIFETIME_SEC - (time.time() - started)
                if remaining <= 0:
                    # Потолок жизни стрима — пусть фронт переподключится.
                    yield _sse_pack("timeout", {"status": "running"})
                    break

                # Пока ждём финал — часто подливаем живые чанки.
                # Короткий таймаут ожидания mark_done, чтобы цикл быстро
                # возвращался к вычитыванию pubsub (живой текст > редкого HB).
                step = 0.25 if pubsub is not None else min(_SSE_HEARTBEAT_SEC, remaining)

                # 1) слить чанки, что уже пришли
                for frame in await _drain_chunks():
                    yield frame

                # 2) подождать финал (коротко)
                ready = await status_store.wait_chat_ready(chat_id, timeout=step)
                if ready:
                    # долить хвост чанков перед финалом
                    for frame in await _drain_chunks():
                        yield frame
                    final = status_store.get_chat_state(chat_id)
                    if final.get("status") == "done":
                        yield _sse_pack("done", final)
                        break
                    if final.get("status") == "idle":
                        yield _sse_pack("idle", final)
                        break
                    continue

                # 3) heartbeat только когда чанков нет (реже, чтоб не шуметь)
                if pubsub is None:
                    yield ": heartbeat\n\n"
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(chan)
                    await pubsub.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        event_gen(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@app.get("/api/chats/user_events/stream")
async def stream_user_events(request: Request):
    """SSE-замена polling'а GET /api/chats/user_events.

    Пушит накопленные и новые пользовательские события (релевантные статьи,
    ошибки парсера, отзывы) по мере появления. Событие в store взводит
    asyncio.Event — стрим просыпается и отдаёт всё, что накопилось.

    События:
      event: user_events  data: {"events":[...]}  — пачка событий
      :heartbeat                                   — keep-alive
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    username = user["username"]

    async def event_gen():
        started = time.time()
        # Сразу отдаём то, что уже накопилось до подключения.
        pending = status_store.pop_user_events(username)
        if pending:
            yield _sse_pack("user_events", {"events": pending})

        while True:
            if await request.is_disconnected():
                break
            remaining = _SSE_MAX_LIFETIME_SEC - (time.time() - started)
            if remaining <= 0:
                break
            wait = min(_SSE_HEARTBEAT_SEC, remaining)
            ready = await status_store.wait_user_ready(username, timeout=wait)
            if ready:
                events = status_store.pop_user_events(username)
                if events:
                    yield _sse_pack("user_events", {"events": events})
            else:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_gen(), media_type="text/event-stream", headers=_SSE_HEADERS
    )


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
        # агент недоступен — не 502, а нейтральный ответ для UI
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


# ========== ОБЩИЙ ЧАТ (group chat, SSE-доставка) ==========
# sigma не держит WS: события комнаты копятся в status_store и отдаются по SSE.
class GroupSendBody(BaseModel):
    message: str
    client_msg_id: str | None = None


class GroupMsgActionBody(BaseModel):
    message_id: int


class GroupEditBody(BaseModel):
    message_id: int
    new_text: str


class GroupPinBody(BaseModel):
    message_id: int
    is_pinned: bool


@app.post("/api/group/{room_uid}/send")
async def group_send(room_uid: str, body: GroupSendBody, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = {
        "room_uid": room_uid,
        "user_id": user["user_id"],
        "username": user.get("username"),
        "message": body.message,
        "client_msg_id": body.client_msg_id,
        "gateway_target": "sigma",
    }
    resp = await app.state.http_client.post(f"{SERVICES['chats']['url']}/group/send", json=payload)
    return resp.json()


@app.get("/api/group/{room_uid}/messages")
async def group_messages(room_uid: str, request: Request, limit: int = 50, offset: int = 0,
                             before_id: int | None = None):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await app.state.http_client.get(
        f"{SERVICES['chats']['url']}/group/{room_uid}/messages",
        params={k: v for k, v in {"limit": limit, "offset": offset, "before_id": before_id,
                                  "viewer_id": user.get("user_id")}.items() if v is not None},
    )
    return resp.json()


@app.get("/api/group/{room_uid}/unread")
async def group_unread(room_uid: str, request: Request):
    user = await get_current_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await app.state.http_client.get(
        f"{SERVICES['chats']['url']}/group/{room_uid}/unread",
        params={"viewer_id": int(user["user_id"])})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="unread unavailable")
    return resp.json()


@app.get("/api/group/{room_uid}/pinned")
async def group_pinned(room_uid: str, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await app.state.http_client.get(f"{SERVICES['chats']['url']}/group/{room_uid}/pinned")
    return resp.json()


@app.get("/api/group/file/{file_id}")
async def group_file(file_id: int, request: Request, download: int = 0):
    """Sigma: только скачивание/потоковая отдача (HTTP Range 206) файла из chats_service (OBS).
    Загрузка через интерфейс не поддерживается (нет upload-эндпоинта)."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    fwd_headers = {}
    if request.headers.get("range"):
        fwd_headers["Range"] = request.headers["range"]
    resp = await app.state.http_client.get(
        f"{SERVICES['chats']['url']}/group/file/{file_id}",
        params={"download": download},
        headers=fwd_headers,
    )
    if resp.status_code == 416:
        out_hdrs = {}
        if resp.headers.get("content-range"):
            out_hdrs["Content-Range"] = resp.headers["content-range"]
        return StreamingResponse(iter([b""]), status_code=416, headers=out_hdrs)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="файл не найден")
    out_headers = {
        "Content-Disposition": resp.headers.get("content-disposition", "inline"),
        "Accept-Ranges": resp.headers.get("accept-ranges", "bytes"),
        "Cache-Control": resp.headers.get("cache-control", "public, max-age=3600"),
    }
    if resp.headers.get("content-range"):
        out_headers["Content-Range"] = resp.headers["content-range"]
    if resp.headers.get("content-length"):
        out_headers["Content-Length"] = resp.headers["content-length"]
    return StreamingResponse(
        iter([resp.content]),
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers=out_headers,
    )


@app.post("/api/group/message/delete")
async def group_delete(body: GroupMsgActionBody, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    is_admin = user.get("username") in ADMIN_USERNAMES
    resp = await app.state.http_client.post(
        f"{SERVICES['chats']['url']}/group/message/delete",
        json={"message_id": body.message_id, "user_id": user["user_id"], "is_admin": is_admin},
    )
    return resp.json()


@app.post("/api/group/message/edit")
async def group_edit(body: GroupEditBody, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await app.state.http_client.post(
        f"{SERVICES['chats']['url']}/group/message/edit",
        json={"message_id": body.message_id, "user_id": user["user_id"], "new_text": body.new_text},
    )
    return resp.json()


@app.post("/api/group/message/pin")
async def group_pin(body: GroupPinBody, request: Request):
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.get("username") not in ADMIN_USERNAMES:
        raise HTTPException(status_code=403, detail="Только админ может закреплять")
    resp = await app.state.http_client.post(
        f"{SERVICES['chats']['url']}/group/message/pin",
        json={"message_id": body.message_id, "is_pinned": body.is_pinned},
    )
    return resp.json()


class ReactionItem(BaseModel):
    message_id: int = Field(gt=0)
    emoji: str = Field(default="", max_length=16)


class ReactionsBody(BaseModel):
    items: list[ReactionItem] = Field(max_length=100)


@app.post("/api/group/reactions")
async def group_reactions(body: ReactionsBody, request: Request):
    """Пачка реакций с фронта -> Redis -> reactions_bus -> chats_service.
    Тот же контракт, что в api_gateway/group_chat_gw.py."""
    user = await get_current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    items = [i.model_dump() for i in body.items]
    via = await reactions_bus.enqueue(int(user["user_id"]), items)
    return {"ok": True, "queued": len(items), "via": via}


class ReadBody(BaseModel):
    last_read_id: int = Field(gt=0)


@app.post("/api/group/{room_uid}/read")
async def group_read(room_uid: str, body: ReadBody, request: Request):
    """Отметка прочтения -> Redis zset -> reads_bus -> chats_service.
    Тот же контракт, что в api_gateway/group_chat_gw.py."""
    user = await get_current_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not reads_bus.valid_room(room_uid):
        raise HTTPException(status_code=400, detail="bad room")
    via = await reads_bus.enqueue(room_uid, int(user["user_id"]), user.get("username") or "",
                                  body.last_read_id)
    return {"ok": True, "via": via}


@app.get("/api/group/message/{message_id}/readers")
async def group_readers(message_id: int, request: Request):
    user = await get_current_user(request)
    if not user or user.get("user_id") is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resp = await app.state.http_client.get(
        f"{SERVICES['chats']['url']}/group/message/{message_id}/readers",
        params={"viewer_id": int(user["user_id"])})
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail="readers unavailable")
    return resp.json()


@app.post("/internal/group_message")
async def internal_group_message(request: Request):
    """Приём события от group-chat-consumer -> в status_store для SSE-рассылки."""
    body = await request.json()
    room_uid = body.get("room_uid", "global")
    status_store.push_group_event(room_uid, body)
    return {"ok": True}


@app.get("/api/group/{room_uid}/stream")
async def stream_group(room_uid: str, request: Request):
    """SSE-стрим событий общего чата. Новый клиент получает только
    свежие события (с текущего курсора); история — через REST /messages."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async def event_gen():
        started = time.time()
        cursor = status_store.group_cursor(room_uid)
        while True:
            if await request.is_disconnected():
                break
            remaining = _SSE_MAX_LIFETIME_SEC - (time.time() - started)
            if remaining <= 0:
                break
            wait = min(_SSE_HEARTBEAT_SEC, remaining)
            ready = await status_store.wait_group_ready(room_uid, timeout=wait)
            if ready:
                events, cursor = status_store.pop_group_events(room_uid, cursor)
                for evt in events:
                    yield _sse_pack("group", evt)
            else:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_gen(), media_type="text/event-stream", headers=_SSE_HEADERS
    )

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


# ── ЗАДАЧНИК (task_gw.py): у sigma нет WS, карточки отдаются в GET /api/chats/{id}/status ──
import task_gw as _task_gw

_SIGMA_TASKS_MAX = 20
_sigma_tasks: dict[str, dict] = {}


_sigma_task_seq = 0                                   # сквозной курсор событий всех чатов
_sigma_task_waiters: dict[str, asyncio.Event] = {}     # chat_id -> будильник SSE-стрима


def _sigma_task_push(chat_id, event):
    global _sigma_task_seq
    if event.get("type") != "task_update":
        return  # agent_status sigma берёт из status_store
    t = dict(event.get("task") or {})
    _sigma_task_seq += 1
    t["_seq"] = _sigma_task_seq
    d = _sigma_tasks.setdefault(str(chat_id), {})
    d.pop(t.get("task_id"), None)
    d[t.get("task_id")] = t
    while len(d) > _SIGMA_TASKS_MAX:
        d.pop(next(iter(d)))
    w = _sigma_task_waiters.get(str(chat_id))
    if w is not None:
        w.set()


def sigma_chat_tasks(chat_id) -> list:
    return list(_sigma_tasks.get(str(chat_id), {}).values())


def _sigma_task_register(request_id, chat_id):
    pr = globals().get("pending_requests")
    if isinstance(pr, dict):
        pr[request_id] = chat_id


async def _sigma_task_mark_running(chat_id, request_id):
    status_store.mark_running(chat_id, request_id)
    await run_status.mark_running(chat_id, request_id)


_task_gw.bind(push=_sigma_task_push, register=_sigma_task_register, mark_running=_sigma_task_mark_running)
app.include_router(_task_gw.router)


@app.get("/api/chats/{chat_id}/tasks/stream")
async def stream_chat_tasks(chat_id: str, request: Request, after: int | None = None):
    """SSE событий задач чата (у sigma нет WS): карточки и ручной триггер, когда агент не работает.

    Без after — первое событие {"hello": true, "cursor": N}: фронт запоминает N как базу и при
    открытии другого чата передаёт after, чтобы получить задачи, завершившиеся, пока чат был закрыт.
      event: tasks  data: {"cursor": N, "tasks": [...]}   :heartbeat — keep-alive
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async def event_gen():
        cur = after
        if cur is None:
            cur = _sigma_task_seq
            yield _sse_pack("tasks", {"hello": True, "cursor": cur, "tasks": []})
        started = time.time()
        while time.time() - started < 600 and not await request.is_disconnected():
            w = _sigma_task_waiters.setdefault(str(chat_id), asyncio.Event())
            w.clear()   # до проверки: push между проверкой и ожиданием не потеряется
            fresh = [t for t in sigma_chat_tasks(chat_id) if (t.get("_seq") or 0) > cur]
            if fresh:
                cur = max(t["_seq"] for t in fresh)
                yield _sse_pack("tasks", {"cursor": cur, "tasks": fresh})
                continue
            try:
                await asyncio.wait_for(w.wait(), timeout=20)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
