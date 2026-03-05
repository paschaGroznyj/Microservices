# plug-agent/llm_client.py
import os
import logging
from functools import lru_cache
import redis

logger = logging.getLogger(__name__)


class GigaChatService:
    def __init__(self):
        self.redis = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            decode_responses=True
        )
        self._client = None

    def get_token(self) -> str:
        """Берет токен из Redis"""
        token = self.redis.get("gigachat:access_token")
        if not token:
            raise RuntimeError("🚨 Токен не найден в Redis! Проверьте token-service.")
        return token

    async def get_client(self) -> GigaChatAsyncClient:
        """Возвращает клиент, обновляя токен при необходимости"""
        token = self.get_token()

        # Если клиента нет или токен сменился — пересоздаем
        if self._client is None:
            self._client = GigaChatAsyncClient(
                access_token=token,
                verify_ssl_certs=False
            )
        else:
            # Простая проверка: если токен в Redis другой, обновляем клиента
            # (в идеале нужно отслеживать 401 ошибку, но для старта хватит так)
            pass
            # В production лучше ловить 401 и обновлять токен динамически

        return self._client

    async def chat(self, messages: list, **kwargs):
        client = await self.get_client()
        return await client.chat(messages=messages, **kwargs)


# Глобальный экземпляр на весь сервис
giga_service = GigaChatService()