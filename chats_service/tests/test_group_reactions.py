"""Реакции общего чата: нормализация, сводка, /group/reactions/apply, консюмер."""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import group_chat
from app.core.reactions import REACTION_EMOJI, load_map, normalize_emoji, summarize
from app.group_endpoint_consumer import GroupDBConsumer


ADDED_EMOJI = (
    "✅", "❌", "0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
    "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🦧", "🤷‍♂️", "🤦‍♂️", "🫂",
)


def test_palette_has_80_unique_and_appends_exact_sequences():
    import ast
    from pathlib import Path
    assert len(REACTION_EMOJI) == len(set(REACTION_EMOJI)) == 80
    assert REACTION_EMOJI[64:] == ADDED_EMOJI
    source = Path(__file__).resolve().parents[2] / "api_gateway/reactions_bus.py"
    assignments = ast.parse(source.read_text()).body
    gateway_palette = next(ast.literal_eval(node.value) for node in assignments
                           if isinstance(node, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == "REACTION_EMOJI" for t in node.targets))
    assert gateway_palette == REACTION_EMOJI
    # The browser menu must never offer emoji rejected by either backend.
    import re
    frontend = (source.parent / 'static/js/groupInteraction.js').read_text()
    js_palette = re.findall(r"'([^']+)'", frontend.split('const REACTIONS = [', 1)[1].split('];', 1)[0])
    assert tuple(js_palette) == REACTION_EMOJI


def test_normalize():
    assert normalize_emoji("👍") == "👍"
    assert normalize_emoji("") == ""
    assert normalize_emoji(None) == ""
    assert normalize_emoji("<script>") is None
    assert normalize_emoji("🫠") is None


def test_load_map_str_dict_garbage():
    assert load_map('{"1": "👍", "2": "bad"}') == {"1": ["👍"]}
    assert load_map({"3": "🔥"}) == {"3": ["🔥"]}
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
    assert db.ops[0] == {"message_id": 5, "user_id": 7, "emojis": ["👍"]}
    assert db.ops[1]["emojis"] == []
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
        {"message_id": "x", "user_id": 1, "emoji": "👍"},
    ]))
    updates = [e for e in log if e[0] != "fetch"]
    assert len(updates) == 2
    assert json.loads(updates[0][1][1]) == {"7": ["🔥"]}
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


def test_load_mixed_map_and_unique_users():
    raw = {"1": ["👍", "🔥", "👍"], "2": "👍", "3": ["🔥"],
           "4": [], "5": ["bad"], "6": None, "7": ["👍", "🔥", "❤️", "😁"]}
    assert load_map(json.dumps(raw).encode()) == {"1": ["👍", "🔥"], "2": ["👍"], "3": ["🔥"]}
    assert summarize(raw) == [
        {"emoji": "🔥", "count": 2, "user_ids": [1, 3]},
        {"emoji": "👍", "count": 2, "user_ids": [1, 2]},
    ]


@pytest.mark.parametrize("payload", [
    {"emojis": ["👍", "🔥", "❤️", "😁"]}, {"emojis": ["bad"]},
    {"emojis": [""]}, {"emojis": [None]}, {"emojis": "👍"},
    {"emojis": None}, {"emojis": {}}, {"emoji": "bad"}, {"emoji": None},
])
def test_reaction_op_rejects_invalid_and_api_does_not_write(env, payload):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        group_chat.ReactionOp(message_id=5, user_id=7, **payload)
    client, db, prod = env
    response = client.post("/group/reactions/apply", json={"ops": [
        {"message_id": 5, "user_id": 7, "emojis": ["🔥"]},
        {"message_id": 5, "user_id": 7, **payload},
    ]})
    assert response.status_code == 422
    assert db.ops is None and prod.sent == []


@pytest.mark.parametrize("payload, expected", [
    ({"emojis": ["🔥", "👍", "❤️", "🔥"]}, ["🔥", "👍", "❤️"]),
    ({"emoji": "🔥"}, ["🔥"]), ({"emoji": ""}, []), ({}, []),
    ({"emojis": [], "emoji": "👍"}, []),
])
def test_reaction_op_normalizes(payload, expected):
    assert group_chat.ReactionOp(message_id=5, user_id=7, **payload).emojis == expected


@pytest.mark.parametrize("payload", [
    {"emojis": ["👍", "🔥", "❤️", "😁"]}, {"emojis": ["bad"]},
    {"emojis": None}, {"emojis": "🔥"}, {"emoji": "bad"},
])
def test_db_invalid_rejects_entire_batch_before_queries(payload):
    from app.db.group_chat_db import GroupChatDB
    log = []
    with pytest.raises(ValueError):
        asyncio.run(GroupChatDB(_Pool(log)).apply_reactions([
            {"message_id": 5, "user_id": 7, "emojis": ["👍"]},
            {"message_id": 5, "user_id": 8, **payload},
        ]))
    assert log == []


class StatefulConn:
    """Mock SQL execution, not apply_reactions; verify transaction and JSONB patches."""
    def __init__(self):
        self.rows = {5: {"7": "👍", "8": ["❤️", "🔥"]}, 6: {"7": "👍"}}
        self.deleted = {6}
        self.in_transaction = False
        self.transactions = 0
        self.statements = []

    def transaction(self):
        conn = self
        class Transaction:
            async def __aenter__(self):
                conn.in_transaction = True
                conn.transactions += 1
            async def __aexit__(self, *args):
                conn.in_transaction = False
        return Transaction()

    async def execute(self, sql, mid, patch):
        assert self.in_transaction
        assert "WHERE id = $1 AND is_deleted = FALSE" in sql
        self.statements.append(sql)
        if mid not in self.rows or mid in self.deleted:
            return
        if "|| $2::jsonb" in sql:
            assert "COALESCE(reactions, '{}'::jsonb)" in sql
            self.rows[mid].update(json.loads(patch))
        else:
            assert "- $2::text" in sql
            self.rows[mid].pop(patch, None)

    async def fetch(self, sql, touched):
        assert self.in_transaction
        assert "m.is_deleted = FALSE" in sql
        return [{"id": mid, "room_uid": "global", "reactions": self.rows[mid]}
                for mid in touched if mid in self.rows and mid not in self.deleted]


class StatefulPool:
    def __init__(self, conn):
        self.conn = conn
    def acquire(self):
        conn = self.conn
        class Acquire:
            async def __aenter__(self): return conn
            async def __aexit__(self, *args): pass
        return Acquire()


def test_db_atomic_per_user_patch_replaces_clears_and_preserves_other_users():
    from app.db.group_chat_db import GroupChatDB
    conn = StatefulConn()
    db = GroupChatDB(StatefulPool(conn))
    out = asyncio.run(db.apply_reactions([
        {"message_id": 5, "user_id": 7, "emoji": "😁"},
        {"message_id": 5, "user_id": 7, "emojis": ["🔥", "👍", "🔥", "😁"]},
        {"message_id": 5, "user_id": 9, "emoji": "❤️"},
        {"message_id": 6, "user_id": 7, "emojis": ["🔥"]},
        {"message_id": 999, "user_id": 7, "emojis": ["🔥"]},
    ]))
    assert conn.transactions == 1 and len(conn.statements) == 4
    assert conn.rows[5] == {"7": ["🔥", "👍", "😁"], "8": ["❤️", "🔥"], "9": ["❤️"]}
    assert conn.rows[6] == {"7": "👍"}
    assert out == [{"room_uid": "global", "message_id": 5, "reactions": summarize(conn.rows[5])}]
    asyncio.run(db.apply_reactions([{"message_id": 5, "user_id": 7, "emojis": []}]))
    assert conn.rows[5] == {"8": ["❤️", "🔥"], "9": ["❤️"]}
    asyncio.run(db.apply_reactions([{"message_id": 5, "user_id": 8, "emoji": ""}]))
    assert conn.rows[5] == {"9": ["❤️"]}


@pytest.mark.parametrize("emoji", ADDED_EMOJI)
def test_added_emoji_api_db_summary_and_broadcast(env, emoji):
    from app.db.group_chat_db import GroupChatDB
    client, _, prod = env
    conn = StatefulConn()
    group_chat.bind(GroupChatDB(StatefulPool(conn)), prod)
    response = client.post("/group/reactions/apply", json={"ops": [
        {"message_id": 5, "user_id": 7, "emojis": [emoji, "👍", "🔥", emoji]},
    ]})
    assert response.status_code == 200
    assert conn.rows[5]["7"] == [emoji, "👍", "🔥"]
    expected = summarize(conn.rows[5])
    assert {"emoji": emoji, "count": 1, "user_ids": [7]} in expected
    assert GroupChatDB._serialize({"id": 5, "user_id": 7, "reactions": conn.rows[5]}, "global")["reactions"] == expected
    assert prod.sent[-1][2]["items"] == [{"message_id": 5, "reactions": expected}]
    response = client.post("/group/reactions/apply", json={"ops": [
        {"message_id": 5, "user_id": 7, "emojis": []},
    ]})
    assert response.status_code == 200
    assert conn.rows[5] == {"8": ["❤️", "🔥"]}


@pytest.mark.parametrize("emoji", ["0⃣", "0", "🤷‍♂", "🤦‍♂", "🤷♂️", "🤦♂️", "🫠"])
def test_non_whitelisted_sequences_rejected(env, emoji):
    client, db, prod = env
    response = client.post("/group/reactions/apply", json={"ops": [
        {"message_id": 5, "user_id": 7, "emojis": [emoji]},
    ]})
    assert response.status_code == 422
    assert db.ops is None and prod.sent == []
