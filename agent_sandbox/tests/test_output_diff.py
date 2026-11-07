"""saved_files при workspace: только файлы, созданные или изменённые этим вызовом."""
import os


def _names(res):
    return [getattr(f, "filename", None) or f["filename"] for f in res["files"]]


def _ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / "output").mkdir(parents=True)
    (ws / "output" / "old.txt").write_text("old")
    (ws / "output" / "big.zip").write_bytes(b"x" * 1000)
    return ws


def test_workspace_returns_only_new_file(appmod, tmp_path):
    ws = _ws(tmp_path)
    res = appmod._run_code_sync("open('output/new.png','wb').write(b'png')",
                                timeout=30, run_dir=str(tmp_path / "run"), ws_path=str(ws))
    assert res["ok"], res
    assert _names(res) == ["new.png"]


def test_workspace_no_changes_no_files(appmod, tmp_path):
    ws = _ws(tmp_path)
    res = appmod._run_code_sync("print('hi')", timeout=30,
                                run_dir=str(tmp_path / "run"), ws_path=str(ws))
    assert res["ok"], res
    assert _names(res) == []


def test_workspace_modified_file_returned(appmod, tmp_path):
    ws = _ws(tmp_path)
    old = ws / "output" / "old.txt"
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    res = appmod._run_code_sync("open('output/old.txt','w').write('old')",
                                timeout=30, run_dir=str(tmp_path / "run"), ws_path=str(ws))
    assert _names(res) == ["old.txt"]


def test_workspace_newest_first(appmod, tmp_path):
    ws = _ws(tmp_path)
    code = ("import time\n"
            "open('output/a.txt','w').write('a'); time.sleep(0.05)\n"
            "open('output/b.txt','w').write('b')\n")
    res = appmod._run_code_sync(code, timeout=30, run_dir=str(tmp_path / "run"), ws_path=str(ws))
    assert _names(res) == ["b.txt", "a.txt"]


def test_shell_in_workspace_without_changes(appmod, tmp_path):
    ws = _ws(tmp_path)
    res = appmod._run_commands_sync("ls output && echo ok", timeout=30,
                                    run_dir=str(tmp_path / "run"), ws_path=str(ws))
    assert res["ok"], res
    assert _names(res) == []


def test_shell_in_workspace_new_file(appmod, tmp_path):
    ws = _ws(tmp_path)
    res = appmod._run_commands_sync("echo r > output/report.txt", timeout=30,
                                    run_dir=str(tmp_path / "run"), ws_path=str(ws))
    assert _names(res) == ["report.txt"]


def test_without_workspace_all_output_files(appmod, tmp_path):
    code = ("open('output/b.txt','w').write('b')\n"
            "open('output/a.txt','w').write('a')\n")
    res = appmod._run_code_sync(code, timeout=30, run_dir=str(tmp_path / "run"))
    assert _names(res) == ["a.txt", "b.txt"]
