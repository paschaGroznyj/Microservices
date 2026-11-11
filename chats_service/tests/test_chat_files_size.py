"""chat_files.file_size: размер пишется при вставке и уходит фронту."""
import asyncio

from app.db.chats_tables import DataBaseChats, _file_size, _pixels, _thumbhash
from app.endpoint_consumer import _sanitize_files_for_ws


class _Conn:
    def __init__(self, log):
        self.log = log

    async def execute(self, sql, *a):
        self.log.append(("execute", sql, a))

    async def executemany(self, sql, rows):
        self.log.append(("executemany", sql, rows))

    def transaction(self):
        class _T:
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
        return _T()


class _Pool:
    def __init__(self):
        self.log = []

    def acquire(self):
        pool = self

        class _CM:
            async def __aenter__(s): return _Conn(pool.log)
            async def __aexit__(s, *a): return False
        return _CM()


def test_file_size_parsing():
    assert _file_size({"size": 10}) == 10
    assert _file_size({"file_size": "2048"}) == 2048
    assert _file_size({"size_bytes": 5}) == 5
    assert _file_size({"size": -1}) is None
    assert _file_size({"size": True}) is None
    assert _file_size({"size": "abc"}) is None
    assert _file_size({}) is None


def test_add_message_writes_file_size():
    db = DataBaseChats()
    db.pool = _Pool()
    asyncio.run(db.add_message(
        user_id=56, message="hi", chat_id="c1", is_from_bot=True, message_id="m1",
        attached_files=[
            {"name": "slide-1.png", "path": "/app/generated_files/r1/slide-1.png", "kind": "image", "size": 1234},
            {"name": "gen.jpg", "kind": "image", "image_key": "k"},
        ],
    ))
    kind, sql, rows = db.pool.log[-1]
    assert kind == "executemany" and "file_size" in sql and "$10" in sql
    assert rows[0][9] == 1234 and rows[1][9] is None


def test_ws_payload_has_size():
    out = _sanitize_files_for_ws([{"name": "a.png", "path": "/x", "kind": "image", "size": 7}])
    assert out[0]["size"] == 7 and "path" not in out[0]


TH = "4AcKNJqAh3dwd3d4eHZwgAf4dw=="


def test_thumbhash_validation():
    assert _thumbhash({"thumbhash": TH}) == TH
    assert _thumbhash({"thumbhash": "x" * 200}) is None
    assert _thumbhash({"thumbhash": "<script>"}) is None
    assert _thumbhash({"thumbhash": 123}) is None
    assert _thumbhash({}) is None


def test_add_message_writes_thumbhash():
    db = DataBaseChats()
    db.pool = _Pool()
    asyncio.run(db.add_message(
        user_id=56, message="hi", chat_id="c1", is_from_bot=True, message_id="m1",
        attached_files=[
            {"name": "a.png", "path": "/app/generated_files/r1/a.png", "kind": "image", "size": 1, "thumbhash": TH},
            {"name": "b.txt", "path": "/app/generated_files/r1/b.txt", "kind": "file", "size": 2},
        ],
    ))
    kind, sql, rows = db.pool.log[-1]
    assert "thumbhash" in sql and "$13" in sql
    assert rows[0][10] == TH and rows[1][10] is None


def test_ws_payload_has_thumbhash():
    out = _sanitize_files_for_ws([{"name": "a.png", "path": "/x/a.png", "kind": "image", "size": 5, "thumbhash": TH}])
    assert out[0]["thumbhash"] == TH and "path" not in out[0]


def test_pixels_validation():
    assert _pixels({"width": 1024}, "width") == 1024
    assert _pixels({"width": "768"}, "width") == 768
    for bad in (0, -5, 70000, "abc", True, None):
        assert _pixels({"width": bad}, "width") is None
    assert _pixels({}, "height") is None


def test_add_message_writes_width_height():
    db = DataBaseChats()
    db.pool = _Pool()
    asyncio.run(db.add_message(
        user_id=56, message="hi", chat_id="c1", is_from_bot=True, message_id="m2",
        attached_files=[
            {"name": "a.png", "path": "/p/a.png", "kind": "image", "width": 1600, "height": 900},
            {"name": "b.txt", "path": "/p/b.txt", "kind": "file"},
        ],
    ))
    kind, sql, rows = db.pool.log[-1]
    assert "width, height" in sql
    assert rows[0][-2:] == (1600, 900) and rows[1][-2:] == (None, None)


def test_ws_payload_has_width_height():
    out = _sanitize_files_for_ws([{"name": "a.mp4", "path": "/x/a.mp4", "kind": "video", "width": 1080, "height": 1920}])
    assert out[0]["width"] == 1080 and out[0]["height"] == 1920


def test_add_message_writes_type_query():
    for given, saved in (("model", "model"), ("agent", "agent"), (None, "agent"), ("pm", "agent")):
        db = DataBaseChats()
        db.pool = _Pool()
        asyncio.run(db.add_message(user_id=56, message="hi", chat_id="c1", is_from_bot=False,
                                   message_id="m1", type_query=given))
        kind, sql, args = next(e for e in db.pool.log if "INSERT INTO chats" in e[1])
        assert "type_query" in sql and "$15" in sql
        assert args[-1] == saved
