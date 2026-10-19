"""Тесты presence.leave — уникальная для sigma функция (beacon на закрытие вкладки).
Остальной presence совпадает с api_gateway и покрыт там.
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


def test_leave_removes_user_from_zset(redis_mock):
    run(presence.leave("sigma", "pavel"))
    redis_mock.zrem.assert_awaited_once_with("online:sigma", "pavel")


def test_leave_empty_username_noop(redis_mock):
    run(presence.leave("sigma", ""))
    redis_mock.zrem.assert_not_awaited()


def test_leave_redis_none_noop(mocker):
    mocker.patch.object(presence, "_redis", None)
    run(presence.leave("sigma", "pavel"))  # не должно падать


def test_leave_swallows_redis_error(redis_mock):
    redis_mock.zrem.side_effect = RuntimeError("boom")
    run(presence.leave("sigma", "pavel"))  # ошибка глотается
