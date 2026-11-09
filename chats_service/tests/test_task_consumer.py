"""task_consumer: upsert по seq, push в гейтвей, атомарная побудка (SET NX), пачки, повтор после сбоев."""
import asyncio
import json

import pytest

from app import task_consumer as tc
from app.db.tasks_db import event_row

NOW = 1_000_000.0


class FakeTasks:
    """agent_tasks в памяти: те же выборки, что TasksDB (без фильтра по возрасту)."""
    def __init__(self):
        self.rows = {}
        self.mid = None
        self.mark_calls = []

    async def upsert_event(self, ev):
        r = self.rows.get(ev["task_id"])
        if r and r["seq"] >= ev["seq"]:
            return False
        row = event_row(ev)
        row["owner"] = json.loads(row["owner"])
        row["finished_at"] = ev.get("finished_at")
        row["message_id"] = self.mid
        row["triggered_at"] = r["triggered_at"] if r else None
        if r and not r["notify_agent"]:
            row["notify_agent"] = False
        self.rows[ev["task_id"]] = row
        return self.mid

    async def chat_state(self, chat_id, max_age_s, gateways):
        rows = [dict(r) for r in self.rows.values()
                if r["chat_id"] == chat_id and r["triggered_at"] is None and r["notify_agent"]
                and r["status"] in ("completed", "failed")
                and (r["owner"].get("gateway_target") or "main") in gateways]
        rows.sort(key=lambda r: r["finished_at"] or 0)
        open_req = {r["request_id"] for r in self.rows.values()
                    if r["chat_id"] == chat_id and r["status"] in ("pending", "running") and r["request_id"]}
        return rows, open_req

    async def pending_chats(self, max_age_s, gateways):
        return sorted({r["chat_id"] for r in self.rows.values()
                       if r["triggered_at"] is None and r["notify_agent"]
                       and r["status"] in ("completed", "failed")})

    async def mark_triggered_many(self, ids):
        self.mark_calls.append(list(ids))
        out = []
        for i in ids:
            if self.rows[i]["triggered_at"] is None:
                self.rows[i]["triggered_at"] = NOW
                out.append(i)
        return out

    async def disable_notify(self, task_id):
        await self.disable_notify_many([task_id])

    async def disable_notify_many(self, ids):
        for i in ids:
            self.rows[i]["notify_agent"] = False


class FakeRedis:
    def __init__(self, flag=None):
        self.flag, self.kv, self.down = flag, {}, False

    def _chk(self):
        if self.down:
            raise ConnectionError("redis down")

    def ping(self):
        self._chk()
        return True

    def get(self, key):
        self._chk()
        if key == tc.AUTOTRIGGER_KEY:
            return self.flag
        return self.kv.get(key)

    def exists(self, key):
        self._chk()
        return int(key in self.kv)

    def set(self, key, val, nx=False, ex=None):
        self._chk()
        if nx and key in self.kv:
            return None
        self.kv[key] = val
        return True

    def delete(self, key):
        self._chk()
        self.kv.pop(key, None)


class FakeProducer:
    def __init__(self, fail=0):
        self.sent, self.fail = [], fail

    def send(self, topic, key=None, value=None):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("kafka down")
        self.sent.append((topic, key, value))

    def flush(self):
        pass


class Resp:
    status_code = 200


def ev(task_id="t1", seq=1, status="running", event="progress", gw="main", req="r0",
       finished_at=None, user_id=56, chat_id="c1", **kw):
    e = {"event": event, "task_id": task_id, "chat_id": chat_id, "seq": seq, "status": status,
         "label": f"Задача {task_id}", "kind": "script", "notify_agent": True,
         "finished_at": finished_at if finished_at is not None else (NOW - 5 if status in tc.FINISHED else None),
         "owner": {"user_id": user_id, "gateway_target": gw, "agent_model": "designer",
                   "selected_tools": ["sessions"], "context_size": "medium", "request_id": req}}
    e.update(kw)
    return e


def done(task_id="t1", status="completed", seq=9, **kw):
    return ev(task_id=task_id, seq=seq, status=status, event="finished", **kw)


async def _direct(fn, *a):
    return fn(*a)


def make(redis="default", producer=None, clock=lambda: NOW, **kw):
    posts = []
    rc = FakeRedis() if redis == "default" else redis
    c = tc.TaskConsumer(FakeTasks(), rc, producer or FakeProducer(),
                        http_post=lambda url, json, timeout: posts.append((url, json)) or Resp(),
                        run_blocking=_direct, poll_s=0, clock=clock, **kw)
    return c, posts


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TASK_AUTOTRIGGER", "1")


def triggers(c):
    return [v for (t, k, v) in c.producer.sent if t == tc.CHAT_MESSAGES_TOPIC]


# ---------- события и карточки ----------

def test_progress_pushed_without_owner_and_no_trigger():
    c, posts = make()
    res = run(c.handle_event(ev()))
    assert res == {"stored": True, "pushed": True, "trigger": False}
    url, body = posts[0]
    assert url.endswith("api_gateway:7000/internal/task_event")
    assert body["task"]["task_id"] == "t1" and "owner" not in body["task"]
    assert c.pending == set()


def test_stale_event_is_ignored():
    c, posts = make()
    run(c.handle_event(ev(seq=5)))
    assert run(c.handle_event(ev(seq=3)))["stored"] is False and len(posts) == 1


def test_sigma_gateway_routing():
    c, posts = make()
    run(c.handle_event(ev(gw="sigma")))
    assert posts[0][0].startswith("http://api_gateway_sigma:7010/")


def test_event_row_kind_fallback_and_owner_snapshot():
    r = event_row(ev(kind="weird"))
    assert r["kind"] == "script" and r["user_id"] == 56 and r["request_id"] == "r0"
    assert json.loads(r["owner"])["gateway_target"] == "main"


# ---------- побудка ----------

def test_finished_triggers_once_and_marks_after_send():
    c, posts = make()
    assert run(c.handle_event(done()))["trigger"] is True
    assert run(c.tick()) == {"c1": "sent"}
    assert len(triggers(c)) == 1 and c.tasks.rows["t1"]["triggered_at"] == NOW
    p = triggers(c)[0]
    assert p["user_id"] == -2 and p["task_trigger"] is True and p["owner_user_id"] == 56
    assert p["task"]["task_id"] == "t1" and [t["task_id"] for t in p["tasks"]] == ["t1"]
    assert p["selected_tools"] == ["sessions"] and p["agent_model"] == "designer"
    gw = [b for u, b in posts if u.endswith("/internal/task_trigger")][0]
    assert gw["task_ids"] == ["t1"] and gw["request_id"] == p["request_id"]
    # флаг занят ровно этой побудкой, в формате run_status
    lock = json.loads(c.redis.kv["agent:running:c1"])
    assert lock["request_id"] == p["request_id"] and lock["source"] == "task_trigger"
    # повторное finished: агент занят, потом свободен — но всё уже отправлено
    run(c.handle_event(done(seq=10)))
    c.redis.kv.clear()
    assert run(c.tick()) == {"c1": "nothing"}
    assert len(triggers(c)) == 1 and c.pending == set()


@pytest.mark.parametrize("status,notify,gw", [("cancelled", True, "main"), ("failed", False, "main"),
                                              ("completed", True, "telegram")])
def test_no_trigger_cases(status, notify, gw):
    c, posts = make()
    run(c.handle_event(done(status=status, gw=gw, notify_agent=notify)))
    run(c.tick())
    assert triggers(c) == []
    if gw == "telegram":
        assert posts == [] and c.pending == set()


def test_redis_flag_overrides_env(monkeypatch):
    assert tc.autotrigger_enabled(FakeRedis(flag="0")) is False
    monkeypatch.setenv("TASK_AUTOTRIGGER", "0")
    assert tc.autotrigger_enabled(FakeRedis(flag="1")) is True
    assert tc.autotrigger_enabled(FakeRedis(flag=None)) is False


def _pushed_tasks(posts):
    return [p[1]["task"] for p in posts if p[0].endswith("/internal/task_event")]


def test_disabled_autotrigger_marks_awaiting_user_and_does_not_wake():
    c, posts = make(redis=FakeRedis(flag="0"))
    run(c.handle_event(done()))
    assert c.pending == set() and triggers(c) == []
    assert c.tasks.rows["t1"]["notify_agent"] is True and c.tasks.rows["t1"]["triggered_at"] is None
    assert _pushed_tasks(posts)[-1]["awaiting_user"] is True


def test_awaiting_user_only_for_finished_notify_tasks():
    c, posts = make(redis=FakeRedis(flag="0"))
    run(c.handle_event(ev(seq=1)))                                   # progress
    run(c.handle_event(done(task_id="t2", status="cancelled")))      # отмена
    assert [t["awaiting_user"] for t in _pushed_tasks(posts)] == [False, False]


def test_enabled_autotrigger_no_awaiting_user():
    c, posts = make()
    run(c.handle_event(done()))
    assert _pushed_tasks(posts)[-1]["awaiting_user"] is False and c.pending == {"c1"}


def test_manual_mode_then_enabled_sweep_picks_unsent():
    c, _ = make(redis=FakeRedis(flag="0"))
    run(c.handle_event(done()))
    c.redis.flag = "1"
    run(c.sweep())
    assert c.pending == {"c1"}


def test_busy_agent_waits_until_flag_released():
    c, _ = make()
    c.redis.kv["agent:running:c1"] = json.dumps({"request_id": "user-req"})
    run(c.handle_event(done()))
    assert run(c.tick()) == {"c1": "busy"} and triggers(c) == []
    assert c.tasks.rows["t1"]["triggered_at"] is None   # отметка только после отправки
    del c.redis.kv["agent:running:c1"]
    assert run(c.tick()) == {"c1": "sent"} and len(triggers(c)) == 1


def test_lost_race_to_user_message_keeps_waiting():
    """exists() видит свободно, но пользователь успел поставить флаг до SET NX."""
    c, _ = make()
    real_exists = c.redis.exists

    def exists_then_user_writes(key):
        r = real_exists(key)
        c.redis.kv[key] = json.dumps({"request_id": "user-req"})
        return r
    c.redis.exists = exists_then_user_writes
    run(c.handle_event(done()))
    assert run(c.tick()) == {"c1": "busy"}
    assert triggers(c) == [] and json.loads(c.redis.kv["agent:running:c1"])["request_id"] == "user-req"


def test_two_consumers_share_redis_only_one_wakes_agent():
    shared = FakeRedis()
    a, _ = make(redis=shared)
    b, _ = make(redis=shared)
    b.tasks = a.tasks
    run(a.handle_event(done()))
    b.pending.add("c1")
    a.tasks.rows["t1"]["triggered_at"] = None
    ra, rb = run(a.dispatch_chat("c1")), run(b.dispatch_chat("c1"))
    assert sorted([ra, rb]) == ["busy", "sent"]
    assert len(triggers(a)) + len(triggers(b)) == 1


def test_two_tasks_finished_together_one_batch():
    c, posts = make()
    run(c.handle_event(done("t1", finished_at=NOW - 3)))
    run(c.handle_event(done("t2", status="failed", finished_at=NOW - 2, error="boom")))
    assert run(c.tick()) == {"c1": "sent"}
    assert len(triggers(c)) == 1
    p = triggers(c)[0]
    assert [t["task_id"] for t in p["tasks"]] == ["t1", "t2"]
    assert p["message_id"] == f"tasks-{p['request_id']}"
    assert c.tasks.mark_calls == [["t1", "t2"]]


def test_batch_waits_for_sibling_of_same_request():
    c, _ = make()
    run(c.handle_event(ev("t2", seq=1, status="running")))
    run(c.handle_event(done("t1", finished_at=NOW - 5)))
    assert run(c.tick()) == {"c1": "waiting"} and triggers(c) == []
    assert "agent:running:c1" not in c.redis.kv          # флаг не занимаем, пока ждём
    run(c.handle_event(done("t2", seq=5, finished_at=NOW - 1)))
    assert run(c.tick()) == {"c1": "sent"}
    assert [t["task_id"] for t in triggers(c)[0]["tasks"]] == ["t1", "t2"]


def test_cancelled_sibling_releases_batch():
    c, _ = make()
    run(c.handle_event(ev("t2", seq=1, status="running")))
    run(c.handle_event(done("t1")))
    run(c.tick())
    run(c.handle_event(done("t2", status="cancelled", seq=5)))
    assert run(c.tick()) == {"c1": "sent"}
    assert [t["task_id"] for t in triggers(c)[0]["tasks"]] == ["t1"]


def test_batch_wait_limit_sends_ready_ones():
    c, _ = make(batch_wait_s=60)
    run(c.handle_event(ev("t2", seq=1, status="running")))
    run(c.handle_event(done("t1", finished_at=NOW - 61)))
    assert run(c.tick()) == {"c1": "sent"}
    assert [t["task_id"] for t in triggers(c)[0]["tasks"]] == ["t1"]


def test_other_request_does_not_block_batch():
    c, _ = make()
    run(c.handle_event(ev("t9", seq=1, status="running", req="r-other")))
    run(c.handle_event(done("t1")))
    assert run(c.tick()) == {"c1": "sent"}


def test_kafka_failure_releases_flag_and_retries():
    c, _ = make(producer=FakeProducer(fail=1))
    run(c.handle_event(done()))
    assert run(c.tick()) == {"c1": "send_failed"}
    assert "agent:running:c1" not in c.redis.kv and c.tasks.rows["t1"]["triggered_at"] is None
    assert run(c.tick()) == {"c1": "sent"} and len(triggers(c)) == 1


def test_release_does_not_drop_foreign_flag():
    c, _ = make()
    c.redis.kv["agent:running:c1"] = json.dumps({"request_id": "user-req"})
    c._release(c.redis, "c1", "my-req")
    assert "agent:running:c1" in c.redis.kv


def test_redis_down_does_not_wake_agent():
    rc = FakeRedis()
    c, _ = make(redis=rc)
    run(c.handle_event(done()))
    rc.down = True
    assert run(c.tick()) == {"c1": "redis_down"} and triggers(c) == []
    rc.down = False
    assert run(c.tick()) == {"c1": "sent"}


def test_redis_missing_at_start_reconnects():
    rc = FakeRedis()
    c, _ = make(redis=None, redis_factory=lambda: rc)
    c.redis = None
    run(c.handle_event(done()))
    assert run(c.tick()) == {"c1": "sent"} and c.redis is rc


def test_restart_sweep_picks_untriggered():
    old, _ = make()
    run(old.handle_event(done()))          # консьюмер упал до побудки
    fresh, _ = make()
    fresh.tasks = old.tasks
    assert fresh.pending == set()
    assert run(fresh.tick()) == {"c1": "sent"}
    assert len(triggers(fresh)) == 1


def test_other_owner_goes_in_next_batch():
    c, _ = make()
    run(c.handle_event(done("t1", user_id=56, finished_at=NOW - 3)))
    run(c.handle_event(done("t2", user_id=77, req="r7", finished_at=NOW - 2)))
    assert run(c.tick()) == {"c1": "sent"}
    assert [t["task_id"] for t in triggers(c)[0]["tasks"]] == ["t2"]
    c.redis.kv.clear()
    assert run(c.tick()) == {"c1": "sent"}
    assert triggers(c)[1]["owner_user_id"] == 56


def test_poll_throttle():
    t = [NOW]
    c, _ = make(clock=lambda: t[0])
    c.poll_s = 2
    run(c.handle_event(done()))
    c.redis.kv["agent:running:c1"] = "x"
    assert run(c.tick()) == {"c1": "busy"}
    assert run(c.tick()) == {}
    t[0] += 2
    assert run(c.tick()) == {"c1": "busy"}
