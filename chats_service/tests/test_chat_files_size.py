"""chat_files.file_size: размер пишется при вставке и уходит фронту."""
import asyncio

from app.db.chats_tables import DataBaseChats, _file_size
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
    assert rows[0][-1] == 1234 and rows[1][-1] is None


def test_ws_payload_has_size():
    out = _sanitize_files_for_ws([{"name": "a.png", "path": "/x", "kind": "image", "size": 7}])
    assert out[0]["size"] == 7 and "path" not in out[0]
