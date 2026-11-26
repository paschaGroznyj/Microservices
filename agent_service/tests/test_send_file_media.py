"""send_file_to_user: отправка файлов из media_store чата по media_id."""
import asyncio

import pytest

from agent.media import media_store
from agent.tools.telegram import tool_send_file as sf


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(sf, "MEDIA_DEST_ROOT", str(tmp_path))
    data = {
        ("chat-1", "a1b2c3"): (b"OggS-voice", {"name": "voice.ogg", "mime": "audio/ogg"}),
        ("chat-1", "noext1"): (b"\x89PNG....", {"name": "screenshot", "mime": "image/png"}),
        ("chat-1", "evil01"): (b"x", {"name": "../../etc/passwd", "mime": "text/plain"}),
    }
    calls = []

    async def fake_get(chat_id, mid):
        calls.append((chat_id, mid))
        return data.get((chat_id, mid), (None, None))

    monkeypatch.setattr(media_store, "get_media", fake_get)
    return tmp_path, calls


def send(chat_id, **kw):
    tool = sf.make_send_file_tool(agent=None, chat_id=chat_id)
    return asyncio.run(tool.coroutine(**kw))


def test_media_id_sent_as_saved_file(store):
    root, _ = store
    res = send("chat-1", media_ids=["a1b2c3"])
    assert res["ok"] is True
    f = res["saved_files"][0]
    assert f["name"] == "voice.ogg" and f["kind"] == "voice" and f["size_bytes"] == 10
    assert f["path"] == str(root / "media_a1b2c3" / "voice.ogg")
    assert open(f["path"], "rb").read() == b"OggS-voice"


def test_extension_from_mime_and_basename_only(store):
    root, _ = store
    res = send("chat-1", media_ids=["noext1", "evil01"])
    names = [f["name"] for f in res["saved_files"]]
    assert names == ["screenshot.png", "passwd"]
    assert all(f["path"].startswith(str(root)) for f in res["saved_files"])


def test_lookup_scoped_to_own_chat(store):
    _, calls = store
    res = send("chat-2", media_ids=["a1b2c3"])
    assert res["ok"] is False and "не найден" in res["error"]
    assert calls == [("chat-2", "a1b2c3")]


def test_missing_media_reported_next_to_found(store):
    res = send("chat-1", media_ids=["a1b2c3", "nope00"])
    assert res["ok"] is True and len(res["saved_files"]) == 1
    assert "nope00" in res["errors"][0]


def test_no_chat_id(store):
    res = send(None, media_ids=["a1b2c3"])
    assert res["ok"] is False and "chat_id" in res["error"]


def test_empty_call_mentions_media_ids():
    res = send("chat-1")
    assert res["ok"] is False and "media_ids" in res["error"]
