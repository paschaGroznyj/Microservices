"""Тесты HTTP-роутера group_chat.
Мокаем group_db (БД) и producer (Kafka) через bind().
Сеть/Kafka/Postgres не задействованы.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import group_chat


class FakeProducer:
    def __init__(self):
        self.sent = []
        self.flushed = 0
    def send(self, topic, key=None, value=None):
        self.sent.append({"topic": topic, "key": key, "value": value})
    def flush(self):
        self.flushed += 1


class FakeGroupDB:
    def __init__(self):
        self.calls = []
    async def get_messages(self, room_uid, limit=50, offset=0):
        self.calls.append(("get_messages", room_uid, limit, offset))
        return {"messages": [{"id": 1, "message_text": "hi"}],
                "total_count": 1, "has_more": False}
    async def get_pinned(self, room_uid):
        return [{"id": 7, "is_pinned": True}]
    async def edit_message(self, message_id, user_id, new_text):
        if user_id != 42:
            return None
        return {"id": message_id, "room_uid": "global", "message_text": new_text}
    async def set_pin(self, message_id, is_pinned):
        if message_id == 0:
            return None
        return {"id": message_id, "room_uid": "global", "is_pinned": is_pinned}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(group_chat.router)
    return TestClient(app)


@pytest.fixture
def fake_db():
    db = FakeGroupDB()
    prod = FakeProducer()
    group_chat.bind(db, prod)
    yield db, prod
    group_chat.bind(None, None)


# ── /send ───────────────────────────────────────────────────

def test_send_puts_message_to_kafka(client, fake_db):
    _, prod = fake_db
    r = client.post("/group/send", json={
        "room_uid": "global", "user_id": 42, "username": "pasha",
        "message": "hello",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["client_msg_id"]  # сгенерирован автоматически
    assert len(prod.sent) == 1
    sent = prod.sent[0]
    assert sent["topic"] == group_chat.GROUP_TOPIC
    assert sent["value"]["event"] == "new_message"
    assert sent["value"]["message"] == "hello"
    assert prod.flushed == 1


def test_send_keeps_client_msg_id(client, fake_db):
    _, prod = fake_db
    r = client.post("/group/send", json={
        "user_id": 1, "username": "u", "message": "m",
        "client_msg_id": "fixed-123",
    })
    assert r.json()["client_msg_id"] == "fixed-123"
    assert prod.sent[0]["value"]["client_msg_id"] == "fixed-123"


def test_send_503_when_producer_not_ready(client):
    group_chat.bind(object(), None)  # producer=None
    try:
        r = client.post("/group/send", json={
            "user_id": 1, "username": "u", "message": "m",
        })
        assert r.status_code == 503
    finally:
        group_chat.bind(None, None)


# ── /messages, /pinned ──────────────────────────────────

def test_messages_returns_history(client, fake_db):
    db, _ = fake_db
    r = client.get("/group/room1/messages?limit=10&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["room_uid"] == "room1"
    assert body["total_count"] == 1
    assert body["has_more"] is False
    assert ("get_messages", "room1", 10, 0) in db.calls


def test_messages_503_when_db_not_ready(client):
    group_chat.bind(None, object())
    try:
        assert client.get("/group/x/messages").status_code == 503
    finally:
        group_chat.bind(None, None)


def test_pinned_returns_list(client, fake_db):
    r = client.get("/group/room1/pinned")
    assert r.status_code == 200
    assert r.json()["pinned"][0]["id"] == 7


# ── /message/edit, /message/pin ──────────────────────────

def test_edit_ok_broadcasts(client, fake_db):
    _, prod = fake_db
    r = client.post("/group/message/edit", json={
        "message_id": 5, "user_id": 42, "new_text": "edited",
    })
    assert r.status_code == 200
    assert r.json()["message"]["message_text"] == "edited"
    assert prod.sent[-1]["value"]["event"] == "edit_message"


def test_edit_forbidden_returns_403(client, fake_db):
    r = client.post("/group/message/edit", json={
        "message_id": 5, "user_id": 999, "new_text": "nope",
    })
    assert r.status_code == 403


def test_pin_ok(client, fake_db):
    _, prod = fake_db
    r = client.post("/group/message/pin", json={
        "message_id": 5, "is_pinned": True,
    })
    assert r.status_code == 200
    assert r.json()["message"]["is_pinned"] is True
    assert prod.sent[-1]["value"]["event"] == "pin_message"


def test_pin_not_found_returns_404(client, fake_db):
    r = client.post("/group/message/pin", json={
        "message_id": 0, "is_pinned": True,
    })
    assert r.status_code == 404
