"""push_gw.py (/sw.js, /api/push/*) и дебаунс notify_enabled в run_status."""
import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import push_gw
import run_status


class FakeRedis:
    def __init__(self):
        self.kv, self.ttl, self.lists = {}, {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        self.kv[k] = v
        self.ttl[k] = ex
        return True

    async def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)
        return 1

    async def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)
        return len(self.lists[k])


class FakeResp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b = code, body or {"ok": True}

    def json(self):
        return self._b


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, json))
        return FakeResp()

    async def get(self, url, timeout=None, **kw):
        self.calls.append(("GET", url))
        return FakeResp(body={"public_key": "BPUB"})


@pytest.fixture()
def env(tmp_path):
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "sw.js").write_text("self.addEventListener('push',()=>{})")
    redis, http = FakeRedis(), FakeHttp()
    user = {"v": {"user_id": 56, "username": "pavel"}}

    async def get_user(request):
        return user["v"]

    push_gw.bind(http_client=http, chats_url="http://chats", get_current_user=get_user,
                 get_redis=lambda: redis, static_dir=tmp_path, gw="main")
    app = FastAPI()
    app.include_router(push_gw.router)
    return TestClient(app), redis, http, user


def test_sw_served_from_root_with_scope_header(env):
    c, *_ = env
    r = c.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert r.headers["content-type"].startswith("application/javascript")


def test_unauthenticated_401(env):
    c, _, _, user = env
    user["v"] = None
    for path in ("/api/push/subscribe", "/api/push/unsubscribe", "/api/push/viewing"):
        assert c.post(path, json={}).status_code == 401
    assert c.get("/api/push/vapid").status_code == 401


def test_vapid_cached(env):
    c, _, http, _ = env
    assert c.get("/api/push/vapid").json() == {"public_key": "BPUB"}
    c.get("/api/push/vapid")
    assert [x for x in http.calls if x[0] == "GET"] == [("GET", "http://chats/push/vapid")]


def test_subscribe_proxies_with_user_and_gw(env):
    c, _, http, _ = env
    r = c.post("/api/push/subscribe", json={"endpoint": "https://fcm/x", "keys": {"p256dh": "P", "auth": "A"}, "gw": "sigma"},
               headers={"user-agent": "UA"})
    assert r.status_code == 200
    _, url, headers, body = http.calls[-1]
    assert url == "http://chats/push/subscribe" and headers == {"X-User-Id": "56"}
    assert body == {"endpoint": "https://fcm/x", "keys": {"p256dh": "P", "auth": "A"}, "gw": "main", "user_agent": "UA"}


def test_viewing_set_switch_and_hide(env):
    c, redis, _, _ = env
    c.post("/api/push/viewing", json={"chat_id": "c1", "visible": True})
    assert redis.kv["viewing:56:c1"] == "1" and redis.ttl["viewing:56:c1"] == push_gw.VIEWING_TTL
    c.post("/api/push/viewing", json={"chat_id": "c2", "visible": True, "prev_chat_id": "c1"})
    assert "viewing:56:c1" not in redis.kv and "viewing:56:c2" in redis.kv
    c.post("/api/push/viewing", json={"chat_id": "c2", "visible": False})
    assert "viewing:56:c2" not in redis.kv


def test_viewing_rejects_bad_chat_id(env):
    c, redis, _, _ = env
    c.post("/api/push/viewing", json={"chat_id": "a b*", "visible": True})
    assert redis.kv == {}


# ---------- дебаунс notify_enabled ----------

def test_notify_debounce_sends_last_value_once(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(run_status, "_redis", redis)

    async def scenario():
        for v in (True, False, True):
            assert await run_status.push_notify_setting(56, v, delay=0.05)
            # state виден сразу — GET после клика не ждёт дебаунса
            assert json.loads(redis.kv["user:settings:state:56"])["notify_enabled"] is v
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.15)

    asyncio.run(scenario())
    q = redis.lists.get(run_status.USER_SETTINGS_QUEUE_KEY, [])
    assert len(q) == 1
    ev = json.loads(q[0])
    assert ev["user_id"] == 56 and ev["notify_enabled"] is True


def test_notify_two_spaced_clicks_both_sent(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(run_status, "_redis", redis)

    async def scenario():
        await run_status.push_notify_setting(56, True, delay=0.03)
        await asyncio.sleep(0.08)
        await run_status.push_notify_setting(56, False, delay=0.03)
        await asyncio.sleep(0.08)

    asyncio.run(scenario())
    vals = [json.loads(x)["notify_enabled"] for x in redis.lists[run_status.USER_SETTINGS_QUEUE_KEY]]
    assert vals == [True, False]


def test_notify_without_redis_returns_false(monkeypatch):
    monkeypatch.setattr(run_status, "_redis", None)
    assert asyncio.run(run_status.push_notify_setting(56, True)) is False
