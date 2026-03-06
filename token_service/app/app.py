"""
GigaChat Token Proxy Service
Проксирует запросы к внутреннему API получения токена с кэшированием.

POST /api/v2/oauth → возвращает access_token в формате, ожидаемом langchain-gigachat
"""

import os
import time
import logging
import requests
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# Настройки из конфига
from core.config import (
    PORT_TOKEN_GENERATE_SERVICE,
    IAM_API_KEY,
    INTERNAL_TOKEN_API_URL,
    TOKEN_TTL,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(title="GigaChat Token Proxy", version="1.0")


class TokenCache:
    """In-memory кэш токена с TTL"""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._token: Optional[str] = None
        self._issued_at: float = 0

    def get(self) -> Optional[str]:
        """Возвращает токен, если он ещё валиден"""
        if self._token is None:
            return None
        age = time.time() - self._issued_at
        if age < self.ttl:
            remaining = self.ttl - age
            logger.debug(f"✅ Кэш: токен валиден ещё {remaining:.0f} сек")
            return self._token
        logger.info(f"⏰ Токен истёк (возраст: {age:.0f} сек)")
        return None

    def set(self, token: str):
        """Сохраняет новый токен"""
        self._token = token
        self._issued_at = time.time()
        logger.info(f"🔑 Токен кэширован (TTL: {self.ttl} сек)")

    def is_valid(self) -> bool:
        """Проверяет наличие валидного токена"""
        return self.get() is not None


token_cache = TokenCache(ttl_seconds=TOKEN_TTL)


def fetch_token_from_internal_api() -> str:
    """
    Запрашивает токен у cloud.
    """
    try:
        logger.debug("🔄 Запрос токена у внутреннего API...")
        response = requests.post(
            url=INTERNAL_TOKEN_API_URL,
            headers={"IAM-User-ID": IAM_API_KEY},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("token")
        if not token:
            raise ValueError(f"Нет поля 'token' в ответе: {data}")
        logger.debug(f"✅ Токен получен (первые 10 символов): {token[:10]}...")
        return token
    except requests.RequestException as e:
        logger.error(f"❌ Ошибка запроса к внутреннему API: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга ответа: {e}")
        raise


@app.post("/api/v2/oauth")
def oauth_token(
        authorization: Optional[str] = Header(None),
        scope: str = "GIGACHAT_API_PERS",
):
    """
    Эмулирует стандартный OAuth endpoint GigaChat API.

    Логика:
    1. Если в кэше есть валидный токен → возвращаем его
    2. Если нет → запрашиваем у внутреннего API → кэшируем → возвращаем
    """
    cached = token_cache.get()
    if cached:
        return {
            "access_token": cached,
            "token_type": "Bearer",
            "expires_in": 300  # Сообщаем клиенту, что токен живёт ~5 мин
        }

    logger.info("🔄 Кэш пуст — запрашиваем токен у внутреннего API")
    try:
        new_token = fetch_token_from_internal_api() # Запрос токена внутри cloud
        token_cache.set(new_token)

        return {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300
        }
    except Exception as e:
        logger.error(f"❌ Критическая ошибка получения токена: {e}")
        raise HTTPException(status_code=502, detail=f"Token API error: {str(e)}")


@app.get("/health")
def health():
    """Health check для Kubernetes / мониторинга"""
    return {
        "status": "ok",
        "service": "gigachat-token-proxy",
        "token_cached": token_cache.is_valid(),
        "cache_ttl_sec": TOKEN_TTL
    }


@app.get("/metrics")
def metrics():
    """Простые метрики для Prometheus / отладки"""
    age = time.time() - token_cache._issued_at if token_cache._token else None
    return {
        "token_age_sec": round(age, 1) if age else None,
        "token_valid": token_cache.is_valid(),
        "cache_ttl_sec": TOKEN_TTL,
        "internal_api_url": INTERNAL_TOKEN_API_URL
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=PORT_TOKEN_GENERATE_SERVICE,
        reload=True
    )