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


def test_context_flow_preserves_payload(wired):
    sent, rs = wired
    context = {'tokens': 45678, 'cache_pct': None}
    flow = {'ev': 'context_usage', 'message_id': 'msg-123-a', 'context': context,
            'tokens_usage': {'context_tokens': 45678}}
    run(app_mod.bot_stream(app_mod.BotStream(request_id='r1', chunk='', kind='flow', flow=flow)))
    assert sent == [('chat-1', {'type': 'flow', 'request_id': 'r1', **flow})]
    rs.assert_not_awaited()


@pytest.mark.parametrize("kind,chunk", [
    ("answer_delta", "candidate"),
    ("answer_reset", ""),
    ("answer_end", ""),
    ("answer_end", "authoritative snapshot"),
    ("live", "reasoning"),
    ("reasoning_end", ""),
    ("final_commit", ""),
])
def test_http_stream_preserves_kind_chunk_and_id(wired, kind, chunk):
    import os
    os.environ['OPENBLAS_NUM_THREADS'] = '1'
    import httpx

    sent, rs = wired

    async def send():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_mod.app), base_url="http://gateway"
        ) as client:
            return await client.post("/internal/bot_stream", json={
                "request_id": "r1", "chunk": chunk, "kind": kind, "id": "m1",
            })

    response = run(send())
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == [("chat-1", {
        "is_from_bot": True, "chunk": chunk, "id": "m1", "kind": kind,
        "active_requests": 2,
    })]
    rs.assert_awaited_once_with("chat-1", chunk, kind, "m1")


def test_unknown_request_does_not_forward_or_persist(wired):
    sent, rs = wired
    with pytest.raises(app_mod.HTTPException) as exc:
        run(app_mod.bot_stream(app_mod.BotStream(
            request_id="unknown-request", chunk="", kind="answer_reset"
        )))
    assert exc.value.status_code == 404
    assert sent == []
    rs.assert_not_awaited()
