"""GET /api/chats/{chat_id}/ws_diff/step — patch одного шага: auth, владение workspace, прокси в sandbox."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

app_mod = pytest.importorskip("app")


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, resp):
        self.resp, self.calls = resp, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return self.resp


@pytest.fixture
def wired(monkeypatch):
    user = {"user_id": 56}
    monkeypatch.setattr(app_mod, "get_current_user", AsyncMock(return_value=user))
    owns = AsyncMock(return_value=None)
    monkeypatch.setattr(app_mod.editor_gw, "_assert_owns", owns)
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"files": [{"path": "a.py"}], "call_id": "c1"}
    fc = FakeClient(resp)
    monkeypatch.setattr(app_mod.httpx, "AsyncClient", lambda **kw: fc)
    return owns, fc, resp, user


def call(**kw):
    q = {"workspace_id": "d976e0e002fd", "request_id": "r1", "call_id": "call_ab-1"}
    q.update(kw)
    return run(app_mod.chat_ws_diff_step("chat-9", MagicMock(), **q))


def test_proxies_with_chat_id(wired):
    owns, fc, _, _ = wired
    r = call()
    assert r.status_code == 200
    owns.assert_awaited_once_with(56, "d976e0e002fd")
    url, params = fc.calls[0]
    assert url.endswith("/workspace/d976e0e002fd/diff/step")
    assert params == {"request_id": "r1", "call_id": "call_ab-1", "chat_id": "chat-9"}


def test_not_owner_403_no_sandbox_call(wired):
    owns, fc, _, _ = wired
    owns.side_effect = HTTPException(status_code=403, detail="no")
    with pytest.raises(HTTPException) as e:
        call()
    assert e.value.status_code == 403 and fc.calls == []


def test_unauth_401(wired, monkeypatch):
    monkeypatch.setattr(app_mod, "get_current_user", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as e:
        call()
    assert e.value.status_code == 401


@pytest.mark.parametrize("kw", [{"workspace_id": "../etc"}, {"workspace_id": "a/b"},
                                {"call_id": "x y"}, {"request_id": "a" * 300}])
def test_bad_ids_400(wired, kw):
    owns, fc, _, _ = wired
    with pytest.raises(HTTPException) as e:
        call(**kw)
    assert e.value.status_code == 400 and fc.calls == [] and not owns.await_count


def test_sandbox_404_passthrough(wired):
    _, _, resp, _ = wired
    resp.status_code = 404
    with pytest.raises(HTTPException) as e:
        call()
    assert e.value.status_code == 404
