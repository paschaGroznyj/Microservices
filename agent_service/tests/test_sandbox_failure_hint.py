"""Подсказка после упавшего execute_python_code не должна вести к админским тулам."""
import asyncio
import time

import pytest

import agent.tools.sandbox.tool_execute_sandbox as sb

ADMIN_ONLY = ("edit_sandbox_file", "read_file", "write_sandbox_file", "/sandbox/executed_scripts")
STDERR = 'Traceback (most recent call last):\n  File "script.py", line 2, in <module>\nNameError: name \'y\' is not defined'
CODE = "x = 1\nprint(y)\n"


@pytest.fixture(autouse=True)
def _no_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "_save_script", lambda code, run_id, ok, reusable=False: f"/sandbox/executed_scripts/failed/{run_id}.py")
    monkeypatch.setattr(sb, "SAVE_DIR", str(tmp_path))


def run(result, workspace_id=None):
    return asyncio.run(sb._process_sandbox_result(result, time.monotonic(), "rid", CODE, False,
                                                  workspace_id=workspace_id))


def test_hint_without_workspace_uses_only_user_tools():
    r = run({"ok": False, "stdout": "", "stderr": STDERR, "exit_code": 1})
    hint = r["hint"]
    assert r["error_line"] == 2 and "print(y)" in r["error_context"]
    assert "execute_python_code" in hint and "create_workspace" in hint
    assert not any(a in hint for a in ADMIN_ONLY)


def test_hint_with_workspace_points_to_workspace_script():
    r = run({"ok": False, "stdout": "", "stderr": STDERR, "exit_code": 1,
             "saved_script": "output/.scripts/rid.py"}, workspace_id="ws1")
    hint = r["hint"]
    assert r["workspace_id"] == "ws1"
    assert "ws1" in hint and "output/.scripts/rid.py" in hint
    assert "read_workspace_file" in hint and "edit_workspace_file" in hint
    assert not any(a in hint for a in ADMIN_ONLY)


def test_success_has_no_hint():
    r = run({"ok": True, "stdout": "1", "stderr": "", "exit_code": 0})
    assert "hint" not in r


def test_docstrings_do_not_suggest_admin_tools():
    src = open(sb.__file__, encoding="utf-8").read()
    assert "via edit_sandbox_file" not in src
    assert "через edit_sandbox_file" not in src
    assert "available via read_file" not in src


def test_saved_script_path_only_for_reusable():
    plain = run({"ok": True, "stdout": "1", "stderr": "", "exit_code": 0})
    assert "saved_script_path" not in plain
    reus = asyncio.run(sb._process_sandbox_result(
        {"ok": True, "stdout": "1", "stderr": "", "exit_code": 0},
        time.monotonic(), "rid", CODE, True))
    assert reus["saved_script_path"].endswith("rid.py")
