# presence.py
"""
Онлайн-присутствие пользователей через Redis (общий для обоих гейтвеев).

Модель данных:
  online:{gw}          ZSET  member=username, score=unix_ts последнего пинга.
                       Онлайн = кто пинговался за последние WINDOW секунд.
  peak:{gw}:{YYYYMMDD} STRING максимум одновременно онлайн за сутки (UTC), TTL 48ч.

gw — "main" или "sigma". Два процесса пишут в один Redis — метрики честные.
Все вызовы безопасны: если Redis недоступен, функции не роняют запрос
(presence — не критичный путь), а логируют и возвращают безопасный дефолт.
"""
import os
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WINDOW = 60          # секунд — окно актуальности онлайна
PEAK_TTL = 60 * 60 * 48
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:  # модуля нет / баты конфига
    logger.warning("presence: redis init failed: %s", e)
    _redis = None


def _day_key(gw, ts):
    day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
    return f"peak:{gw}:{day}"


async def touch(gw: str, username: str) -> None:
    """Отметить пользователя живым на интерфейсе gw. Вызывается на heartbeat."""
    if _redis is None or not username:
        return
    key = f"online:{gw}"
    now = time.time()
    try:
        pipe = _redis.pipeline()
        pipe.zadd(key, {username: now})
        pipe.zremrangebyscore(key, 0, now - WINDOW)
        pipe.zcount(key, now - WINDOW, "+inf")
        res = await pipe.execute()
        online = int(res[-1])
        # обновить пик за сутки
        pkey = _day_key(gw, now)
        cur = await _redis.get(pkey)
        if cur is None or online > int(cur):
            await _redis.set(pkey, online)
            await _redis.expire(pkey, PEAK_TTL)
    except Exception as e:
        logger.debug("presence.touch failed: %s", e)


async def count_online(gw: str) -> int:
    """Сколько пользователей онлайн сейчас на интерфейсе gw."""
    if _redis is None:
        return 0
    key = f"online:{gw}"
    now = time.time()
    try:
        await _redis.zremrangebyscore(key, 0, now - WINDOW)
        return int(await _redis.zcount(key, now - WINDOW, "+inf"))
    except Exception as e:
        logger.debug("presence.count_online failed: %s", e)
        return 0


async def online_usernames(gw: str) -> list:
    """Список username, кто онлайн сейчас (для админки/дебага)."""
    if _redis is None:
        return []
    key = f"online:{gw}"
    now = time.time()
    try:
        return await _redis.zrangebyscore(key, now - WINDOW, "+inf")
    except Exception as e:
        logger.debug("presence.online_usernames failed: %s", e)
        return []


async def peak_today(gw: str) -> int:
    """Максимум одновременно онлайн за текущие сутки (UTC)."""
    if _redis is None:
        return 0
    try:
        cur = await _redis.get(_day_key(gw, time.time()))
        return int(cur) if cur else 0
    except Exception as e:
        logger.debug("presence.peak_today failed: %s", e)
        return 0
