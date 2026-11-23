"""DB-authoritative sessions, one-time Redis migration, and real dispatch routing.

Only network/Redis boundaries and unrelated settings/title helpers are mocked;
get/set/ensure_session and both dispatch routes execute their production code.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

PREFIX = "max"
CHAT = -10055
SELECTED = "d0923394-5d5a-4a88-bd41-a057951888db"
LEGACY = "cf5d62df-a8fc-45b4-8285-704015dc7621"
WINNER = "75adf85b-0bdf-4814-9c60-4a18ca39df48"
OTHER_GROUP = str(CHAT - 1)  # physical ID of a different group


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def bot():
    from app import bot as module
    return module


@pytest.fixture
def env(bot, monkeypatch):
    state = SimpleNamespace(
        sid=None, legacy=None, registry={}, calls=[], payloads=[], generated=[],
        race=None, get_status=200, post_status=200, get_error=None,
        post_error=None, reply=None,
    )
    redis = AsyncMock()
    redis.get.side_effect = lambda key: state.legacy
    redis.hgetall.side_effect = lambda key: state.registry
    redis.set.return_value = True  # MAX scheduler deduplication, not session writes
    state.redis = redis
    state.get_redis = AsyncMock(return_value=redis)
    monkeypatch.setattr(bot, "get_redis", state.get_redis)
    monkeypatch.setattr(bot, "get_chat_settings", AsyncMock(return_value={}))
    monkeypatch.setattr(bot, "touch_session_title", AsyncMock())
    if PREFIX == "max":
        monkeypatch.setattr(bot, "mark_trigger_fired_remote", AsyncMock())
        monkeypatch.setattr(bot, "fetch_due_triggers", AsyncMock(return_value=[{
            "id": 123, "chat_id": CHAT, "kind": "poke",
        }]))

    endpoint = f"{bot.CHATS_SERVICE_URL}/{PREFIX}/session/{CHAT}"
    state.endpoint = endpoint

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, **kwargs):
            assert url == endpoint
            state.calls.append(("GET", url, None))
            if state.get_error:
                raise state.get_error
            return httpx.Response(state.get_status, json={"session_id": state.sid},
                                  request=httpx.Request("GET", url))

        async def post(self, url, *, json):
            state.calls.append(("POST", url, dict(json)))
            if url == endpoint:
                if state.post_error:
                    raise state.post_error
                if state.post_status == 200:
                    # Simulate a concurrent menu choice after GET returned null.
                    if state.race:
                        state.sid = state.race
                    if not json.get("only_if_unset") or state.sid is None:
                        state.sid = json.get("session_id")
                        if state.sid is None:
                            # UUID creation belongs to chats_service, not the bot.
                            state.sid = str(uuid4())
                            state.generated.append(state.sid)
                return httpx.Response(
                    state.post_status,
                    json={"session_id": state.reply if state.reply is not None else state.sid},
                    request=httpx.Request("POST", url),
                )
            assert url == f"{bot.CHATS_SERVICE_URL}/send_message", url
            state.payloads.append(dict(json))
            return httpx.Response(200, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(bot.httpx, "AsyncClient", Client)
    return state


def session_posts(env):
    return [body for method, url, body in env.calls
            if method == "POST" and url == env.endpoint]


async def proactive(bot, slot="poke"):
    if PREFIX == "tg":
        await bot.dispatch_initiation(CHAT, slot, False, None)
    else:
        bot.fetch_due_triggers.return_value[0]["kind"] = slot
        await bot.run_trigger_scan()


def test_db_is_authoritative_even_with_conflicting_redis(bot, env):
    env.sid = SELECTED
    env.legacy = LEGACY
    assert run(bot.get_current_session(CHAT)) == SELECTED
    assert run(bot.ensure_session(CHAT, 56)) == SELECTED
    env.get_redis.assert_not_awaited()
    assert env.calls == [("GET", env.endpoint, None)] * 2


def test_evicted_redis_mapping_does_not_lose_db_session(bot, env):
    env.sid = SELECTED
    env.get_redis.side_effect = ConnectionError("Redis evicted/unavailable")
    assert run(bot.ensure_session(CHAT, 56)) == SELECTED
    env.get_redis.assert_not_awaited()
    assert session_posts(env) == []


def test_valid_legacy_uuid_migrates_once_and_preserves_history(bot, env):
    env.legacy = LEGACY.upper()
    assert run(bot.ensure_session(CHAT, 56)) == LEGACY
    assert env.sid == LEGACY
    assert session_posts(env) == [{"only_if_unset": True, "session_id": LEGACY}]
    env.redis.get.assert_awaited_once_with(f"{PREFIX}:session:{CHAT}")
    env.redis.hgetall.assert_not_awaited()
    env.redis.set.assert_not_awaited()
    env.redis.hset.assert_awaited_once()  # Migrated UUID is also visible in the menu.
    calls_after_migration = env.get_redis.await_count
    # Old Redis data can disappear or fail after migration without changing memory.
    env.redis.get.side_effect = ConnectionError("Redis gone")
    assert run(bot.ensure_session(CHAT, 56)) == LEGACY
    assert env.get_redis.await_count == calls_after_migration
    assert len(session_posts(env)) == 1
    assert env.generated == []


def test_null_db_continues_group_root_history_not_new_uuid(bot, env):
    """Migration: group used the agent before sessions existed -> keep its history."""
    sid = run(bot.ensure_session(CHAT, 56))
    assert sid == str(CHAT)
    assert env.generated == []
    assert env.sid == str(CHAT)
    assert session_posts(env) == [{"only_if_unset": True, "session_id": str(CHAT)}]
    env.redis.get.assert_awaited_once_with(f"{PREFIX}:session:{CHAT}")
    env.redis.set.assert_not_awaited()
    assert run(bot.ensure_session(CHAT, 56)) == str(CHAT)
    assert len(session_posts(env)) == 1


@pytest.mark.parametrize("legacy", [None, LEGACY])
def test_only_if_unset_honors_db_returned_race_winner(bot, env, legacy):
    env.legacy = legacy
    env.race = WINNER
    assert run(bot.ensure_session(CHAT, 56)) == WINNER
    expected = {"only_if_unset": True, "session_id": legacy or str(CHAT)}
    assert session_posts(env) == [expected]
    assert env.sid == WINNER
    assert env.generated == []
    env.redis.set.assert_not_awaited()


@pytest.mark.parametrize("legacy", [OTHER_GROUP, "not-a-uuid", "chosen-session"])
def test_invalid_legacy_fails_closed(bot, env, legacy):
    env.legacy = legacy
    with pytest.raises(RuntimeError, match="Invalid legacy session"):
        run(bot.ensure_session(CHAT, 56))
    assert session_posts(env) == []
    assert env.generated == []
    env.redis.set.assert_not_awaited()


def test_missing_mapping_with_registry_continues_group_root_history(bot, env):
    env.registry = {LEGACY: '{"title": "original", "ts": 1}'}
    assert run(bot.ensure_session(CHAT, 56)) == str(CHAT)
    assert session_posts(env) == [{"only_if_unset": True, "session_id": str(CHAT)}]
    assert env.generated == []
    env.redis.set.assert_not_awaited()


@pytest.mark.parametrize("stage", ["connect", "read"])
def test_redis_migration_errors_fail_closed(bot, env, stage):
    error = ConnectionError("Redis unavailable during migration")
    if stage == "connect":
        env.get_redis.side_effect = error
    else:
        env.redis.get.side_effect = error
    with pytest.raises(ConnectionError):
        run(bot.ensure_session(CHAT, 56))
    assert session_posts(env) == []
    assert env.sid is None
    env.redis.set.assert_not_awaited()


@pytest.mark.parametrize("is_proactive", [False, True])
@pytest.mark.parametrize("failure", ["db_get", "db_post", "db_transport", "redis_read", "invalid_legacy"])
def test_resolution_failure_prevents_both_dispatch_routes(bot, env, is_proactive, failure):
    if failure == "db_get":
        env.get_status = 503
    elif failure == "db_post":
        env.post_status = 503
    elif failure == "db_transport":
        env.get_error = httpx.ConnectError("DB service unavailable")
    elif failure == "redis_read":
        env.redis.get.side_effect = ConnectionError("Redis read failed")
    else:
        env.legacy = OTHER_GROUP

    async def dispatch():
        if is_proactive:
            await proactive(bot)
        else:
            await bot.dispatch_to_agent(CHAT, 56, "Pavel", "@bot test")

    if is_proactive and PREFIX == "max":
        run(dispatch())  # Scheduler logs errors rather than propagating them.
        bot.mark_trigger_fired_remote.assert_not_awaited()
    else:
        with pytest.raises((httpx.HTTPError, ConnectionError, RuntimeError)):
            run(dispatch())
    assert env.payloads == []
    assert env.generated == []
    if failure in ("db_get", "db_transport"):
        env.redis.get.assert_not_awaited()
        assert session_posts(env) == []


@pytest.mark.parametrize("initial", ["db", "migration", "first_use"])
@pytest.mark.parametrize("slot", ["poke", "breakfast", "reaction"])
def test_incoming_and_proactive_share_memory_and_physical_delivery(bot, env, initial, slot):
    if initial == "db":
        env.sid = SELECTED
        env.legacy = LEGACY  # DB must win, even with stale Redis data.
    elif initial == "migration":
        env.legacy = LEGACY

    async def scenario():
        await proactive(bot, slot)
        await bot.dispatch_to_agent(CHAT, 56, "Pavel", "@bot test")
        await bot.set_current_session(CHAT, WINNER)
        await proactive(bot, slot)
        await bot.dispatch_to_agent(CHAT, 56, "Pavel", "reply")

    run(scenario())
    first = SELECTED if initial == "db" else LEGACY if initial == "migration" else str(CHAT)
    assert [p["chat_id"] for p in env.payloads] == [first, first, WINNER, WINNER]
    assert all(p[f"{PREFIX}_chat_id"] == str(CHAT) for p in env.payloads)
    assert all(p["gateway_target"] == ("telegram" if PREFIX == "tg" else "max")
               for p in env.payloads)
    assert [p.get("initiated", False) for p in env.payloads] == [True, False, True, False]
    assert [p["slot"] for p in env.payloads if p.get("initiated")] == [slot, slot]
    assert env.redis.get.await_count == (0 if initial == "db" else 1)
    if PREFIX == "max":
        assert bot.mark_trigger_fired_remote.await_count == 2


def test_explicit_selection_persists_to_db_and_is_read_back(bot, env):
    run(bot.set_current_session(CHAT, UUID(SELECTED)))
    assert env.calls == [("POST", env.endpoint, {"session_id": SELECTED})]
    assert run(bot.get_current_session(CHAT)) == SELECTED
    assert run(bot.ensure_session(CHAT, 56)) == SELECTED
    env.get_redis.assert_not_awaited()


@pytest.mark.parametrize("failure", ["status", "transport", "mismatch"])
def test_explicit_selection_failures_are_surfaced(bot, env, failure):
    if failure == "status":
        env.post_status = 503
    elif failure == "transport":
        env.post_error = httpx.ConnectError("DB unavailable")
    else:
        env.reply = WINNER
    with pytest.raises((httpx.HTTPError, RuntimeError)):
        run(bot.set_current_session(CHAT, SELECTED))
    assert session_posts(env) == [{"session_id": SELECTED}]
    env.get_redis.assert_not_awaited()


@pytest.mark.parametrize("bad", [OTHER_GROUP, "not-a-uuid", ""])
def test_invalid_explicit_selection_fails_before_any_write(bot, env, bad):
    with pytest.raises(ValueError):
        run(bot.set_current_session(CHAT, bad))
    assert env.calls == []
    env.get_redis.assert_not_awaited()


@pytest.mark.parametrize("operation", ["get", "ensure"])
def test_invalid_db_uuid_is_not_silently_replaced(bot, env, operation):
    env.sid = OTHER_GROUP
    with pytest.raises(ValueError):
        run(bot.get_current_session(CHAT) if operation == "get" else bot.ensure_session(CHAT, 56))
    env.get_redis.assert_not_awaited()
    assert session_posts(env) == []


# Pre-session history of a group lives under its physical ID. It is a real,
# selectable session (production crash: menu "open:<group id>" -> ValueError),
# but it is never substituted automatically.
@pytest.mark.parametrize("raw", [str(CHAT), CHAT])
def test_group_root_history_is_selectable_and_persisted(bot, env, raw):
    run(bot.set_current_session(CHAT, raw))
    assert env.calls == [("POST", env.endpoint, {"session_id": str(CHAT)})]
    assert run(bot.get_current_session(CHAT)) == str(CHAT)
    assert run(bot.ensure_session(CHAT, 56)) == str(CHAT)
    env.get_redis.assert_not_awaited()
    assert env.generated == []


def test_legacy_redis_root_history_migrates_once(bot, env):
    env.legacy = str(CHAT)
    assert run(bot.ensure_session(CHAT, 56)) == str(CHAT)
    assert session_posts(env) == [{"only_if_unset": True, "session_id": str(CHAT)}]
    assert env.sid == str(CHAT)
    assert env.generated == []


@pytest.mark.parametrize("is_proactive", [False, True])
def test_root_history_session_routes_both_dispatch_paths(bot, env, is_proactive):
    env.sid = str(CHAT)
    if is_proactive:
        run(proactive(bot))
    else:
        run(bot.dispatch_to_agent(CHAT, 56, "Pavel", "@bot test"))
    assert env.payloads, "dispatch did not reach the agent"
    assert all(p["chat_id"] == str(CHAT) for p in env.payloads)
    assert all(p[f"{PREFIX}_chat_id"] == str(CHAT) for p in env.payloads)
