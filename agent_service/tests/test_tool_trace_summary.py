"""Сводка tool_trace: метки [успех]/[ОШИБКА] у execute_python_code и execute_shell_commands.

Регрессия: _invoke_tool отдаёт результат строкой str(dict), а _summarize_tool_call
проверял isinstance(result, dict) — любой вызов execute_python_code попадал в trace
как [ОШИБКА] с пустым текстом ошибки.
"""
import pytest

import agent.event_loop as el
from agent.context_compactor import _extract_facts_from_pairs
from langchain_core.messages import AIMessage, ToolMessage
from tests.test_process_query import Harness, ai  # noqa: F401  (фикстура h ниже)


def tc(name="execute_python_code", **args):
    return {"name": name, "args": args, "id": "call_x"}


OK_RESULT = {
    "ok": True, "stdout": "done", "stderr": "", "exit_code": 0,
    "saved_files": [{"filename": "report.xlsx", "path": "/app/generated_files/u/report.xlsx", "size_bytes": 10}],
    "saved_script_path": "/sandbox/executed_scripts/abc.py",
}
FAIL_RESULT = {
    "ok": False, "stdout": "", "exit_code": 1,
    "stderr": "Traceback (most recent call last):\n  File \"x.py\", line 3\nZeroDivisionError: division by zero",
    "saved_script_path": "/sandbox/executed_scripts/failed/abc.py", "error_line": 3,
}


@pytest.mark.parametrize("result", [OK_RESULT, str(OK_RESULT)], ids=["dict", "str"])
def test_python_success_marked_success(result):
    s = el._summarize_tool_call(tc(code="print(1)"), result)
    assert s.startswith("execute_python_code() [успех]")
    assert "report.xlsx" in s
    assert "/sandbox/executed_scripts" not in s  # архив только для админа — не подсказываем
    assert "ОШИБКА" not in s


@pytest.mark.parametrize("result", [FAIL_RESULT, str(FAIL_RESULT)], ids=["dict", "str"])
def test_python_failure_has_real_error(result):
    s = el._summarize_tool_call(tc(code="1/0"), result)
    assert "[ОШИБКА]" in s
    assert "ZeroDivisionError: division by zero" in s
    assert "строка 3" in s
    assert "/sandbox/executed_scripts" not in s
    assert "код:\n1/0" in s


def test_python_success_workspace_script_in_trace():
    r = dict(OK_RESULT, saved_script_in_workspace="output/.scripts/abc.py", workspace_id="ws1")
    s = el._summarize_tool_call(tc(code="print(1)", workspace_id="ws1"), str(r))
    assert "workspace ws1 → output/.scripts/abc.py" in s
    assert "/sandbox/executed_scripts" not in s


def test_python_failure_long_code_shows_error_context():
    code = "import os\n" + "\n".join(f"x{i} = {i}" for i in range(300)) + "\nboom()\n"
    ctx = ">>>  302 | boom()"
    r = dict(FAIL_RESULT, error_line=302, error_context=ctx)
    s = el._summarize_tool_call(tc(code=code), r)
    assert "import os" in s                 # начало скрипта
    assert "вокруг ошибки:" in s and ctx in s
    assert len(s) < 2500


def test_python_failure_without_stderr_not_empty():
    s = el._summarize_tool_call(tc(code="x"), str({"ok": False, "exit_code": 137}))
    assert "[ОШИБКА]" in s
    assert not s.rstrip().endswith("ошибка:")
    assert "exit_code=137" in s


def test_python_transport_error():
    s = el._summarize_tool_call(tc(code="x"), str({"ok": False, "error": "sandbox timeout"}))
    assert "[ОШИБКА]" in s and "sandbox timeout" in s


def test_python_exception_result():
    s = el._summarize_tool_call(tc(code="x"), RuntimeError("boom"))
    assert "[ОШИБКА]" in s and "RuntimeError: boom" in s


def test_python_unparsable_result_not_marked_error():
    # ToolMessage в компакторе бывает обрезан — такой результат не должен становиться [ОШИБКА]
    cut = str(OK_RESULT)[:40]
    s = el._summarize_tool_call(tc(code="x"), cut)
    assert "[ОШИБКА]" not in s and "[успех]" not in s
    assert "не разобран" in s


@pytest.mark.parametrize("result", [
    {"ok": True, "stdout": "hello", "stderr": "", "exit_code": 0},
    str({"ok": True, "stdout": "hello", "stderr": "", "exit_code": 0}),
], ids=["dict", "str"])
def test_shell_summary_parses_string(result):
    s = el._summarize_tool_call(tc("execute_shell_commands", commands="echo hello"), result)
    assert "ok=True rc=0 stdout=hello" in s


def test_text_tool_unchanged():
    res = "[S1] Заголовок — http://a\nтекст\n[S2] Второй — http://b"
    s = el._summarize_tool_call(tc("search_internet", query="q"), res)
    assert "[S1] Заголовок — http://a | [S2] Второй — http://b" in s


def test_compactor_marks_success():
    ai_msg = AIMessage(content="", tool_calls=[{"name": "execute_python_code", "args": {"code": "1"}, "id": "c1"}])
    tm = ToolMessage(content=str(OK_RESULT), tool_call_id="c1")
    text = _extract_facts_from_pairs([ai_msg, tm], [0], el._summarize_tool_call)
    assert "[успех]" in text and "ОШИБКА" not in text


@pytest.fixture
def h(monkeypatch, fast_sleep):
    return Harness(monkeypatch)


def test_process_query_trace_success(h):
    """Сквозь цикл: _invoke_tool превращает dict в строку, в trace всё равно [успех]."""
    h.add_tool("execute_python_code", result=OK_RESULT)
    h.models["coder"].script = [ai("", [("execute_python_code", {"code": "print(1)"})]), "Готово"]
    out = h.run()
    raw_trace = out["tool_trace"]
    h.summarize_trace.assert_not_called()
    assert "execute_python_code() [успех]" in raw_trace
    assert "ОШИБКА" not in raw_trace


def test_process_query_trace_failure(h):
    h.add_tool("execute_python_code", result=FAIL_RESULT)
    h.models["coder"].script = [ai("", [("execute_python_code", {"code": "1/0"})]), "Упало"]
    out = h.run()
    raw_trace = out["tool_trace"]
    h.summarize_trace.assert_not_called()
    assert "[ОШИБКА]" in raw_trace and "ZeroDivisionError" in raw_trace
