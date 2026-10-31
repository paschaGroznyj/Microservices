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


async def count_active_requests() -> int:
    """Считает количество активных запросов пользователей к агенту по ключам Redis agent*."""
    if _redis is None:
        return 0
    try:
        active_chats: set[str] = set()
        reasoning_candidates: list[tuple[str, str]] = []
        async for key in _redis.scan_iter(match="agent*"):
            k = str(key)
            if k.startswith("agent:running:"):
                cid = k[len("agent:running:"):]
                if cid:
                    active_chats.add(cid)
            elif k.startswith("agent:loop:"):
                cid = k[len("agent:loop:"):]
                if cid:
                    active_chats.add(cid)
            elif k.startswith("agent:reasoning:"):
                cid = k[len("agent:reasoning:"):]
                if cid:
                    reasoning_candidates.append((cid, k))
        for cid, rkey in reasoning_candidates:
            if cid in active_chats:
                continue
            raw = await _redis.get(rkey)
            if not raw:
                continue
            try:
                st = json.loads(raw)
                if not st.get("completed") and not st.get("is_final"):
                    active_chats.add(cid)
            except Exception:
                pass
        return len(active_chats)
    except Exception as e:
        logger.debug("run_status.count_active_requests failed: %s", e)
        return 0


async def append_reasoning_chunk(
    chat_id,
    chunk: str,
    kind: str = "live",
    msg_id: str | None = None,
) -> int:
    """Накапливает шаги рассуждений и фиксирует время размышлений по TTL Redis."""
    if _redis is None or chat_id is None:
        return 0
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
        return await count_active_requests()
    except Exception as e:
        logger.debug("run_status.append_reasoning_chunk failed: %s", e)
        return 0


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
        data["active_requests"] = await count_active_requests()
        return data
    except Exception as e:
        logger.debug("run_status.get_running failed: %s", e)
        return None


REACTION_QUEUE_KEY = "chat:reactions:pending"
REACTION_KEY_TTL = 86400


def _reaction_key(chat_id, msg_key) -> str:
    return f"chat:reaction:{chat_id}:{msg_key}"


async def push_message_reaction(
    chat_id,
    message_id: str | None = None,
    reaction: str | None = None,
    user_id: int | None = None,
    db_id: int | str | None = None,
) -> bool:
    if _redis is None or not chat_id:
        return False
    norm_reaction = str(reaction).strip().lower() if reaction else None
    if norm_reaction not in ("like", "dislike"):
        norm_reaction = None
    mid_str = str(message_id or "").strip()
    db_id_str = str(db_id if db_id is not None else "").strip()
    if not mid_str and not db_id_str:
        return False
    payload = json.dumps({
        "chat_id": str(chat_id),
        "message_id": mid_str or None,
        "id": db_id_str or None,
        "reaction": norm_reaction,
        "user_id": user_id,
        "ts": time.time(),
    })
    try:
        if mid_str:
            await _redis.set(_reaction_key(chat_id, mid_str), payload, ex=REACTION_KEY_TTL)
        if db_id_str and db_id_str != mid_str:
            await _redis.set(_reaction_key(chat_id, db_id_str), payload, ex=REACTION_KEY_TTL)
        await _redis.rpush(REACTION_QUEUE_KEY, payload)
        return True
    except Exception as e:
        logger.warning("run_status.push_message_reaction failed: %s", e)
        return False


USER_SETTINGS_QUEUE_KEY = "user:settings:pending"
USER_SETTINGS_STATE_TTL = 7 * 86400

_SETTING_MODEL_MAP = {
    "coder": "gpt",
    "gpt": "gpt",
    "designer": "claude",
    "claude": "claude",
    "universal": "glm",
    "glm": "glm",
    "gemini": "gemini",
}


def normalize_setting_model(model: str | None) -> str:
    raw = str(model or "").strip().lower()
    return _SETTING_MODEL_MAP.get(raw, "gpt")


def _user_settings_state_key(user_id: int) -> str:
    return f"user:settings:state:{int(user_id)}"


async def push_user_settings(
    user_id: int,
    selected_tools: list | None = None,
    selected_model: str | None = None,
    active_chat_id: str | None = None,
    has_active_chat: bool = False,
) -> bool:
    if _redis is None or user_id is None:
        return False
    uid = int(user_id)
    event: dict = {"user_id": uid, "ts": time.time()}
    if selected_tools is not None:
        event["selected_tools"] = [str(t).strip() for t in selected_tools if str(t).strip()]
    if selected_model is not None:
        event["selected_model"] = normalize_setting_model(selected_model)
    if has_active_chat:
        norm_chat = str(active_chat_id).strip() if active_chat_id is not None else ""
        event["active_chat_id"] = norm_chat or None
        event["has_active_chat"] = True

    try:
        state_key = _user_settings_state_key(uid)
        raw_cur = await _redis.get(state_key)
        cur = json.loads(raw_cur) if raw_cur else {
            "user_id": uid,
            "selected_tools": [],
            "selected_model": "gpt",
            "active_chat_id": None,
        }
        if "selected_tools" in event:
            cur["selected_tools"] = event["selected_tools"]
        if "selected_model" in event:
            cur["selected_model"] = event["selected_model"]
        if has_active_chat:
            cur["active_chat_id"] = event["active_chat_id"]
        await _redis.set(state_key, json.dumps(cur, ensure_ascii=False), ex=USER_SETTINGS_STATE_TTL)
        await _redis.rpush(USER_SETTINGS_QUEUE_KEY, json.dumps(event, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning("run_status.push_user_settings failed: %s", e)
        return False
