"""GroupChatDB.get_messages: курсор before_id (limit+1 без второго COUNT) и старый offset.
asyncpg заменён фейковым пулом, который фильтрует список сообщений в памяти."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.db.group_chat_db import GroupChatDB

TS = datetime(2026, 11, 4, tzinfo=timezone.utc)


class FakeConn:
    def __init__(self, store):
        self.s = store

    async def fetchrow(self, sql, *args):
        if "FROM group_rooms" in sql:
            return {"id": 1, "room_uid": args[0], "room_type": "global", "title": "",
                    "created_by": None, "is_deleted": False, "created_at": TS} \
                if args[0] in self.s["rooms"] else None

    async def fetchval(self, sql, *args):
        if "COUNT(*)" in sql:
            return len(self.s["msgs"])

    async def fetch(self, sql, *args):
        if "FROM group_message_files" in sql:
            return []
        self.s["queries"].append((sql, args))
        rows = sorted(self.s["msgs"], key=lambda m: -m["id"])  # ORDER BY id DESC
        if "id < $2" in sql:
            _, before_id, limit = args
            rows = [r for r in rows if r["id"] < before_id][:limit]
        else:
            _, limit, offset = args
            rows = rows[offset:offset + limit]
        return rows


class FakePool:
    def __init__(self, store):
        self.store = store

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return FakeConn(pool.store)

            async def __aexit__(self, *a):
                return False

        return _Ctx()


def _msg(i):
    return {"id": i, "room_id": 1, "user_id": 56, "username": "pavel",
            "message_text": f"m{i}", "client_msg_id": None, "is_deleted": False,
            "is_pinned": False, "edited_at": None, "created_at": TS}


@pytest.fixture
def db():
    store = {"rooms": {"global"}, "msgs": [_msg(i) for i in range(1, 151)], "queries": []}
    d = GroupChatDB(FakePool(store))
    d.store = store
    return d


def get(db, **kw):
    return asyncio.run(db.get_messages("global", **kw))


def ids(res):
    return [m["id"] for m in res["messages"]]


def test_first_page_is_latest_in_chronological_order(db):
    res = get(db, limit=40)
    assert ids(res) == list(range(111, 151))
    assert res["has_more"] is True and res["total_count"] == 150


def test_before_id_returns_older_page(db):
    res = get(db, limit=40, before_id=111)
    assert ids(res) == list(range(71, 111))
    assert res["has_more"] is True
    sql, args = db.store["queries"][-1]
    assert "id < $2" in sql and args == (1, 111, 41)  # limit+1 для has_more


def test_before_id_last_page_has_no_more(db):
    res = get(db, limit=40, before_id=31)
    assert ids(res) == list(range(1, 31))
    assert res["has_more"] is False


def test_before_id_exact_remainder_has_no_more(db):
    res = get(db, limit=40, before_id=41)  # ровно 40 сообщений старее
    assert ids(res) == list(range(1, 41))
    assert res["has_more"] is False


def test_pages_have_no_duplicates_even_with_new_messages(db):
    seen, before = [], None
    first = get(db, limit=40)
    seen += ids(first)
    before = first["messages"][0]["id"]
    db.store["msgs"] += [_msg(i) for i in range(151, 161)]  # пока листали, написали 10 новых
    while True:
        page = get(db, limit=40, before_id=before)
        if not page["messages"]:
            break
        seen += ids(page)
        before = page["messages"][0]["id"]
        if not page["has_more"]:
            break
    assert sorted(seen) == list(range(1, 151)) and len(seen) == len(set(seen))


def test_before_id_ignores_offset(db):
    assert ids(get(db, limit=10, before_id=50, offset=30)) == list(range(40, 50))


def test_offset_mode_still_works(db):
    res = get(db, limit=10, offset=140)
    assert ids(res) == list(range(1, 11)) and res["has_more"] is False


@pytest.mark.parametrize("limit, expected", [(0, 1), (-5, 1), (1000, 150)])
def test_limit_is_clamped(db, limit, expected):
    assert len(ids(get(db, limit=limit))) == expected


def test_unknown_room_is_empty(db):
    res = asyncio.run(db.get_messages("nope", before_id=10))
    assert res == {"messages": [], "total_count": 0, "has_more": False}


def test_messages_have_files_list(db):
    assert all(m["files"] == [] for m in get(db, limit=5)["messages"])
