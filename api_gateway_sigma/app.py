# gateway/app.py  (SIGMA — вариант без WebSocket, GET-polling + собственный Kafka-consumer)
from fastapi import FastAPI, HTTPException, Request, Depends, Form, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
    PORT_GATEWAY_PORT, PORT_PARSERS_SERVICE,
    ADMIN_USERNAMES, KAFKA_BROKER
)

from sigma_status_store import status_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up API Gateway SIGMA...")

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
    logger.info("Shutting down API Gateway SIGMA...")
    await app.state.http_client.aclose()
app = FastAPI(title="API Gateway SIGMA", lifespan=lifespan)
# app = FastAPI(title="API Gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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
                max_age=3600,
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
@app.post("/api/chats/send_message")
async def send_message(request: Request):
    """Отправка сообщения (для SPA)"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Получаем данные из запроса
    data = await request.json()
    logging.info(data)
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
    logger.info(f"Что летит при отправке сообщений {data['chat_id']}, {data['message']}")

    # SIGMA: WS-эхо убрано. Помечаем в store, что для этого чата ожидается
    # ответ бота (status=running). Фронт сразу оптимистично отрисует своё
    # сообщение и начнёт поллить GET /api/chats/{id}/status.
    status_store.mark_running(data["chat_id"], request_id)
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
async def poll_chat_status(chat_id: int, request: Request):
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

    state = status_store.get_chat_state(chat_id)
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


@app.get("/api/chats/{chat_id}/image/{message_id}/{file_name}")
async def get_chat_db_image(chat_id: int, message_id: str, file_name: str, request: Request, image_key: str | None = None):
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
            params={"message_id": message_id, "image_key": image_key, "file_name": file_name},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {e}")

    if agent_resp.status_code != 200:
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
async def download_chat_file(chat_id: int, message_id: str, file_name: str, request: Request):
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

    # 2. Стримим файл с agent_service
    try:
        agent_resp = await app.state.http_client.get(
            f"{SERVICES['agent_service']['url']}/web/file",
            params={"path": path},
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Agent service unreachable: {e}")

    if agent_resp.status_code != 200:
        raise HTTPException(status_code=agent_resp.status_code, detail="Failed to fetch file")

    return Response(
        content=agent_resp.content,
        media_type=agent_resp.headers.get("content-type", "application/octet-stream"),
        headers={
            "Content-Disposition": agent_resp.headers.get(
                "content-disposition", f'attachment; filename="{file_name}"'
            )
        },
    )

@app.post("/api/chats/{chat_id}/delete")
async def delete_chat(chat_id: int, request: Request):
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
async def rename_chat(chat_id: int, request: Request):
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
async def favorite_chat(chat_id: int, request: Request):
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


@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: int, request: Request):
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

# ========= Найти чат по типу (ouroboros) ========
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
            # SIGMA: рассылку нового отзыва по WS заменяем на broadcast-событие
            # в store — все онлайн-клиенты заберут его через GET /api/chats/user_events.
            status_store.broadcast_user_event({
                "type": "feedback_new",
                "feedback": body
            })
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


@app.get("/api/providers/status")
async def get_providers_status(request: Request):
    """Доступность LLM-провайдеров под моделями тоггла (для ModelPicker).

    Проксирует /internal/providers_status agent_service. При недоступности
    самого агента отдаёт мягкий 200 с providers=null, чтобы фронт показал
    нейтральное состояние, а не рушился."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    agent_url = SERVICES['agent_service']['url']
    try:
        resp = await app.state.http_client.get(
            f"{agent_url}/internal/providers_status",
            timeout=15.0
        )
        return resp.json()
    except httpx.RequestError:
        # агент недоступен — не 502, а нейтральный ответ для UI
        return {"providers": None, "cached_at": None}

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
