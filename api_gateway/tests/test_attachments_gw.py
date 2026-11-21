"""attachments_gw.py и group_attachments_gw.py: прокси списка вложений и готовых превью из chats_service."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw_shared import attachments_gw as gw
from gw_shared import group_attachments_gw as ggw


class R:
    def __init__(self, code=200, content=b"", ctype="application/json", js=None):
        self.status_code, self.content, self.headers, self._js = code, content, {"content-type": ctype}, js

    def json(self):
        return self._js


class FakeClient:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        for k, v in self.routes.items():
            if url.endswith(k):
                if isinstance(v, Exception):
                    raise v
                return v(params) if callable(v) else v
        return R(404)


def make(routes, user={"username": "pavel", "user_id": 56}):
    fc = FakeClient(routes)

    async def get_user(_):
        return user
    gw.bind(get_client=lambda: fc, chats_url="http://chats/", get_user=get_user)
    ggw.bind(get_client=lambda: fc, chats_url="http://chats/", get_user=get_user)
    app = FastAPI()
    app.include_router(gw.router)
    app.include_router(ggw.router)
    return TestClient(app), fc


def test_list_proxies_params_and_user_header():
    c, fc = make({"/chat/F/attachments": R(200, b'{"items":[],"next_cursor":null}')})
    r = c.get("/api/chats/F/attachments?tab=files&author=me&cursor=12.3&limit=36")
    assert r.status_code == 200 and r.json() == {"items": [], "next_cursor": None}
    assert r.headers["cache-control"] == "no-store"
    url, params, headers = fc.calls[0]
    assert url == "http://chats/chat/F/attachments"
    assert params == {"tab": "files", "author": "me", "limit": 36, "cursor": "12.3"}
    assert headers == {"X-User-Name": "pavel"}


@pytest.mark.parametrize("qs", ["tab=all", "author=bot", "limit=0", "limit=61"])
def test_list_validates_before_proxy(qs):
    c, fc = make({})
    assert c.get(f"/api/chats/F/attachments?{qs}").status_code == 422
    assert fc.calls == []


def test_list_maps_errors():
    c, _ = make({"/chat/F/attachments": R(404)})
    assert c.get("/api/chats/F/attachments").status_code == 404
    c, _ = make({"/chat/F/attachments": R(500)})
    assert c.get("/api/chats/F/attachments").status_code == 502
    c, _ = make({"/chat/F/attachments": httpx.ConnectError("down")})
    assert c.get("/api/chats/F/attachments").status_code == 502


def test_requires_auth():
    c, fc = make({}, user=None)
    assert c.get("/api/chats/F/attachments").status_code == 401
    assert c.get("/api/chats/F/thumb/7").status_code == 401
    assert c.get("/api/group/thumb/7").status_code == 401
    assert fc.calls == []


WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 fake"


def test_chat_thumb_proxies_ready_preview():
    c, fc = make({"/chat/F/thumb/7": R(200, WEBP, "image/webp")})
    r = c.get("/api/chats/F/thumb/7")
    assert r.status_code == 200 and r.content == WEBP and r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert fc.calls == [("http://chats/chat/F/thumb/7", None, {"X-User-Name": "pavel"})]


def test_group_thumb_passes_viewer():
    c, fc = make({"/group/thumb/9": R(200, WEBP, "image/webp")})
    r = c.get("/api/group/thumb/9")
    assert r.status_code == 200 and r.content == WEBP
    assert fc.calls == [("http://chats/group/thumb/9", {"viewer_id": 56}, None)]


def test_thumb_errors():
    c, _ = make({"/chat/F/thumb/7": R(404)})
    assert c.get("/api/chats/F/thumb/7").status_code == 404
    c, _ = make({"/chat/F/thumb/7": R(500)})
    assert c.get("/api/chats/F/thumb/7").status_code == 502
    c, _ = make({"/chat/F/thumb/7": R(200, b"{}", "application/json")})
    assert c.get("/api/chats/F/thumb/7").status_code == 502   # не картинка наружу не уходит
    c, _ = make({"/group/thumb/9": httpx.ConnectError("down")})
    assert c.get("/api/group/thumb/9").status_code == 502
    c, fc = make({})
    assert c.get("/api/chats/F/thumb/abc").status_code == 422
    assert c.get("/api/chats/F/thumb/m1/a.png").status_code == 404   # старый адрес больше не обслуживается
    assert c.get("/api/chats/a%5Cb/thumb/7").status_code == 400
    assert fc.calls == []
