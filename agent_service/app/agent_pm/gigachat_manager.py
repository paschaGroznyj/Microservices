"""
GigaChatManager - автоматическая переинициализация при смене токена из tokens.json
Использование: GigaChatManager.get_instance().invoke(messages)
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict
from threading import RLock
from langchain.chat_models.gigachat import GigaChat
from langchain.schema import BaseMessage
import logging
import hashlib

logger = logging.getLogger(__name__)

class GigaChatManager:
    _instance: Optional['GigaChatManager'] = None
    _lock = RLock()
    
    def __new__(cls, token_file: str = 'tokens.json'):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, token_file: str = 'tokens.json'):
        if self._initialized:
            return
            
        self.token_file = Path(token_file)
        self._gigachat = None
        self._last_token_hash = None
        self._initialized = True
        logger.info(f"🟢 GigaChatManager: tokens={self.token_file}")
    
    def _get_credentials(self) -> Optional[str]:
        """Читает GIGA_CHAT_ из tokens.json"""
        if not self.token_file.exists():
            logger.warning(f"tokens.json не найден: {self.token_file}")
            return None
        
        try:
            with open(self.token_file, 'r', encoding='utf-8') as f:
                config: Dict = json.load(f)
            
            # print(config)
            creds = config.get('GIGA_CHAT_')
            # print(creds)
            if not creds:
                logger.warning("GIGA_CHAT_ не найден в tokens.json")
            return creds
            
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка JSON в {self.token_file}: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка чтения {self.token_file}: {e}")
            return None
    
    def _needs_reinit(self) -> bool:
        """Проверяет необходимость переинициализации"""
        creds = self._get_credentials()
        if not creds:
            return False
            
        current_hash = hashlib.md5(creds.encode()).hexdigest()
        print("HASHHHHHHH", current_hash)
        print(self._last_token_hash)
        needs_reinit = current_hash != self._last_token_hash
        if needs_reinit:
            logger.info("🔄 Токен изменился - переинициализация GigaChat")
            self._last_token_hash = current_hash
        return needs_reinit
    
    def _create_gigachat(self) -> GigaChat:
        """Создает новый GigaChat"""
        creds = self._get_credentials()
        if not creds:
            raise ValueError(f"GIGA_CHAT_ отсутствует в {self.token_file}")
        
        return GigaChat(
            access_token=creds,
            verify_ssl_certs=False,
            model="GigaChat-2-Max"
        )
    
    @property
    def gigachat(self) -> GigaChat:
        """Возвращает актуальный GigaChat (авто-реинициализация)"""
        if self._needs_reinit() or self._gigachat is None:
            self._gigachat = self._create_gigachat()
        return self._gigachat
    
    def invoke(self, messages: list[BaseMessage], **kwargs):
        """Простое использование"""
        return self.gigachat.invoke(messages, **kwargs)


# ✅ Глобальный экземпляр
manager = GigaChatManager('agent_pm/tokens.json')
