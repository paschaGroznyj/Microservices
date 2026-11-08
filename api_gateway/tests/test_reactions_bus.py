"""reactions_bus: Redis-хеш, дебаунс-выгрузка, возврат при ошибке, fallback."""
import asyncio
import fnmatch

import pytest

import reactions_bus as rb


def run(c):
    return asyncio.run(c)


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []
    def hsetnx(self, k, f, v):
        self.ops.append((k, f, v))
    async def execute(self):
        for k, f, v in self.ops:
            await self.r.hsetnx(k, f, v)


class FakeRedis:
    def __init__(self):
        self.h, self.ttls = {}, {}
    async def hset(self, k, mapping):
        self.h.setdefault(k, {}).update(mapping)
    async def hsetnx(self, k, f, v):
        d = self.h.setdefault(k, {})
        if f not in d:
            d[f] = v
    async def hgetall(self, k):
        return dict(self.h.get(k, {}))
    async def hlen(self, k):
        return len(self.h.get(k, {}))
    async def rename(self, a, b):
        if a not in self.h:
            raise Exception("ERR no such key")
        self.h[b] = self.h.pop(a)
    async def expire(self, k, t):
        self.ttls[k] = t
    async def ttl(self, k):
        return self.ttls.get(k, -1)
    async def delete(self, k):
        self.h.pop(k, None); self.ttls.pop(k, None)
    def pipeline(self):
        return FakePipe(self)
    async def scan_iter(self, match, count=100):
        for k in list(self.h):
            if fnmatch.fnmatch(k, match):
                yield k


class Resp:
    def __init__(self, code):
        self.status_code, self.text = code, ""


class FakeHttp:
    def __init__(self, code=200, exc=None):
        self.code, self.exc, self.calls = code, exc, []
    async def post(self, url, json=None):
        self.calls.append((url, json))
        if self.exc:
            raise self.exc
        return Resp(self.code)


@pytest.fixture
def env(mocker):
    r, http = FakeRedis(), FakeHttp()
    mocker.patch.object(rb, "_redis", r)
    rb.bind(http, "http://chats")
    return r, http


def test_enqueue_last_click_wins(env):
    r, _ = env
    run(rb.enqueue(7, [{"message_id": 5, "emoji": "👍"}]))
    run(rb.enqueue(7, [{"message_id": 5, "emoji": "🔥"}, {"message_id": 6, "emoji": ""}]))
    assert r.h[rb.PENDING_KEY] == {"5:7": "🔥", "6:7": ""}


def test_flush_posts_batch_and_cleans(env):
    r, http = env
    run(rb.enqueue(7, [{"message_id": 5, "emoji": "👍"}]))
    run(rb.enqueue(8, [{"message_id": 5, "emoji": "❤️"}]))
    assert run(rb.flush_once()) == 2
    assert len(http.calls) == 1
    url, body = http.calls[0]
    assert url == "http://chats/group/reactions/apply"
    assert sorted((o["user_id"], o["emoji"]) for o in body["ops"]) == [(7, "👍"), (8, "❤️")]
    assert r.h == {}
    assert run(rb.flush_once()) == 0


def test_flush_error_requeues_without_clobbering(env, mocker):
    r, _ = env
    run(rb.enqueue(7, [{"message_id": 5, "emoji": "👍"}]))

    async def fail_then_newer(url, json=None):
        # пока пачка в полёте, пользователь кликнул ещё раз
        await r.hset(rb.PENDING_KEY, mapping={"5:7": "🔥"})
        raise OSError("down")
    mocker.patch.object(rb._http, "post", side_effect=fail_then_newer)
    run(rb.flush_once())
    assert r.h == {rb.PENDING_KEY: {"5:7": "🔥"}}


def test_4xx_dropped(env):
    r, http = env
    http.code = 422
    run(rb.enqueue(7, [{"message_id": 5, "emoji": "👍"}]))
    run(rb.flush_once())
    assert r.h == {}


def test_redis_down_direct(mocker):
    http = FakeHttp()
    rb.bind(http, "http://chats")
    broken = mocker.AsyncMock()
    broken.hset.side_effect = ConnectionError("no redis")
    mocker.patch.object(rb, "_redis", broken)
    assert run(rb.enqueue(7, [{"message_id": 5, "emoji": "👍"}])) == "direct"
    assert http.calls[0][1]["ops"] == [{"message_id": 5, "user_id": 7, "emoji": "👍"}]


def test_recover_orphans(env):
    r, _ = env
    old = rb.INFLIGHT_PREFIX + "dead"
    fresh = rb.INFLIGHT_PREFIX + "live"
    r.h[old] = {"5:7": "👍"}; r.ttls[old] = 10
    r.h[fresh] = {"6:7": "🔥"}; r.ttls[fresh] = rb.INFLIGHT_TTL * 10
    r.h[rb.PENDING_KEY] = {"5:7": "😢"}
    assert run(rb.recover_orphans()) == 1
    assert r.h[rb.PENDING_KEY] == {"5:7": "😢"}   # свежий клик не затёрт
    assert fresh in r.h and old not in r.h


def test_loop_debounces(env, mocker):
    r, http = env
    mocker.patch.object(rb, "DEBOUNCE", 0.2)

    async def scenario():
        rb._task = None
        rb.start()
        await rb.enqueue(7, [{"message_id": 5, "emoji": "👍"}])
        await asyncio.sleep(0.05)
        await rb.enqueue(8, [{"message_id": 5, "emoji": "🔥"}])
        await asyncio.sleep(0.1)
        early = len(http.calls)
        await asyncio.sleep(0.3)
        await rb.stop()
        return early
    assert run(scenario()) == 0
    assert len(http.calls) == 1 and len(http.calls[0][1]["ops"]) == 2
