"""FlowMap: /internal/bot_stream с kind="flow" — отдельный WS-тип без chunk и без reasoning-state."""
import asyncio
from unittest.mock import AsyncMock

import pytest

app_mod = pytest.importorskip("app")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wired(monkeypatch):
    sent = []

    async def fake_broadcast(chat_id, message):
        sent.append((chat_id, message))

    monkeypatch.setattr(app_mod, "broadcast", fake_broadcast)
    rs = AsyncMock(return_value=2)
    monkeypatch.setattr(app_mod.run_status, "append_reasoning_chunk", rs)
    monkeypatch.setitem(app_mod.pending_requests, "r1", "chat-1")
    return sent, rs


def test_flow_frame_broadcast_as_type_flow(wired):
    sent, rs = wired
    p = app_mod.BotStream(request_id="r1", chunk="", kind="flow",
                          flow={"ev": "tool_start", "name": "search_internet", "call_id": "c1"})
    assert run(app_mod.bot_stream(p)) == {"ok": True}
    assert sent == [("chat-1", {"type": "flow", "request_id": "r1", "ev": "tool_start",
                                "name": "search_internet", "call_id": "c1"})]
    assert "chunk" not in sent[0][1]
    rs.assert_not_awaited()


def test_regular_chunk_unchanged(wired):
    sent, rs = wired
    p = app_mod.BotStream(request_id="r1", chunk="текст", kind="live")
    run(app_mod.bot_stream(p))
    rs.assert_awaited_once()
    assert sent[0][1]["chunk"] == "текст" and sent[0][1]["active_requests"] == 2


def test_flow_field_optional_for_old_agents():
    p = app_mod.BotStream(request_id="r1", chunk="x")
    assert p.flow is None and p.kind == "final"
