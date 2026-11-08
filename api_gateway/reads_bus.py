"""
Шина отметок прочтения общего чата: Redis + дебаунсер 1 с.

Фронт (groupInteraction.js) шлёт только новый максимум видимого сообщения,
не чаще раза в секунду, в POST /api/group/{room}/read. Гейтвей кладёт его в
  gc:read:pending   zset  member "<room_uid>:<user_id>" -> score last_read_id
  gc:read:names     hash  "<user_id>" -> username (для списка «кто прочитал»)
ZADD GT обновляет score только вверх: опоздавший запрос с меньшим id не
затрёт больший (хеш с «последний пишет» здесь не подходит).

Воркер, как в reactions_bus: ждёт сигнал, ещё DEBOUNCE секунд копит, затем
RENAME pending -> inflight и одним POST отдаёт всё в chats_service
/group/reads/apply (UPSERT с GREATEST + событие read в комнату). При ошибке
отметки возвращаются в pending тем же ZADD GT. Раз в SWEEP секунд
подбираются хвосты: pending без сигнала и брошенные inflight.

Если Redis недоступен, отметка уходит в chats_service напрямую.
"""
import asyncio
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PENDING_KEY = "gc:read:pending"
NAMES_KEY = "gc:read:names"
INFLIGHT_PREFIX = "gc:read:inflight:"
DEBOUNCE = float(os.getenv("GC_READ_DEBOUNCE", "1.0"))
SWEEP = float(os.getenv("GC_READ_SWEEP", "5.0"))
INFLIGHT_TTL = 60
# room_uid идёт в member через ":" — пускаем только безопасные имена
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:  # pragma: no cover
    logger.warning("reads_bus: redis init failed: %s", e)
    _redis = None

_http = None
_chats_url = None
_wake: asyncio.Event | None = None
_task: asyncio.Task | None = None


def bind(http_client, chats_url):
    global _http, _chats_url
    _http = http_client
    _chats_url = chats_url


def valid_room(room_uid: str) -> bool:
    return bool(ROOM_RE.match(room_uid or ""))


def _member(room_uid: str, user_id: int) -> str:
    return f"{room_uid}:{int(user_id)}"


def _ops_from(pairs, names: dict) -> list:
    ops = []
    for member, score in pairs or []:
        try:
            room_uid, uid = member.rsplit(":", 1)
            ops.append({"room_uid": room_uid, "user_id": int(uid),
                        "username": (names or {}).get(uid) or "",
                        "last_read_id": int(score)})
        except (ValueError, AttributeError, TypeError):
            continue
    return ops


async def _post(ops: list) -> bool:
    if not ops:
        return True
    try:
        resp = await _http.post(f"{_chats_url}/group/reads/apply", json={"ops": ops})
    except Exception as e:
        logger.error("reads_bus: apply failed: %s", e)
        return False
    if resp.status_code >= 500:
        logger.error("reads_bus: apply %s %s", resp.status_code, resp.text[:200])
        return False
    if resp.status_code >= 400:
        logger.warning("reads_bus: apply rejected %s %s", resp.status_code, resp.text[:200])
    return True


async def enqueue(room_uid: str, user_id: int, username: str, last_read_id: int) -> str:
    """Возвращает 'redis' | 'direct'."""
    member = _member(room_uid, user_id)
    if _redis is not None:
        try:
            pipe = _redis.pipeline()
            pipe.zadd(PENDING_KEY, {member: int(last_read_id)}, gt=True)
            if username:
                pipe.hset(NAMES_KEY, str(int(user_id)), username[:128])
            await pipe.execute()
            if _wake is not None:
                _wake.set()
            return "redis"
        except Exception as e:
            logger.warning("reads_bus: redis down, direct apply: %s", e)
    await _post([{"room_uid": room_uid, "user_id": int(user_id),
                  "username": username or "", "last_read_id": int(last_read_id)}])
    return "direct"


async def _requeue(pairs) -> None:
    if pairs:
        await _redis.zadd(PENDING_KEY, {m: s for m, s in pairs}, gt=True)


async def _drain(key: str) -> None:
    pairs = await _redis.zrange(key, 0, -1, withscores=True)
    if pairs:
        uids = sorted({m.rsplit(":", 1)[-1] for m, _ in pairs})
        vals = await _redis.hmget(NAMES_KEY, uids)
        names = {u: v for u, v in zip(uids, vals) if v}
        if not await _post(_ops_from(pairs, names)):
            await _requeue(pairs)
    await _redis.delete(key)


async def flush_once() -> int:
    if _redis is None:
        return 0
    key = f"{INFLIGHT_PREFIX}{uuid.uuid4().hex}"
    try:
        await _redis.rename(PENDING_KEY, key)
    except Exception:
        return 0
    await _redis.expire(key, INFLIGHT_TTL * 10)
    n = await _redis.zcard(key)
    await _drain(key)
    return int(n or 0)


async def recover_orphans() -> int:
    if _redis is None:
        return 0
    n = 0
    async for key in _redis.scan_iter(match=f"{INFLIGHT_PREFIX}*", count=100):
        ttl = await _redis.ttl(key)
        if ttl is not None and 0 <= ttl < INFLIGHT_TTL * 9:
            await _requeue(await _redis.zrange(key, 0, -1, withscores=True))
            await _redis.delete(key)
            n += 1
    return n


async def _loop():
    while True:
        try:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=SWEEP)
                await asyncio.sleep(DEBOUNCE)
            except asyncio.TimeoutError:
                await recover_orphans()
            _wake.clear()
            await flush_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("reads_bus loop: %s", e)
            await asyncio.sleep(1)


def start():
    global _wake, _task
    if _task is None:
        _wake = asyncio.Event()
        _task = asyncio.create_task(_loop())


async def stop():
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
        _task = None
    try:
        await flush_once()
    except Exception as e:
        logger.warning("reads_bus stop flush: %s", e)
