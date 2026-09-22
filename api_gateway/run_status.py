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
RUN_TTL = int(os.getenv("AGENT_RUN_TTL", "900"))  # 15 минут
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:  # модуля нет / кривой конфиг
    logger.warning("run_status: redis init failed: %s", e)
    _redis = None


def _key(chat_id) -> str:
    return f"agent:running:{chat_id}"


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
