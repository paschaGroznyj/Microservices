"""GET /api/editor/raw — поток из песочницы: auth, владение, Range проксируется, CSP sandbox для html/svg."""
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

editor_gw = pytest.importorskip("editor_gw")

DATA = b"0123456789" * 100


def _sandbox(req: httpx.Request):
    path = req.url.params.get("path")
    if path == "missing.png":
        return httpx.Response(404, json={"detail": "nope"})
    if path == "boom":
        return httpx.Response(500)
    ct = {"p.html": "text/html; charset=utf-8", "i.svg": "image/svg+xml; charset=utf-8"}.get(path, "video/mp4")
    disp = "attachment" if req.url.params.get("download") == "1" else "inline"
    h = {"content-type": ct, "accept-ranges": "bytes", "content-disposition": f'{disp}; filename="{path}"',
         "set-cookie": "leak=1", "x-sandbox": "1"}
    rng = req.headers.get("range")
    if rng:
        a, b = map(int, rng.split("=")[1].split("-"))
        h["content-range"] = f"bytes {a}-{b}/{len(DATA)}"
        return httpx.Response(206, stream=httpx.ByteStream(DATA[a:b + 1]), headers=h)
    return httpx.Response(200, stream=httpx.ByteStream(DATA), headers=h)


@pytest.fixture
def wired(monkeypatch):
    seen = []

    def handler(req):
        seen.append(req)
        return _sandbox(req)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    user = AsyncMock(return_value={"user_id": 56})
    editor_gw.bind(http, "http://chats", "http://sb", user, None)
    owns = AsyncMock(return_value=None)
    monkeypatch.setattr(editor_gw, "_assert_owns", owns)
    app = FastAPI(); app.include_router(editor_gw.router)
    return TestClient(app), owns, seen, user


def test_stream_range_and_headers(wired):
    c, owns, seen, _ = wired
    r = c.get("/api/editor/raw", params={"workspace_id": "w1", "path": "out/a.mp4"}, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == DATA[10:20]
    assert r.headers["content-range"] == "bytes 10-19/1000" and r.headers["accept-ranges"] == "bytes"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in r.headers and "x-sandbox" not in r.headers
    assert "content-security-policy" not in r.headers
    assert seen[0].url.path == "/workspace/w1/raw" and seen[0].headers["range"] == "bytes=10-19"
    owns.assert_awaited_once_with(56, "w1")


@pytest.mark.parametrize("p", ["p.html", "i.svg"])
def test_html_svg_sandboxed(wired, p):
    c, *_ = wired
    r = c.get("/api/editor/raw", params={"workspace_id": "w1", "path": p})
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "allow-same-origin" not in csp


def test_download_passes_flag(wired):
    c, _, seen, _ = wired
    r = c.get("/api/editor/raw", params={"workspace_id": "w1", "path": "p.html", "download": 1})
    assert r.headers["content-disposition"].startswith("attachment") and seen[0].url.params["download"] == "1"
    assert "content-security-policy" not in r.headers


def test_errors(wired):
    c, *_ = wired
    assert c.get("/api/editor/raw", params={"workspace_id": "w1", "path": "missing.png"}).status_code == 404
    assert c.get("/api/editor/raw", params={"workspace_id": "w1", "path": "boom"}).status_code == 502
    assert c.get("/api/editor/raw", params={"workspace_id": "../x", "path": "a"}).status_code == 400


def test_not_owner_no_sandbox_call(wired, monkeypatch):
    c, _, seen, _ = wired
    from fastapi import HTTPException
    monkeypatch.setattr(editor_gw, "_assert_owns", AsyncMock(side_effect=HTTPException(403)))
    assert c.get("/api/editor/raw", params={"workspace_id": "w2", "path": "a.mp4"}).status_code == 403
    assert not seen


def test_unauth(wired):
    c, _, seen, user = wired
    user.return_value = None
    assert c.get("/api/editor/raw", params={"workspace_id": "w1", "path": "a.mp4"}).status_code == 401
    assert not seen
