"""HTTP-backed helpers (chats_service) with httpx mocked via mocker."""
import asyncio

import pytest


def run(coro):
    return asyncio.run(coro)


class _Resp:
    def __init__(self, data):
        self._data = data
    def raise_for_status(self):
        return None
    def json(self):
        return self._data


class _Client:
    """Async context manager faking httpx.AsyncClient with canned get()."""
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, *a, **k):
        if self._exc:
            raise self._exc
        return self._resp


def test_fetch_allowed_from_chats_ok(bot, mocker):
    mocker.patch.object(bot.httpx, "AsyncClient",
                        return_value=_Client(_Resp({"chat_ids": ["1", "2"]})))
    out = run(bot._fetch_allowed_from_chats())
    assert out == ["1", "2"]


def test_fetch_allowed_returns_none_on_error(bot, mocker):
    mocker.patch.object(bot.httpx, "AsyncClient",
                        return_value=_Client(exc=RuntimeError("down")))
    assert run(bot._fetch_allowed_from_chats()) is None


def test_get_chat_settings_ok(bot, mocker):
    settings = {"agent_model": "gemini", "trigger_mode": "mention"}
    mocker.patch.object(bot.httpx, "AsyncClient",
                        return_value=_Client(_Resp({"settings": settings})))
    out = run(bot.get_chat_settings(5))
    assert out == settings


def test_get_chat_settings_defaults_on_error(bot, mocker):
    mocker.patch.object(bot.httpx, "AsyncClient",
                        return_value=_Client(exc=RuntimeError("boom")))
    out = run(bot.get_chat_settings(5))
    assert out["trigger_mode"] == "orchestrator"
    assert out["agent_model"] is None


def test_get_chat_settings_empty_settings_fallback(bot, mocker):
    mocker.patch.object(bot.httpx, "AsyncClient",
                        return_value=_Client(_Resp({"settings": None})))
    out = run(bot.get_chat_settings(5))
    assert out["trigger_mode"] == "orchestrator"


def test_is_allowed_hits_redis_cache(bot, fake_redis):
    fake_redis.exists.return_value = True
    fake_redis.sismember.return_value = True
    assert run(bot.is_allowed(100)) is True
    fake_redis.sismember.assert_awaited_once_with("tg:allowed_chats", "100")


def test_is_allowed_fallback_fetches_and_caches(bot, fake_redis, mocker):
    fake_redis.exists.return_value = False
    mocker.patch.object(bot, "_fetch_allowed_from_chats",
                        new=mocker.AsyncMock(return_value=["100", "200"]))
    assert run(bot.is_allowed(100)) is True
    fake_redis.pipeline.return_value.execute.assert_awaited_once()


def test_is_allowed_fail_closed_when_service_down(bot, fake_redis, mocker):
    fake_redis.exists.return_value = False
    mocker.patch.object(bot, "_fetch_allowed_from_chats",
                        new=mocker.AsyncMock(return_value=None))
    assert run(bot.is_allowed(100)) is False
