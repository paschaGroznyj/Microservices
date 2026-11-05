"""Тесты presence.py — онлайн-присутствие через Redis ZSET.
Redis и pipeline подменяются моками; сеть не дёргаем.
"""
import asyncio
import pytest

import presence


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def redis_mock(mocker):
    rm = mocker.AsyncMock()
    mocker.patch.object(presence, "_redis", rm)
    return rm


# ---------- ключ пика по суткам ----------

def test_day_key_format():
    # 2021-01-01 00:00:00 UTC
    key = presence._day_key("main", 1609459200)
    assert key == "peak:main:20210101"


# ---------- count_online ----------

def test_count_online_cleans_and_counts(redis_mock):
    redis_mock.zcount.return_value = 3
    res = run(presence.count_online("main"))
    assert res == 3
    redis_mock.zremrangebyscore.assert_awaited_once()
    redis_mock.zcount.assert_awaited_once()


def test_count_online_redis_none_zero(mocker):
    mocker.patch.object(presence, "_redis", None)
    assert run(presence.count_online("main")) == 0


def test_count_online_error_returns_zero(redis_mock):
    redis_mock.zcount.side_effect = RuntimeError("boom")
    assert run(presence.count_online("main")) == 0


# ---------- online_usernames ----------

def test_online_usernames_returns_list(redis_mock):
    redis_mock.zrangebyscore.return_value = ["a", "b"]
    assert run(presence.online_usernames("main")) == ["a", "b"]


def test_online_usernames_empty_when_no_redis(mocker):
    mocker.patch.object(presence, "_redis", None)
    assert run(presence.online_usernames("main")) == []


# ---------- peak_today ----------

def test_peak_today_parses_int(redis_mock):
    redis_mock.get.return_value = "7"
    assert run(presence.peak_today("main")) == 7


def test_peak_today_zero_when_absent(redis_mock):
    redis_mock.get.return_value = None
    assert run(presence.peak_today("main")) == 0


# ---------- touch ----------

def test_touch_empty_username_noop(redis_mock):
    run(presence.touch("main", ""))
    redis_mock.pipeline.assert_not_called()


def test_touch_updates_zset_and_peak(mocker):
    # _redis.pipeline() вызывается СИНХРОННО -> сам _redis делаем MagicMock,
    # а async-методы (get/set/expire) — AsyncMock поштучно.
    rm = mocker.MagicMock()
    rm.get = mocker.AsyncMock(return_value=None)  # пика ещё нет -> выставить
    rm.set = mocker.AsyncMock()
    rm.expire = mocker.AsyncMock()
    pipe = mocker.MagicMock()
    pipe.execute = mocker.AsyncMock(return_value=[1, 0, 5])  # online=5
    rm.pipeline.return_value = pipe
    mocker.patch.object(presence, "_redis", rm)
    run(presence.touch("main", "pavel"))
    pipe.zadd.assert_called_once()
    pipe.execute.assert_awaited_once()
    rm.set.assert_awaited_once()  # новый пик


def test_touch_redis_none_noop(mocker):
    mocker.patch.object(presence, "_redis", None)
    run(presence.touch("main", "pavel"))  # не должно падать
