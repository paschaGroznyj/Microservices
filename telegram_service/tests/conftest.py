"""Shared fixtures for telegram_service tests.

bot.py reads TELEGRAM_BOT_TOKEN at import time and imports python-telegram-bot,
so we must set env BEFORE importing app.bot. A fake async Redis is provided for
the Redis-backed helpers (pipeline() is SYNC, returns an object whose execute()
is awaitable; command methods are AsyncMock).
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("CHATS_SERVICE_URL", "http://chats.test")
os.environ.setdefault("AGENT_SERVICE_URL", "http://agent.test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


def make_fake_redis():
    """A MagicMock redis whose command coroutines are AsyncMock.

    pipeline() is called synchronously in bot.py and then awaited on execute(),
    so pipeline() returns a MagicMock with an async execute().
    """
    r = MagicMock(name="redis")
    for m in ("get", "set", "delete", "exists", "sismember", "sadd",
              "expire", "rpush", "ltrim", "lrange", "hget", "hset",
              "hgetall", "zadd", "zrem"):
        setattr(r, m, AsyncMock(name=m))
    pipe = MagicMock(name="pipeline")
    for m in ("delete", "sadd", "expire", "rpush", "ltrim", "set", "hset"):
        setattr(pipe, m, MagicMock(return_value=pipe))
    pipe.execute = AsyncMock(name="pipe.execute")
    r.pipeline = MagicMock(return_value=pipe)
    return r


@pytest.fixture
def fake_redis():
    return make_fake_redis()


@pytest.fixture
def bot(fake_redis, monkeypatch):
    """Import app.bot and wire get_redis() to the fake redis."""
    import app.bot as b
    monkeypatch.setattr(b, "_redis", fake_redis, raising=False)

    async def _get_redis():
        return fake_redis
    monkeypatch.setattr(b, "get_redis", _get_redis)
    return b
