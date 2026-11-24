"""messenger_limits.py: лимиты TG/MAX-чатов по уровню. auth/chats_service — моки."""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import messenger_limits as ml


class R:
    def __init__(self, js, code=200):
        self._js, self.status_code = js, code

    def json(self):
        return self._js


USER = {"user_id": 7, "username": "ivan"}
ADMIN = {"user_id": 1, "username": "boss"}


def setup(mocker, chats, agent_requests, user=USER):
    http = mocker.AsyncMock()
    http.get.return_value = R({"chats": chats})
    http.post.return_value = R({"stats": {"agent_requests": agent_requests}})

    async def cur(_req):
        return user

    ml.bind(http, "http://chats", "http://auth", cur, ["boss"])
    return http


def run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.mark.parametrize("n,lvl", [(0, 1), (49, 1), (50, 2), (199, 2), (200, 3), (5000, 5), (None, 1), ("x", 1)])
def test_level_of(n, lvl):
    assert ml.level_of(n) == lvl


def test_limits_by_level():
    assert ml.limits_for(1) == {"private": 1, "group": 1}
    assert ml.limits_for(2) == {"private": 2, "group": 2}
    assert ml.limits_for(5) == {"private": 2, "group": 2}


def test_chat_type():
    assert ml.chat_type_of("-100123") == "group"
    assert ml.chat_type_of(" 42 ") == "private"
    assert ml.chat_type_of("abc") == "group"


def test_l1_second_private_blocked(mocker):
    setup(mocker, [{"chat_id": "11", "chat_type": "private"}], 10)
    with pytest.raises(HTTPException) as e:
        run(ml.ensure_can_add(USER, "tg", "22"))
    assert e.value.status_code == 403 and "L1" in e.value.detail


def test_l1_group_still_allowed_when_private_used(mocker):
    setup(mocker, [{"chat_id": "11", "chat_type": "private"}], 10)
    run(ml.ensure_can_add(USER, "max", "-100500"))


def test_l2_second_allowed_third_blocked(mocker):
    setup(mocker, [{"chat_id": "11", "chat_type": "private"}], 60)
    run(ml.ensure_can_add(USER, "tg", "22"))
    setup(mocker, [{"chat_id": "11", "chat_type": "private"}, {"chat_id": "22", "chat_type": "private"}], 60)
    with pytest.raises(HTTPException):
        run(ml.ensure_can_add(USER, "tg", "33"))


def test_reembed_existing_is_not_new(mocker):
    setup(mocker, [{"chat_id": "11", "chat_type": "private"}, {"chat_id": "12", "chat_type": "private"}], 0)
    run(ml.ensure_can_add(USER, "tg", "11"))


def test_admin_unlimited(mocker):
    http = setup(mocker, [{"chat_id": str(i), "chat_type": "private"} for i in range(9)], 0, ADMIN)
    run(ml.ensure_can_add(ADMIN, "tg", "100"))
    http.get.assert_not_called()


def test_auth_down_falls_back_to_l1(mocker):
    import httpx
    http = setup(mocker, [], 0)
    http.post.side_effect = httpx.ConnectError("down")
    assert run(ml._user_level(USER)) == 1


def test_endpoint(mocker):
    setup(mocker, [{"chat_id": "-1", "chat_type": "group"}, {"chat_id": "-2", "chat_type": "group"}, {"chat_id": "5"}], 60)
    app = FastAPI(); app.include_router(ml.router)
    js = TestClient(app).get("/api/messengers/limits").json()
    assert js["is_admin"] is False and js["level"] == 2
    assert js["limits"] == {"private": 2, "group": 2}
    assert js["usage"]["tg"] == {"private": 1, "group": 2}


def test_endpoint_admin(mocker):
    setup(mocker, [], 0, ADMIN)
    app = FastAPI(); app.include_router(ml.router)
    js = TestClient(app).get("/api/messengers/limits").json()
    assert js["is_admin"] is True and js["limits"] is None


def test_sanitize_tools(mocker):
    setup(mocker, [], 0)
    assert ml.sanitize_tools(USER, ["internet", "admin", "media_gen"]) == ["internet"]
    assert ml.sanitize_tools(ADMIN, ["internet", "admin"]) == ["internet", "admin"]
    assert ml.sanitize_tools(USER, None) is None
