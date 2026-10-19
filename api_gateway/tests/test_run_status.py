"""Тесты run_status.py — Redis-флаги «агент в работе» / «самоцикл».
Redis-клиент подменяется AsyncMock. async-корутины гоняем через asyncio.run
(без pytest-asyncio — его нет в эталонном окружении репо).
"""
import asyncio
import json
import pytest

import run_status


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def redis_mock(mocker):
    rm = mocker.AsyncMock()
    mocker.patch.object(run_status, "_redis", rm)
    return rm


# ---------- ключи ----------

def test_key_builders():
    assert run_status._key("c1") == "agent:running:c1"
    assert run_status._loop_key("c1") == "agent:loop:c1"


# ---------- mark_running / get_running ----------

def test_mark_running_sets_key_with_ttl(redis_mock):
    run(run_status.mark_running("c1", "req-1", model="coder"))
    redis_mock.set.assert_awaited_once()
    args, kwargs = redis_mock.set.call_args
    assert args[0] == "agent:running:c1"
    payload = json.loads(args[1])
    assert payload["request_id"] == "req-1" and payload["model"] == "coder"
    assert kwargs["ex"] == run_status.RUN_TTL


def test_get_running_parses_json(redis_mock):
    redis_mock.get.return_value = json.dumps({"request_id": "r", "model": "m"})
    res = run(run_status.get_running("c1"))
    assert res["request_id"] == "r"


def test_get_running_none_when_empty(redis_mock):
    redis_mock.get.return_value = None
    assert run(run_status.get_running("c1")) is None


def test_clear_running_deletes_key(redis_mock):
    run(run_status.clear_running("c1"))
    redis_mock.delete.assert_awaited_once_with("agent:running:c1")


# ---------- loop-флаг ----------

def test_mark_loop_sets_with_ttl(redis_mock):
    run(run_status.mark_loop("c1"))
    redis_mock.set.assert_awaited_once_with("agent:loop:c1", "1", ex=run_status.LOOP_TTL)


def test_is_loop_active_true_false(redis_mock):
    redis_mock.exists.return_value = 1
    assert run(run_status.is_loop_active("c1")) is True
    redis_mock.exists.return_value = 0
    assert run(run_status.is_loop_active("c1")) is False


# ---------- безопасные дефолты ----------

def test_none_chat_id_is_noop(redis_mock):
    run(run_status.mark_running(None, "req"))
    run(run_status.mark_loop(None))
    redis_mock.set.assert_not_awaited()
    assert run(run_status.get_running(None)) is None
    assert run(run_status.is_loop_active(None)) is False


def test_redis_none_returns_safe_defaults(mocker):
    mocker.patch.object(run_status, "_redis", None)
    assert run(run_status.get_running("c1")) is None
    assert run(run_status.is_loop_active("c1")) is False
    run(run_status.mark_running("c1", "r"))  # не должно падать


def test_get_running_swallows_redis_error(redis_mock):
    redis_mock.get.side_effect = RuntimeError("boom")
    assert run(run_status.get_running("c1")) is None
