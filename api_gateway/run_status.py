# run_status.py
"""
Флаг «агент в работе» через Redis (общий для обоих гейтвеев).

Задача: после F5 или переключения чата фронт должен понимать, что по
конкретному chat_id прямо сейчас крутится генерация (ещё не пришёл финал).
In-memory dict pending_requests это не даёт — он в памяти процесса и фронту
не виден. Redis-флаг переживает перезагрузку страницы.

Модель данных:
  agent:running:{chat_id}  STRING(json) = {request_id, model, started_at}
                           TTL RUN_TTL — самоочистка, если финал потеряется.

gw-агностично: оба гейтвея пишут/читают один и тот же ключ по chat_id.
Все вызовы безопасны: если Redis недоступен — не роняем запрос, логируем
и возвращаем безопасный дефолт (None / no-op).
"""
import os
import json
import time
import logging

logger = logging.getLogger(__name__)

# TTL флага. Если финал по какой-то причине не снял флаг (упал агент, потеря
# сообщения) — ключ протухнет сам и фронт не застрянет в «running» навсегда.
RUN_TTL = int(os.getenv("AGENT_RUN_TTL", "3600"))  # 60 минут
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:  # модуля нет / кривой конфиг
    logger.warning("run_status: redis init failed: %s", e)
    _redis = None


def _key(chat_id) -> str:
    return f"agent:running:{chat_id}"


def _loop_key(chat_id) -> str:
    return f"agent:loop:{chat_id}"


# TTL loop-флага: предохранитель на случай, если стоп потеряется и петля
# как-то повиснет. Сутки — петля живёт долго, но не вечно.
LOOP_TTL = int(os.getenv("AGENT_LOOP_TTL", "86400"))


async def mark_loop(chat_id) -> None:
    """Пометить, что по chat_id запущен САМОЦИКЛ. В отличие от agent:running
    (эфемерный флаг одного хода, гаснет между тиками), этот флаг стабилен и
    живёт всё время работы петли — по нему фронт после F5 понимает, что
    работа продолжается, хотя текущего тика прямо сейчас может и не быть."""
    if _redis is None or chat_id is None:
        return
    try:
        await _redis.set(_loop_key(chat_id), "1", ex=LOOP_TTL)
    except Exception as e:
        logger.debug("run_status.mark_loop failed: %s", e)


async def clear_loop(chat_id) -> None:
    """Снять флаг самоцикла: стоп-кнопка / deactivate. Идемпотентно."""
    if _redis is None or chat_id is None:
        return
    try:
        await _redis.delete(_loop_key(chat_id))
    except Exception as e:
        logger.debug("run_status.clear_loop failed: %s", e)


async def is_loop_active(chat_id) -> bool:
    """Активен ли самоцикл по chat_id (стабильно между тиками)."""
    if _redis is None or chat_id is None:
        return False
    try:
        return bool(await _redis.exists(_loop_key(chat_id)))
    except Exception as e:
        logger.debug("run_status.is_loop_active failed: %s", e)
        return False


async def mark_running(chat_id, request_id: str, model: str | None = None) -> None:
    """Пометить, что по chat_id стартовала генерация. Вызывается в send_message."""
    if _redis is None or chat_id is None:
        return
    try:
        payload = json.dumps({
            "request_id": request_id,
            "model": model,
            "started_at": time.time(),
        })
        await _redis.set(_key(chat_id), payload, ex=RUN_TTL)
    except Exception as e:
        logger.debug("run_status.mark_running failed: %s", e)


async def clear_running(chat_id) -> None:
    """Снять флаг: пришёл финал / стоп / ошибка. Идемпотентно."""
    if _redis is None or chat_id is None:
        return
    try:
        await _redis.delete(_key(chat_id))
    except Exception as e:
        logger.debug("run_status.clear_running failed: %s", e)


async def get_running(chat_id) -> dict | None:
    """Вернуть {request_id, model, started_at} если по chat_id идёт работа,
    иначе None. Фронт дёргает при загрузке/переключении чата."""
    if _redis is None or chat_id is None:
        return None
    try:
        raw = await _redis.get(_key(chat_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("run_status.get_running failed: %s", e)
        return None
