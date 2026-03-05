"""
GigaChatManager - автоматическая переинициализация при смене токена из Redis
Использование: GigaChatManager.get_instance().invoke(messages)
"""
import os
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, List
from threading import RLock

import redis
from langchain.chat_models.gigachat import GigaChat
from langchain.schema import BaseMessage
from core.config import REDIS_HOST, REDIS_PORT, REDIS_KEY

logger = logging.getLogger(__name__)


class GigaChatManager:
    _instance: Optional['GigaChatManager'] = None
    _lock = RLock()

    REDIS_TIMEOUT = 2  # секунды на подключение/операцию

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._redis_client: Optional[redis.Redis] = None
        self._gigachat: Optional[GigaChat] = None
        self._last_token_hash: Optional[str] = None
        self._initialized = True

        logger.info(f"🟢 GigaChatManager: Redis={REDIS_HOST}:{REDIS_PORT}, key={REDIS_KEY}")

    def _get_redis_client(self) -> Optional[redis.Redis]:
        """Возвращает Redis-клиент с кэшированием"""
        if self._redis_client is None:
            try:
                self._redis_client = redis.Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    decode_responses=True,
                    socket_connect_timeout=self.REDIS_TIMEOUT,
                    socket_timeout=self.REDIS_TIMEOUT,
                    health_check_interval=30
                )
                # Проверка подключения
                self._redis_client.ping()
                logger.debug("✅ Подключено к Redis")
            except redis.ConnectionError as e:
                logger.warning(f"⚠️ Не удалось подключиться к Redis: {e}")
                self._redis_client = None
        return self._redis_client

    def _get_token_from_redis(self) -> Optional[str]:
        """Читает токен из Redis"""
        client = self._get_redis_client()
        if not client:
            return None

        try:
            token = client.get(REDIS_KEY)
            if token:
                logger.debug(f"🔑 Токен получен из Redis (хэш: {hashlib.md5(token.encode()).hexdigest()[:8]}...)")
                return token
            else:
                logger.warning(f"⚠️ Ключ {REDIS_KEY} не найден в Redis")
                return None
        except redis.RedisError as e:
            logger.error(f"❌ Ошибка чтения Redis: {e}")
            return None

    def _get_token(self) -> Optional[str]:
        """Основной метод получения токена: Redis"""
        token = self._get_token_from_redis()
        if token:
            return token

        logger.error("🚨 Не удалось получить токен: Redis недоступен и fallback не настроен")
        return None

    def _needs_reinit(self) -> bool:
        """Проверяет, нужно ли переинициализировать клиент"""
        token = self._get_token()
        if not token:
            return False

        current_hash = hashlib.md5(token.encode()).hexdigest()

        if current_hash != self._last_token_hash:
            logger.info(f"🔄 Токен изменился - переинициализация (старый: {self._last_token_hash[:8] if self._last_token_hash else 'None'}, новый: {current_hash[:8]})")
            self._last_token_hash = current_hash
            return True

        return False

    def _create_gigachat(self, token: str) -> GigaChat:
        """Создает новый экземпляр GigaChat"""
        return GigaChat(
            access_token=token,
            verify_ssl_certs=False,
            model="GigaChat-2-Max"
        )

    @property
    def gigachat(self) -> GigaChat:
        """Возвращает актуальный GigaChat-клиент с авто-реинициализацией"""
        token = self._get_token()
        if not token:
            raise RuntimeError("🚨 Не удалось получить access token для GigaChat")

        if self._needs_reinit() or self._gigachat is None:
            self._gigachat = self._create_gigachat(token)

        return self._gigachat

    def invoke(self, messages: List[BaseMessage], **kwargs):
        """Основной метод для вызова модели"""
        return self.gigachat.invoke(messages, **kwargs)

    def health_check(self) -> Dict[str, bool]:
        """Проверка здоровья: Redis + токен"""
        result = {
            "redis_connected": False,
            "token_available": False,
            "client_ready": False
        }

        # Проверка Redis
        client = self._get_redis_client()
        if client:
            try:
                client.ping()
                result["redis_connected"] = True
            except:
                pass

        # Проверка токена
        token = self._get_token()
        result["token_available"] = token is not None

        # Проверка клиента
        try:
            _ = self.gigachat
            result["client_ready"] = True
        except:
            pass

        return result


def get_gigachat_manager() -> GigaChatManager:
    """Factory-функция для получения менеджера"""
    return GigaChatManager()


# Для обратной совместимости
manager = GigaChatManager()
