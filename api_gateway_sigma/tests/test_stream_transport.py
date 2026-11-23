import asyncio
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def sigma(monkeypatch):
    import cache_bust

    static_files = cache_bust.CacheBustStaticFiles
    static_dir = Path(__file__).resolve().parents[2] / "api_gateway" / "static"
    with monkeypatch.context() as patch:
        patch.setattr(cache_bust, "CacheBustStaticFiles", lambda **kw: static_files(
            **{**kw, "directory": static_dir}
        ))
        module = importlib.import_module("api_gateway_sigma.app")
    monkeypatch.setattr(module, "get_current_user", AsyncMock(return_value={"username": "tester"}))
    monkeypatch.setattr(module.presence, "touch", AsyncMock())
    monkeypatch.setattr(module.run_status, "get_flow", AsyncMock(return_value=[]))
    monkeypatch.setattr(module.run_status, "append_reasoning_chunk", AsyncMock(return_value=3))
    return module


def decode(frames):
    return [(frame.splitlines()[0][7:], json.loads(frame.splitlines()[1][6:]))
            for frame in frames if frame.startswith("event: ")]


def wire(sigma, monkeypatch, frames, *, done=True):
    messages = [{"data": json.dumps(frame)} for frame in frames]
    pubsub = Mock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    pubsub.get_message = AsyncMock(side_effect=messages + [None, None])
    redis = Mock()
    redis.pubsub.return_value = pubsub
    monkeypatch.setattr(sigma, "_get_sse_redis", AsyncMock(return_value=redis))
    store = Mock()
    store.get_chat_request_id.return_value = "r1"
    store.get_chat_state.side_effect = [
        {"status": "running"}, {"status": "done", "message": {"message": "saved answer"}},
    ]
    store.wait_chat_ready = AsyncMock(return_value=done)
    monkeypatch.setattr(sigma, "status_store", store)
    request = Mock()
    request.is_disconnected = AsyncMock(side_effect=[False, True])
    return pubsub, request


def collect(sigma, request):
    async def scenario():
        response = await sigma.stream_chat_status("c1", request)
        return [frame async for frame in response.body_iterator]
    return decode(asyncio.run(scenario()))


def test_sigma_forwards_all_kinds_including_empty_controls(sigma, monkeypatch):
    source = [
        {"kind": kind, "chunk": chunk, "id": "m1", "request_id": "r1"}
        for kind, chunk in [
            ("live", "thought"), ("reasoning_end", ""),
            ("answer_delta", "candidate"), ("answer_reset", ""),
            ("answer_delta", "replacement"), ("answer_end", ""),
            ("answer_end", "authoritative snapshot"), ("final_commit", ""),
        ]
    ]
    pubsub, request = wire(sigma, monkeypatch, source)
    events = collect(sigma, request)
    assert events[0] == ("running", {"status": "running"})
    assert events[-1][0] == "done"
    assert events[1:-1] == [(frame["kind"], {
        "chunk": frame["chunk"], "id": "m1", "kind": frame["kind"], "active_requests": 3,
    }) for frame in source]
    assert sigma.run_status.append_reasoning_chunk.await_args_list == [
        (("c1", frame["chunk"], frame["kind"], "m1"),) for frame in source
    ]
    pubsub.subscribe.assert_awaited_once_with("stream:r1")
    pubsub.unsubscribe.assert_awaited_once_with("stream:r1")
    pubsub.aclose.assert_awaited_once()


def test_sigma_reconnect_does_not_replay_candidate_but_end_snapshot_survives(sigma, monkeypatch):
    _, request = wire(sigma, monkeypatch, [], done=False)
    assert collect(sigma, request) == [("running", {"status": "running"})]
    sigma.run_status.append_reasoning_chunk.assert_not_awaited()
    _, request = wire(sigma, monkeypatch, [
        {"kind": "answer_end", "chunk": "complete snapshot", "id": "m1"},
    ])
    events = collect(sigma, request)
    assert events[1][0] == "answer_end"
    assert events[1][1]["chunk"] == "complete snapshot"
    assert events[-1][0] == "done"


def test_sigma_flow_and_diff_bypass_reasoning(sigma, monkeypatch):
    _, request = wire(sigma, monkeypatch, [
        {"kind": "flow", "flow": {"ev": "tool_start", "call_id": "t1"}},
        {"kind": "ws_diff", "diff": {"files": []}},
    ])
    events = collect(sigma, request)
    assert [event for event, _ in events] == ["running", "flow", "ws_diff", "done"]
    sigma.run_status.append_reasoning_chunk.assert_not_awaited()
