"""Курсорная пагинация общего чата в gateway: before_id прокидывается в chats_service,
None-параметры не отправляются (старый chats_service их не ждёт)."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import group_chat_gw as gw


@pytest.fixture
def client_http():
    http = AsyncMock()
    r = MagicMock(); r.status_code = 200; r.json.return_value = {"messages": []}
    http.get.return_value = r

    async def get_user(request):
        return {"user_id": 56, "username": "pavel"}

    gw.bind(http, "http://chats", get_user, set())
    app = FastAPI(); app.include_router(gw.router)
    return TestClient(app), http


def test_before_id_is_forwarded(client_http):
    client, http = client_http
    assert client.get("/api/group/global/messages?limit=40&before_id=777").status_code == 200
    args, kwargs = http.get.await_args
    assert args[0] == "http://chats/group/global/messages"
    assert kwargs["params"] == {"limit": 40, "offset": 0, "before_id": 777}


def test_no_before_id_means_no_param(client_http):
    client, http = client_http
    client.get("/api/group/global/messages?limit=40")
    assert "before_id" not in http.get.await_args.kwargs["params"]


def test_before_id_must_be_int(client_http):
    client, http = client_http
    assert client.get("/api/group/global/messages?before_id=abc").status_code == 422
    http.get.assert_not_awaited()
