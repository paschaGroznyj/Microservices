"""Общие фикстуры auth_service.

Цель — импортировать app.py БЕЗ реальной БД, redis и секретов из окружения.
Делаем это до импорта app: подкидываем env и мокаем DataBaseUser/redis,
чтобы глобальный `db = DataBaseUser()` и init redis не трогали внешний мир.
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT))

# --- env до импорта config/app (иначе SECRET_KEY/ALGORITHM = None) ---
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("PORT_PM_AGENT", "8010")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

# FERNET_KEY обязателен: users_tables на импорте делает Fernet(FERNET_KEY).
if not os.environ.get("FERNET_KEY"):
    from cryptography.fernet import Fernet
    os.environ["FERNET_KEY"] = Fernet.generate_key().decode()


# --- мокаем users_tables.DataBaseUser до импорта app ---
import users_tables as _ut


class _FakeDB:
    """Заглушка DataBaseUser: все методы — AsyncMock, никаких соединений."""
    def __init__(self, *a, **kw):
        self.init_db_pool = AsyncMock()
        self.pool = MagicMock()
        self.pool.close = AsyncMock()
        self.add_user = AsyncMock()
        self.add_guest_user = AsyncMock()
        self.check_user_password = AsyncMock()
        self.get_user_stats = AsyncMock()
        self.set_scholar_token = AsyncMock()
        self.get_user_preferences = AsyncMock()
        self.upsert_user_preferences = AsyncMock()
        self.save_feedback = AsyncMock()
        self.get_feedbacks = AsyncMock()
        self.get_pending_whatsnew = AsyncMock()
        self.get_app_update_body = AsyncMock()
        self.mark_demo_seen = AsyncMock()
        self.mark_update_seen = AsyncMock()


_ut.DataBaseUser = _FakeDB

# redis.asyncio.from_url не должен лезть в сеть на импорте app
try:
    import redis.asyncio as _aioredis
    _aioredis.from_url = lambda *a, **kw: MagicMock()
except Exception:
    pass


@pytest.fixture()
def app_module():
    """Импортирует (свежий) модуль app с уже подменёнными БД/redis."""
    import importlib
    import app as app_mod
    importlib.reload(app_mod)
    return app_mod


@pytest.fixture()
def fake_db(app_module):
    """Глобальный db внутри app — экземпляр _FakeDB."""
    return app_module.db


@pytest.fixture()
def client(app_module):
    """TestClient без запуска lifespan (init_db_pool замокан в любом случае)."""
    from fastapi.testclient import TestClient
    return TestClient(app_module.app)
