"""task_consumer: upsert по seq, push в нужный гейтвей, автотриггер один раз, ожидание агента."""
import asyncio

import pytest

from app import task_consumer as tc
from app.db.tasks_db import event_row


class FakeTasks:
    def __init__(self):
        self.seq, self.triggered, self.mid = {}, set(), None

    async def upsert_event(self, ev):
        if self.seq.get(ev["task_id"], -1) >= ev["seq"]:
            return False
        self.seq[ev["task_id"]] = ev["seq"]
        return self.mid

    async def mark_triggered(self, task_id):
        if task_id in self.triggered:
            return False
        self.triggered.add(task_id)
        return True


class FakeRedis:
    def __init__(self, flag=None, busy=()):
        self.flag, self.busy = flag, list(busy)

    def get(self, key):
        return self.flag

    def exists(self, key):
        return self.busy.pop(0) if self.busy else 0


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, topic, key=None, value=None):
        self.sent.append((topic, key, value))

    def flush(self):
        pass


class Resp:
    status_code = 200


def ev(seq=1, status="running", event="progress", gw="main", **kw):
    e = {"event": event, "task_id": "t1", "chat_id": "c1", "seq": seq, "status": status,
         "label": "Парсинг", "kind": "script", "notify_agent": True,
         "owner": {"user_id": 56, "gateway_target": gw, "agent_model": "designer",
                   "selected_tools": ["sessions"], "context_size": "medium", "request_id": "r0"}}
    e.update(kw)
    return e


def make(redis=None, wait_s=0):
    posts, spawned = [], []
    c = tc.TaskConsumer(FakeTasks(), redis, FakeProducer(),
                        http_post=lambda url, json, timeout: posts.append((url, json)) or Resp(),
                        spawn=lambda fn, *a: spawned.append(a), wait_s=wait_s, poll_s=0,
                        sleep=lambda s: None)
    return c, posts, spawned


def run(coro):
    return asyncio.run(coro)


def test_progress_pushed_without_owner_and_no_trigger(monkeypatch):
    monkeypatch.setenv("TASK_AUTOTRIGGER", "1")
    c, posts, spawned = make()
    res = run(c.handle_event(ev()))
    assert res == {"stored": True, "pushed": True, "trigger": False}
    url, body = posts[0]
    assert url.endswith("api_gateway:7000/internal/task_event")
    assert body["task"]["task_id"] == "t1" and "owner" not in body["task"]
    assert spawned == []


def test_stale_event_is_ignored(monkeypatch):
    c, posts, _ = make()
    run(c.handle_event(ev(seq=5)))
    res = run(c.handle_event(ev(seq=3)))
    assert res["stored"] is False and len(posts) == 1


def test_finished_triggers_once(monkeypatch):
    monkeypatch.setenv("TASK_AUTOTRIGGER", "1")
    c, posts, spawned = make()
    assert run(c.handle_event(ev(seq=7, status="completed", event="finished")))["trigger"] is True
    assert run(c.handle_event(ev(seq=8, status="completed", event="finished")))["trigger"] is False
    assert len(spawned) == 1


@pytest.mark.parametrize("status,notify,gw", [("cancelled", True, "main"), ("failed", False, "main"),
                                              ("completed", True, "telegram")])
def test_no_trigger_cases(monkeypatch, status, notify, gw):
    monkeypatch.setenv("TASK_AUTOTRIGGER", "1")
    c, posts, spawned = make()
    run(c.handle_event(ev(seq=2, status=status, event="finished", gw=gw, notify_agent=notify)))
    assert spawned == []
    if gw == "telegram":
        assert posts == []


def test_redis_flag_overrides_env(monkeypatch):
    monkeypatch.setenv("TASK_AUTOTRIGGER", "1")
    assert tc.autotrigger_enabled(FakeRedis(flag="0")) is False
    monkeypatch.setenv("TASK_AUTOTRIGGER", "0")
    assert tc.autotrigger_enabled(FakeRedis(flag="1")) is True
    assert tc.autotrigger_enabled(FakeRedis(flag=None)) is False
    c, _, spawned = make(redis=FakeRedis(flag="0"))
    run(c.handle_event(ev(seq=2, status="completed", event="finished")))
    assert spawned == []


def test_trigger_waits_for_idle_and_sends_service_input():
    c, posts, _ = make(redis=FakeRedis(busy=[1, 1, 0]), wait_s=60)
    assert c.trigger(ev(seq=9, status="failed", event="finished", error="boom")) is True
    url, body = posts[-1]
    assert url.endswith("/internal/task_trigger") and body["task_id"] == "t1"
    topic, key, payload = c.producer.sent[0]
    assert topic == "chat-messages" and key == -2
    assert payload["user_id"] == -2 and payload["task_trigger"] is True
    assert payload["owner_user_id"] == 56 and payload["request_id"] == body["request_id"]
    assert payload["selected_tools"] == ["sessions"] and payload["agent_model"] == "designer"
    assert payload["task"]["owner"] == {"user_id": 56} and payload["task"]["error"] == "boom"


def test_trigger_gives_up_when_agent_stays_busy():
    c, posts, _ = make(redis=FakeRedis(busy=[1] * 100), wait_s=0)
    assert c.trigger(ev(seq=9, status="completed", event="finished")) is False
    assert c.producer.sent == [] and posts == []


def test_sigma_gateway_routing():
    c, posts, _ = make()
    run(c.handle_event(ev(gw="sigma")))
    assert posts[0][0].startswith("http://api_gateway_sigma:7010/")


def test_event_row_kind_fallback():
    r = event_row(ev(kind="weird"))
    assert r["kind"] == "script" and r["user_id"] == 56 and r["request_id"] == "r0"
