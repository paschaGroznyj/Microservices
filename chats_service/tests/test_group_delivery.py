"""Доставка сообщений общего чата: дедуп по client_msg_id, ретраи и ручной
commit в консюмере, ack Kafka в /group/send."""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.db.group_chat_db import GroupChatDB
from app.group_endpoint_consumer import GroupDBConsumer
from app import group_chat

TS = datetime(2026, 11, 8, tzinfo=timezone.utc)


class Conn:
    def __init__(self, s): self.s = s
    async def fetchrow(self, sql, *a):
        if "FROM group_rooms" in sql:
            return {"id": 1, "room_uid": a[0], "room_type": "global", "title": "",
                    "created_by": None, "is_deleted": False, "created_at": TS}
        if "SELECT" in sql and "client_msg_id = $2" in sql:
            return next((r for r in self.s if r["client_msg_id"] == a[1]), None)
        if "INSERT INTO group_messages" in sql:
            r = {"id": len(self.s) + 1, "room_id": a[0], "user_id": a[1], "username": a[2],
                 "message_text": a[3], "client_msg_id": a[4], "is_deleted": False,
                 "is_pinned": False, "edited_at": None, "created_at": a[5], "reactions": {}}
            self.s.append(r); return r


class Pool:
    def __init__(self): self.s = []
    def acquire(self):
        pool = self
        class Ctx:
            async def __aenter__(self): return Conn(pool.s)
            async def __aexit__(self, *e): return False
        return Ctx()


def test_add_message_dedups_by_client_msg_id():
    pool = Pool(); db = GroupChatDB(pool)
    a = asyncio.run(db.add_message("global", 1, "u", "hi", client_msg_id="c1"))
    b = asyncio.run(db.add_message("global", 1, "u", "hi", client_msg_id="c1"))
    assert len(pool.s) == 1 and a["id"] == b["id"]


def test_add_message_dup_of_deleted_returns_none():
    pool = Pool(); db = GroupChatDB(pool)
    asyncio.run(db.add_message("global", 1, "u", "hi", client_msg_id="c1"))
    pool.s[0]["is_deleted"] = True
    assert asyncio.run(db.add_message("global", 1, "u", "hi", client_msg_id="c1")) is None
    assert len(pool.s) == 1


class KC:
    def __init__(self, msgs): self.msgs = msgs; self.commits = 0
    def __iter__(self): return iter(self.msgs)
    def commit(self): self.commits += 1


class Msg:
    def __init__(self, v): self.value = v


def test_consume_retries_then_commits(monkeypatch):
    c = GroupDBConsumer(); calls = []
    async def proc(p):
        calls.append(p)
        if len(calls) < 3: raise RuntimeError("db down")
    async def nosleep(_): pass
    monkeypatch.setattr(c, "process", proc)
    monkeypatch.setattr("app.group_endpoint_consumer.asyncio.sleep", nosleep)
    c.consumer = KC([Msg({"event": "new_message"})])
    asyncio.run(c.consume())
    assert len(calls) == 3 and c.consumer.commits == 1


def test_consume_drops_after_max_attempts(monkeypatch):
    c = GroupDBConsumer(); calls = []
    async def proc(p): calls.append(p); raise RuntimeError("bad")
    async def nosleep(_): pass
    monkeypatch.setattr(c, "process", proc)
    monkeypatch.setattr("app.group_endpoint_consumer.asyncio.sleep", nosleep)
    c.consumer = KC([Msg({"event": "new_message"}), Msg("not json")])
    asyncio.run(c.consume())
    assert len(calls) == GroupDBConsumer.MAX_ATTEMPTS and c.consumer.commits == 2


def test_process_reraises(monkeypatch):
    c = GroupDBConsumer()
    class DB:
        async def add_message(self, **kw): raise RuntimeError("x")
    c.group_db = DB()
    with pytest.raises(RuntimeError):
        asyncio.run(c.process({"event": "new_message", "client_msg_id": "c"}))


class Fut:
    def __init__(self, err=None): self.err = err
    def get(self, timeout=None):
        if self.err: raise self.err
        return "meta"


class Prod:
    def __init__(self, err=None): self.err = err; self.sent = []
    def send(self, t, key=None, value=None): self.sent.append(value); return Fut(self.err)


def test_send_or_503_waits_for_ack(monkeypatch):
    p = Prod(); monkeypatch.setattr(group_chat, "producer", p)
    group_chat._send_or_503("t", key="global", value={"a": 1})
    assert p.sent == [{"a": 1}]


def test_send_or_503_raises_on_failed_ack(monkeypatch):
    monkeypatch.setattr(group_chat, "producer", Prod(TimeoutError("no ack")))
    with pytest.raises(HTTPException) as e:
        group_chat._send_or_503("t", key="global", value={})
    assert e.value.status_code == 503


def test_broadcast_event_swallows_kafka_error(monkeypatch):
    monkeypatch.setattr(group_chat, "producer", Prod(TimeoutError("no ack")))
    group_chat._broadcast_event("global", {"event": "pin_message"})
