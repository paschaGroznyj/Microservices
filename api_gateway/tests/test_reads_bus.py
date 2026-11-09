"""reads_bus: zset с ZADD GT, дебаунс-выгрузка, возврат при ошибке, fallback."""
import asyncio
import fnmatch

import pytest

import reads_bus as rb


def run(c):
    return asyncio.run(c)


class FakeRedis:
    """Минимум redis.asyncio: zset с gt, hash, rename, ttl, scan."""
    def __init__(self):
        self.z, self.h, self.ttls = {}, {}, {}
    async def zadd(self, k, mapping, gt=False):
        d = self.z.setdefault(k, {})
        for m, s in mapping.items():
            if not gt or m not in d or s > d[m]:
                d[m] = float(s)
    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v
    async def hmget(self, k, fields):
        d = self.h.get(k, {})
        return [d.get(f) for f in fields]
    async def zrange(self, k, a, b, withscores=False):
        return sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
    async def zcard(self, k):
        return len(self.z.get(k, {}))
    async def rename(self, a, b):
        if a not in self.z:
            raise Exception("no such key")
        self.z[b] = self.z.pop(a)
    async def expire(self, k, t):
        self.ttls[k] = t
    async def ttl(self, k):
        return self.ttls.get(k, -1)
    async def delete(self, k):
        self.z.pop(k, None); self.ttls.pop(k, None)
    async def scan_iter(self, match, count=100):
        for k in list(self.z):
            if fnmatch.fnmatch(k, match):
                yield k
    def pipeline(self):
        r = self
        class P:
            def __init__(s): s.ops = []
            def zadd(s, *a, **kw): s.ops.append(("zadd", a, kw))
            def hset(s, *a): s.ops.append(("hset", a, {}))
            async def execute(s):
                for name, a, kw in s.ops:
                    await getattr(r, name)(*a, **kw)
        return P()


class FakeResp:
    def __init__(self, code): self.status_code, self.text = code, ""


class FakeHttp:
    def __init__(self):
        self.calls, self.code, self.exc = [], 200, None
    async def post(self, url, json):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return FakeResp(self.code)


@pytest.fixture
def env(monkeypatch):
    r, http = FakeRedis(), FakeHttp()
    monkeypatch.setattr(rb, "_redis", r)
    rb.bind(http, "http://chats")
    return r, http


def test_enqueue_only_grows(env):
    r, _ = env
    run(rb.enqueue("global", 7, "Петя", 50))
    run(rb.enqueue("global", 7, "Петя", 30))   # опоздавший меньший
    assert r.z[rb.PENDING_KEY] == {"global:7": 50.0}
    assert r.h[rb.NAMES_KEY] == {"7": "Петя"}


def test_flush_posts_with_names_and_cleans(env):
    r, http = env
    run(rb.enqueue("global", 7, "Петя", 50))
    run(rb.enqueue("global", 8, "", 40))
    assert run(rb.flush_once()) == 2
    (url, body), = http.calls
    assert url == "http://chats/group/reads/apply"
    ops = sorted(body["ops"], key=lambda o: o["user_id"])
    assert ops == [{"room_uid": "global", "user_id": 7, "username": "Петя", "last_read_id": 50},
                   {"room_uid": "global", "user_id": 8, "username": "", "last_read_id": 40}]
    assert not any(k.startswith(rb.INFLIGHT_PREFIX) for k in r.z)
    assert rb.PENDING_KEY not in r.z
    assert run(rb.flush_once()) == 0


def test_flush_error_requeues_keeping_max(env):
    r, http = env
    run(rb.enqueue("global", 7, "", 50))
    http.code = 503
    async def scenario():
        key = rb.INFLIGHT_PREFIX + "x"
        await r.rename(rb.PENDING_KEY, key)
        await rb.enqueue("global", 7, "", 60)   # пока inflight висел, пользователь читал дальше
        await rb._drain(key)
    run(scenario())
    assert r.z[rb.PENDING_KEY] == {"global:7": 60.0}


def test_4xx_dropped(env):
    r, http = env
    run(rb.enqueue("global", 7, "", 50))
    http.code = 422
    run(rb.flush_once())
    assert rb.PENDING_KEY not in r.z


def test_redis_down_direct(monkeypatch):
    http = FakeHttp()
    class Down:
        def pipeline(self):
            class P:
                def zadd(s, *a, **k): pass
                def hset(s, *a): pass
                async def execute(s): raise ConnectionError("down")
            return P()
    monkeypatch.setattr(rb, "_redis", Down())
    rb.bind(http, "http://chats")
    assert run(rb.enqueue("global", 7, "a", 5)) == "direct"
    assert http.calls[0][1]["ops"] == [{"room_uid": "global", "user_id": 7,
                                        "username": "a", "last_read_id": 5}]


def test_recover_orphans(env):
    r, _ = env
    r.z[rb.INFLIGHT_PREFIX + "old"] = {"global:7": 50.0}
    r.ttls[rb.INFLIGHT_PREFIX + "old"] = 10
    r.z[rb.INFLIGHT_PREFIX + "fresh"] = {"global:8": 1.0}
    r.ttls[rb.INFLIGHT_PREFIX + "fresh"] = rb.INFLIGHT_TTL * 10
    assert run(rb.recover_orphans()) == 1
    assert r.z[rb.PENDING_KEY] == {"global:7": 50.0}
    assert rb.INFLIGHT_PREFIX + "fresh" in r.z


def test_valid_room():
    assert rb.valid_room("global") and rb.valid_room("team_1-a")
    assert not rb.valid_room("a:b") and not rb.valid_room("") and not rb.valid_room("x" * 65)


def test_loop_debounces(env, monkeypatch):
    r, http = env
    monkeypatch.setattr(rb, "DEBOUNCE", 0.05)
    async def scenario():
        rb._task = None
        rb.start()
        await rb.enqueue("global", 7, "", 10)
        await asyncio.sleep(0.01)
        await rb.enqueue("global", 7, "", 20)
        await asyncio.sleep(0.15)
        await rb.stop()
    run(scenario())
    assert len(http.calls) == 1
    assert http.calls[0][1]["ops"][0]["last_read_id"] == 20
