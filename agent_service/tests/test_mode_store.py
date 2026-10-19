"""Mobile mode store (Redis-backed lite<->agent). No pytest-asyncio -> asyncio.run."""
import asyncio
import json

import pytest

from agent.mobile import mode_store


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wired(fake_redis, monkeypatch):
    monkeypatch.setattr(mode_store, "_redis", fake_redis, raising=False)
    monkeypatch.setattr(mode_store, "_get_redis", lambda: fake_redis)
    return fake_redis


def test_enable_agent_mode_sets_key(wired):
    run(mode_store.enable_agent_mode("c1", turns=5))
    key = wired.set.call_args[0][0]
    val = json.loads(wired.set.call_args[0][1])
    assert key == "mobile:mode:c1"
    assert val == {"mode": "agent", "turns_left": 5}


def test_get_mode_no_key_is_lite(wired):
    wired.get.return_value = None
    assert run(mode_store.get_mode("c1")) == {"mode": "lite", "turns_left": 0}


def test_get_mode_agent_active(wired):
    wired.get.return_value = json.dumps({"mode": "agent", "turns_left": 3})
    assert run(mode_store.get_mode("c1")) == {"mode": "agent", "turns_left": 3}


def test_get_mode_agent_exhausted_is_lite(wired):
    wired.get.return_value = json.dumps({"mode": "agent", "turns_left": 0})
    assert run(mode_store.get_mode("c1")) == {"mode": "lite", "turns_left": 0}


def test_get_mode_fail_open_on_error(wired):
    wired.get.side_effect = RuntimeError("redis down")
    assert run(mode_store.get_mode("c1")) == {"mode": "lite", "turns_left": 0}


def test_consume_turn_no_key_lite(wired):
    wired.get.return_value = None
    assert run(mode_store.consume_agent_turn("c1")) == {"tier": "lite", "farewell": False}


def test_consume_turn_decrements(wired):
    wired.get.return_value = json.dumps({"mode": "agent", "turns_left": 3})
    out = run(mode_store.consume_agent_turn("c1"))
    assert out == {"tier": "agent", "farewell": False}
    # rewrote key with turns_left=2
    val = json.loads(wired.set.call_args[0][1])
    assert val["turns_left"] == 2


def test_consume_turn_last_is_farewell_and_deletes(wired):
    wired.get.return_value = json.dumps({"mode": "agent", "turns_left": 1})
    out = run(mode_store.consume_agent_turn("c1"))
    assert out == {"tier": "agent", "farewell": True}
    wired.delete.assert_awaited_once()


def test_consume_turn_zero_left_resets(wired):
    wired.get.return_value = json.dumps({"mode": "agent", "turns_left": 0})
    out = run(mode_store.consume_agent_turn("c1"))
    assert out == {"tier": "lite", "farewell": False}
    wired.delete.assert_awaited_once()


def test_consume_turn_non_agent_mode_lite(wired):
    wired.get.return_value = json.dumps({"mode": "lite", "turns_left": 0})
    assert run(mode_store.consume_agent_turn("c1")) == {"tier": "lite", "farewell": False}


def test_push_bridge_reply_writes_voice(wired, tmp_path):
    voice = tmp_path / "bridge.ogg"
    voice.write_bytes(b"audiobytes")
    ok = run(mode_store.push_bridge_reply("c1", str(voice)))
    assert ok is True
    key = wired.rpush.call_args[0][0]
    payload = json.loads(wired.rpush.call_args[0][1])
    assert key == "mobile:reply:c1"
    assert payload["files"][0]["kind"] == "voice"
    assert payload["files"][0]["ext"] == "ogg"
    assert payload["text"] == ""
    wired.expire.assert_awaited_once()


def test_push_bridge_reply_oversize_rejected(wired, tmp_path, monkeypatch):
    monkeypatch.setattr(mode_store, "_BRIDGE_MAX_BYTES", 2)
    voice = tmp_path / "big.ogg"
    voice.write_bytes(b"toolarge")
    assert run(mode_store.push_bridge_reply("c1", str(voice))) is False


def test_push_bridge_reply_missing_file_fail_open(wired):
    assert run(mode_store.push_bridge_reply("c1", "/nope/x.ogg")) is False
