"""Тесты group_chat_gw.py — роутер общего чата.
router монтируем в чистое FastAPI; _http / _get_current_user / admin — моки.
chats_service по сети не дёргаем.
"""
import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import group_chat_gw as gw


ADMIN = "admin_user"
USER = {"user_id": 56, "username": "pavel"}


@pytest.fixture()
def http_mock(mocker):
    return mocker.AsyncMock()


@pytest.fixture()
def bind_gw(mocker, http_mock):
    """Привязываем router к мокам и возвращаем (client, http_mock, set_user)."""
    user_holder = {"user": USER}

    async def _get_user(request):
        return user_holder["user"]

    gw.bind(http_mock, "http://chats", _get_user, {ADMIN})
    app = FastAPI()
    app.include_router(gw.router)
    client = TestClient(app)

    def set_user(u):
        user_holder["user"] = u

    return client, http_mock, set_user


def _resp(mocker, data, status=200, text=""):
    r = mocker.MagicMock()
    r.json.return_value = data
    r.status_code = status
    r.text = text
    return r


# ---------- send ----------

def test_group_send_proxies_to_chats(bind_gw, mocker):
    client, http_mock, _ = bind_gw
    http_mock.post.return_value = _resp(mocker, {"ok": True, "id": 1})
    r = client.post("/api/group/room1/send", json={"message": "hi"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # в chats ушёл правильный payload
    _, kwargs = http_mock.post.call_args
    assert kwargs["json"]["room_uid"] == "room1"
    assert kwargs["json"]["gateway_target"] == "main"


def test_group_send_unauthenticated_401(bind_gw):
    client, _, set_user = bind_gw
    set_user(None)
    r = client.post("/api/group/room1/send", json={"message": "hi"})
    assert r.status_code == 401


# ---------- messages / pinned ----------

def test_group_messages_forwards_pagination(bind_gw, mocker):
    client, http_mock, _ = bind_gw
    http_mock.get.return_value = _resp(mocker, {"items": []})
    r = client.get("/api/group/room1/messages?limit=10&offset=5")
    assert r.status_code == 200
    _, kwargs = http_mock.get.call_args
    # viewer_id: chats_service отдаёт галочки и свою отметку прочтения
    assert kwargs["params"] == {"limit": 10, "offset": 5, "viewer_id": 56}


# ---------- delete: флаг is_admin ----------

def test_group_delete_passes_is_admin_true_for_admin(bind_gw, mocker):
    client, http_mock, set_user = bind_gw
    set_user({"user_id": 1, "username": ADMIN})
    http_mock.post.return_value = _resp(mocker, {"ok": True})
    client.post("/api/group/message/delete", json={"message_id": 9})
    _, kwargs = http_mock.post.call_args
    assert kwargs["json"]["is_admin"] is True


def test_group_delete_is_admin_false_for_regular(bind_gw, mocker):
    client, http_mock, _ = bind_gw
    http_mock.post.return_value = _resp(mocker, {"ok": True})
    client.post("/api/group/message/delete", json={"message_id": 9})
    _, kwargs = http_mock.post.call_args
    assert kwargs["json"]["is_admin"] is False


# ---------- pin: только админ ----------

def test_group_pin_forbidden_for_regular_user(bind_gw):
    client, _, _ = bind_gw
    r = client.post("/api/group/message/pin", json={"message_id": 9, "is_pinned": True})
    assert r.status_code == 403


def test_group_pin_allowed_for_admin(bind_gw, mocker):
    client, http_mock, set_user = bind_gw
    set_user({"user_id": 1, "username": ADMIN})
    http_mock.post.return_value = _resp(mocker, {"ok": True})
    r = client.post("/api/group/message/pin", json={"message_id": 9, "is_pinned": True})
    assert r.status_code == 200


# ---------- _broadcast ----------

def test_broadcast_drops_dead_sockets(mocker):
    class WS:
        def __init__(self, fail):
            self.fail = fail
            self.sent = []
        async def send_json(self, ev):
            if self.fail:
                raise RuntimeError("closed")
            self.sent.append(ev)
    alive, dead = WS(False), WS(True)
    gw.group_connections["roomX"] = [alive, dead]
    asyncio.run(gw._broadcast("roomX", {"type": "msg"}))
    assert alive.sent == [{"type": "msg"}]
    assert dead not in gw.group_connections["roomX"]  # мёртвый удалён


# ---------- internal/group_message ----------

def test_internal_group_message_broadcasts(bind_gw, mocker):
    client, _, _ = bind_gw
    bcast = mocker.patch.object(gw, "_broadcast", new=mocker.AsyncMock())
    r = client.post("/internal/group_message", json={"room_uid": "r1", "text": "x"})
    assert r.status_code == 200 and r.json()["ok"] is True
    bcast.assert_awaited_once()
