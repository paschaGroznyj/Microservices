"""Шаги агента и все вызовы тулов хода: payload ответа -> chats.agent_steps / chats.tool_calls."""
from tests.test_process_query import Harness
from tests.conftest import ai


def test_all_tools_logged_not_only_first(monkeypatch):
    h = Harness(monkeypatch)
    h.add_tool("add", result="1")
    h.add_tool("mul", result="2")
    h.models["coder"].script = [
        ai("", calls=[("add", {"x": 1}), ("mul", {"x": 2})]),   # шаг 1: два тула параллельно
        ai("", calls=[("mul", {"x": 3})]),                      # шаг 2
        ai("done"),                                             # шаг 3: финал
    ]
    r = h.run("go")
    assert r["message"] == "done"
    assert r["action"] == "add"                       # chats.tool_name как раньше: первый
    assert r["used_tools"] == ["add", "mul", "mul"]
    assert [(c["step"], c["name"]) for c in r["tool_calls"]] == [(1, "add"), (1, "mul"), (2, "mul")]
    assert all(c["ok"] for c in r["tool_calls"])
    assert r["agent_steps"] == 3
    assert r["agent_run"] == {"steps": 3, "tool_calls": 3}


def test_no_tools_one_step(monkeypatch):
    h = Harness(monkeypatch)
    h.models["coder"].script = [ai("hi")]
    r = h.run("hi")
    assert r["agent_steps"] == 1 and r["tool_calls"] == [] and r["action"] == ""


def test_failed_tool_marked(monkeypatch):
    h = Harness(monkeypatch)
    h.add_tool("bad", result="{'ok': False, 'error': 'x'}")
    h.models["coder"].script = [ai("", calls=[("bad", {})]), ai("ok")]
    r = h.run("go")
    assert r["tool_calls"] == [{"step": 1, "name": "bad", "ok": False}]


def test_steps_run_shape():
    import datetime
    from api.steps_api import _run
    row = {"id": 7, "message_id": "m", "chat_id": "c", "created_at": datetime.datetime(2026, 11, 19),
           "model_type": "gpt", "agent_steps": 3,
           "tool_calls": '[{"step":1,"name":"add","ok":true},{"step":1,"name":"mul","ok":false},{"step":2,"name":"add","ok":true}]'}
    r = _run(row)
    assert r["steps"] == 3 and r["tool_calls"] == 3 and r["failed"] == 1
    assert r["tools"] == [{"name": "add", "count": 2}, {"name": "mul", "count": 1}]
    assert [s["step"] for s in r["by_step"]] == [1, 2]


def test_model_label_from_config():
    from agent.core.agent_provider import get_model_label
    assert get_model_label("anthropic/claude-opus-5.5") == "Claude"
    assert get_model_label("designer") == "Claude"
    assert get_model_label("openai/gpt-6.1-sol") == "GPT"
    assert get_model_label("vendor/unknown-x") == "unknown-x"
    assert get_model_label(None) == ""
