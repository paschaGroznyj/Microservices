"""/web/upload_files: агент ничего не парсит, только раскладывает по file_modes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import web_ui


@pytest.fixture
def client(monkeypatch, tmp_path):
    stored = []

    async def fake_put(chat_id, raw, name, mime, kind="image", desc=""):
        stored.append({"name": name, "size": len(raw), "kind": kind})
        return f"mid{len(stored)}"

    async def fake_obs(path, chat_id):
        return None

    monkeypatch.setattr(web_ui.media_store, "put_media", fake_put)
    monkeypatch.setattr(web_ui.obs_file_storage, "upload_user_file", fake_obs)
    web_ui.pending_files.clear()
    app = FastAPI()
    app.include_router(web_ui.web_router)
    return TestClient(app), stored


def _post(c, files, modes=None, mid="m1"):
    data = {"chat_id": "c1", "message_id": mid, "user_id": "56"}
    if modes is not None:
        data["file_modes"] = modes
    r = c.post("/web/upload_files", data=data,
               files=[("files", (n, b, "application/octet-stream")) for n, b in files])
    assert r.status_code == 200, r.text
    return web_ui.pending_files[mid]["text"]


def test_inline_text_goes_to_prompt(client):
    c, stored = client
    text = _post(c, [("a.md", "# Заголовок".encode())], ["inline"])
    assert "# Заголовок" in text and "Содержимое файла" in text
    assert stored[0]["kind"] == "file"


def test_file_mode_keeps_content_out_of_prompt(client):
    c, stored = client
    body = ("секрет " * 3000).encode()
    text = _post(c, [("big.txt", body)], ["file"])
    assert "секрет" not in text
    assert "[DOCUMENT]" in text and "media_id=mid1" in text and "upload_to_workspace" in text


def test_pdf_is_not_parsed(client):
    c, _ = client
    text = _post(c, [("doc.pdf", b"%PDF-1.4 hidden text")], ["file"])
    assert "hidden text" not in text and "doc.pdf" in text


def test_missing_modes_default_to_file(client):
    """TG/MAX-боты шлют файлы без file_modes — текст в промпт не попадает."""
    c, _ = client
    text = _post(c, [("note.txt", b"plain body")])
    assert "plain body" not in text and "[DOCUMENT]" in text


def test_mixed_modes_follow_order(client):
    c, _ = client
    text = _post(c, [("a.txt", b"AAA-inline"), ("b.txt", b"BBB-file")], ["inline", "file"])
    assert "AAA-inline" in text and "BBB-file" not in text


def test_image_still_goes_to_vision(client):
    c, _ = client
    text = _post(c, [("p.png", b"\x89PNG fake")], ["file"])
    assert "[IMAGE #0: p.png]" in text
    assert web_ui.pending_files["m1"]["images"][0]["mime_type"] == "image/png"
