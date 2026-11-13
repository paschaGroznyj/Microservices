"""Тесты GroupDBConsumer.process / _broadcast.
Мокаем group_db (БД) и httpx (HTTP на гейтвеи). Kafka не задействован.
"""
import asyncio
from unittest.mock import AsyncMock
import pytest

from app.group_endpoint_consumer import GroupDBConsumer


class FakeGroupDB:
    def __init__(self):
        self.added = []
        self.bound = []
    async def add_message(self, **kw):
        self.added.append(kw)
        return {"id": 101, "client_msg_id": kw.get("client_msg_id"),
                "message_text": kw.get("message_text")}
    async def bind_files_to_message(self, msg_id, client_msg_id):
        self.bound.append((msg_id, client_msg_id))
        return [{"file_id": "f1"}]


@pytest.fixture
def consumer():
    c = GroupDBConsumer()
    c.group_db = FakeGroupDB()
    return c



def test_broadcast_posts_to_both_gateways(consumer, mocker):
    post = mocker.patch("app.group_endpoint_consumer.httpx.AsyncClient.post", new_callable=AsyncMock)
    asyncio.run(consumer._broadcast("global", {"event": "x"}, "main"))
    assert post.call_count == 2  # main + sigma всегда
    urls = [c.args[0] for c in post.call_args_list]
    assert any("7000" in u for u in urls)
    assert any("7010" in u for u in urls)


def test_process_new_message_inserts_and_broadcasts(consumer, mocker):
    post = mocker.patch("app.group_endpoint_consumer.httpx.AsyncClient.post", new_callable=AsyncMock)
    asyncio.run(consumer.process({
        "event": "new_message", "room_uid": "global",
        "user_id": 1, "username": "u", "message": "hi",
        "client_msg_id": "c1", "gateway_target": "both",
    }))
    assert len(consumer.group_db.added) == 1
    assert consumer.group_db.added[0]["message_text"] == "hi"
    assert consumer.group_db.bound == [(101, "c1")]
    assert post.call_count == 2


def test_process_edit_only_broadcasts(consumer, mocker):
    post = mocker.patch("app.group_endpoint_consumer.httpx.AsyncClient.post", new_callable=AsyncMock)
    asyncio.run(consumer.process({
        "event": "edit_message", "room_uid": "global",
        "gateway_target": "both", "message_id": 5,
    }))
    assert consumer.group_db.added == []  # в БД не пишем
    assert post.call_count == 2


def test_process_unknown_event_noop(consumer, mocker):
    post = mocker.patch("app.group_endpoint_consumer.httpx.AsyncClient.post", new_callable=AsyncMock)
    asyncio.run(consumer.process({
        "event": "wat", "room_uid": "global",
    }))
    assert consumer.group_db.added == []
    assert post.call_count == 0
