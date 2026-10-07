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

# Обратный путь мобилки (как в chats_service/endpoint_consumer.py). Контракт
# ОДИН: мобилка забирает mobile:reply:{chat_id} через GET /mobile/poll (BLPOP).
# При бесшовной эскалации реплику-мостик Haiku ("дай-ка подумать…") шлём
# НАПРЯМУЮ из agent_service в этот же список ПЕРЕД тем как дёрнуть тяжёлого
# агента — чтобы юзер услышал голос героя сразу, а не вместе с финальным
# ответом через 3-5 секунд. Мостик в БД НЕ сохраняется (это промежуточная
# реплика), поэтому идёт мимо Kafka/истории — только голос в Redis.
MOBILE_REPLY_KEY = "mobile:reply:{chat_id}"
MOBILE_REPLY_TTL = 300        # сек (совпадает с endpoint_consumer)
_BRIDGE_MAX_BYTES = 20 * 1024 * 1024  # 20MB — как лимит картинок в Telegram-пути

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


async def push_bridge_reply(chat_id: str, voice_path: str) -> bool:
    """Положить ОЗВУЧЕННЫЙ мостик-реплику Haiku напрямую в mobile:reply:{chat_id}.

    Вызывается ТОЛЬКО при бесшовной эскалации (Haiku -> агент) ПЕРЕД запуском
    тяжёлого агента. Юзер слышит голос героя сразу ("дай-ка подумать…"), пока
    агент работает; финальный ответ приедет вторым сообщением штатным путём
    (Kafka -> endpoint_consumer -> этот же список).

    Контракт reply ИДЕНТИЧЕН endpoint_consumer.push_to_mobile:
      {"text","files":[{"name","kind","ext","file_b64"}],"actions","message_id","ts"}
    Мостик: text="" (в БД/историю НЕ кладём), один voice-файл, actions=[].

    Возвращает True если реплика легла в Redis, иначе False (fail-open —
    эскалация продолжится, просто без раннего голоса).
    """
    import time
    import base64
    try:
        with open(voice_path, "rb") as fh:
            raw = fh.read()
        if len(raw) > _BRIDGE_MAX_BYTES:
            logger.warning("[mobile-bridge] voice %s = %d байт > лимита, пропускаю",
                           voice_path, len(raw))
            return False
        ext = voice_path.rsplit(".", 1)[-1].lower() if "." in voice_path else "ogg"
        name = voice_path.rsplit("/", 1)[-1] if "/" in voice_path else voice_path
        reply = {
            "text": "",  # промежуточная реплика — в историю/БД не идёт
            "files": [{
                "name": name,
                "kind": "voice",
                "ext": ext,
                "file_b64": base64.b64encode(raw).decode("ascii"),
            }],
            "actions": [],
            "message_id": None,
            "ts": int(time.time()),
        }
        r = _get_redis()
        key = MOBILE_REPLY_KEY.format(chat_id=str(chat_id))
        await r.rpush(key, json.dumps(reply))
        await r.expire(key, MOBILE_REPLY_TTL)
        logger.info("[mobile-bridge] мостик-голос положен в Redis key=%s (%d байт)", key, len(raw))
        return True
    except Exception as e:
        logger.error("[mobile-bridge] не удалось положить мостик в Redis chat=%s: %s", chat_id, e)
        return False
