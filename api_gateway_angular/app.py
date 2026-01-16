from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import httpx
import logging
from utils.config import HOST_LLM_CONSUMER, HOST_DB_CONSUMER, HOST_AUTH_SERVICE, HOST_CHATS_SERVICE, HOST_CRB_SERVICE
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi import Form

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="API Gateway")

# Настройки маршрутов
SERVICES = {
    "auth": f"http://localhost:{HOST_AUTH_SERVICE}",
    "chats": f"http://localhost:{HOST_CHATS_SERVICE}",
    "llm_bot": f"http://localhost:{HOST_LLM_CONSUMER}",
    "db_consumer": f"http://localhost:{HOST_DB_CONSUMER}",
    "crb": f"http://localhost:{HOST_CRB_SERVICE}"
}

class ChatMessage(BaseModel):
    user_id: int
    message: str
    chat_id: int

#
# @app.post("/auth/{path:path}") # Пока не доделал
# async def auth_proxy(request: Request, path: str):
#     async with httpx.AsyncClient() as client:
#         url = f"{SERVICES['auth']}/{path}"
#         response = await client.request(
#             method=request.method,
#             url=url,
#             headers=dict(request.headers),
#             content=await request.body(),
#             timeout=30.0
#         )
#         return response.json()



@app.post("/auth/register")
async def register(
    username: str = Form(...),
    password: str = Form(...)
):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SERVICES['auth']}/register",
            data={"username": username, "password": password}
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type="application/json"
        )

@app.post("/auth/login")
async def login_gateway(
    username: str = Form(...),
    password: str = Form(...)
):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{SERVICES['auth']}/login",
                data={"username": username, "password": password},  # form-data
                timeout=10.0
            )
            # Возвращаем оригинальный ответ
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type="application/json"
            )
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Auth service unreachable: {str(e)}")
@app.post("/chats/send_message")
async def send_message(chat: ChatMessage):
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{SERVICES['chats']}/send_message", json=chat.model_dump())
        return resp.json()

@app.get("/chats/chat/{chat_id}/messages")
async def get_messages(chat_id: int, limit: int = 50, offset: int = 0):
    params = {"limit": limit, "offset": offset}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{SERVICES['chats']}/chat/{chat_id}/messages", params=params)
        return resp.json()

@app.get("/health")
async def gateway_health():
    async with httpx.AsyncClient() as client:
        auth_ok = False
        chats_ok = False
        llm_ok = False
        db_ok = False
        cbr_ok = False
        try:
            await client.get(f"http://localhost:{HOST_AUTH_SERVICE}/health", timeout=2.0)
            auth_ok = True
        except:
            pass
        try:
            await client.get(f"http://localhost:{HOST_CHATS_SERVICE}/health", timeout=2.0)
            chats_ok = True
        except:
            pass
        try:
            await client.get(f"http://localhost:{HOST_LLM_CONSUMER}/health", timeout=2.0)
            llm_ok = True
        except:
            pass
        try:
            await client.get(f"http://localhost:{HOST_DB_CONSUMER}/health", timeout=2.0)
            db_ok = True
        except:
            pass
        try:
            await client.get(f"http://localhost:{HOST_CRB_SERVICE}/health", timeout=2.0)
            cbr_ok = True
        except:
            pass

        return {
            "gateway": "ok",
            "auth_service": "ok" if auth_ok else "down",
            "chats_service": "ok" if chats_ok else "down",
            "llm_service": "ok" if llm_ok else "down",
            "db_service": "ok" if db_ok else "down",
            "cbr_service": "ok" if cbr_ok else "down"
        }


@app.get("/")
async def root():
    return {"gateway": "running", "services": SERVICES}