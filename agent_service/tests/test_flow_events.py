"""FlowMap: служебные события пути запроса (kind="flow") от агента."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import build_agent


def run(coro):
    return asyncio.run(coro)


class FakeTool:
    def __init__(self, name, result=None, exc=None):
        self.name = name
        self._result = result
        self._exc = exc

    async def ainvoke(self, args):
        if self._exc:
            raise self._exc
        return self._result


@pytest.fixture
def agent(monkeypatch):
    a = build_agent(monkeypatch, db=AsyncMock(name="db"))
    client = MagicMock(name="http")
    client.post = AsyncMock(name="post")
    monkeypatch.setattr(a, "_get_http_client", AsyncMock(return_value=client))
    a._http = client
    a._pub = AsyncMock(name="publish")
    monkeypatch.setattr(a, "_publish_sigma_stream", a._pub)
    return a


def set_target(a, target):
    a._request_ctx.set({"request_id": "r1", "gateway_target": target})


def test_flow_main_posts_bot_stream(agent):
    set_target(agent, "main")
    run(agent._send_flow("r1", "tool_start", name="search_internet", call_id="c1", ok=None))
    agent._http.post.assert_awaited_once()
    url = agent._http.post.await_args.args[0]
    body = agent._http.post.await_args.kwargs["json"]
    assert url.endswith("/internal/bot_stream")
    assert body["kind"] == "flow" and body["chunk"] == "" and body["request_id"] == "r1"
    assert body["flow"] == {"ev": "tool_start", "name": "search_internet", "call_id": "c1"}
    agent._pub.assert_not_awaited()


def test_flow_sigma_goes_to_redis(agent):
    set_target(agent, "sigma")
    run(agent._send_flow("r1", "agent_start"))
    agent._pub.assert_awaited_once()
    rid, payload = agent._pub.await_args.args
    assert rid == "r1" and payload["kind"] == "flow" and payload["flow"] == {"ev": "agent_start"}
    agent._http.post.assert_not_awaited()


@pytest.mark.parametrize("target", ["telegram", "max", "mobile"])
def test_flow_skipped_for_non_web(agent, target):
    set_target(agent, target)
    run(agent._send_flow("r1", "agent_start"))
    agent._http.post.assert_not_awaited()
    agent._pub.assert_not_awaited()


def test_flow_explicit_target_wins_over_ctx(agent):
    """accepted шлётся из main.py до process_query: ctx ещё нет, target передаётся явно."""
    agent._request_ctx.set(None)
    run(agent._send_flow("r1", "accepted", target="sigma"))
    agent._pub.assert_awaited_once()


def test_flow_no_request_id_noop(agent):
    set_target(agent, "main")
    run(agent._send_flow("", "agent_start"))
    agent._http.post.assert_not_awaited()


def test_flow_errors_are_swallowed(agent):
    set_target(agent, "main")
    agent._http.post.side_effect = RuntimeError("gateway down")
    run(agent._send_flow("r1", "agent_start"))


def test_invoke_tool_emits_start_and_end(agent, monkeypatch):
    set_target(agent, "main")
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    out = run(agent._invoke_tool({"t": FakeTool("t", result="ok")}, {"name": "t", "args": {}, "id": "call_7"}, "r1"))
    assert out == "ok"
    evs = [(c.args[1], c.kwargs) for c in sent.await_args_list]
    assert evs[0] == ("tool_start", {"name": "t", "call_id": "call_7"})
    assert evs[1][0] == "tool_end"
    assert evs[1][1]["ok"] is True and evs[1][1]["call_id"] == "call_7"
    assert isinstance(evs[1][1]["ms"], int)


def test_invoke_tool_error_emits_end_not_ok(agent, monkeypatch):
    set_target(agent, "main")
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    out = run(agent._invoke_tool({"t": FakeTool("t", exc=ValueError("boom"))}, {"name": "t", "args": {}, "id": "c9"}, "r1"))
    assert "Ошибка при выполнении t" in out
    end = sent.await_args_list[-1]
    assert end.args[1] == "tool_end" and end.kwargs["ok"] is False


def test_invoke_tool_unknown_tool_no_flow(agent, monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    run(agent._invoke_tool({}, {"name": "nope", "args": {}}, "r1"))
    sent.assert_not_awaited()


def test_flow_payload_json_serializable(agent):
    set_target(agent, "sigma")
    run(agent._send_flow("r1", "tool_end", name="x", call_id="c", ok=False, ms=12))
    json.dumps(agent._pub.await_args.args[1])
