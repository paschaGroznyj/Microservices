"""Тесты sigma_consumer.py — без реальной Kafka.
Покрываем _sanitize_files (чистая) и SigmaConsumer._handle (маршрутизация в store).
"""
import pytest

from sigma_consumer import SigmaConsumer, _sanitize_files


# ---------- _sanitize_files ----------

def test_sanitize_none_returns_none():
    assert _sanitize_files(None) is None
    assert _sanitize_files([]) is None


def test_sanitize_strips_path_keeps_public_fields():
    files = [{"name": "report.PDF", "path": "/secret/report.pdf",
             "kind": "doc", "image_key": "k1"}]
    out = _sanitize_files(files)
    assert out == [{"name": "report.PDF", "ext": "pdf",
                   "kind": "doc", "image_key": "k1", "size": None, "thumbhash": None,
                   "width": None, "height": None}]
    assert "path" not in out[0]


def test_sanitize_ext_none_when_no_dot():
    out = _sanitize_files([{"name": "README", "kind": "doc"}])
    assert out[0]["ext"] is None


# ---------- SigmaConsumer._handle ----------

class _FakeStore:
    def __init__(self):
        self.done = []
    def mark_done(self, chat_id, message):
        self.done.append((chat_id, message))


@pytest.fixture()
def consumer():
    store = _FakeStore()
    c = SigmaConsumer("broker:9092", store)
    return c, store


def test_handle_ignores_non_bot_message(consumer):
    c, store = consumer
    c._handle({"is_from_bot": False, "chat_id": 1, "message": "hi"})
    assert store.done == []


def test_handle_ignores_missing_chat_id(consumer):
    c, store = consumer
    c._handle({"is_from_bot": True, "message": "hi"})
    assert store.done == []


def test_handle_bot_message_marks_done(consumer):
    c, store = consumer
    c._handle({
        "is_from_bot": True,
        "chat_id": 7,
        "user_id": 56,
        "message": "готово",
        "message_id": 99,
        "action": "final",
        "attached_files": [{"name": "a.png", "path": "/x", "kind": "img", "image_key": "k"}],
    })
    assert len(store.done) == 1
    chat_id, msg = store.done[0]
    assert chat_id == 7
    assert msg["is_from_bot"] is True
    assert msg["message"] == "готово"
    assert msg["files"] == [{"name": "a.png", "ext": "png", "kind": "img", "image_key": "k", "size": None, "thumbhash": None,
                             "width": None, "height": None}]


def test_handle_swallows_bad_payload(consumer):
    c, store = consumer
    # payload не dict -> внутри поймается исключение, не должно падать
    c._handle("not-a-dict")
    assert store.done == []
