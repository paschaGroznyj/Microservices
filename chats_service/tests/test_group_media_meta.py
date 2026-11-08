"""Метаданные медиа общего чата: width/height/thumbhash при загрузке."""
import base64
import io
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import group_chat
from app.core import thumbhash as th
from app.db.group_chat_db import GroupChatDB, _pixels, _thumbhash


def _png(w, h, color=(200, 60, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_image_meta_from_bytes():
    meta = th.media_meta_from_bytes(_png(640, 360), "image/png", "a.png")
    assert (meta["width"], meta["height"]) == (640, 360)
    raw = base64.b64decode(meta["thumbhash"])
    assert 5 <= len(raw) <= 64


def test_not_media_is_empty():
    assert th.media_meta_from_bytes(b"%PDF-1.4", "application/pdf", "a.pdf") == {}
    assert th.media_meta_from_bytes(b"", "image/png", "a.png") == {}


def test_broken_image_has_no_meta():
    assert th.media_meta_from_bytes(b"not a png", "image/png", "a.png") == {}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_video_meta_from_bytes(tmp_path):
    out = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:s=320x240:d=1", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    meta = th.media_meta_from_bytes(out.read_bytes(), "video/mp4", "v.mp4")
    assert (meta["width"], meta["height"]) == (320, 240)
    assert meta.get("thumbhash")


def test_validators():
    assert _pixels("640") == 640
    assert _pixels(0) is None and _pixels(70000) is None and _pixels(True) is None
    assert _thumbhash("YJqGPQw7sFlslqhFafSE+Q6oJ1h2iHB2Rw") is not None
    assert _thumbhash("x" * 100) is None and _thumbhash("<script>") is None


def test_serialize_file_has_meta():
    d = GroupChatDB._serialize_file({"id": 1, "obs_key": "k", "file_name": "a.png",
                                      "mime_type": "image/png", "size_bytes": 10,
                                      "is_image": True, "width": 640, "height": 360,
                                      "thumbhash": "YJqGPQw7sFlslqhFafSE+Q6oJ1h2iHB2Rw"})
    assert d["width"] == 640 and d["height"] == 360 and d["thumbhash"]
    old = GroupChatDB._serialize_file({"id": 2, "is_image": True})
    assert old["width"] is None and old["thumbhash"] is None


class _DB:
    def __init__(self):
        self.calls = []

    async def add_file(self, **kw):
        self.calls.append(kw)
        return {"id": len(self.calls), **kw}


def test_upload_passes_meta(monkeypatch):
    async def fake_upload(key, body, mime):
        return True
    monkeypatch.setattr(group_chat.obs_file_storage, "upload_bytes", fake_upload)
    db = _DB()
    group_chat.bind(db, None)
    try:
        app = FastAPI()
        app.include_router(group_chat.router)
        c = TestClient(app)
        prefix = next(r.path for r in app.routes if r.path.endswith("/upload"))
        url = prefix.replace("{room_uid}", "global")
        r = c.post(url, data={"client_msg_id": "c1"},
                   files=[("files", ("a.png", _png(300, 600), "image/png")),
                          ("files", ("d.txt", b"hello", "text/plain"))])
        assert r.status_code == 200, r.text
        img, txt = db.calls
        assert (img["width"], img["height"]) == (300, 600) and img["thumbhash"]
        assert txt["width"] is None and txt["thumbhash"] is None
    finally:
        group_chat.bind(None, None)
