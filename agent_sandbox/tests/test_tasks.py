"""Фоновые задачи песочницы (sandbox_tasks.py): протокол ##STAGE/##PROGRESS,
события жизненного цикла, отмена, ошибки."""
import asyncio
import uuid

import pytest
from fastapi import HTTPException

import sandbox_tasks as st


@pytest.fixture
def env(appmod, ws_root, monkeypatch):
    st._tasks.clear()
    events = []

    async def fake_send(ev):
        events.append(ev)

    async def noop_after(*a, **k):
        return None

    monkeypatch.setattr(st, "_send", fake_send)
    monkeypatch.setitem(st._hooks, "after_run", noop_after)
    wid = appmod._create_workspace_dir()
    return {"wid": wid, "events": events}


async def _run_task(env, code, **kw):
    tid = uuid.uuid4().hex
    req = st.SubmitRequest(code=code, workspace_id=env["wid"], task_id=tid, chat_id="c1", **kw)
    await st.submit_task(req)
    for _ in range(400):
        await asyncio.sleep(0.05)
        if st._tasks[tid]["status"] in st.TERMINAL:
            break
    await asyncio.sleep(0.1)
    return tid


def _ev(env, tid):
    return sorted((e for e in env["events"] if e["task_id"] == tid), key=lambda e: e["seq"])


# ── парсер и этапы ─────────────────────────────────────────────────

def test_parse_marker():
    assert st.parse_marker("##STAGE Парсинг\n") == ("stage", "Парсинг")
    assert st.parse_marker("##PROGRESS 40 Парсинг 4/10") == ("progress", {"percent": 40, "text": "Парсинг 4/10"})
    assert st.parse_marker("##PROGRESS 250%") == ("progress", {"percent": 100, "text": ""})
    assert st.parse_marker("## PROGRESS 10") == (None, None)
    assert st.parse_marker("обычная строка") == (None, None)


def test_apply_stage_planned_skip_and_append():
    t = {"stages": st.init_stages(["A", "B", "C"]), "progress": {}}
    st.apply_stage(t, "A")
    st.apply_stage(t, "C")                       # B пропущен
    assert [s["status"] for s in t["stages"]] == ["done", "skipped", "active"]
    st.apply_stage(t, "D")                       # не в плане -> добавлен
    assert [s["title"] for s in t["stages"]][-1] == "D" and t["stages"][2]["status"] == "done"
    st.close_stages(t, "failed")
    assert t["stages"][-1]["status"] == "failed"


def test_close_stages_completed_marks_pending_done():
    t = {"stages": st.init_stages(["A", "B"])}
    st.apply_stage(t, "A")
    st.close_stages(t, "completed")
    assert [s["status"] for s in t["stages"]] == ["done", "done"]


def test_stage_limit_goes_to_progress_text():
    t = {"stages": st.init_stages([f"s{i}" for i in range(st.MAX_STAGES)]), "progress": {}}
    st.apply_stage(t, "лишний")
    assert len(t["stages"]) == st.MAX_STAGES and t["progress"]["text"] == "лишний"


def test_strip_markers():
    assert st.strip_markers("##STAGE a\nok\n  ##PROGRESS 5\nend") == "ok\nend"


# ── жизненный цикл ─────────────────────────────────────────────────

def test_completed_task_events_and_stages(env):
    code = (
        "print('##STAGE Загрузка', flush=True)\n"
        "print('##PROGRESS 50 половина', flush=True)\n"
        "print('hello')\n"
        "print('##STAGE Сохранение', flush=True)\n"
        "open('output/r.txt','w').write('x')\n"
    )
    tid = asyncio.run(_run_task(env, code, label="Тест", kind="media", stages=["Загрузка", "Сохранение"]))
    evs = _ev(env, tid)
    assert evs[0]["event"] == "submitted" and evs[1]["event"] == "started"
    fin = evs[-1]
    assert fin["event"] == "finished" and fin["status"] == "completed" and fin["kind"] == "media"
    assert [s["status"] for s in fin["stages"]] == ["done", "done"]
    assert fin["progress"]["percent"] == 50
    assert "hello" in fin["stdout_tail"] and "##" not in fin["stdout_tail"]
    assert any(f["filename"] == "r.txt" for f in fin["files"])
    assert len({e["seq"] for e in evs}) == len(evs)


def test_failed_task_has_stderr_and_exit_code(env):
    tid = asyncio.run(_run_task(env, "print('##STAGE Шаг', flush=True)\nraise ValueError('boom')\n"))
    fin = _ev(env, tid)[-1]
    assert fin["status"] == "failed" and fin["exit_code"] == 1
    assert "ValueError: boom" in fin["stderr_tail"]
    assert fin["error"] == "Скрипт завершился с кодом 1"
    assert fin["stages"][0]["status"] == "failed"


def test_cancel_running_task(env):
    async def scenario():
        tid = uuid.uuid4().hex
        await st.submit_task(st.SubmitRequest(code="import time\ntime.sleep(30)\n",
                                              workspace_id=env["wid"], task_id=tid, chat_id="c1"))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if st._tasks[tid].get("_proc") is not None:
                break
        r = await st.cancel_task(tid)
        assert r["status"] == "cancelling"
        for _ in range(200):
            await asyncio.sleep(0.05)
            if st._tasks[tid]["status"] in st.TERMINAL:
                break
        await asyncio.sleep(0.1)
        return tid

    tid = asyncio.run(scenario())
    fin = _ev(env, tid)[-1]
    assert fin["status"] == "cancelled" and fin["elapsed_ms"] < 10_000


def test_heartbeat_without_markers(env, monkeypatch):
    monkeypatch.setattr(st, "HEARTBEAT_SEC", 0.3)
    tid = asyncio.run(_run_task(env, "import time\ntime.sleep(2.5)\n"))
    assert any(e["event"] == "progress" and e["elapsed_ms"] > 0 for e in _ev(env, tid))


def test_submit_errors(env):
    async def scenario():
        with pytest.raises(HTTPException) as e:
            await st.submit_task(st.SubmitRequest(code="1", workspace_id="nope", task_id="t", chat_id="c"))
        assert e.value.status_code == 404
        st._tasks["dup"] = {"status": "running"}
        with pytest.raises(HTTPException) as e:
            await st.submit_task(st.SubmitRequest(code="1", workspace_id=env["wid"], task_id="dup", chat_id="c"))
        assert e.value.status_code == 409
    asyncio.run(scenario())


def test_purge_expired():
    st._tasks.clear()
    st._tasks["old"] = {"status": "completed", "finished_at": 1.0}
    st._tasks["run"] = {"status": "running", "finished_at": None}
    assert st.purge_expired(now=1.0 + st.TASK_TTL_SECONDS + 1) == 1
    assert list(st._tasks) == ["run"]
