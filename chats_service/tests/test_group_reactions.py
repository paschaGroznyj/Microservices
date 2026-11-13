"""Реакции общего чата: нормализация, сводка, /group/reactions/apply, консюмер."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import group_chat
from app.core.reactions import REACTION_EMOJI, load_map, normalize_emoji, summarize
from app.group_endpoint_consumer import GroupDBConsumer


def test_set_is_8x8_unique():
    assert len(REACTION_EMOJI) == 64
    assert len(set(REACTION_EMOJI)) == 64


def test_normalize():
    assert normalize_emoji("👍") == "👍"
    assert normalize_emoji("") == ""
    assert normalize_emoji(None) == ""
    assert normalize_emoji("<script>") is None
    assert normalize_emoji("🫠") is None


def test_load_map_str_dict_garbage():
    assert load_map('{"1": "👍", "2": "bad"}') == {"1": "👍"}
    assert load_map({"3": "🔥"}) == {"3": "🔥"}
    assert load_map("not json") == {}
    assert load_map(None) == {}
    assert load_map("[1,2]") == {}


def test_summarize_order_and_users():
    raw = json.dumps({"1": "🔥", "2": "👍", "3": "👍", "4": "❤️"})
    out = summarize(raw)
    assert out[0] == {"emoji": "👍", "count": 2, "user_ids": [2, 3]}
    # при равенстве порядок набора: ❤️ раньше 🔥
    assert [r["emoji"] for r in out[1:]] == ["❤️", "🔥"]


class FakeDB:
    def __init__(self):
        self.ops = None
    async def apply_reactions(self, ops):
        self.ops = ops
        return [
            {"room_uid": "global", "message_id": 5, "reactions": [{"emoji": "👍", "count": 1, "user_ids": [7]}]},
            {"room_uid": "global", "message_id": 6, "reactions": []},
            {"room_uid": "other", "message_id": 9, "reactions": []},
        ]


class FakeProducer:
    def __init__(self):
        self.sent = []
    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))
    def flush(self):
        pass


@pytest.fixture
def env():
    app = FastAPI()
    app.include_router(group_chat.router)
    db, prod = FakeDB(), FakeProducer()
    group_chat.bind(db, prod)
    yield TestClient(app), db, prod
    group_chat.bind(None, None)


def test_apply_broadcasts_per_room(env):
    client, db, prod = env
    r = client.post("/group/reactions/apply", json={"ops": [
        {"message_id": 5, "user_id": 7, "emoji": "👍"},
        {"message_id": 6, "user_id": 7, "emoji": ""},
    ]})
    assert r.status_code == 200 and r.json()["applied"] == 3
    assert db.ops[0] == {"message_id": 5, "user_id": 7, "emoji": "👍"}
    events = {key: val for _, key, val in prod.sent}
    assert set(events) == {"global", "other"}
    assert events["global"]["event"] == "reactions"
    assert [i["message_id"] for i in events["global"]["items"]] == [5, 6]


def test_apply_too_big(env):
    client, _, _ = env
    ops = [{"message_id": 1, "user_id": i, "emoji": "👍"} for i in range(2001)]
    assert client.post("/group/reactions/apply", json={"ops": ops}).status_code == 413


class _Conn:
    def __init__(self, log):
        self.log = log
    async def execute(self, sql, *a):
        self.log.append((" ".join(sql.split()), a))
    async def fetch(self, sql, *a):
        self.log.append(("fetch", a))
        return [{"id": 5, "room_uid": "global", "reactions": '{"7": "🔥"}'}]
    def transaction(self):
        class T:
            async def __aenter__(s): return s
            async def __aexit__(s, *e): return False
        return T()


class _Pool:
    def __init__(self, log):
        self.log = log
    def acquire(self):
        conn = _Conn(self.log)
        class A:
            async def __aenter__(s): return conn
            async def __aexit__(s, *e): return False
        return A()


def test_db_apply_last_wins_and_filters():
    from app.db.group_chat_db import GroupChatDB
    log = []
    db = GroupChatDB(_Pool(log))
    out = asyncio.run(db.apply_reactions([
        {"message_id": 5, "user_id": 7, "emoji": "👍"},
        {"message_id": 5, "user_id": 7, "emoji": "🔥"},   # перезаписывает
        {"message_id": 5, "user_id": 8, "emoji": ""},     # снять
        {"message_id": 5, "user_id": 9, "emoji": "evil"}, # не из набора
        {"message_id": "x", "user_id": 1, "emoji": "👍"},
    ]))
    updates = [e for e in log if e[0] != "fetch"]
    assert len(updates) == 2
    assert json.loads(updates[0][1][1]) == {"7": "🔥"}
    assert "- $2::text" in updates[1][0] and updates[1][1] == (5, "8")
    assert out == [{"room_uid": "global", "message_id": 5,
                    "reactions": [{"emoji": "🔥", "count": 1, "user_ids": [7]}]}]


def test_db_apply_empty_no_query():
    from app.db.group_chat_db import GroupChatDB
    log = []
    assert asyncio.run(GroupChatDB(_Pool(log)).apply_reactions([{"message_id": 1}])) == []
    assert log == []


def test_consumer_passes_reactions(mocker):
    from unittest.mock import AsyncMock
    c = GroupDBConsumer()
    c.group_db = object()
    post = mocker.patch("app.group_endpoint_consumer.httpx.AsyncClient.post", new_callable=AsyncMock)
    asyncio.run(c.process({"event": "reactions", "room_uid": "global", "items": []}))
    assert post.call_count == 2
    assert post.call_args.kwargs["json"]["event"] == "reactions"


def test_serialize_has_reactions():
    from app.db.group_chat_db import GroupChatDB
    m = GroupChatDB._serialize({"id": 1, "user_id": 2, "reactions": '{"2": "👍"}'}, "global")
    assert m["reactions"] == [{"emoji": "👍", "count": 1, "user_ids": [2]}]
    assert GroupChatDB._serialize({"id": 1, "user_id": 2}, "global")["reactions"] == []
