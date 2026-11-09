"""
Шина реакций общего чата: Redis + дебаунсер 1 с.

Фронт (groupInteraction.js) копит клики 1 с и шлёт пачку в
POST /api/group/reactions. Гейтвей кладёт её в Redis-хеш
  gc:react:pending   field "<message_id>:<user_id>" -> emoji ('' = снять)
Последний клик пользователя по сообщению перезаписывает прежний, поэтому
пачка всегда содержит итоговое состояние, а повтор безопасен (операция
«установить», не «переключить»).

Воркер ждёт первый enqueue, ещё DEBOUNCE секунд копит, затем атомарно
переименовывает pending в inflight-ключ и одним POST отдаёт всё в
chats_service /group/reactions/apply (БД одной транзакцией + событие
reactions в комнату). При ошибке поля возвращаются в pending через HSETNX,
чтобы не затереть более свежие клики. Раз в SWEEP секунд подбираются
хвосты: pending без сигнала (другой воркер, рестарт) и брошенные inflight.

Если Redis недоступен, пачка уходит в chats_service напрямую.
"""
import asyncio
import logging
import os
import uuid

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
PENDING_KEY = "gc:react:pending"
INFLIGHT_PREFIX = "gc:react:inflight:"
DEBOUNCE = float(os.getenv("GC_REACT_DEBOUNCE", "1.0"))
SWEEP = float(os.getenv("GC_REACT_SWEEP", "5.0"))
# inflight старше этого считаем брошенным упавшим воркером
INFLIGHT_TTL = 60

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:  # pragma: no cover
    logger.warning("reactions_bus: redis init failed: %s", e)
    _redis = None

_http = None
_chats_url = None
_wake: asyncio.Event | None = None
_task: asyncio.Task | None = None


def bind(http_client, chats_url):
    global _http, _chats_url
    _http = http_client
    _chats_url = chats_url


def _field(message_id: int, user_id: int) -> str:
    return f"{int(message_id)}:{int(user_id)}"


def _ops_from(mapping: dict) -> list:
    ops = []
    for field, emoji in (mapping or {}).items():
        try:
            mid, uid = field.split(":", 1)
            ops.append({"message_id": int(mid), "user_id": int(uid), "emoji": emoji or ""})
        except (ValueError, AttributeError):
            continue
    return ops


async def _post(ops: list) -> bool:
    if not ops:
        return True
    try:
        resp = await _http.post(f"{_chats_url}/group/reactions/apply", json={"ops": ops})
    except Exception as e:
        logger.error("reactions_bus: apply failed: %s", e)
        return False
    if resp.status_code >= 500:
        logger.error("reactions_bus: apply %s %s", resp.status_code, resp.text[:200])
        return False
    if resp.status_code >= 400:
        # 4xx повторять бессмысленно (битая пачка) — отбрасываем
        logger.warning("reactions_bus: apply rejected %s %s", resp.status_code, resp.text[:200])
    return True


async def enqueue(user_id: int, items: list) -> str:
    """items: [{message_id, emoji}]. Возвращает 'redis' | 'direct'."""
    mapping = {_field(it["message_id"], user_id): it.get("emoji") or "" for it in items}
    if not mapping:
        return "redis"
    if _redis is not None:
        try:
            await _redis.hset(PENDING_KEY, mapping=mapping)
            if _wake is not None:
                _wake.set()
            return "redis"
        except Exception as e:
            logger.warning("reactions_bus: redis down, direct apply: %s", e)
    await _post(_ops_from(mapping))
    return "direct"


async def _drain(key: str) -> None:
    """Отдаёт содержимое inflight-ключа; при ошибке возвращает в pending."""
    data = await _redis.hgetall(key)
    if data and not await _post(_ops_from(data)):
        pipe = _redis.pipeline()
        for f, v in data.items():
            pipe.hsetnx(PENDING_KEY, f, v)
        await pipe.execute()
    await _redis.delete(key)


async def flush_once() -> int:
    """Одна выгрузка pending. Возвращает число отданных полей."""
    if _redis is None:
        return 0
    key = f"{INFLIGHT_PREFIX}{uuid.uuid4().hex}"
    try:
        await _redis.rename(PENDING_KEY, key)
    except Exception:
        # ключа нет (пусто или забрал другой воркер)
        return 0
    await _redis.expire(key, INFLIGHT_TTL * 10)
    n = await _redis.hlen(key)
    await _drain(key)
    return int(n or 0)


async def recover_orphans() -> int:
    """inflight-ключи упавших воркеров: TTL ушёл ниже порога — значит брошен."""
    if _redis is None:
        return 0
    n = 0
    async for key in _redis.scan_iter(match=f"{INFLIGHT_PREFIX}*", count=100):
        ttl = await _redis.ttl(key)
        if ttl is not None and 0 <= ttl < INFLIGHT_TTL * 9:
            data = await _redis.hgetall(key)
            if data:
                pipe = _redis.pipeline()
                for f, v in data.items():
                    pipe.hsetnx(PENDING_KEY, f, v)
                await pipe.execute()
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
            logger.error("reactions_bus loop: %s", e)
            await asyncio.sleep(1)


def start():
    global _wake, _task
    if _task is None:
        _wake = asyncio.Event()
        _task = asyncio.create_task(_loop())


async def stop():
    """Перед остановкой отдаём накопленное, чтобы клики не ждали рестарта."""
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
        logger.warning("reactions_bus stop flush: %s", e)
