"""task_gw.py: task_update идёт в чат через push, триггер регистрирует request_id и ставит флаг работы."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

import task_gw


def client(**hooks):
    app = FastAPI()
    app.include_router(task_gw.router)
    if hooks:
        task_gw.bind(**hooks)
    else:
        task_gw._hooks.clear()
    return TestClient(app)


def test_unbound_returns_503():
    c = client()
    assert c.post("/internal/task_event", json={"chat_id": "c", "task": {}}).status_code == 503


def test_task_event_pushes_task_update():
    got = []

    async def push(chat_id, ev):
        got.append((chat_id, ev))
    c = client(push=push)
    r = c.post("/internal/task_event", json={"chat_id": "c1", "task": {"task_id": "t1", "status": "running"}})
    assert r.status_code == 200
    assert got == [("c1", {"type": "task_update", "chat_id": "c1", "task": {"task_id": "t1", "status": "running"}})]


def test_trigger_registers_marks_and_sends_status():
    reg, run, pushed = {}, [], []
    c = client(push=lambda cid, ev: pushed.append(ev), register=lambda rid, cid: reg.__setitem__(rid, cid),
               mark_running=lambda cid, rid: run.append((cid, rid)))
    r = c.post("/internal/task_trigger", json={"chat_id": "c1", "request_id": "r1", "task_id": "t1", "label": "Парсинг"})
    assert r.status_code == 200
    assert reg == {"r1": "c1"} and run == [("c1", "r1")]
    assert pushed[0]["type"] == "agent_status" and "Парсинг" in pushed[0]["status"]
    assert all(e.get("type") != "bot_message" for e in pushed)
