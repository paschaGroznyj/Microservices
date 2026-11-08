"""Отметки прочтения: /group/reads/apply, readers, read_state, консюмер."""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import group_chat
from app.db.group_chat_db import GroupChatDB
from app.group_endpoint_consumer import GroupDBConsumer


class FakeDB:
    def __init__(self):
        self.ops = None
        self.readers = []
    async def mark_reads(self, ops):
        self.ops = ops
        return [{"room_uid": "global", "user_id": 7, "last_read_id": 50},
                {"room_uid": "global", "user_id": 8, "last_read_id": 40}]
    async def get_readers(self, message_id, viewer_id):
        if message_id == 404:
            return None
        if viewer_id != 56:
            return False
        return self.readers
    async def get_messages(self, room_uid, limit=50, offset=0, before_id=None):
        return {"messages": [], "total_count": 0, "has_more": False}
    async def read_state(self, room_uid, viewer_id):
        return {"read_upto": 40, "my_last_read_id": 30}


class FakeProducer:
    def __init__(self):
        self.sent = []
    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))
        class F:
            def get(self, timeout=None): return None
        return F()


@pytest.fixture
def env():
    app = FastAPI()
    app.include_router(group_chat.router)
    db, prod = FakeDB(), FakeProducer()
    group_chat.bind(db, prod)
    yield TestClient(app), db, prod
    group_chat.bind(None, None)


def test_apply_broadcasts_read_event(env):
    client, db, prod = env
    r = client.post("/group/reads/apply", json={"ops": [
        {"room_uid": "global", "user_id": 7, "username": "a", "last_read_id": 50}]})
    assert r.status_code == 200 and r.json()["moved"] == 2
    assert db.ops[0]["last_read_id"] == 50
    (_, key, ev), = prod.sent
    assert key == "global" and ev["event"] == "read"
    assert ev["items"] == [{"user_id": 7, "last_read_id": 50}, {"user_id": 8, "last_read_id": 40}]


def test_apply_too_big(env):
    client, _, _ = env
    ops = [{"room_uid": "global", "user_id": i, "last_read_id": 1} for i in range(5001)]
    assert client.post("/group/reads/apply", json={"ops": ops}).status_code == 413


def test_readers_author_only(env):
    client, db, _ = env
    db.readers = [{"user_id": 7, "username": "Петя"}]
    r = client.get("/group/message/10/readers", params={"viewer_id": 56})
    assert r.json() == {"message_id": 10, "count": 1, "readers": db.readers}
    assert client.get("/group/message/10/readers", params={"viewer_id": 7}).status_code == 403
    assert client.get("/group/message/404/readers", params={"viewer_id": 56}).status_code == 404


def test_history_reads_only_with_viewer(env):
    client, _, _ = env
    assert client.get("/group/global/messages").json()["reads"] is None
    r = client.get("/group/global/messages", params={"viewer_id": 56}).json()
    assert r["reads"] == {"read_upto": 40, "my_last_read_id": 30}


# ── слой БД: дедуп пачки и кламп к последнему id комнаты ──

class _Conn:
    def __init__(self, log):
        self.log = log
    async def fetch(self, sql, *a):
        if "FROM group_rooms" in sql:
            return [{"id": 1, "room_uid": "global", "last_id": 100},
                    {"id": 2, "room_uid": "empty", "last_id": None}]
        self.log.append(a)
        return [{"chat_id": c, "user_id": u, "last_read_id": r}
                for c, u, r in zip(a[1], a[2], a[4])]


class _Pool:
    def __init__(self, log):
        self.log = log
    def acquire(self):
        conn = _Conn(self.log)
        class A:
            async def __aenter__(s): return conn
            async def __aexit__(s, *e): return False
        return A()


def test_mark_reads_max_per_user_and_clamp():
    log = []
    db = GroupChatDB(_Pool(log))
    out = asyncio.run(db.mark_reads([
        {"room_uid": "global", "user_id": 7, "username": "a", "last_read_id": 20},
        {"room_uid": "global", "user_id": 7, "username": "", "last_read_id": 90},
        {"room_uid": "global", "user_id": 7, "last_read_id": 50},     # опоздавший меньший
        {"room_uid": "global", "user_id": 8, "last_read_id": 5000},   # дальше конца комнаты
        {"room_uid": "empty", "user_id": 9, "last_read_id": 3},       # пустая комната
        {"room_uid": "nope", "user_id": 9, "last_read_id": 3},
        {"room_uid": "global", "user_id": "x", "last_read_id": 3},
        {"room_uid": "global", "user_id": 9, "last_read_id": 0},
    ]))
    (args,) = log
    scope, chat_ids, uids, names, rids = args
    assert scope == "group" and chat_ids == [1, 1] and uids == [7, 8]
    assert rids == [90, 100]
    assert names == ["a", ""]       # имя из более ранней записи не теряется
    assert out == [{"room_uid": "global", "user_id": 7, "last_read_id": 90},
                   {"room_uid": "global", "user_id": 8, "last_read_id": 100}]


@pytest.mark.parametrize("has_mark", [True, False])
def test_read_state_has_mark(has_mark):
    """Без своей записи my_last_read_id = последний id комнаты, но has_mark=False:
    фронт не считает его отправленным и шлёт первую отметку сам."""
    seen = {}
    class C:
        async def fetchrow(self, sql, *a):
            seen["sql"], seen["args"] = sql, a
            return {"read_upto": 175, "my_last_read_id": 179, "has_mark": has_mark}
    class P:
        def acquire(self):
            class A:
                async def __aenter__(s): return C()
                async def __aexit__(s, *e): return False
            return A()
    db = GroupChatDB(P())
    async def room(uid): return {"id": 1}
    db.get_room_by_uid = room
    out = asyncio.run(db.read_state("global", 213919))
    assert out == {"read_upto": 175, "my_last_read_id": 179, "has_mark": has_mark}
    assert "EXISTS" in seen["sql"] and seen["args"] == ("group", 1, 213919)


def test_read_state_no_room():
    db = GroupChatDB(_Pool([]))
    async def room(uid): return None
    db.get_room_by_uid = room
    assert asyncio.run(db.read_state("nope", 1))["has_mark"] is False


def test_mark_reads_empty():
    assert asyncio.run(GroupChatDB(_Pool([])).mark_reads([])) == []


# ── консюмер: отправитель прочитал своё, сбой отметки не валит доставку ──

class _GDB:
    def __init__(self, fail=False):
        self.fail, self.reads = fail, []
    async def add_message(self, **kw):
        return {"id": 77, "user_id": kw["user_id"], "username": kw["username"],
                "client_msg_id": kw["client_msg_id"]}
    async def bind_files_to_message(self, mid, cid):
        return []
    async def mark_reads(self, ops):
        if self.fail:
            raise RuntimeError("db down")
        self.reads.extend(ops)
        return []


@pytest.mark.parametrize("fail", [False, True])
def test_consumer_marks_sender_read(fail):
    c = GroupDBConsumer.__new__(GroupDBConsumer)
    c.group_db = _GDB(fail)
    sent = []
    async def bc(room, ev, tgt): sent.append(ev)
    c._broadcast = bc
    asyncio.run(c.process({"event": "new_message", "room_uid": "global", "user_id": 56,
                           "username": "Павел", "message": "hi", "client_msg_id": "c1"}))
    assert sent and sent[0]["event"] == "new_message"
    if not fail:
        assert c.group_db.reads == [{"room_uid": "global", "user_id": 56,
                                     "username": "Павел", "last_read_id": 77}]


def test_consumer_relays_read_event():
    c = GroupDBConsumer.__new__(GroupDBConsumer)
    c.group_db = _GDB()
    sent = []
    async def bc(room, ev, tgt): sent.append(ev)
    c._broadcast = bc
    asyncio.run(c.process({"event": "read", "room_uid": "global", "items": []}))
    assert sent[0]["event"] == "read"
