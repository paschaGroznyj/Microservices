"""Единая обрезка вывода тулов: common/tool_output.py и её подключение в цикле."""
import ast
import json

import pytest

from langchain_core.messages import AIMessage, ToolMessage

import agent.event_loop as el
from agent.common.tool_output import (
    clip_text, collapse_repeats, coerce_tool_result, fit_result, tool_content,
)
from agent.context_compactor import _truncate_heavy_tool_messages
from tests.test_process_query import Harness, ai, tool_msgs  # noqa: F401


# ── clip_text ────────────────────────────────────────────────────────
def test_short_text_untouched():
    assert clip_text("abc\n", 100) == "abc\n"


def test_non_string_passthrough():
    assert clip_text(None, 10) is None
    assert clip_text(42, 10) == 42


def test_keeps_head_and_tail_with_tail_bias():
    lines = [f"line {i:04d}" for i in range(2000)]
    text = "\n".join(lines) + "\nTraceback: ZeroDivisionError"
    out = clip_text(text, 2000)
    assert len(out) <= 2000
    assert out.startswith("line 0000")
    assert out.endswith("Traceback: ZeroDivisionError")
    head, tail = out.split("… [пропущено", 1)
    assert len(tail) > len(head)  # перевес к концу


def test_cuts_on_line_boundaries():
    text = "\n".join(f"row-{i:05d}-" + "x" * 30 for i in range(500))
    out = clip_text(text, 3000)
    for line in out.splitlines():
        if line.startswith("… ["):
            continue
        assert line.startswith("row-") and line.endswith("x" * 30), line


def test_mark_reports_skipped_lines_and_chars():
    text = "\n".join(str(i) for i in range(10_000))
    out = clip_text(text, 1000)
    mark = next(l for l in out.splitlines() if l.startswith("… [пропущено"))
    n_lines = int(mark.split()[2])
    n_chars = int(mark.split()[4])
    kept = out.replace(mark, "")
    assert n_chars + len(kept) - 1 == len(text) or abs(n_chars + len(kept) - len(text)) <= 2
    assert n_lines > 9000


def test_tail_only_for_stderr():
    out = clip_text("A" * 5000 + "\nValueError: bad", 300, head_ratio=0)
    assert len(out) <= 300
    assert out.endswith("ValueError: bad")
    assert not out.startswith("A")


def test_single_huge_line_without_newlines():
    out = clip_text("Z" * 100_000, 3000)
    assert 2900 <= out.count("Z") <= 3000
    assert len(out) <= 3000


# ── повторы ──────────────────────────────────────────────────────────
def test_collapse_identical_lines():
    text = "start\n" + "Downloading pkg...\n" * 50 + "end\n"
    assert collapse_repeats(text) == "start\nDownloading pkg...  [×50]\nend\n"


def test_collapse_keeps_short_runs_and_blank_lines():
    text = "a\na\nb\n\n\n\nc"
    assert collapse_repeats(text) == text


def test_collapse_does_not_merge_distinct_table_rows():
    text = "\n".join(f"{i}  0.{i}" for i in range(20))
    assert collapse_repeats(text) == text


def test_carriage_return_progress_bar():
    text = "\r 10%\r 50%\r100% done\nnext"
    assert collapse_repeats(text) == "100% done\nnext"


def test_repeats_collapsed_before_clipping():
    text = "x\n" * 10_000 + "RESULT=42"
    out = clip_text(text, 1000)
    assert out == "x  [×10000]\nRESULT=42"


# ── fit_result / coerce ──────────────────────────────────────────────
BIG = {
    "ok": True, "stdout": "\n".join(f"out {i}" for i in range(5000)) + "\nDONE",
    "stderr": "w\n" * 10 + "E" * 3000 + "\nKeyError: 'x'", "exit_code": 0,
    "saved_files": [{"filename": "a.xlsx", "path": "/app/generated_files/u/a.xlsx", "size_bytes": 1}],
}


def test_fit_result_stays_parseable_and_in_budget():
    s = fit_result(BIG, 8000)
    assert len(s) <= 8000
    d = ast.literal_eval(s)
    assert d["ok"] is True and d["exit_code"] == 0
    assert d["saved_files"] == BIG["saved_files"]
    assert d["stdout"].endswith("DONE")
    assert d["stderr"].endswith("KeyError: 'x'")


def test_fit_result_field_limits():
    d = ast.literal_eval(fit_result(BIG, 50_000, field_limits={"stdout": 3000, "stderr": 1000}))
    assert len(d["stdout"]) <= 3000 and len(d["stderr"]) <= 1000


def test_fit_result_clips_long_lists():
    rows = [{"id": i, "name": f"name {i}"} for i in range(2000)]
    s = fit_result({"status": "ok", "rows": rows}, 4000)
    assert len(s) <= 4000
    d = ast.literal_eval(s)
    assert d["rows"][0]["id"] == 0 and d["rows"][-1]["id"] == 1999
    assert any(isinstance(r, str) and "пропущено" in r for r in d["rows"])


def test_fit_result_json_serializer():
    dumps = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    s = fit_result(BIG, 6000, serialize=dumps)
    assert len(s) <= 6000
    assert json.loads(s)["stdout"].endswith("DONE")


def test_fit_result_small_untouched():
    small = {"ok": True, "stdout": "hi"}
    assert fit_result(small, 8000) == str(small)


def test_tool_content_plain_text_and_exception():
    assert len(tool_content("y" * 50_000, 8000)) <= 8000
    assert tool_content(RuntimeError("boom"), 8000) == "Ошибка инструмента: boom"


def test_coerce_roundtrip():
    assert coerce_tool_result(str(BIG)) == BIG
    assert coerce_tool_result(json.dumps({"a": 1})) == {"a": 1}
    assert coerce_tool_result("plain text") is None


# ── цикл агента ──────────────────────────────────────────────────────
@pytest.fixture
def h(monkeypatch, fast_sleep):
    return Harness(monkeypatch)


def test_tool_message_keeps_traceback_and_parses(h):
    res = dict(BIG, ok=False, exit_code=1)
    h.add_tool("execute_python_code", result=res)
    h.models["coder"].script = [ai("", [("execute_python_code", {"code": "1/0"})]), "ok"]
    h.run()
    [tm] = tool_msgs(h.models["coder"].calls[1])
    assert len(tm.content) <= el.MAX_TOOL_MSG_CHARS
    d = ast.literal_eval(tm.content)
    assert d["stdout"].endswith("DONE")
    assert d["stderr"].endswith("KeyError: 'x'")


def test_huge_saved_files_list_does_not_break_dict(h):
    files = [{"filename": f"f{i}.png", "path": f"/app/generated_files/u/f{i}.png", "size_bytes": i}
             for i in range(400)]
    h.add_tool("execute_python_code", result={"ok": True, "stdout": "ok", "saved_files": files})
    h.models["coder"].script = [ai("", [("execute_python_code", {})]), "ok"]
    h.run()
    [tm] = tool_msgs(h.models["coder"].calls[1])
    assert len(tm.content) <= el.MAX_TOOL_MSG_CHARS
    assert ast.literal_eval(tm.content)["ok"] is True


def test_trace_shell_keeps_stdout_tail():
    out = "\n".join(f"step {i}" for i in range(3000)) + "\nBUILD OK"
    s = el._summarize_tool_call(
        {"name": "execute_shell_commands", "args": {"commands": "make"}, "id": "c"},
        str({"ok": True, "exit_code": 0, "stdout": out, "stderr": ""}),
    )
    assert "BUILD OK" in s and "step 0" in s


# ── компактор ────────────────────────────────────────────────────────
def test_compactor_truncation_keeps_dict_parseable():
    msgs = [
        AIMessage(content="", tool_calls=[{"name": "execute_python_code", "args": {}, "id": "a"}]),
        ToolMessage(content=str(BIG), tool_call_id="a"),
        AIMessage(content="", tool_calls=[{"name": "execute_python_code", "args": {}, "id": "b"}]),
        ToolMessage(content="ok", tool_call_id="b"),
    ]
    msgs, n, saved = _truncate_heavy_tool_messages(msgs, max_chars=4000)
    assert n == 1 and saved > 0
    assert len(msgs[1].content) <= 4000
    d = ast.literal_eval(msgs[1].content)
    assert d["stderr"].endswith("KeyError: 'x'")
    s = el._summarize_tool_call({"name": "execute_python_code", "args": {}, "id": "a"}, msgs[1].content)
    assert "[успех]" in s
