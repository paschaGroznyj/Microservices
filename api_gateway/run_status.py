# run_status.py
"""
Флаг «агент в работе» + тайминг по TTL и буфер рассуждений через Redis
(общий для обоих гейтвеев).
"""
import os
import json
import time
import uuid
import asyncio
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


# ── FlowMap: хронология пути запроса (Redis LIST flow:{chat_id}) ──────────────
# Каждый элемент — JSON {ev, rid, ts(ms epoch), ...поля flow: slot/label/ext/ws/call_id/ok/ms/gap_ms/role}.
# Пишет источник события (гейтвей: send/gateway/done, агент: accepted/agent_start/tool_*/think_end/answer/obs,
# chats-consumer: db_saved), чтобы хронология не зависела от того, подписан ли сейчас SSE/WS.
# Тексты размышлений сюда не дублируются — они в agent:reasoning:{chat_id}.steps.
FLOW_TTL = int(os.getenv("FLOW_TTL", str(RUN_TTL)))
FLOW_MAX = int(os.getenv("FLOW_MAX", "600"))


def _flow_key(chat_id) -> str:
    return f"flow:{chat_id}"


async def flow_push(chat_id, ev: str, request_id: str | None = None, **data) -> None:
    """Дописать событие пути запроса. Ошибки глушим: визуализация не должна ронять запрос."""
    if _redis is None or chat_id is None or not ev:
        return
    rec = {"ev": ev, "ts": int(time.time() * 1000)}
    if request_id:
        rec["rid"] = request_id
    rec.update({k: v for k, v in data.items() if v is not None})
    key = _flow_key(chat_id)
    try:
        n = await _redis.rpush(key, json.dumps(rec, ensure_ascii=False))
        if isinstance(n, int) and n > FLOW_MAX:
            await _redis.ltrim(key, -FLOW_MAX, -1)
        await _redis.expire(key, FLOW_TTL)
    except Exception as e:
        logger.debug("run_status.flow_push failed: %s", e)


async def get_flow(chat_id) -> list[dict]:
    """Хронология последнего запроса чата (битые элементы пропускаем)."""
    if _redis is None or chat_id is None:
        return []
    try:
        raw = await _redis.lrange(_flow_key(chat_id), 0, -1)
    except Exception as e:
        logger.debug("run_status.get_flow failed: %s", e)
        return []
    out = []
    for item in raw or []:
        try:
            d = json.loads(item.decode() if isinstance(item, bytes) else item)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("ev"):
            out.append(d)
    return out


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
        # Новый запрос — новая хронология FlowMap: старую сносим, отмечаем отправку и шлюз.
        await _redis.delete(_flow_key(chat_id))
    except Exception as e:
        logger.debug("run_status.mark_running failed: %s", e)
    await flow_push(chat_id, "send", request_id)
    await flow_push(chat_id, "gateway", request_id)


async def count_active_requests() -> int:
    """Считает чаты с активным запросом к агенту: есть agent:running или agent:loop.

    agent:reasoning:* не учитывается: ключ рассуждений может пережить флаг
    running (агент упал, флаги сняли напрямую), и счётчик залипал до RUN_TTL.
    """
    if _redis is None:
        return 0
    try:
        active_chats: set[str] = set()
        async for key in _redis.scan_iter(match="agent:*"):
            k = key.decode() if isinstance(key, bytes) else str(key)
            for prefix in ("agent:running:", "agent:loop:"):
                if k.startswith(prefix):
                    cid = k[len(prefix):]
                    if cid:
                        active_chats.add(cid)
                    break
        return len(active_chats)
    except Exception as e:
        logger.debug("run_status.count_active_requests failed: %s", e)
        return 0


NOTICE_KEY = "sys:notice"
NOTICE_TEXT_MAX = 20000


async def get_system_notice() -> dict | None:
    """Системное уведомление для всех вкладок (пишет restart.sh на хосте).

    Хеш sys:notice: id, phase (maintenance|done), text (markdown), services, ts.
    Нет ключа, не тот тип или Redis недоступен -> None.
    """
    if _redis is None:
        return None
    try:
        raw = await _redis.hgetall(NOTICE_KEY)
    except Exception as e:
        logger.debug("run_status.get_system_notice failed: %s", e)
        return None
    if not raw:
        return None
    d = {
        (k.decode() if isinstance(k, bytes) else str(k)):
        (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v))
        for k, v in raw.items()
    }
    nid = d.get("id", "").strip()
    if not nid:
        return None
    phase = d.get("phase", "maintenance").strip()
    if phase not in ("maintenance", "done"):
        phase = "maintenance"
    try:
        ts = int(float(d.get("ts") or 0))
    except ValueError:
        ts = 0
    return {
        "id": nid[:64],
        "phase": phase,
        "text": d.get("text", "")[:NOTICE_TEXT_MAX],
        "services": [x for x in d.get("services", "").split() if x][:20],
        "ts": ts,
    }


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


async def clear_running(chat_id, ok: bool = True, request_id: str | None = None) -> None:
    """Снять флаг активной генерации и зафиксировать итоговое время рассуждений по TTL.

    ok=False — остановлено пользователем (стоп-кнопка): в хронологию FlowMap уходит done с ok=false."""
    if _redis is None or chat_id is None:
        return
    rid = request_id
    raw_r = None
    try:
        raw_run = await _redis.get(_key(chat_id))
        if raw_run and not rid:
            rid = (json.loads(raw_run) or {}).get("request_id")
    except Exception:
        pass
    try:
        raw_r = await _redis.get(_reasoning_key(chat_id))
        if raw_r and not rid:
            rid = (json.loads(raw_r) or {}).get("request_id")
    except Exception:
        raw_r = None
    if rid:
        try:
            tail = await _redis.lrange(_flow_key(chat_id), -1, -1)
            last = json.loads(tail[0].decode() if isinstance(tail[0], bytes) else tail[0]) if tail else {}
        except Exception:
            last = {}
        if not (isinstance(last, dict) and last.get("ev") == "done" and last.get("rid") == rid):
            await flow_push(chat_id, "done", rid, ok=None if ok else False)
    try:
        if raw_r:
            state = json.loads(raw_r)
            elapsed_sec, _ = await _compute_elapsed_from_ttl(chat_id, state.get("started_at"))
            if state.get("duration_sec") is None:
                state["duration_sec"] = elapsed_sec
            state["completed"] = True
            state["is_final"] = True
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
    "gigachat": "gigachat",
    "giga": "gigachat",
    "deepseek": "deepseek",
    "ds": "deepseek",
}


def register_setting_models(models: list[dict] | None) -> None:
    """Динамически пополняет карту моделей и алиасов из каталога agent_service."""
    if not isinstance(models, list):
        return
    for item in models:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip().lower()
        skey = str(item.get("setting_key") or mid).strip().lower()
        if not mid or not skey:
            continue
        _SETTING_MODEL_MAP[mid] = skey
        _SETTING_MODEL_MAP[skey] = skey
        for alias in item.get("aliases") or []:
            al = str(alias or "").strip().lower()
            if al:
                _SETTING_MODEL_MAP[al] = skey


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


# ── Web Push: настройка notify_enabled с дебаунсом 1 с ──
# Частые клики по чекбоксу схлопываются: state-ключ обновляется сразу (GET видит
# новое значение), а в очередь user:settings:pending уходит только последнее
# значение, простоявшее NOTIFY_DEBOUNCE_SEC. Токен в Redis — чтобы дебаунс работал
# и при нескольких воркерах гейтвея.
NOTIFY_DEBOUNCE_SEC = float(os.getenv("NOTIFY_DEBOUNCE_SEC", "1.0"))
_notify_tasks: set = set()


def _notify_debounce_key(user_id: int) -> str:
    return f"user:settings:notify_debounce:{int(user_id)}"


async def _notify_flush_later(uid: int, token: str, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
        raw = await _redis.get(_notify_debounce_key(uid))
        data = json.loads(raw) if raw else {}
        if data.get("token") != token:
            return  # был клик новее — его задача и отправит
        event = {"user_id": uid, "ts": time.time(), "notify_enabled": bool(data.get("enabled"))}
        await _redis.rpush(USER_SETTINGS_QUEUE_KEY, json.dumps(event, ensure_ascii=False))
    except Exception as e:
        logger.warning("run_status.notify flush failed: %s", e)


async def push_notify_setting(user_id: int, enabled: bool, delay: float | None = None) -> bool:
    if _redis is None or user_id is None:
        return False
    uid = int(user_id)
    token = uuid.uuid4().hex
    try:
        state_key = _user_settings_state_key(uid)
        raw_cur = await _redis.get(state_key)
        cur = json.loads(raw_cur) if raw_cur else {"user_id": uid}
        cur["notify_enabled"] = bool(enabled)
        await _redis.set(state_key, json.dumps(cur, ensure_ascii=False), ex=USER_SETTINGS_STATE_TTL)
        await _redis.set(_notify_debounce_key(uid), json.dumps({"token": token, "enabled": bool(enabled)}), ex=60)
    except Exception as e:
        logger.warning("run_status.push_notify_setting failed: %s", e)
        return False
    task = asyncio.create_task(_notify_flush_later(uid, token, NOTIFY_DEBOUNCE_SEC if delay is None else delay))
    _notify_tasks.add(task)
    task.add_done_callback(_notify_tasks.discard)
    return True


SKILL_SELECT_QUEUE_KEY = "skills:selected:pending"
SKILL_SELECT_STATE_TTL = 7 * 86400

SKILL_LIKE_QUEUE_KEY = "skills:likes:pending"
SKILL_LIKE_STATE_TTL = 7 * 86400

def _skill_liked_state_key(user_id: int) -> str:
    return f"skills:likes:state:{int(user_id)}"

def _skill_likes_count_key(skill_id: int) -> str:
    return f"skills:likes:count:{int(skill_id)}"

async def push_skill_like(
    user_id: int,
    skill_id: int,
    liked: bool | None = None,
    liked_ids: list | None = None,
    likes_count: int | None = None,
) -> dict:
    if _redis is None or not user_id or not skill_id:
        return {"ok": False, "reason": "redis_unavailable"}
    uid = int(user_id)
    sid = int(skill_id)
    try:
        state_key = _skill_liked_state_key(uid)
        if isinstance(liked_ids, list):
            cur_ids = []
            for x in liked_ids:
                try:
                    xi = int(x or 0)
                    if xi > 0 and xi not in cur_ids:
                        cur_ids.append(xi)
                except (TypeError, ValueError):
                    pass
        else:
            raw_state = await _redis.get(state_key)
            if raw_state:
                state_data = json.loads(raw_state)
                cur_ids = state_data.get("liked_ids") or []
            else:
                cur_ids = []

        target = liked
        if target is None:
            target = sid not in cur_ids
            
        if target and sid not in cur_ids:
            cur_ids.append(sid)
        elif not target and sid in cur_ids:
            cur_ids.remove(sid)
            
        if likes_count is not None:
            next_count = max(0, int(likes_count) + (1 if target and liked is None else 0))
        else:
            count_key = _skill_likes_count_key(sid)
            raw_c = await _redis.get(count_key)
            base = int(raw_c or 0)
            next_count = max(0, base + (1 if target else -1))
            
        count_key = _skill_likes_count_key(sid)
        await _redis.set(count_key, next_count, ex=SKILL_LIKE_STATE_TTL)

        event = {
            "user_id": uid,
            "skill_id": sid,
            "liked": target,
            "ts": __import__("time").time()
        }
        
        state_obj = {
            "user_id": uid,
            "skill_id": sid,
            "liked": target,
            "liked_ids": cur_ids,
            "ts": __import__("time").time(),
        }
        await _redis.set(state_key, json.dumps(state_obj, ensure_ascii=False), ex=SKILL_LIKE_STATE_TTL)
        await _redis.rpush(SKILL_LIKE_QUEUE_KEY, json.dumps(event, ensure_ascii=False))
        return {
            "ok": True,
            "skill_id": sid,
            "liked": target,
            "liked_ids": cur_ids,
            "likes_count": next_count,
        }
    except Exception as e:
        logger.warning("run_status.push_skill_like failed: %s", e)
        return {"ok": False, "reason": "redis_error"}



def _skill_selected_state_key(user_id: int) -> str:
    return f"skills:selected:state:{int(user_id)}"


async def push_skill_select(
    user_id: int,
    skill_id: int,
    selected: bool | None = None,
    selected_ids: list | None = None,
    max_selected: int = 5,
) -> dict:
    if _redis is None or not user_id or not skill_id:
        return {"ok": False, "reason": "redis_unavailable"}
    uid = int(user_id)
    sid = int(skill_id)
    try:
        state_key = _skill_selected_state_key(uid)
        if isinstance(selected_ids, list):
            cur_ids = []
            for x in selected_ids:
                try:
                    xi = int(x or 0)
                    if xi > 0 and xi not in cur_ids:
                        cur_ids.append(xi)
                except (TypeError, ValueError):
                    pass
        else:
            raw_cur = await _redis.get(state_key)
            if raw_cur:
                cur_data = json.loads(raw_cur)
                cur_ids = [int(x) for x in (cur_data.get("selected_ids") or []) if int(x or 0) > 0]
            else:
                cur_ids = []

        target = (sid not in cur_ids) if selected is None else bool(selected)
        if target:
            if sid not in cur_ids:
                if len(cur_ids) >= max_selected:
                    return {
                        "ok": False,
                        "reason": "limit",
                        "skill_id": sid,
                        "selected": False,
                        "selected_ids": cur_ids,
                        "count": len(cur_ids),
                        "max_selected": max_selected,
                    }
                cur_ids.append(sid)
        else:
            cur_ids = [x for x in cur_ids if x != sid]

        state_obj = {
            "user_id": uid,
            "selected_ids": cur_ids,
            "count": len(cur_ids),
            "max_selected": max_selected,
        }
        event = {
            "user_id": uid,
            "skill_id": sid,
            "selected": target,
            "selected_ids": cur_ids,
            "ts": time.time(),
        }
        await _redis.set(state_key, json.dumps(state_obj, ensure_ascii=False), ex=SKILL_SELECT_STATE_TTL)
        await _redis.rpush(SKILL_SELECT_QUEUE_KEY, json.dumps(event, ensure_ascii=False))
        return {
            "ok": True,
            "skill_id": sid,
            "selected": target,
            "selected_ids": cur_ids,
            "count": len(cur_ids),
            "max_selected": max_selected,
        }
    except Exception as e:
        logger.warning("run_status.push_skill_select failed: %s", e)
        return {"ok": False, "reason": "redis_error"}



async def evict_deleted_skill(skill_id: int) -> None:
    if _redis is None or not skill_id:
        return
    sid = int(skill_id)
    try:
        async for key in _redis.scan_iter(match="skills:selected:state:*", count=100):
            k = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
            raw = await _redis.get(k)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                ids = [int(x) for x in (data.get("selected_ids") or []) if int(x or 0) > 0 and int(x) != sid]
                data["selected_ids"] = ids
                data["count"] = len(ids)
                await _redis.set(k, json.dumps(data, ensure_ascii=False), ex=SKILL_SELECT_STATE_TTL)
            except Exception:
                pass
    except Exception as e:
        logger.debug("run_status.evict_deleted_skill failed: %s", e)
        
    try:
        await _redis.delete(_skill_likes_count_key(sid))
        async for key in _redis.scan_iter(match="skills:likes:state:*", count=100):
            k = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
            raw = await _redis.get(k)
            if not raw: continue
            try:
                data = json.loads(raw)
                if "liked_ids" in data:
                    data["liked_ids"] = [int(x) for x in data["liked_ids"] if int(x) != sid]
                    await _redis.set(k, json.dumps(data, ensure_ascii=False), ex=SKILL_LIKE_STATE_TTL)
            except Exception: pass
    except Exception: pass
