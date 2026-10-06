"""Redis-хранилище режима мобильного оркестратора (lite <-> agent).

ЕДИНЫЙ ИСТОЧНИК ПРАВДЫ по ключу/TTL/счётчику. Читают оба сервиса:
  - chats_service (роутер): перед Kafka смотрит режим, декрементит счётчик;
  - agent_service: при call_agent=true включает agent-режим.

Контракт:
  ключ   = mobile:mode:{chat_id}
  value = JSON {"mode":"agent","turns_left":N}
  отсутствие ключа = lite (дефолт)
  TTL   = 24ч (пока Redis жив, сутки)
Счётчик AGENT_TURNS = 5 ходов агента, потом авто-выход в lite.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
MODE_KEY = "mobile:mode:{chat_id}"
MODE_TTL = 24 * 3600          # сутки
AGENT_TURNS = 5               # ходов агента до авто-выхода

_redis = None


def _get_redis():
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def enable_agent_mode(chat_id: str, turns: int = AGENT_TURNS) -> None:
    """Включить agent-режим на N ходов (вызывает agent_service при call_agent=true)."""
    r = _get_redis()
    key = MODE_KEY.format(chat_id=chat_id)
    await r.set(key, json.dumps({"mode": "agent", "turns_left": int(turns)}), ex=MODE_TTL)


async def get_mode(chat_id: str) -> dict:
    """Текущий режим. Нет ключа/ошибка -> lite (fail-open)."""
    try:
        r = _get_redis()
        raw = await r.get(MODE_KEY.format(chat_id=chat_id))
        if not raw:
            return {"mode": "lite", "turns_left": 0}
        data = json.loads(raw)
        if data.get("mode") == "agent" and int(data.get("turns_left", 0)) > 0:
            return {"mode": "agent", "turns_left": int(data["turns_left"])}
        return {"mode": "lite", "turns_left": 0}
    except Exception as e:
        logger.warning("[mobile-mode] get_mode fail-open lite: %s", e)
        return {"mode": "lite", "turns_left": 0}


async def consume_agent_turn(chat_id: str) -> dict:
    """Списать один ход agent-режима (вызывает роутер chats_service).

    Возвращает {"tier": "agent"|"lite", "farewell": bool}:
      - tier=agent  -> этот запрос идёт на полного агента;
      - farewell=True -> это ПОСЛЕДНИЙ ход, после него режим сброшен в lite;
      - tier=lite   -> режима нет, обычный lite-путь.
    fail-open: любая ошибка -> lite.
    """
    try:
        r = _get_redis()
        key = MODE_KEY.format(chat_id=chat_id)
        raw = await r.get(key)
        if not raw:
            return {"tier": "lite", "farewell": False}
        data = json.loads(raw)
        if data.get("mode") != "agent":
            return {"tier": "lite", "farewell": False}
        left = int(data.get("turns_left", 0))
        if left <= 0:
            await r.delete(key)
            return {"tier": "lite", "farewell": False}
        left -= 1
        if left <= 0:
            # последний ход — сбрасываем режим, сигналим прощание
            await r.delete(key)
            return {"tier": "agent", "farewell": True}
        await r.set(key, json.dumps({"mode": "agent", "turns_left": left}), ex=MODE_TTL)
        return {"tier": "agent", "farewell": False}
    except Exception as e:
        logger.warning("[mobile-mode] consume_agent_turn fail-open lite: %s", e)
        return {"tier": "lite", "farewell": False}
