"""Эндпоинты прочтения в gateway: POST /api/group/{room}/read и readers."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import group_chat_gw as gw
import reads_bus


@pytest.fixture
def env(monkeypatch):
    http = AsyncMock()
    r = MagicMock(); r.status_code = 200; r.json.return_value = {"count": 0, "readers": []}
    http.get.return_value = r
    state = {"user": {"user_id": 56, "username": "pavel"}}

    async def get_user(request):
        return state["user"]

    calls = []
    async def enqueue(room, uid, name, rid):
        calls.append((room, uid, name, rid)); return "redis"
    monkeypatch.setattr(reads_bus, "enqueue", enqueue)
    gw.bind(http, "http://chats", get_user, set())
    app = FastAPI(); app.include_router(gw.router)
    return TestClient(app), http, state, calls


def test_read_enqueues_with_user(env):
    client, _, _, calls = env
    r = client.post("/api/group/global/read", json={"last_read_id": 120})
    assert r.status_code == 200 and r.json()["via"] == "redis"
    assert calls == [("global", 56, "pavel", 120)]


def test_read_validation(env):
    client, _, state, calls = env
    assert client.post("/api/group/global/read", json={"last_read_id": 0}).status_code == 422
    assert client.post("/api/group/a:b/read", json={"last_read_id": 5}).status_code == 400
    state["user"] = None
    assert client.post("/api/group/global/read", json={"last_read_id": 5}).status_code == 401
    state["user"] = {"username": "no-id"}
    assert client.post("/api/group/global/read", json={"last_read_id": 5}).status_code == 401
    assert calls == []


def test_readers_passes_viewer_and_status(env):
    client, http, _, _ = env
    assert client.get("/api/group/message/10/readers").status_code == 200
    args, kwargs = http.get.await_args
    assert args[0] == "http://chats/group/message/10/readers"
    assert kwargs["params"] == {"viewer_id": 56}
    http.get.return_value.status_code = 403
    assert client.get("/api/group/message/10/readers").status_code == 403


def test_delete_route_not_shadowed_by_read(env):
    """/api/group/message/delete и /api/group/{room}/read не пересекаются."""
    client, http, _, calls = env
    http.post.return_value = MagicMock(status_code=200, json=MagicMock(return_value={"ok": True}))
    r = client.post("/api/group/message/delete", json={"message_id": 1})
    assert r.status_code == 200 and calls == []


def test_unread_passes_viewer_and_status(env):
    client, http, state, _ = env
    http.get.return_value.json.return_value = {"room_uid": "global", "reads": {"unread": 3}}
    r = client.get("/api/group/global/unread")
    assert r.status_code == 200 and r.json()["reads"]["unread"] == 3
    args, kwargs = http.get.await_args
    assert args[0] == "http://chats/group/global/unread"
    assert kwargs["params"] == {"viewer_id": 56}
    http.get.return_value.status_code = 500
    assert client.get("/api/group/global/unread").status_code == 500
    state["user"] = {"username": "no-id"}
    assert client.get("/api/group/global/unread").status_code == 401
