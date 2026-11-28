"""endpoint_consumer: offset коммитится только после записи в БД.

Сбой соединения повторяем, ошибку данных отправляем в DLQ, дубль не доставляем второй раз.
"""
import asyncio
import json
from types import SimpleNamespace

import asyncpg
import pytest

from app import endpoint_consumer as ec
from app.db.chats_tables import DataBaseChats


def _rec(payload, offset=7):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SimpleNamespace(topic="chat-messages", partition=0, offset=offset, value=raw, key=None)


class _DB:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.pool = None

    async def add_message(self, **kw):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def _consumer(results):
    c = ec.DBConsumer.__new__(ec.DBConsumer)
    c.db = _DB(results)
    c.redis = None
    c.dlq = None
    c.delivered = []

    async def _after(*a, **k):
        c.delivered.append(a)
    c._send_flow = _after
    c.send_to_main_service = _after
    c._maybe_push = _after
    c.gateway_urls = {"main": "http://x", "sigma": "http://y"}
    return c


USER = {"user_id": 56, "chat_id": "c1", "message": "hi", "message_id": "m1"}


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    slept = []

    async def _sleep(s):
        slept.append(s)
    monkeypatch.setattr(ec.asyncio, "sleep", _sleep)
    return slept


def test_transient_error_is_retried_then_saved(_fast_sleep):
    c = _consumer([asyncpg.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
                   OSError("Temporary failure in name resolution"), True])
    assert asyncio.run(c._handle_record(_rec(USER))) is True
    assert c.db.calls == 3
    assert _fast_sleep == [0.5, 1.0]


def test_db_down_longer_than_window_returns_false(monkeypatch):
    monkeypatch.setattr(ec, "SAVE_RETRY_WINDOW", 0.0)
    c = _consumer([asyncpg.exceptions.AdminShutdownError("terminating connection due to administrator command")])
    assert asyncio.run(c._handle_record(_rec(USER))) is False
    assert c.delivered == []


def test_data_error_goes_to_dlq():
    sent = []

    class _Fut:
        def get(self, timeout=None):
            return None

    class _Prod:
        def send(self, topic, value, key, headers):
            sent.append((topic, value, dict(headers)))
            return _Fut()
    c = _consumer([asyncpg.exceptions.StringDataRightTruncationError("value too long")])
    c.dlq = _Prod()
    rec = _rec(USER)
    assert asyncio.run(c._handle_record(rec)) is True
    assert c.db.calls == 1
    assert sent[0][0] == ec.DLQ_TOPIC and sent[0][1] == rec.value
    assert b"StringDataRightTruncationError" in sent[0][2]["error"]


def test_dlq_unavailable_keeps_message():
    class _Prod:
        def send(self, *a, **k):
            raise RuntimeError("broker down")
    c = _consumer([ValueError("bad")])
    c.dlq = _Prod()
    assert asyncio.run(c._handle_record(_rec(USER))) is False


def test_broken_json_goes_to_dlq():
    c = _consumer([])
    seen = []

    async def _dlq(rec, err, where, payload=None):
        seen.append(type(err).__name__)
        return True
    c._to_dlq = _dlq
    assert asyncio.run(c._handle_record(_rec(b"{not json"))) is True
    assert seen == ["JSONDecodeError"]


def test_duplicate_is_not_delivered_again():
    bot = {"user_id": -1, "chat_id": "c1", "message": "ok", "message_id": "b1", "is_from_bot": True}
    c = _consumer([False])
    assert asyncio.run(c._handle_record(_rec(bot))) is True
    assert c.delivered == []


def test_error_after_save_does_not_retry():
    c = _consumer([True])

    async def _boom(*a, **k):
        raise RuntimeError("gateway down")
    c._send_flow = _boom
    assert asyncio.run(c._handle_record(_rec(USER))) is True
    assert c.db.calls == 1


@pytest.mark.parametrize("err,ok", [
    (asyncpg.ConnectionDoesNotExistError("x"), True),
    (asyncpg.exceptions.AdminShutdownError("x"), True),
    (asyncpg.exceptions.CannotConnectNowError("x"), True),
    (asyncpg.TooManyConnectionsError("x"), True),
    (OSError("gaierror"), True),
    (asyncio.TimeoutError(), True),
    (asyncpg.InterfaceError("pool is closing"), True),
    (asyncpg.InterfaceError("the server expects 17 arguments for this query, 16 were passed"), False),
    (asyncpg.exceptions.UndefinedColumnError("x"), False),
    (TypeError("'NoneType' object is not subscriptable"), False),
])
def test_transient_classification(err, ok):
    assert ec._is_transient_db_error(err) is ok


class _Conn:
    def __init__(self, existing):
        self.existing = existing
        self.sql = []

    async def execute(self, sql, *a):
        self.sql.append(sql)

    async def executemany(self, sql, rows):
        self.sql.append(sql)

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return 1 if self.existing else None

    def transaction(self):
        class _T:
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
        return _T()


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _CM:
            async def __aenter__(s): return conn
            async def __aexit__(s, *a): return False
        return _CM()


@pytest.mark.parametrize("existing,expected", [(False, True), (True, False)])
def test_add_message_dedup(existing, expected):
    db = DataBaseChats()
    conn = _Conn(existing)
    db.pool = _Pool(conn)
    r = asyncio.run(db.add_message(user_id=56, message="hi", chat_id="c1", is_from_bot=False, message_id="m1"))
    assert r is expected
    assert "pg_advisory_xact_lock" in conn.sql[0]
    assert any("INSERT INTO chats" in s for s in conn.sql) is (not existing)


def test_add_message_without_message_id_inserts():
    db = DataBaseChats()
    conn = _Conn(True)
    db.pool = _Pool(conn)
    assert asyncio.run(db.add_message(user_id=56, message="hi", chat_id="c1", is_from_bot=False)) is True
    assert not any("pg_advisory" in s for s in conn.sql)
