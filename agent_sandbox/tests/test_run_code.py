"""_run_code_sync: real subprocess execution in an isolated run_dir."""
import os


def test_run_code_captures_stdout(appmod, tmp_path):
    res = appmod._run_code_sync("print('hello world')", timeout=30,
                                run_dir=str(tmp_path / "run"))
    assert res["ok"] is True
    assert res["exit_code"] == 0
    assert "hello world" in res["stdout"]


def test_run_code_nonzero_exit_and_stderr(appmod, tmp_path):
    res = appmod._run_code_sync("import sys; sys.stderr.write('boom'); sys.exit(2)",
                                timeout=30, run_dir=str(tmp_path / "run"))
    assert res["ok"] is False
    assert res["exit_code"] == 2
    assert "boom" in res["stderr"]


def test_run_code_timeout(appmod, tmp_path):
    res = appmod._run_code_sync("import time; time.sleep(5)", timeout=1,
                                run_dir=str(tmp_path / "run"))
    assert res["ok"] is False
    assert "таймаут" in res["error"].lower()


def test_run_code_missing_module_hint(appmod, tmp_path):
    res = appmod._run_code_sync("import nonexistent_pkg_xyz", timeout=30,
                                run_dir=str(tmp_path / "run"))
    assert res["ok"] is False
    assert res["error"] is not None
    assert "sandbox" in res["error"].lower()


def test_run_code_collects_output_file(appmod, tmp_path):
    ws = tmp_path / "ws"; (ws / "output").mkdir(parents=True)
    code = "open('output/result.txt','w').write('done')"
    res = appmod._run_code_sync(code, timeout=30, run_dir=str(tmp_path / "run"),
                                ws_path=str(ws))
    assert res["ok"] is True
    names = {f.filename for f in res["files"]}
    assert "result.txt" in names


def test_run_code_persist_script(appmod, tmp_path):
    ws = tmp_path / "ws"; (ws / "output").mkdir(parents=True)
    res = appmod._run_code_sync("print(1)", timeout=30, run_dir=str(tmp_path / "run"),
                                ws_path=str(ws), persist_script=True, run_id="abc")
    assert res["saved_script"] == "output/.scripts/abc.py"
    assert (ws / "output" / ".scripts" / "abc.py").exists()
    assert (ws / "output" / ".scripts" / "last_script.py").exists()
