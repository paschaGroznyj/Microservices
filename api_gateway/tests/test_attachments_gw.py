"""attachments_gw.py: прокси списка вложений в chats_service и уменьшенные превью картинок."""
import io

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw_shared import attachments_gw as gw

PIL = pytest.importorskip("PIL.Image")


def png(w, h, color=(200, 30, 30)):
    b = io.BytesIO()
    PIL.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


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
    gw.bind(get_client=lambda: fc, chats_url="http://chats/", agent_url="http://agent", get_user=get_user)
    gw._cache.clear()
    gw._cache_size = 0
    app = FastAPI()
    app.include_router(gw.router)
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
    assert c.get("/api/chats/F/thumb/m1/a.png").status_code == 401
    assert fc.calls == []


def test_thumb_from_file_resized_and_cached():
    big = png(1600, 900)
    c, fc = make({"/internal/resolve_file": R(200, js={"path": "/app/generated_files/m1/a.png"}),
                  "/web/file": R(200, big, "image/png")})
    r = c.get("/api/chats/F/thumb/m1/a.png?w=230")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    im = PIL.open(io.BytesIO(r.content))
    assert im.size == (216, 122) and len(r.content) < len(big)
    assert [u.rsplit("/", 1)[-1] for u, *_ in fc.calls] == ["resolve_file", "file"]
    n = len(fc.calls)
    assert c.get("/api/chats/F/thumb/m1/a.png?w=216").content == r.content
    assert len(fc.calls) == n  # второй раз из кэша
    assert c.get("/api/chats/OTHER/thumb/m1/a.png?w=216").status_code == 200
    assert len(fc.calls) > n   # другой чат — мимо кэша


def test_thumb_db_first_with_image_key_and_fallback():
    c, fc = make({"/web/db_image": R(404),
                  "/internal/resolve_file": R(200, js={"path": "/p"}),
                  "/web/file": R(200, png(100, 50), "image/png")})
    r = c.get("/api/chats/F/thumb/m1/generated_0.jpg?image_key=abc&w=480")
    assert r.status_code == 200
    assert [u.rsplit("/", 1)[-1] for u, *_ in fc.calls] == ["db_image", "resolve_file", "file"]
    # маленький оригинал не раздуваем: отдаётся как есть, если превью не меньше
    assert PIL.open(io.BytesIO(r.content)).size == (100, 50)


def test_thumb_not_found_and_bad_name():
    c, _ = make({})
    assert c.get("/api/chats/F/thumb/m1/a.png").status_code == 404
    assert c.get("/api/chats/F/thumb/m1/..").status_code in (400, 404)
    assert c.get("/api/chats/F/thumb/m1/a%5Cb.png").status_code == 400


def test_thumb_not_image_passthrough():
    c, _ = make({"/internal/resolve_file": R(200, js={"path": "/p"}),
                 "/web/file": R(200, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")})
    r = c.get("/api/chats/F/thumb/m1/a.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["x-content-type-options"] == "nosniff"
