import logging
from pathlib import Path
from contextlib import asynccontextmanager
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, Request, status, APIRouter
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from users_tables import DataBaseUser
from config import SECRET_KEY, ALGORITHM, REDIS_URL
import json
from datetime import datetime, timedelta, timezone
import httpx
from datetime import datetime, timezone

ACCESS_TOKEN_EXPIRE_DAYS = 14
COOKIE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
BASE_DIR = Path(__file__).parent


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

db = DataBaseUser()
service_auth_alive = False

# Redis-кэш персональных настроек (палитра и т.п.). Не критичный путь:
# если redis недоступен — просто ходим в Postgres, запрос не падает.
PREFS_TTL = 600  # секунд
try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    logging.warning("auth: redis init failed: %s", e)
    _redis = None


def _prefs_key(user_id: int) -> str:
    return f"prefs:{user_id}"


async def _prefs_cache_get(user_id: int):
    if _redis is None:
        return None
    try:
        raw = await _redis.get(_prefs_key(user_id))
        return json.loads(raw) if raw else None
    except Exception as e:
        logging.debug("prefs cache get failed: %s", e)
        return None


async def _prefs_cache_set(user_id: int, data: dict):
    if _redis is None:
        return
    try:
        await _redis.set(_prefs_key(user_id), json.dumps(data), ex=PREFS_TTL)
    except Exception as e:
        logging.debug("prefs cache set failed: %s", e)
router = APIRouter(prefix='/auth', tags=['Auth'])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Ограничения длины логина/пароля.
# USERNAME_MAX=50 жёстко завязан на схему БД: users.username VARCHAR(50).
# Ввод длиннее упирался в Postgres (value too long) -> 500; теперь режем
# на уровне приложения и отдаём аккуратный 400.
# PASSWORD_MAX=72 — предел bcrypt: всё после 72 байт тихо игнорируется,
# поэтому длиннее принимать нельзя (иначе часть пароля не проверяется).
USERNAME_MIN = 3
USERNAME_MAX = 50
PASSWORD_MIN = 6
PASSWORD_MAX = 72


def validate_credentials(username: str, password: str, *, check_password: bool = True):
    """Проверка длины логина/пароля. Кидает HTTPException 400 при нарушении.

    check_password=False — для логина (не навязываем правила длины старым
    паролям, проверяем только логин, чтобы длинный username не ронял БД).
    """
    uname = (username or "").strip()
    if not (USERNAME_MIN <= len(uname) <= USERNAME_MAX):
        raise HTTPException(
            status_code=400,
            detail=f"Логин должен быть от {USERNAME_MIN} до {USERNAME_MAX} символов"
        )
    if check_password:
        # bcrypt считает в байтах — проверяем длину в байтах UTF-8.
        pwd_bytes = len((password or "").encode("utf-8"))
        if pwd_bytes < PASSWORD_MIN:
            raise HTTPException(
                status_code=400,
                detail=f"Пароль должен быть не короче {PASSWORD_MIN} символов"
            )
        if pwd_bytes > PASSWORD_MAX:
            raise HTTPException(
                status_code=400,
                detail=f"Пароль должен быть не длиннее {PASSWORD_MAX} символов"
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global service_auth_alive
    # Startup
    service_auth_alive = True
    await db.init_db_pool()
    yield
    # Shutdown
    service_auth_alive = False
    await db.pool.close()

app = FastAPI(title="Auth Service", lifespan=lifespan)
# @app.on_event("startup")
# async def startup():
#     await db.init_db_pool()
#
# @app.on_event("shutdown")
# async def shutdown():
#     await db.pool.close()
# app.mount("/static", StaticFiles(directory="static"), name="static")
# templates = Jinja2Templates(directory=BASE_DIR / "templates")

def get_auth_data():
    return {"secret_key": SECRET_KEY, "algorithm": ALGORITHM}

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    auth_data = get_auth_data()
    encode_jwt = jwt.encode(to_encode, auth_data['secret_key'], algorithm=auth_data['algorithm'])
    return encode_jwt


def get_token(request: Request):
    token = request.cookies.get('users_access_token')
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token not found')
    return token



@app.post("/register")
async def register(form_data: OAuth2PasswordRequestForm = Depends()):
    validate_credentials(form_data.username, form_data.password)
    user_id = await db.add_user(form_data.username, form_data.password)
    if user_id:
        return {"msg": f"Пользователь {form_data.username} зарегистрирован", "user_id": user_id}
    raise HTTPException(status_code=400, detail="Пользователь уже существует")


@app.post("/login")
async def login(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    # На логине проверяем только длину логина (чтобы >50 символов не упёрлись
    # в VARCHAR(50) и не дали 500). Правила длины пароля на логин не навязываем.
    validate_credentials(form_data.username, form_data.password, check_password=False)
    # Проверка пользователя
    user_id = await db.check_user_password(form_data.username, form_data.password, back_user_id=True)
    if not user_id:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    access_token = create_access_token({
        "sub": form_data.username,
        "user_id": user_id["id"]
    })
    response.set_cookie(
        key="users_access_token",
        value=access_token,
        httponly=True,
        max_age=COOKIE_MAX_AGE_SECONDS
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.post("/guest-login")
async def guest_login(response: Response):
    """Вход без регистрации — создаём гостевой аккаунт"""
    user_id, username = await db.add_guest_user()
    if not user_id:
        raise HTTPException(status_code=500, detail="Не удалось создать гостевого пользователя")

    access_token = create_access_token({
        "sub": username,
        "user_id": user_id
    })
    response.set_cookie(
        key="users_access_token",
        value=access_token,
        httponly=True,
        max_age=COOKIE_MAX_AGE_SECONDS
    )
    return {"access_token": access_token, "token_type": "bearer", "user_id": user_id}


@app.post("/authentik")
async def guest_login(request: Request):
    """Вход через никнейм authentik"""
    data = await request.json()
    logging.info(f"Прилетел username: {data['username']}")
    # Внешняя аутентификация: пароля нет, пишем dummy-хеш.
    # Идемпотентно — при повторном входе вернётся id существующего юзера.
    user_id = await db.add_user(data["username"], external=True, source="authentik")
    return {"user_id": user_id}
    

@app.post("/register-and-login")
async def register_and_login(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    """Регистрация + автоматический логин (оптимизированный обработчик)"""
    validate_credentials(form_data.username, form_data.password)
    user_id = await db.add_user(form_data.username, form_data.password)
    if not user_id:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")

    access_token = create_access_token({
        "sub": form_data.username,
        "user_id": user_id
    })
    response.set_cookie(
        key="users_access_token",
        value=access_token,
        httponly=True,
        max_age=COOKIE_MAX_AGE_SECONDS
    )
    return {"access_token": access_token, "token_type": "bearer", "user_id": user_id}

@app.post("/logout")
async def logout_user(response: Response):
    response.delete_cookie(key="users_access_token")
    return {'message': 'Пользователь успешно вышел из системы'}

@app.post("/profile/me")
async def me(request: Request):
    data = await request.json()
    username = data['username']
    logging.info(f"profile_me: {username}")
    result = await db.get_user_stats(username=username)
    return result

@app.post("/profile/token")
async def me(request: Request):
    data = await request.json()
    username = data['username']
    token = data['token']
    result = await db.set_scholar_token(username=username, token=token)
    return result

@app.post("/profile/preferences/get")
async def preferences_get(request: Request):
    """Персональные настройки юзера по user_id. Redis-cache → Postgres."""
    data = await request.json()
    user_id = data["user_id"]
    cached = await _prefs_cache_get(user_id)
    if cached is not None:
        return cached
    result = await db.get_user_preferences(user_id=user_id)
    await _prefs_cache_set(user_id, result)
    return result


@app.post("/profile/preferences/set")
async def preferences_set(request: Request):
    """UPSERT палитры/extra по user_id + инвалидация кэша."""
    data = await request.json()
    user_id = data["user_id"]
    palette = data.get("palette")  # None => не трогаем
    extra = data.get("extra")
    result = await db.upsert_user_preferences(
        user_id=user_id, palette=palette, extra=extra
    )
    await _prefs_cache_set(user_id, result)
    return result



@app.post("/whatsnew/pending")
async def whatsnew_pending(request: Request):
    """
    Что показать юзеру при входе: онбординг-демо (для новых) и непросмотренные
    published-обновления. Аутентификацию делает gateway (get_current_user по
    куке), сюда прилетает уже готовый user_id в теле — как в /profile/me.
    """
    data = await request.json()
    user_id = data.get("user_id")
    # if user_id == 56:
    #     return {"demo": True, "updates": []}
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id обязателен")
    result = await db.get_pending_whatsnew(user_id=int(user_id))
    return result


@app.post("/whatsnew/demo/seen")
async def whatsnew_demo_seen(request: Request):
    """Юзер закрыл онбординг-демо — больше не показываем."""
    data = await request.json()
    user_id = data.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=400, detail="user_id обязателен")
    await db.mark_demo_seen(user_id=int(user_id))
    return {"status": "ok"}


@app.post("/whatsnew/updates/seen")
async def whatsnew_update_seen(request: Request):
    """Юзер закрыл обновление(я) — помечаем как просмотренные в user_seen_updates."""
    data = await request.json()
    user_id = data.get("user_id")
    update_id = data.get("update_id")
    update_ids = data.get("update_ids")
    ids = []
    if isinstance(update_ids, list):
        ids.extend(int(x) for x in update_ids if x is not None)
    if update_id is not None:
        ids.append(int(update_id))
    if user_id is None or not ids:
        raise HTTPException(status_code=400, detail="user_id и update_id/update_ids обязательны")
    await db.mark_updates_seen(user_id=int(user_id), update_ids=ids)
    return {"status": "ok", "seen_count": len(set(ids))}


@app.get("/health")
async def health():
    status = "ok" if service_auth_alive else "error"
    return {"service": "auth_service", "status": status}

# import uvicorn
# from utils.config import PORT_AUTH_SERVICE
#
# if __name__ == "__main__":
#     uvicorn.run(app, host="127.0.0.1", port=int(PORT_AUTH_SERVICE), reload=True)

# uvicorn auth_service.app:app --host 127.0.0.1 --port 8000 --reload
