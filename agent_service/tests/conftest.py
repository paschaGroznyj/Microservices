"""Fixtures for agent_service tests.

pythonpath=["app"] lets us import agent.* modules directly. A fake async Redis
is provided for mode_store (redis.asyncio client).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def fake_redis():
    r = MagicMock(name="redis")
    for m in ("get", "set", "delete", "rpush", "expire"):
        setattr(r, m, AsyncMock(name=m))
    return r
