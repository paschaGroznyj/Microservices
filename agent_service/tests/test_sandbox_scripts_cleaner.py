"""TTL архива скриптов /sandbox и проверка пути в save_to_code_library."""
import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.core import sandbox_scripts_cleaner as ssc
from agent.tools.code_base import tool_work_with_codebase as wc


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    ex, re_ = tmp_path / "executed_scripts", tmp_path / "reusable"
    (ex / "failed").mkdir(parents=True); re_.mkdir()
    monkeypatch.setattr(ssc, "EXECUTED_DIR", ex)
    monkeypatch.setattr(ssc, "REUSABLE_DIR", re_)
    return tmp_path, ex, re_


def mk(path, age):
    path.write_text("print(1)")
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def test_sweep(dirs):
    root, ex, re_ = dirs
    old = mk(ex / "old.py", 3600)
    fresh = mk(ex / "fresh.py", 60)
    failed = mk(ex / "failed" / "f.py", 3600)
    orphan = mk(re_ / "orphan.py", 3600)
    lib = mk(re_ / "lib.py", 10 ** 6)
    draft = mk(root / "draft.py", 10 ** 6)  # вне архива
    n = ssc.sweep_scripts(keep={str(lib)}, ttl=1800, dirs=[ex, re_])
    assert n == 3
    assert not old.exists() and not failed.exists() and not orphan.exists()
    assert fresh.exists() and lib.exists() and draft.exists()


def run_once(monkeypatch, get_keep):
    class Stop(Exception):
        pass

    async def stop(_):
        raise Stop
    monkeypatch.setattr(ssc.asyncio, "sleep", stop)
    with pytest.raises(Stop):
        asyncio.run(ssc.start_scripts_cleaner(get_keep, ttl=1800))


def test_loop_keeps_library(dirs, monkeypatch):
    _, ex, re_ = dirs
    lib, orphan, old = mk(re_ / "lib.py", 3600), mk(re_ / "o.py", 3600), mk(ex / "x.py", 3600)

    async def keep():
        return [str(lib)]
    run_once(monkeypatch, keep)
    assert lib.exists() and not orphan.exists() and not old.exists()


def test_loop_skips_when_db_fails(dirs, monkeypatch):
    _, ex, re_ = dirs
    files = [mk(re_ / "lib.py", 3600), mk(ex / "x.py", 3600)]

    async def keep():
        raise RuntimeError("db down")
    run_once(monkeypatch, keep)
    assert all(f.exists() for f in files)


def save_tool(db):
    tools = wc.make_work_codebase_tool(SimpleNamespace(db=db))
    return next(t for t in tools if t.name == "save_to_code_library")


def payload(path):
    import json
    return json.dumps({"title": "t", "description": "d", "code_type": "util", "path": str(path)})


def test_save_rejects_outside_reusable(dirs):
    root, ex, re_ = dirs
    db = SimpleNamespace(save_to_code_library=AsyncMock(return_value=1))
    tool = save_tool(db)
    for bad in (mk(ex / "a.py", 0), mk(root / "draft.py", 0), re_ / "missing.py"):
        out = asyncio.run(tool.ainvoke({"json_description": payload(bad)}))
        assert "reusable=True" in out, out
    db.save_to_code_library.assert_not_called()


def test_save_accepts_reusable(dirs):
    _, _, re_ = dirs
    ok = mk(re_ / "good.py", 0)
    db = SimpleNamespace(save_to_code_library=AsyncMock(return_value=7))
    out = asyncio.run(save_tool(db).ainvoke({"json_description": payload(ok)}))
    assert "id=7" in out
    assert db.save_to_code_library.call_args.kwargs["path"] == os.path.realpath(ok)
