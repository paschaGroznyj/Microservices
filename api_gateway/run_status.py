# run_status.py
"""
Флаг «агент в работе» + тайминг по TTL и буфер рассуждений через Redis
(общий для обоих гейтвеев).
"""
import os
import json
import time
import logging

logger = logging.getLogger(__name__)

RUN_TTL = int(os.getenv("AGENT_RUN_TTL", "3600"))
LOOP_TTL = int(os.getenv("AGENT_LOOP_TTL", "86400"))
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:
    logger.warning("run_status: redis init failed: %s", e)
    _redis = None


def _key(chat_id) -> str:
    return f"agent:running:{chat_id}"


def _reasoning_key(chat_id) -> str:
    return f"agent:reasoning:{chat_id}"


def _loop_key(chat_id) -> str:
    return f"agent:loop:{chat_id}"


async def _compute_elapsed_from_ttl(chat_id, fallback_started_at: float | None = None) -> tuple[int, int | None]:
    """Считает прошедшее время (в секундах) через механизм TTL ключа в Redis."""
    if _redis is None or chat_id is None:
        if fallback_started_at:
            return max(1, int(round(time.time() - float(fallback_started_at)))), None
        return 1, None
    try:
        pttl = await _redis.pttl(_key(chat_id))
        if pttl is not None and pttl > 0:
            ttl_rem = max(0, int(round(pttl / 1000.0)))
            elapsed_ttl = max(0.0, float(RUN_TTL) - (float(pttl) / 1000.0))
            return max(1, int(round(elapsed_ttl))), ttl_rem
    except Exception as e:
        logger.debug("run_status._compute_elapsed_from_ttl failed: %s", e)
    if fallback_started_at:
        return max(1, int(round(max(0.0, time.time() - float(fallback_started_at))))), None
    return 1, None


async def mark_loop(chat_id) -> None:
    if _redis is None or chat_id is None:
        return
    try:
        await _redis.set(_loop_key(chat_id), "1", ex=LOOP_TTL)
    except Exception as e:
        logger.debug("run_status.mark_loop failed: %s", e)


async def clear_loop(chat_id) -> None:
    if _redis is None or chat_id is None:
        return
    try:
        await _redis.delete(_loop_key(chat_id))
    except Exception as e:
        logger.debug("run_status.clear_loop failed: %s", e)


async def is_loop_active(chat_id) -> bool:
    if _redis is None or chat_id is None:
        return False
    try:
        return bool(await _redis.exists(_loop_key(chat_id)))
    except Exception as e:
        logger.debug("run_status.is_loop_active failed: %s", e)
        return False


async def mark_running(
    chat_id,
    request_id: str,
    model: str | None = None,
    user_message: str | None = None,
    user_message_id: str | None = None,
) -> None:
    """Пометить старт генерации и инициализировать TTL-трекер времени и рассуждений."""
    if _redis is None or chat_id is None:
        return
    now = time.time()
    try:
        payload = json.dumps({
            "request_id": request_id,
            "model": model,
            "started_at": now,
            "run_ttl": RUN_TTL,
            "user_message": user_message,
            "user_message_id": user_message_id,
        })
        await _redis.set(_key(chat_id), payload, ex=RUN_TTL)
        reasoning_init = json.dumps({
            "request_id": request_id,
            "started_at": now,
            "run_ttl": RUN_TTL,
            "steps": [],
            "live_text": "",
            "is_final": False,
            "completed": False,
            "duration_sec": None,
            "user_message": user_message,
            "user_message_id": user_message_id,
        })
        await _redis.set(_reasoning_key(chat_id), reasoning_init, ex=RUN_TTL)
    except Exception as e:
        logger.debug("run_status.mark_running failed: %s", e)


async def append_reasoning_chunk(
    chat_id,
    chunk: str,
    kind: str = "live",
    msg_id: str | None = None,
) -> None:
    """Накапливает шаги рассуждений и фиксирует время размышлений по TTL Redis."""
    if _redis is None or chat_id is None:
        return
    try:
        raw = await _redis.get(_reasoning_key(chat_id))
        state = json.loads(raw) if raw else {
            "request_id": msg_id,
            "started_at": time.time(),
            "run_ttl": RUN_TTL,
            "steps": [],
            "live_text": "",
            "is_final": False,
            "completed": False,
            "duration_sec": None,
        }
        elapsed_sec, _ = await _compute_elapsed_from_ttl(chat_id, state.get("started_at"))
        if kind == "live":
            if not state.get("is_final") and chunk:
                state["live_text"] = (state.get("live_text") or "") + chunk
                state["duration_sec"] = elapsed_sec
        elif kind == "reasoning_end":
            text = (state.get("live_text") or "").strip()
            state["live_text"] = ""
            if text:
                steps = state.setdefault("steps", [])
                if not steps or steps[-1] != text:
                    steps.append(text)
            state["duration_sec"] = elapsed_sec
        elif kind == "final_commit":
            state["is_final"] = True
            state["live_text"] = ""
            if state.get("duration_sec") is None or state.get("steps"):
                state["duration_sec"] = elapsed_sec

        pttl = await _redis.pttl(_reasoning_key(chat_id))
        ex_sec = max(60, int(round(pttl / 1000.0))) if (pttl and pttl > 0) else RUN_TTL
        await _redis.set(_reasoning_key(chat_id), json.dumps(state), ex=ex_sec)
    except Exception as e:
        logger.debug("run_status.append_reasoning_chunk failed: %s", e)


async def get_reasoning_state(chat_id) -> dict | None:
    if _redis is None or chat_id is None:
        return None
    try:
        raw = await _redis.get(_reasoning_key(chat_id))
        if not raw:
            return None
        state = json.loads(raw)
        if not state.get("completed") and not state.get("is_final"):
            elapsed_sec, _ = await _compute_elapsed_from_ttl(chat_id, state.get("started_at"))
            state["elapsed_sec"] = elapsed_sec
            if state.get("duration_sec") is None:
                state["duration_sec"] = elapsed_sec
        return state
    except Exception as e:
        logger.debug("run_status.get_reasoning_state failed: %s", e)
        return None


async def clear_running(chat_id) -> None:
    """Снять флаг активной генерации и зафиксировать итоговое время рассуждений по TTL."""
    if _redis is None or chat_id is None:
        return
    try:
        raw_r = await _redis.get(_reasoning_key(chat_id))
        if raw_r:
            state = json.loads(raw_r)
            elapsed_sec, _ = await _compute_elapsed_from_ttl(chat_id, state.get("started_at"))
            if state.get("duration_sec") is None:
                state["duration_sec"] = elapsed_sec
            state["completed"] = True
            state["live_text"] = ""
            state.pop("user_message", None)
            state.pop("user_message_id", None)
            await _redis.set(_reasoning_key(chat_id), json.dumps(state), ex=RUN_TTL)
        await _redis.delete(_key(chat_id))
    except Exception as e:
        logger.debug("run_status.clear_running failed: %s", e)


async def get_running(chat_id) -> dict | None:
    """Вернуть {request_id, model, started_at, elapsed_sec, ttl_remaining, reasoning}."""
    if _redis is None or chat_id is None:
        return None
    try:
        raw = await _redis.get(_key(chat_id))
        if not raw:
            return None
        data = json.loads(raw)
        elapsed_sec, ttl_rem = await _compute_elapsed_from_ttl(chat_id, data.get("started_at"))
        data["elapsed_sec"] = elapsed_sec
        data["ttl_remaining"] = ttl_rem
        data["run_ttl"] = RUN_TTL
        reasoning = await get_reasoning_state(chat_id)
        if reasoning:
            data["reasoning"] = reasoning
        return data
    except Exception as e:
        logger.debug("run_status.get_running failed: %s", e)
        return None
