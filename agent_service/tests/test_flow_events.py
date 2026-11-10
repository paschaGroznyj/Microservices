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
    assert evs[0] == ("tool_start", {"call_id": "call_7", "gap_ms": None, "slot": "other", "label": "Инструмент"})
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


def _flows(sent):
    return [(c.args[1], c.kwargs) for c in sent.await_args_list]


def test_tool_name_never_sent(agent, monkeypatch):
    """Имя тула (в т.ч. админских) на фронт не уходит — только slot/label/ext."""
    set_target(agent, "main")
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    for nm in ("execute_sql_query", "work_with_agent_journal", "search_internet"):
        run(agent._invoke_tool({nm: FakeTool(nm, result="ok")}, {"name": nm, "args": {}, "id": "c%d" % len(nm)}, "r1"))
    blob = json.dumps([kw for _, kw in _flows(sent)], ensure_ascii=False)
    for nm in ("execute_sql_query", "work_with_agent_journal", "search_internet"):
        assert nm not in blob
    assert all("name" not in kw for _, kw in _flows(sent))
    starts = [kw for ev, kw in _flows(sent) if ev == "tool_start"]
    assert starts[0]["slot"] == "kb" and starts[1]["slot"] == "kb"
    assert starts[2] == {**starts[2], "slot": "web", "ext": "searxng"}


def test_gap_ms_between_steps(agent, monkeypatch):
    """gap_ms = время от agent_start / прошлого tool_end до tool_start по request_id."""
    import types
    import agent.event_loop as el
    # monotonic в _invoke_tool: t0 (gap), t0 (старт тула), конец тула, точка tool_end.
    clock = iter([103.5, 104.0, 110.0, 110.0])
    # подменяем модуль time только внутри event_loop: asyncio пользуется настоящим.
    monkeypatch.setattr(el, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    agent._flow_last["r1"] = 100.0           # agent_start
    t = {"t": FakeTool("t", result="ok")}
    # шаг 1: t0=103.5 (gap 3500), второй monotonic -> 104.0 старт, конец 110.0, точка 110.0
    run(agent._invoke_tool(t, {"name": "t", "args": {}, "id": "a"}, "r1"))
    starts = [kw for ev, kw in _flows(sent) if ev == "tool_start"]
    ends = [kw for ev, kw in _flows(sent) if ev == "tool_end"]
    assert starts[0]["gap_ms"] == 3500
    assert ends[0]["ms"] == 6000
    assert agent._flow_last["r1"] == 110.0


def test_gap_ms_none_without_agent_start(agent, monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    agent._flow_last.clear()
    run(agent._invoke_tool({"t": FakeTool("t", result="ok")}, {"name": "t", "args": {}, "id": "a"}, "rX"))
    assert _flows(sent)[0][1]["gap_ms"] is None
    assert "rX" in agent._flow_last   # точка выставлена по tool_end, следующий шаг получит gap


def test_workspace_flow_fields_from_args_and_result(agent, monkeypatch):
    """Тулы с workspace_id в args или create_workspace передают поле ws в flow."""
    set_target(agent, "main")
    sent = AsyncMock()
    monkeypatch.setattr(agent, "_send_flow", sent)
    tools = {
        "create_workspace": FakeTool("create_workspace", result={"workspace_id": "d976e0e002fd"}),
        "execute_python_code": FakeTool("execute_python_code", result="done"),
        "read_workspace_file": FakeTool("read_workspace_file", result="txt"),
    }
    run(agent._invoke_tool(tools, {"name": "create_workspace", "args": {}, "id": "w1"}, "r1"))
    run(agent._invoke_tool(tools, {"name": "execute_python_code", "args": {"code": "1", "workspace_id": "d976e0e002fd"}, "id": "w2"}, "r1"))
    run(agent._invoke_tool(tools, {"name": "read_workspace_file", "args": {"workspace_id": "abc12345", "path": "a.py"}, "id": "w3"}, "r1"))
    evs = _flows(sent)
    # create_workspace: на старте ws="workspace", на финише ws="d976e0e002fd" из result
    assert evs[0] == ("tool_start", {**evs[0][1], "slot": "sandbox", "ws": "workspace"})
    assert evs[1] == ("tool_end", {**evs[1][1], "slot": "sandbox", "ws": "d976e0e002fd"})
    # execute_python_code с workspace_id: и ext="sandbox_c", и ws="d976e0e002fd"
    assert evs[2][1]["ws"] == "d976e0e002fd" and evs[2][1]["ext"] == "sandbox_c"
    assert evs[4][1]["ws"] == "abc12345"
    assert all("name" not in kw for _, kw in evs)



# ── FlowMap: хронология в Redis flow:{chat_id} (восстановление после F5) ──────
def _redis_list(agent):
    r = MagicMock(name="redis")
    r.rpush = AsyncMock(return_value=1)
    r.ltrim = AsyncMock()
    r.expire = AsyncMock()
    agent._redis = r
    return r


def _pushed(r):
    return [(c.args[0], json.loads(c.args[1])) for c in r.rpush.await_args_list]


def test_flow_store_writes_chronology(agent):
    r = _redis_list(agent)
    agent._request_ctx.set({"request_id": "r1", "gateway_target": "main", "chat_id": 42})
    run(agent._send_flow("r1", "tool_start", call_id="c1", slot="web", label="Ищу", gap_ms=900))
    (key, rec), = _pushed(r)
    assert key == "flow:42"
    assert rec["ev"] == "tool_start" and rec["rid"] == "r1" and rec["call_id"] == "c1"
    assert rec["slot"] == "web" and rec["gap_ms"] == 900 and isinstance(rec["ts"], int)
    r.expire.assert_awaited()
    # кадр во фронт ушёл после записи
    agent._http.post.assert_awaited_once()


def test_flow_store_explicit_chat_id_and_no_web_target(agent):
    r = _redis_list(agent)
    run(agent._send_flow("r1", "accepted", target="main", chat_id=7))
    assert _pushed(r)[0][0] == "flow:7"
    r.rpush.reset_mock()
    run(agent._send_flow("r1", "accepted", target="telegram", chat_id=7))
    r.rpush.assert_not_awaited()


def test_flow_store_trims_list(agent, monkeypatch):
    import agent.event_loop as el
    monkeypatch.setattr(el, "FLOW_MAX", 3)
    r = _redis_list(agent)
    r.rpush.return_value = 5
    run(agent._send_flow("r1", "agent_start", target="main", chat_id=1))
    r.ltrim.assert_awaited_once_with("flow:1", -3, -1)


def test_reasoning_boundaries_go_to_chronology(agent):
    """think_end пишется только если до reasoning_end был живой текст (i-й think_end ↔ steps[i]); final_commit → answer."""
    r = _redis_list(agent)
    agent._request_ctx.set({"request_id": "r1", "gateway_target": "main", "chat_id": 5})
    run(agent._send_chunk("", "r1", kind="reasoning_end"))          # пустой шаг — не пишем
    run(agent._send_chunk("думаю", "r1", kind="live"))
    run(agent._send_chunk("", "r1", kind="reasoning_end"))
    run(agent._send_chunk("ответ", "r1", kind="live"))
    run(agent._send_chunk("", "r1", kind="final_commit"))
    assert [rec["ev"] for _, rec in _pushed(r)] == ["think_end", "answer"]


def test_flow_store_redis_error_is_swallowed(agent):
    r = _redis_list(agent)
    r.rpush.side_effect = RuntimeError("down")
    run(agent._send_flow("r1", "agent_start", target="main", chat_id=1))
    agent._http.post.assert_awaited_once()
