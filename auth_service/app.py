import logging
from pathlib import Path
from contextlib import asynccontextmanager
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, Request, status, APIRouter
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from users_tables import DataBaseUser
from config import SECRET_KEY, ALGORITHM
from datetime import datetime, timedelta, timezone

ACCESS_TOKEN_EXPIRE_MINUTES = 30
BASE_DIR = Path(__file__).parent


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

db = DataBaseUser()
service_auth_alive = False
router = APIRouter(prefix='/auth', tags=['Auth'])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

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
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    to_encode.update({"exp": expire})
    auth_data = get_auth_data()
    encode_jwt = jwt.encode(to_encode, auth_data['secret_key'], algorithm=auth_data['algorithm'])
    return encode_jwt


def get_token(request: Request):
    token = request.cookies.get('users_access_token')
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token not found')
    return token


async def get_current_user(token: str = Depends(get_token)):
    try:
        auth_data = get_auth_data()
        payload = jwt.decode(token, auth_data['secret_key'], algorithms=[auth_data['algorithm']])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Токен не валидный!')

    expire = payload.get('exp')
    expire_time = datetime.fromtimestamp(int(expire), tz=timezone.utc)
    if (not expire) or (expire_time < datetime.now(timezone.utc)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Токен истек')

    user_name = payload.get('sub')
    if not user_name:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Не найден ID пользователя')

    user = await db.check_user_password(username=user_name)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')

    return user



@app.post("/register")
async def register(form_data: OAuth2PasswordRequestForm = Depends()):
    user_id = await db.add_user(form_data.username, form_data.password)
    if user_id:
        return {"msg": f"Пользователь {form_data.username} зарегистрирован", "user_id": user_id}
    raise HTTPException(status_code=400, detail="Пользователь уже существует")


@app.post("/login")
async def login(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    # Проверка пользователя
    user_id = await db.check_user_password(form_data.username, form_data.password, back_user_id=True)
    if not user_id:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    access_token = create_access_token({
        "sub": form_data.username,
        "user_id": user_id["id"]
    })
    response.set_cookie(key="users_access_token", value=access_token, httponly=True)
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
        max_age=3600
    )
    return {"access_token": access_token, "token_type": "bearer", "user_id": user_id}

@app.post("/register-and-login")
async def register_and_login(response: Response, form_data: OAuth2PasswordRequestForm = Depends()):
    """Регистрация + автоматический логин (оптимизированный обработчик)"""
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
        max_age=3600
    )
    return {"access_token": access_token, "token_type": "bearer", "user_id": user_id}

@app.post("/logout")
async def logout_user(response: Response):
    response.delete_cookie(key="users_access_token")
    return {'message': 'Пользователь успешно вышел из системы'}

# @app.get("/me")
# async def read_users_me(token: str = Depends(oauth2_scheme)):
#     try:
#         payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
#         username: str = payload.get("sub")
#         if username is None:
#             raise HTTPException(status_code=401, detail="Неверный токен")
#     except JWTError:
#         raise HTTPException(status_code=401, detail="Неверный токен")
#
#     return {"username": username}
@app.get("/me")
async def get_current_user_info(request: Request):
    """Получение информации о текущем пользователе"""
    token = request.cookies.get("users_access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Token not found")

    try:
        auth_data = get_auth_data()
        payload = jwt.decode(token, auth_data['secret_key'], algorithms=[auth_data['algorithm']])

        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Получаем пользователя из БД
        user = await db.check_user_password(username=username)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "username": user["username"],
            "user_id": user["id"],  # предположим, что есть поле id
            "authenticated": True
        }

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


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
