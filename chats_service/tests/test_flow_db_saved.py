"""FlowMap: chats_service сообщает фронту, что сообщение сохранено в БД (kind=flow, ev=db_saved)."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app import endpoint_consumer as ec


@pytest.fixture
def consumer():
    c = ec.DBConsumer.__new__(ec.DBConsumer)
    c.gateway_urls = {"main": "http://api_gateway:7000", "sigma": "http://api_gateway_sigma:7010"}
    c.redis = AsyncMock(name="redis")
    return c


def test_main_posts_bot_stream(consumer, mocker):
    post = mocker.patch("app.endpoint_consumer.requests.post")
    asyncio.run(consumer._send_flow({"request_id": "r1", "gateway_target": "main"}, "db_saved", role="user"))
    post.assert_called_once()
    assert post.call_args.args[0] == "http://api_gateway:7000/internal/bot_stream"
    body = post.call_args.kwargs["json"]
    assert body == {"request_id": "r1", "chunk": "", "id": None, "kind": "flow", "flow": {"ev": "db_saved", "role": "user"}}
    consumer.redis.publish.assert_not_awaited()


def test_sigma_goes_to_redis(consumer, mocker):
    post = mocker.patch("app.endpoint_consumer.requests.post")
    asyncio.run(consumer._send_flow({"request_id": "r2", "gateway_target": "sigma"}, "db_saved", role="bot"))
    post.assert_not_called()
    chan, raw = consumer.redis.publish.await_args.args
    assert chan == "stream:r2" and json.loads(raw)["flow"] == {"ev": "db_saved", "role": "bot"}


@pytest.mark.parametrize("payload", [
    {"gateway_target": "main"},                          # нет request_id
    {"request_id": "r", "gateway_target": "telegram"},
    {"request_id": "r", "gateway_target": "mobile"},
    {"request_id": "r", "gateway_target": "max"},
])
def test_skip_non_web(consumer, mocker, payload):
    post = mocker.patch("app.endpoint_consumer.requests.post")
    asyncio.run(consumer._send_flow(payload, "db_saved"))
    post.assert_not_called()
    consumer.redis.publish.assert_not_awaited()


def test_errors_are_swallowed(consumer, mocker):
    mocker.patch("app.endpoint_consumer.requests.post", side_effect=OSError("down"))
    asyncio.run(consumer._send_flow({"request_id": "r1"}, "db_saved"))  # не падает
