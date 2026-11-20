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
    c = r["tool_calls"][0]
    assert {k: c[k] for k in ("step", "name", "ok", "i", "par")} == {"step": 1, "name": "bad", "ok": False, "i": 0, "par": 1}
    assert c["err"] == "x" and c["ms"] >= 0 and c["t0"] >= 0 and "hint" not in c


def test_parallel_calls_meta(monkeypatch):
    h = Harness(monkeypatch)
    h.add_tool("sh", result="{'ok': True}")
    h.add_tool("q", result="1")
    h.models["coder"].script = [
        ai("", calls=[("sh", {"commands": "git push  https://u:s3cr3t@github.com/x  \n&& echo ok"}), ("q", {"query": "курс золота"})]),
        ai("", calls=[("q", {"query": "слово " * 40})]),
        ai("done"),
    ]
    calls = h.run("go")["tool_calls"]
    assert [(c["step"], c["i"], c["par"]) for c in calls] == [(1, 0, 2), (1, 1, 2), (2, 0, 1)]
    assert calls[0]["hint"] == "git push https://u:***@github.com/x && echo ok"
    assert calls[1]["hint"] == "курс золота"
    assert len(calls[2]["hint"]) == 80 and calls[2]["hint"].endswith("…")
    assert all(isinstance(c["t0"], int) and isinstance(c["ms"], int) for c in calls)
    assert calls[2]["t0"] >= calls[0]["t0"]


def test_tool_exception_err(monkeypatch):
    h = Harness(monkeypatch)
    h.add_tool("boom", exc=RuntimeError("disk full"))
    h.models["coder"].script = [ai("", calls=[("boom", {"path": "/tmp/a"})]), ai("ok")]
    c = h.run("go")["tool_calls"][0]
    assert c["ok"] is False and "disk full" in c["err"] and c["hint"] == "/tmp/a"


def test_unknown_tool_is_error(monkeypatch):
    h = Harness(monkeypatch)
    h.models["coder"].script = [ai("", calls=[("ghost", {})]), ai("ok")]
    c = h.run("go")["tool_calls"][0]
    assert c["ok"] is False and "не найден" in c["err"]


def test_hint_redaction_and_split_fields():
    from agent.common.tool_call_meta import tool_call_hint, redact
    assert tool_call_hint({"__fg_sql_001": "LIMIT 1", "__fg_sql_000": "SELECT 1 "}) == "SELECT 1 LIMIT 1"
    assert "ghp_" not in tool_call_hint({"commands": "export T=ghp_" + "A" * 36})
    assert redact("password=hunter2 x") == "password=*** x"
    assert redact("Authorization: Bearer abcdefghijkl") == "Authorization: ***"
    assert tool_call_hint({}) == "" and tool_call_hint(None) == ""
    assert tool_call_hint({"n": 3, "note": "  first\n line "}) == "first line"


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
    assert r["by_step"][0]["calls"][0] == {"name": "add", "ok": True}   # старая запись: только name/ok


def test_steps_run_extended_fields():
    import datetime
    from api.steps_api import _run
    row = {"id": 8, "message_id": "m", "chat_id": "c", "created_at": datetime.datetime(2026, 11, 20),
           "model_type": "gpt", "agent_steps": 2,
           "tool_calls": '[{"step":1,"name":"sh","ok":false,"i":0,"par":2,"t0":10,"ms":1200,"hint":"ls","err":"rc 1"},'
                         '{"step":1,"name":"q","ok":true,"i":1,"par":2,"t0":11,"ms":300,"hint":"","x":1}]'}
    calls = _run(row)["by_step"][0]["calls"]
    assert calls[0] == {"name": "sh", "ok": False, "hint": "ls", "err": "rc 1", "t0": 10, "ms": 1200, "i": 0, "par": 2}
    assert calls[1] == {"name": "q", "ok": True, "t0": 11, "ms": 300, "i": 1, "par": 2}


def test_model_label_from_config():
    from agent.core.agent_provider import get_model_label
    assert get_model_label("anthropic/claude-opus-5.5") == "Claude"
    assert get_model_label("designer") == "Claude"
    assert get_model_label("openai/gpt-6.1-sol") == "GPT"
    assert get_model_label("vendor/unknown-x") == "unknown-x"
    assert get_model_label(None) == ""
