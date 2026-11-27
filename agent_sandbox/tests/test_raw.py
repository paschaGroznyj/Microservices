"""GET /workspace/{id}/raw — превью/скачивание файла: MIME, Range 206, inline/attachment, защита путей."""
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(appmod, ws_root):
    return TestClient(appmod.app)


def _ws(client):
    r = client.post("/workspace/create")
    return r.json()["workspace_id"], r.json()["path"]


def test_inline_mime_and_range(client):
    wid, path = _ws(client)
    os.makedirs(os.path.join(path, "out"))
    data = bytes(range(256)) * 40
    open(os.path.join(path, "out", "clip.mp4"), "wb").write(data)
    r = client.get(f"/workspace/{wid}/raw", params={"path": "out/clip.mp4"})
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.headers.get("accept-ranges") == "bytes"
    r = client.get(f"/workspace/{wid}/raw", params={"path": "out/clip.mp4"}, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == data[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(data)}"


def test_download_attachment_and_types(client):
    wid, path = _ws(client)
    open(os.path.join(path, "a.webp"), "wb").write(b"RIFF0000WEBP")
    open(os.path.join(path, "p.html"), "w").write("<h1>x</h1>")
    r = client.get(f"/workspace/{wid}/raw", params={"path": "a.webp", "download": 1})
    assert r.headers["content-type"] == "image/webp" and r.headers["content-disposition"].startswith("attachment")
    r = client.get(f"/workspace/{wid}/raw", params={"path": "p.html"})
    assert r.headers["content-type"].startswith("text/html") and "utf-8" in r.headers["content-type"]


@pytest.mark.parametrize("p", ["../x", "/etc/passwd", ".ws_meta/state.json", ".ws_hold", "nope.txt"])
def test_bad_paths(client, p):
    wid, path = _ws(client)
    open(os.path.join(path, ".ws_hold"), "w").write("")
    r = client.get(f"/workspace/{wid}/raw", params={"path": p})
    assert r.status_code in (403, 404)


def test_no_name_fallback(client):
    """В отличие от /file/..., raw не ищет файл по имени в других папках."""
    wid, path = _ws(client)
    os.makedirs(os.path.join(path, "deep"))
    open(os.path.join(path, "deep", "f.png"), "wb").write(b"png")
    assert client.get(f"/workspace/{wid}/raw", params={"path": "f.png"}).status_code == 404


def test_secret_only_download(client):
    wid, path = _ws(client)
    open(os.path.join(path, ".env"), "w").write("T=1")
    assert client.get(f"/workspace/{wid}/raw", params={"path": ".env"}).status_code == 403
    r = client.get(f"/workspace/{wid}/raw", params={"path": ".env", "download": 1})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")


def test_unknown_ws_404(client):
    assert client.get("/workspace/nope/raw", params={"path": "a"}).status_code == 404
