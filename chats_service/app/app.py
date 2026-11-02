import json
import logging
import os
import uuid
import requests
from fastapi import FastAPI, Header, Query
from kafka import KafkaProducer
from pydantic import BaseModel
from app.db.chats_tables import DataBaseChats
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from app.core.config import SECRET_KEY, ALGORITHM, KAFKA_BROKER
from app.db.group_chat_db import GroupChatDB
from app.db.editor_db import EditorDB
from app import group_chat as group_chat_module
from app.db.telegram_db import TelegramDB
from app.db.max_db import MaxDB
import time

WATCHDOG_INTERVAL = 10      # как часто проверяем
MESSAGE_TIMEOUT = 20        # сколько секунд ждём ответ от бота


db = DataBaseChats()  # ← один раз для всего приложения
service_message_alive = False

# Redis — обратный путь для мобильного клиента (gateway_target="mobile").
# Телефон за NAT: сервер не может сам к нему достучаться (в отличие от TG-бота),
# поэтому ответ агента кладётся в Redis-список mobile:reply:{chat_id},
# а мобилка забирает его long-poll'ом через GET /mobile/poll.
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
redis_client = None  # redis.asyncio.Redis — проставится на startup
MOBILE_REPLY_KEY = "mobile:reply:{chat_id}"
MOBILE_REPLY_TTL = 300  # сек — ответ живёт 5 мин, если мобилка не забрала

# ── MOBILE-ОРКЕСТРАТОР: режим lite <-> agent ───────────────────────────────────
# Контракт ключа идентичен agent_service/app/agent/mobile/mode_store.py:
#   ключ   = mobile:mode:{chat_id}
#   value  = JSON {"mode":"agent","turns_left":N}
#   нет ключа = lite (дефолт); TTL 24ч.
# Включает режим agent_service (при call_agent=true от lite-модели), а СПИСЫВАЕТ
# ходы — этот роутер. На последнем ходу режим сбрасывается в lite + farewell.
MOBILE_MODE_KEY = "mobile:mode:{chat_id}"
MOBILE_MODE_TTL = 24 * 3600


async def _route_mobile_tier(chat_id: str) -> dict:
    """Списать один ход agent-режима. Возвращает {"tier":"agent"|"lite","farewell":bool}.

    fail-open: Redis недоступен/любая ошибка -> lite (мобилку обслужит дешёвая модель).
    """
    if redis_client is None:
        return {"tier": "lite", "farewell": False}
    try:
        key = MOBILE_MODE_KEY.format(chat_id=chat_id)
        raw = await redis_client.get(key)
        if not raw:
            return {"tier": "lite", "farewell": False}
        data = json.loads(raw)
        if data.get("mode") != "agent":
            return {"tier": "lite", "farewell": False}
        left = int(data.get("turns_left", 0))
        if left <= 0:
            await redis_client.delete(key)
            return {"tier": "lite", "farewell": False}
        left -= 1
        if left <= 0:
            await redis_client.delete(key)
            return {"tier": "agent", "farewell": True}
        await redis_client.set(key, json.dumps({"mode": "agent", "turns_left": left}), ex=MOBILE_MODE_TTL)
        return {"tier": "agent", "farewell": False}
    except Exception as e:
        logging.warning(f"[mobile-router] fail-open lite: {e}")
        return {"tier": "lite", "farewell": False}

# ── Голос мобильного клиента: ASR на сервере (как в telegram_service) ──────────
# Мобилка НЕ расшифровывает звук сама — она шлёт голос (ogg/opus) в /send_message
# с input_format="voice" и байтами в files[].file_b64. chats_service дёргает
# уже готовый эндпоинт агента /api/tg/transcribe, подменяет message текстом и
# только ПОТОМ кладёт лёгкое текстовое сообщение в Kafka (голос в Kafka не летит).
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://agent_service:9001")
MOBILE_ASR_PROVIDER = os.getenv("TG_ASR_PROVIDER", "cloud_ru")
producer = KafkaProducer(
    bootstrap_servers=[KAFKA_BROKER],
    api_version=(4,1,0),
    key_serializer=lambda k: str(k).encode('utf-8'),
    value_serializer=lambda m: json.dumps(m).encode('utf-8'),
    max_request_size=10485760)
   

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

app = FastAPI(title="Chats Service")

# Общий/групповой чат: отдельный слой БД + роутер (не раздуваем этот app.py)
group_db = GroupChatDB(None)  # pool проставится на startup
editor_db = EditorDB(None)  # pool проставится на startup
app.include_router(group_chat_module.router)

# Telegram-интеграция: отдельный слой БД (allow-list + per-chat настройки).
tg_db = TelegramDB(None)  # pool проставится на startup
max_db = MaxDB(None)  # pool проставится на startup (MAX-интеграция)


def get_auth_data():
    return {"secret_key": SECRET_KEY, "algorithm": ALGORITHM}

def get_token(request: Request):
    token = request.cookies.get('users_access_token')
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token not found')
    return token


async def get_current_user_from_cookie(token: str = Depends(get_token)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail='Invalid token')

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail='Missing username')

    return username


class ChatMessage(BaseModel):
    user_id: int | None
    username: str
    tb_name: str | None = None
    message: str
    chat_id: str
    type_agent: str
    request_id: str
    selected_tools: list = []
    context_size: str = "medium"
    message_id: str
    has_files: bool = False
    files: list = []
    agent_model: str | None = None
    gateway_target: str = "main"
    # Физический telegram chat id — адрес доставки ответа. Для telegram chat_id
    # (выше) теперь = session_id (ключ памяти агента), а push ответа идёт на
    # tg_chat_id. None -> обратная совместимость (push на chat_id, как раньше).
    tg_chat_id: str | None = None
    max_chat_id: str | None = None
    input_format: str = "text"  # text | voice
    # Голосовая персона мобильного героя (Пейто/Атлантик). Прилетает из приложения
    # (его определяет wake-word: какое имя распознано). По ней agent_service
    # жёстко мапит голос+стиль TTS и блок личности в промпте. None -> дефолт.
    persona: str | None = None
    # Проактивная инициация из telegram_service (будильники/poke/грусть).
    initiated: bool = False
    slot: str | None = None
    addressed: bool = False
    target_username: str | None = None
    sad_followup: bool = False
    custom_prompt: str | None = None
    # САМОЦИКЛ АГЕНТА: loop=True — включить петлю для чата. loop_instruction —
    # начальная установка от пользователя (что делать на каждом тике). Сохраняется
    # в agent_loop_tasks ОДИН раз при старте; consumer её НЕ переписывает.
    loop: bool = False
    loop_instruction: str | None = None
    # loop_tick=True — служебный тик петли от consumer (user_id=-100), проходит гейт агента.
    loop_tick: bool = False
    poll_answer: dict | None = None
    

@app.on_event("startup")
async def startup():
    global service_message_alive
    service_message_alive = True
    await db.init_db_pool()

    # Групповой чат: переиспользуем тот же пул asyncpg, инициализируем схему
    # (таблицы group_rooms/group_messages/group_room_members + общая комната).
    group_db.pool = db.pool
    await group_db.init_schema()
    group_chat_module.bind(group_db, producer)

    # Editor: тот же пул (таблица editor_workspaces создаётся миграцией).
    editor_db.pool = db.pool

    # Telegram: переиспользуем тот же пул, создаём таблицы telegram_*.
    tg_db.pool = db.pool
    await tg_db.init_db_pool()

    # MAX: тот же пул, таблицы max_*.
    max_db.pool = db.pool
    await max_db.init_db_pool()

    # Redis-клиент для обратного пути мобильного клиента (long-poll).
    global redis_client
    try:
        import redis.asyncio as aioredis
        # ВАЖНО: socket_timeout должен быть БОЛЬШЕ, чем BLPOP-timeout (<=55с),
        # иначе сокет рвёт блокирующее чтение раньше, чем BLPOP вернётся,
        # и ловим "Timeout reading from redis". Ставим 60 с запасом + keepalive.
        redis_client = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=60,
            socket_connect_timeout=5,
            socket_keepalive=True,
            health_check_interval=30,
            retry_on_timeout=True,
        )
        await redis_client.ping()
        logging.info(f"[mobile] Redis подключён: {REDIS_URL}")
    except Exception as e:
        redis_client = None
        logging.error(f"[mobile] Redis недоступен ({REDIS_URL}): {e} — /mobile/poll вернёт 503")

    # Фоновый воркер чтения реакций из Redis -> запись в БД
    asyncio.create_task(_reactions_redis_worker())
    asyncio.create_task(_user_settings_redis_worker())

    # # 🚀 старт watchdog-а
    # asyncio.create_task(bot_response_watchdog())


REACTION_QUEUE_KEY = "chat:reactions:pending"
USER_SETTINGS_QUEUE_KEY = "user:settings:pending"
USER_SETTINGS_STATE_TTL = 7 * 86400
SKILL_SELECT_QUEUE_KEY = "skills:selected:pending"
SKILL_SELECT_STATE_TTL = 7 * 86400


def _user_settings_state_key(user_id: int) -> str:
    return f"user:settings:state:{int(user_id)}"


def _skill_selected_state_key(user_id: int) -> str:
    return f"skills:selected:state:{int(user_id)}"



SKILL_LIKE_QUEUE_KEY = "skills:likes:pending"
SKILL_LIKE_STATE_TTL = 7 * 86400
def _skill_liked_state_key(user_id: int) -> str:
    return f"skills:likes:state:{int(user_id)}"
def _skill_likes_count_key(skill_id: int) -> str:
    return f"skills:likes:count:{int(skill_id)}"

async def _flush_pending_skill_likes_from_redis(max_items: int = 50) -> int:
    if redis_client is None: return 0
    processed = 0
    for _ in range(max_items):
        try:
            raw = await redis_client.lpop(SKILL_LIKE_QUEUE_KEY)
        except Exception: break
        if not raw: break
        try:
            item = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(item, dict):
                uid = int(item.get("user_id") or 0)
                sid = int(item.get("skill_id") or 0)
                if uid > 0 and sid > 0:
                    res = await db.apply_skill_like(
                        user_id=uid,
                        skill_id=sid,
                        liked=item.get("liked")
                    )
                    if res.get("ok"):
                        try:
                            await redis_client.set(
                                _skill_liked_state_key(uid),
                                json.dumps(res, ensure_ascii=False),
                                ex=SKILL_LIKE_STATE_TTL,
                            )
                            await redis_client.set(
                                _skill_likes_count_key(sid),
                                int(res.get("likes_count") or 0),
                                ex=SKILL_LIKE_STATE_TTL,
                            )
                        except Exception: pass
                processed += 1
        except Exception: pass
    return processed

async def _flush_pending_skill_selections_from_redis(max_items: int = 200) -> int:
    if redis_client is None:
        return 0
    processed = 0
    for _ in range(max_items):
        try:
            raw = await redis_client.lpop(SKILL_SELECT_QUEUE_KEY)
        except Exception as e:
            logging.debug(f"[skills_select] redis lpop failed: {e}")
            break
        if not raw:
            break
        try:
            item = json.loads(raw)
            uid = int(item.get("user_id") or 0)
            sid = int(item.get("skill_id") or 0)
            if uid > 0 and sid > 0:
                sel = item.get("selected")
                res = await db.apply_skill_selection(
                    user_id=uid,
                    skill_id=sid,
                    selected=bool(sel) if sel is not None else None,
                    selected_ids_hint=item.get("selected_ids") if isinstance(item.get("selected_ids"), list) else None,
                )
                try:
                    await redis_client.set(
                        _skill_selected_state_key(uid),
                        json.dumps({
                            "user_id": uid,
                            "selected_ids": res.get("selected_ids") or [],
                            "count": res.get("count") or 0,
                            "max_selected": res.get("max_selected") or 5,
                        }, ensure_ascii=False),
                        ex=SKILL_SELECT_STATE_TTL,
                    )
                except Exception:
                    pass
                processed += 1
        except Exception as e:
            logging.error(f"[skills_select] failed to apply skill selection from redis: {e}")
    return processed


async def _flush_pending_user_settings_from_redis(max_items: int = 200) -> int:
    if redis_client is None:
        return 0
    processed = 0
    for _ in range(max_items):
        try:
            raw = await redis_client.lpop(USER_SETTINGS_QUEUE_KEY)
        except Exception as e:
            logging.debug(f"[user_settings] redis lpop failed: {e}")
            break
        if not raw:
            break
        try:
            item = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(item, dict) and item.get("user_id") is not None:
                uid = int(item["user_id"])
                has_tools = "selected_tools" in item and item.get("selected_tools") is not None
                has_model = "selected_model" in item and item.get("selected_model") is not None
                has_chat = bool(item.get("has_active_chat")) or ("active_chat_id" in item)
                saved = await db.upsert_user_settings(
                    user_id=uid,
                    selected_tools=item.get("selected_tools") if has_tools else None,
                    selected_model=item.get("selected_model") if has_model else None,
                    active_chat_id=item.get("active_chat_id") if has_chat else None,
                    has_active_chat=has_chat,
                )
                try:
                    await redis_client.set(
                        _user_settings_state_key(uid),
                        json.dumps(saved, ensure_ascii=False),
                        ex=USER_SETTINGS_STATE_TTL,
                    )
                except Exception:
                    pass
                processed += 1
        except Exception as e:
            logging.error(f"[user_settings] failed to apply settings from redis: {e}")
    return processed


async def _user_settings_redis_worker():
    while service_message_alive:
        try:
            c1 = await _flush_pending_user_settings_from_redis()
            c2 = await _flush_pending_skill_selections_from_redis()
            c3 = await _flush_pending_skill_likes_from_redis()
            if (c1 + c2 + c3) == 0:
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.debug(f"[user_settings] worker error: {e}")
            await asyncio.sleep(1.0)


async def _flush_pending_reactions_from_redis(max_items: int = 200) -> int:
    if redis_client is None:
        return 0
    processed = 0
    for _ in range(max_items):
        try:
            raw = await redis_client.lpop(REACTION_QUEUE_KEY)
        except Exception as e:
            logging.debug(f"[reactions] redis lpop failed: {e}")
            break
        if not raw:
            break
        try:
            item = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(item, dict):
                await db.set_message_reaction(
                    chat_id=str(item.get("chat_id") or ""),
                    message_id=item.get("message_id"),
                    db_id=item.get("id"),
                    reaction=item.get("reaction"),
                )
                processed += 1
        except Exception as e:
            logging.error(f"[reactions] failed to apply reaction from redis: {e}")
    return processed


async def _reactions_redis_worker():
    while service_message_alive:
        try:
            count = await _flush_pending_reactions_from_redis()
            if count == 0:
                await asyncio.sleep(0.35)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.debug(f"[reactions] worker error: {e}")
            await asyncio.sleep(1.0)


@app.on_event("shutdown")
async def shutdown():
    global service_message_alive
    service_message_alive = False
    await db.pool.close()

def _extract_voice_file(files: list):
    """Находит голосовой файл в files[] мобильного сообщения.

    Возвращает (voice_dict, index) или (None, -1). Голос опознаём по
    kind=="voice" либо по аудио-расширению (ogg/opus/oga/mp3/m4a/wav/aac).
    """
    AUDIO_EXT = ("ogg", "opus", "oga", "mp3", "m4a", "wav", "aac", "flac")
    for i, f in enumerate(files or []):
        if not isinstance(f, dict):
            continue
        kind = (f.get("kind") or "").lower()
        name = (f.get("name") or "")
        ext = (f.get("ext") or (name.rsplit(".", 1)[-1] if "." in name else "")).lower()
        if kind == "voice" or ext in AUDIO_EXT:
            return f, i
    return None, -1


async def transcribe_mobile_voice(chat: "ChatMessage") -> str:
    """Расшифровка голоса мобильного клиента через agent_service /api/tg/transcribe.

    Зеркало telegram_service.transcribe_voice_remote, но на стороне chats_service:
    телефон прислал голос в files[].file_b64, мы отдаём его агенту и получаем текст.
    Возвращает распознанный текст (может быть "") либо кидает исключение при сбое HTTP.
    """
    import base64 as _b64
    import requests as _rq

    voice, _idx = _extract_voice_file(chat.files)
    if not voice:
        return ""
    audio_b64 = voice.get("file_b64")
    if not audio_b64:
        logging.warning("[mobile-voice] голосовой файл без file_b64 — нечего расшифровывать")
        return ""
    name = (voice.get("name") or "")
    fmt = (voice.get("ext") or (name.rsplit(".", 1)[-1] if "." in name else "ogg")).lower() or "ogg"

    def _call() -> str:
        r = _rq.post(
            f"{AGENT_SERVICE_URL}/api/tg/transcribe",
            json={"audio_b64": audio_b64, "fmt": fmt, "language": "ru",
                  "provider": MOBILE_ASR_PROVIDER},
            timeout=130,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            logging.warning(f"[mobile-voice] transcribe not ok: {data.get('error')}")
            return ""
        return (data.get("text") or "").strip()

    import asyncio as _aio
    return await _aio.to_thread(_call)


@app.post("/send_message")
async def send_message(chat: ChatMessage):
    # ── Мобильный голос: расшифровываем на сервере ДО Kafka ────────────────────
    # Телефон прислал голос (input_format="voice" + files[].file_b64). Отдаём его
    # агенту на ASR, подменяем message текстом и выкидываем голос из files, чтобы
    # в Kafka поехало лёгкое текстовое сообщение (как делает telegram_service).
    if chat.gateway_target == "mobile" and chat.input_format == "voice":
        try:
            text = await transcribe_mobile_voice(chat)
        except Exception as e:
            logging.error(f"[mobile-voice] ошибка расшифровки: {e}")
            raise HTTPException(status_code=502, detail="voice transcription failed")
        if not text:
            raise HTTPException(status_code=422, detail="voice not recognized")
        logging.info(f"[mobile-voice] распознано: {text[:80]!r} (chat={chat.chat_id})")
        chat.message = text
        # Голос в Kafka не нужен — агент работает уже с текстом.
        _voice, vidx = _extract_voice_file(chat.files)
        if vidx >= 0:
            chat.files.pop(vidx)
        chat.has_files = bool(chat.files)

    # ── MOBILE-ОРКЕСТРАТОР: роутер lite/agent ──────────────────────────────────
    # По умолчанию мобилка обрабатывается дешёвой моделью (lite). Если юзер ранее
    # явно позвал агента — в Redis mobile:mode:{chat_id} стоит agent + счётчик
    # ходов. Здесь списываем один ход; на последнем ставим farewell (сервер
    # agent_service подмешает прощание в голос и режим сбросится в lite).
    agent_tier = "agent"   # не-mobile всегда идёт обычным путём
    agent_farewell = False
    if chat.gateway_target == "mobile":
        tier_info = await _route_mobile_tier(chat.chat_id)
        agent_tier = tier_info["tier"]
        agent_farewell = tier_info["farewell"]
        logging.info(f"[mobile-router] chat={chat.chat_id} tier={agent_tier} farewell={agent_farewell}")

    # ═══ САМОЦИКЛ: старт петли от пользователя ════════════════════════════
    # Фронт прислал loop=True + loop_instruction — заводим/обновляем установку в БД.
    # Только на РЕАЛЬНОМ старте (loop_tick=False); служебный тик установку НЕ трогает.
    if chat.loop and not chat.loop_tick and (chat.loop_instruction or "").strip():
        try:
            await db.upsert_loop_task(chat.chat_id, chat.user_id, chat.loop_instruction)
            logging.info(f"[self-loop] установка заведена chat={chat.chat_id} user={chat.user_id}")
        except Exception as e:
            logging.error(f"[self-loop] не удалось завести установку chat={chat.chat_id}: {e}")

    if isinstance(chat.poll_answer, dict):
        bot_mid = str(chat.poll_answer.get("bot_message_id") or "").strip()
        try:
            p_idx = int(chat.poll_answer.get("poll_index") or 0)
        except (TypeError, ValueError):
            p_idx = 0
        if bot_mid and await db.is_poll_answered(chat.chat_id, bot_mid, p_idx):
            raise HTTPException(status_code=409, detail="Poll already answered")

    message = {
        "user_id": chat.user_id,
        "tb_name": chat.tb_name,
        "username": chat.username,
        "chat_id": chat.chat_id,
        "message": chat.message,
        "type_agent": chat.type_agent,
        "is_from_bot": False,
        "request_id": chat.request_id,
        "selected_tools": chat.selected_tools,
        "context_size": chat.context_size,
        "message_id": chat.message_id,
        "has_files": chat.has_files,
        "files": chat.files,
        "agent_model": chat.agent_model,
        "gateway_target": chat.gateway_target,
        "tg_chat_id": chat.tg_chat_id,
        "max_chat_id": chat.max_chat_id,
        "input_format": chat.input_format,
        "initiated": chat.initiated,
        "slot": chat.slot,
        "addressed": chat.addressed,
        "target_username": chat.target_username,
        "sad_followup": chat.sad_followup,
        "custom_prompt": chat.custom_prompt,
        "agent_tier": agent_tier,
        "agent_farewell": agent_farewell,
        "persona": chat.persona,
        "loop": chat.loop,
        "loop_instruction": chat.loop_instruction,
        "loop_tick": chat.loop_tick,
        "poll_answer": chat.poll_answer,
    }
    start = time.perf_counter()
    # отправка в Kafka
    producer.send("chat-messages", key=chat.user_id, value=message)
    producer.flush()
    elapsed = time.perf_counter() - start
    # logging.info(f"Message Service. Время на отправку в сокет и на перекладку в кафку = {elapsed:.4f}s")
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM-ИНТЕГРАЦИЯ: allow-list чатов + per-chat настройки.
# Встраивание чата — только через CatchPM UI (залогиненный юзер).
# telegram_service (host-режим) читает эти эндпоинты по 127.0.0.1:<port>.
# ══════════════════════════════════════════════════════════════════════════════
class TgChatIn(BaseModel):
    chat_id: str
    added_by_user_id: int | None = None
    # Опционально: настройки, применяемые сразу при встраивании (из CatchPM UI).
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_model: str | None = None
    selected_tools: list | None = None
    trigger_mode: str | None = None
    orchestrator_context_n: int | None = None
    proactive_enabled: bool | None = None
    timezone_name: str | None = None


class TgSettingsIn(BaseModel):
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_model: str | None = None
    selected_tools: list | None = None
    trigger_mode: str | None = None
    orchestrator_context_n: int | None = None
    proactive_enabled: bool | None = None
    timezone_name: str | None = None


@app.get("/tg/chats")
async def tg_list_chats(added_by_user_id: int | None = None):
    """Список встроенных активных чатов вместе с настройками — для CatchPM UI.
    Если added_by_user_id задан — только чаты этого пользователя."""
    return {"chats": await tg_db.list_chats_with_settings(user_id=added_by_user_id)}


@app.post("/tg/chats")
async def tg_add_chat(body: TgChatIn):
    """Встроить телеграм-чат в allow-list (из CatchPM UI). Тип по знаку chat_id."""
    row = await tg_db.upsert_chat(body.chat_id, body.added_by_user_id)
    # Если вместе с чатом переданы настройки — применяем их сразу.
    settings_fields = {
        k: v for k, v in {
            "system_prompt": body.system_prompt,
            "agent_name": body.agent_name,
            "agent_model": body.agent_model,
            "selected_tools": body.selected_tools,
            "trigger_mode": body.trigger_mode,
            "orchestrator_context_n": body.orchestrator_context_n,
            "proactive_enabled": body.proactive_enabled,
        }.items() if v is not None
    }
    settings = None
    if settings_fields:
        settings = await tg_db.update_settings(body.chat_id, **settings_fields)
    else:
        settings = await tg_db.get_settings(body.chat_id)
    return {"status": "ok", "chat": row, "settings": settings}


@app.delete("/tg/chats/{chat_id}")
async def tg_remove_chat(chat_id: str):
    ok = await tg_db.deactivate_chat(chat_id)
    return {"status": "ok" if ok else "not_found"}


@app.get("/tg/allowed")
async def tg_allowed():
    """Список активных chat_id — telegram_service кэширует в Redis."""
    return {"chat_ids": await tg_db.list_active_chat_ids()}


@app.get("/tg/settings/{chat_id}")
async def tg_get_settings(chat_id: str):
    s = await tg_db.get_settings(chat_id)
    return {"settings": s}


@app.post("/tg/settings/{chat_id}")
async def tg_set_settings(chat_id: str, body: TgSettingsIn):
    """Точечное обновление настроек (из /start бота). Админ-тулы режутся ботом/агентом."""
    s = await tg_db.update_settings(
        chat_id,
        **{k: v for k, v in body.dict().items() if v is not None},
    )
    return {"status": "ok", "settings": s}


class TgMemberIn(BaseModel):
    chat_id: str
    user_id: int
    username: str | None = None
    first_name: str | None = None
    is_bot: bool = False


@app.post("/tg/members")
async def tg_upsert_member(body: TgMemberIn):
    """Пассивный сбор участников: бот шлёт каждого написавшего."""
    await tg_db.upsert_member(
        body.chat_id, body.user_id, body.username, body.first_name, body.is_bot
    )
    return {"status": "ok"}


@app.get("/tg/members/{chat_id}/random")
async def tg_random_member(chat_id: str):
    """Рандомный участник — для проактивной инициации."""
    m = await tg_db.get_random_member(chat_id)
    return {"member": m}


class TgStartLogIn(BaseModel):
    chat_id: str | None = None
    user_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    is_allowed: bool | None = None


@app.post("/tg/log_start")
async def tg_log_start(body: TgStartLogIn):
    """Лог /start: первое нажатие = INSERT, повторы = start_count+1, updated_at."""
    await tg_db.log_start(
        body.chat_id, body.user_id, body.username, body.first_name, body.is_allowed
    )
    return {"status": "ok"}


@app.get("/tg/start_log")
async def tg_start_log(limit: int = 50):
    """Последние входы /start для админ-кнопки в боте."""
    rows = await tg_db.get_start_log(limit=max(1, min(int(limit), 200)))
    return {"status": "ok", "items": rows}


@app.get("/tg/proactive_chats")
async def tg_proactive_chats():
    """Активные чаты с включёнными будильниками — для планировщика бота."""
    return {"chats": await tg_db.list_proactive_chats()}


class TgTriggerIn(BaseModel):
    kind: str = "recurring"            # 'recurring' | 'once'
    title: str | None = None
    time_msk: str                       # 'HH:MM'
    days_of_week: list | None = None    # [1..7] пн..вс; None/[] = все дни
    trigger_date: str | None = None     # 'YYYY-MM-DD' для once
    prompt: str | None = None
    preset_key: str | None = None
    addressed: bool = False
    added_by_user_id: int | None = None


class TgTriggerPatch(BaseModel):
    kind: str | None = None
    title: str | None = None
    time_msk: str | None = None
    days_of_week: list | None = None
    trigger_date: str | None = None
    prompt: str | None = None
    preset_key: str | None = None
    addressed: bool | None = None
    is_active: bool | None = None


@app.get("/tg/triggers/{chat_id}")
async def tg_list_triggers(chat_id: str):
    """Список индивидуальных триггеров чата."""
    return {"triggers": await tg_db.list_triggers(chat_id)}


@app.post("/tg/triggers/{chat_id}")
async def tg_create_trigger(chat_id: str, body: TgTriggerIn):
    from datetime import date as _date
    td = None
    if body.trigger_date:
        try:
            td = _date.fromisoformat(body.trigger_date)
        except ValueError:
            return {"status": "error", "detail": "bad trigger_date"}
    row = await tg_db.create_trigger(
        chat_id, body.added_by_user_id, body.kind, body.time_msk,
        title=body.title, days_of_week=body.days_of_week,
        trigger_date=td, prompt=body.prompt, preset_key=body.preset_key, addressed=body.addressed,
    )
    return {"status": "ok", "trigger": row}


@app.patch("/tg/triggers/{chat_id}/{trigger_id}")
async def tg_update_trigger(chat_id: str, trigger_id: int, body: TgTriggerPatch):
    from datetime import date as _date
    fields = {k: v for k, v in body.dict().items() if v is not None}
    if "trigger_date" in fields:
        try:
            fields["trigger_date"] = _date.fromisoformat(fields["trigger_date"])
        except ValueError:
            return {"status": "error", "detail": "bad trigger_date"}
    row = await tg_db.update_trigger(trigger_id, **fields)
    return {"status": "ok" if row else "not_found", "trigger": row}


@app.delete("/tg/triggers/{chat_id}/{trigger_id}")
async def tg_delete_trigger(chat_id: str, trigger_id: int):
    ok = await tg_db.delete_trigger(trigger_id)
    return {"status": "ok" if ok else "not_found"}


@app.get("/tg/triggers_due")
async def tg_triggers_due(utc_ts: str | None = None):
    """Тик-сканер бота: триггеры, чьё локальное время настало СЕЙЧАС."""
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    if utc_ts:
        try:
            now_utc = datetime.fromisoformat(utc_ts.replace('Z', '+00:00')).astimezone(timezone.utc)
        except ValueError:
            pass
    return {"triggers": await tg_db.scan_due_triggers(now_utc)}


@app.post("/tg/triggers/{chat_id}/{trigger_id}/fired")
async def tg_trigger_fired(chat_id: str, trigger_id: int):
    await tg_db.mark_trigger_fired(trigger_id)
    return {"status": "ok"}


@app.get("/tg/trigger/{trigger_id}")
async def tg_get_trigger(trigger_id: int):
    """Один триггер по id (для debug-прогона из бота)."""
    row = await tg_db.get_trigger(trigger_id)
    return {"trigger": row}


class TgLogIn(BaseModel):
    chat_id: str
    user_id: int | None = None
    username: str
    message: str
    is_from_bot: bool = False


@app.post("/tg/log_message")
async def tg_log_message(body: TgLogIn):
    """
    Пишет в chats ТОЛЬКО одобренные сообщения группы (те, на которые агент
    реагирует). Молчаливый поток группы сюда не попадает — память не засоряется.
    Идёт мимо Kafka (без повторного триггера агента).
    """
    await db.add_message(
        user_id=body.user_id,
        message=body.message,
        chat_id=body.chat_id,
        is_from_bot=body.is_from_bot,
    )
    return {"status": "ok"}

@app.get("/tg/user/chats")
async def get_user_chats(user_id: int = Query(..., alias="user_id"), chat_id: str = Query(..., alias="chat_id")):
    try:
        logging.info(f"[meta] {user_id} {chat_id}")
        chats = await db.get_user_chats_summary_by_user_id(user_id, chat_id)
        return {"chats": chats}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте get_user_chats: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить список чатов")


@app.post("/editor/register")
async def editor_register(payload: dict):
    """Регистрация воркспейса за юзером с учётом TTL (по дефолту 10ч или бессрочно)."""
    user_id = payload.get("user_id")
    workspace_id = payload.get("workspace_id")
    if user_id is None or not workspace_id:
        raise HTTPException(status_code=400, detail="user_id и workspace_id обязательны")
    raw_ttl = payload.get("ttl_hours", 10)
    is_perm = bool(payload.get("is_permanent") or payload.get("permanent"))
    try:
        ttl_hours = None if raw_ttl is None else int(raw_ttl)
    except (TypeError, ValueError):
        ttl_hours = 10
    if ttl_hours is not None and ttl_hours <= 0:
        is_perm = True
        ttl_hours = None
    row = await editor_db.register(
        int(user_id), str(workspace_id),
        chat_id=payload.get("chat_id"),
        title=payload.get("title"),
        ttl_hours=ttl_hours,
        is_permanent=is_perm,
    )
    return {"ok": True, "workspace": row}


@app.get("/editor/workspaces")
async def editor_list(user_id: int, chat_id: str = None):
    """Список воркспейсов юзера (для дерева проекта в редакторе)."""
    items = await editor_db.list_for_user(int(user_id), chat_id=chat_id)
    return {"workspaces": items}


@app.get("/editor/owns")
async def editor_owns(user_id: int, workspace_id: str):
    """Гейт доступа: принадлежит ли воркспейс юзеру."""
    ok = await editor_db.owns(int(user_id), str(workspace_id))
    return {"owns": ok}


@app.post("/editor/workspaces/ttl")
async def editor_update_workspace_ttl(payload: dict):
    """Обновление TTL / бессрочного хранения (.ws_hold) по запросу от agent_sandbox."""
    workspace_id = payload.get("workspace_id")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id обязателен")
    is_active = payload.get("is_active")
    is_perm = payload.get("is_permanent")
    if is_perm is None and "permanent" in payload:
        is_perm = bool(payload.get("permanent"))
    raw_ttl = payload.get("ttl_hours", 10)
    try:
        ttl_hours = None if raw_ttl is None else int(raw_ttl)
    except (TypeError, ValueError):
        ttl_hours = 10
    updated = await editor_db.update_ttl_status(
        str(workspace_id),
        is_permanent=is_perm,
        ttl_hours=ttl_hours,
        is_active=is_active,
    )
    return {"ok": True, "workspace_id": str(workspace_id), "updated": updated}


# ══════════════════════════════════════════════════════════════
# MAX-интеграция (аналог /tg/*). Ядро: allow-list + per-chat настройки.
# ══════════════════════════════════════════════════════════════
class MaxChatIn(BaseModel):
    chat_id: str
    added_by_user_id: int | None = None
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_model: str | None = None
    selected_tools: list | None = None
    trigger_mode: str | None = None
    orchestrator_context_n: int | None = None
    proactive_enabled: bool | None = None


class MaxSettingsIn(BaseModel):
    system_prompt: str | None = None
    agent_name: str | None = None
    agent_model: str | None = None
    selected_tools: list | None = None
    trigger_mode: str | None = None
    orchestrator_context_n: int | None = None
    proactive_enabled: bool | None = None


@app.get("/max/chats")
async def max_list_chats(added_by_user_id: int | None = None):
    return {"chats": await max_db.list_chats_with_settings(user_id=added_by_user_id)}


@app.post("/max/chats")
async def max_add_chat(body: MaxChatIn):
    row = await max_db.upsert_chat(body.chat_id, body.added_by_user_id)
    settings_fields = {
        k: v for k, v in {
            "system_prompt": body.system_prompt,
            "agent_name": body.agent_name,
            "agent_model": body.agent_model,
            "selected_tools": body.selected_tools,
            "trigger_mode": body.trigger_mode,
            "orchestrator_context_n": body.orchestrator_context_n,
            "proactive_enabled": body.proactive_enabled,
        }.items() if v is not None
    }
    if settings_fields:
        settings = await max_db.update_settings(body.chat_id, **settings_fields)
    else:
        settings = await max_db.get_settings(body.chat_id)
    return {"status": "ok", "chat": row, "settings": settings}


@app.delete("/max/chats/{chat_id}")
async def max_remove_chat(chat_id: str):
    ok = await max_db.deactivate_chat(chat_id)
    return {"status": "ok" if ok else "not_found"}


@app.get("/max/allowed_chats")
async def max_allowed():
    """Активные chat_id — max_service кэширует в Redis (ключ max:allowed_chats)."""
    return {"chats": await max_db.list_active_chat_ids()}


class MaxMemberIn(BaseModel):
    chat_id: str
    user_id: int
    username: str | None = None
    first_name: str | None = None
    is_bot: bool = False


@app.post("/max/members")
async def max_upsert_member(body: MaxMemberIn):
    """Пассивный сбор участников MAX-чата (бот шлёт каждого написавшего)."""
    await max_db.upsert_member(
        body.chat_id, body.user_id, body.username, body.first_name, body.is_bot
    )
    return {"status": "ok"}


@app.get("/max/members/{chat_id}/random")
async def max_random_member(chat_id: str):
    m = await max_db.get_random_member(chat_id)
    return {"member": m}


@app.get("/max/settings/{chat_id}")
async def max_get_settings(chat_id: str):
    return await max_db.get_settings(chat_id) or {}


@app.post("/max/settings/{chat_id}")
async def max_set_settings(chat_id: str, body: MaxSettingsIn):
    s = await max_db.update_settings(
        chat_id, **{k: v for k, v in body.dict().items() if v is not None}
    )
    return {"status": "ok", "settings": s}




@app.get("/chat/{chat_id}/messages")
async def get_chat_messages(
    chat_id: str,
    limit: int = 50,
    offset: int = 0,
    around_id: str | None = None,
    radius: int = 10,
    before_id: int | None = None,
    after_id: int | None = None,
    x_user_name: str = Header(..., alias="X-User-Name"),
):
    try:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        radius = max(1, min(radius, 100))

        await _flush_pending_reactions_from_redis()
        result = await db.get_chat_messages(
            chat_id,
            x_user_name,
            limit=limit,
            offset=offset,
            around_id=around_id,
            radius=radius,
            before_id=before_id,
            after_id=after_id,
        )
        messages = result["messages"]
        logging.info(
            f"🦧Чат {chat_id}: отдано {len(messages)} (offset={offset}, around={around_id}, "
            f"before={before_id}, after={after_id}, total={result['total_count']}, "
            f"has_more={result['has_more']}, has_newer={result.get('has_newer', False)})"
        )
        return {
            "chat_id": chat_id,
            "total": len(messages),
            "total_count": result["total_count"],
            "has_more": result["has_more"],
            "has_newer": result.get("has_newer", False),
            "target_id": result.get("target_id"),
            "target_message_id": result.get("target_message_id"),
            "limit": limit,
            "offset": offset,
            "messages": messages,
            "username": x_user_name,
        }
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")

@app.get("/user/all/messages")
async def get_all_user_messages(limit: int = 50, offset: int = 0,  x_user_name: str = Header(..., alias="X-User-Name")):
    try:
        logging.info(f"Лимит по сообщениям для пользователя {limit}")
        messages = await db.get_all_user_messages(x_user_name, limit=limit, offset=offset)
        return {"messages": messages}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить историю чата")


@app.get("/user/messages/search")
async def search_messages(q: str = "", limit: int = 30, x_user_name: str = Header(..., alias="X-User-Name")):
    """Живой поиск по сообщениям пользователя. Требует минимум 2 символа в q."""
    query = (q or "").strip()
    if len(query) < 2:
        return {"results": [], "query": query}
    try:
        limit = max(1, min(limit, 50))
        results = await db.search_user_messages(x_user_name, query, limit=limit)
        return {"results": results, "query": query}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте search_messages: {e}")
        raise HTTPException(status_code=500, detail="Не удалось выполнить поиск по сообщениям")


class ChatSidebarAction(BaseModel):
    chat_id: str
    user_id: int


class ChatRenameAction(BaseModel):
    chat_id: str
    user_id: int
    new_title: str


class ChatFavoriteAction(BaseModel):
    chat_id: str
    user_id: int
    is_favorite: bool


@app.get("/user/chats")
async def get_user_chats(x_user_name: str = Header(..., alias="X-User-Name")):
    try:
        chats = await db.get_user_chats_summary(x_user_name)
        sorted_chats = sorted(
            chats,
            key=lambda x: x["timestamp"],
            reverse=True
        )
        return {"chats": sorted_chats}
    except Exception as e:
        logging.error(f"Ошибка в эндпоинте get_user_chats: {e}")
        raise HTTPException(status_code=500, detail="Не удалось загрузить список чатов")


@app.post("/chat/delete")
async def delete_chat(action: ChatSidebarAction):
    try:
        await db.mark_chat_deleted(action.chat_id, action.user_id)
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка удаления чата {action.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось пометить чат на удаление")


@app.post("/chat/rename")
async def rename_chat(action: ChatRenameAction):
    try:
        new_title = (action.new_title or "").strip()
        if not new_title:
            raise HTTPException(status_code=400, detail="new_title required")
        await db.rename_chat(action.chat_id, action.user_id, new_title)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Ошибка переименования чата {action.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось переименовать чат")


@app.post("/chat/favorite")
async def favorite_chat(action: ChatFavoriteAction):
    try:
        await db.set_chat_favorite(action.chat_id, action.user_id, action.is_favorite)
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка избранного для чата {action.chat_id}: {e}")
        raise HTTPException(status_code=500, detail="Не удалось обновить избранное")


@app.post("/chat/new")
async def create_chat(request: Request):
    return {"chat_id": str(uuid.uuid4())}


@app.get("/chats/{chat_id}/poll_answers")
async def get_chat_poll_answers(chat_id: str):
    return {"poll_answers": await db.get_poll_answers(chat_id)}


@app.get("/user/settings")
async def get_user_settings_endpoint(x_user_id: str = Header(..., alias="X-User-Id")):
    try:
        uid = int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid X-User-Id")
    await _flush_pending_user_settings_from_redis()
    settings = await db.get_user_settings(uid)
    if redis_client is not None:
        try:
            await redis_client.set(
                _user_settings_state_key(uid),
                json.dumps(settings, ensure_ascii=False),
                ex=USER_SETTINGS_STATE_TTL,
            )
        except Exception:
            pass
    return settings


@app.post("/user/settings")
async def post_user_settings_endpoint(request: Request, x_user_id: str = Header(..., alias="X-User-Id")):
    try:
        uid = int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid X-User-Id")
    data = await request.json()
    has_tools = "selected_tools" in data and data.get("selected_tools") is not None
    has_model = "selected_model" in data and data.get("selected_model") is not None
    has_chat = "active_chat_id" in data
    saved = await db.upsert_user_settings(
        user_id=uid,
        selected_tools=data.get("selected_tools") if has_tools else None,
        selected_model=data.get("selected_model") if has_model else None,
        active_chat_id=data.get("active_chat_id") if has_chat else None,
        has_active_chat=has_chat,
    )
    if redis_client is not None:
        try:
            await redis_client.set(
                _user_settings_state_key(uid),
                json.dumps(saved, ensure_ascii=False),
                ex=USER_SETTINGS_STATE_TTL,
            )
        except Exception:
            pass
    return saved


@app.get("/skills/selected")
async def get_selected_skills_chats_ep(user_id: int = 0, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    uid = int(user_id or (x_user_id if x_user_id and str(x_user_id).isdigit() else 0) or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    await _flush_pending_skill_selections_from_redis()
    selected_ids = await db.get_selected_skill_ids(uid)
    state_obj = {
        "user_id": uid,
        "selected_ids": selected_ids,
        "count": len(selected_ids),
        "max_selected": 5,
    }
    if redis_client is not None:
        try:
            await redis_client.set(
                _skill_selected_state_key(uid),
                json.dumps(state_obj, ensure_ascii=False),
                ex=SKILL_SELECT_STATE_TTL,
            )
        except Exception:
            pass
    return state_obj


@app.post("/skills/{skill_id}/select")
async def post_select_skill_chats_ep(skill_id: int, request: Request, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    payload = await request.json()
    uid = int(payload.get("user_id") or (x_user_id if x_user_id and str(x_user_id).isdigit() else 0) or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    await _flush_pending_skill_selections_from_redis()
    sel = payload.get("selected")
    res = await db.apply_skill_selection(
        user_id=uid,
        skill_id=skill_id,
        selected=bool(sel) if sel is not None else None,
        selected_ids_hint=payload.get("selected_ids") if isinstance(payload.get("selected_ids"), list) else None,
    )
    if redis_client is not None:
        try:
            await redis_client.set(
                _skill_selected_state_key(uid),
                json.dumps({
                    "user_id": uid,
                    "selected_ids": res.get("selected_ids") or [],
                    "count": res.get("count") or 0,
                    "max_selected": res.get("max_selected") or 5,
                }, ensure_ascii=False),
                ex=SKILL_SELECT_STATE_TTL,
            )
        except Exception:
            pass
    if not res.get("ok"):
        if res.get("reason") == "not_found":
            raise HTTPException(status_code=404, detail="Skill not found")
        if res.get("reason") == "limit":
            raise HTTPException(status_code=400, detail="Max 5 selected skills per user")
        raise HTTPException(status_code=400, detail="Cannot toggle selected skill")
    return res


@app.get("/chat/by-type/{type_agent}")
async def find_chat_by_type(type_agent: str, x_user_id: str = Header(..., alias="X-User-Id")):
    """Найти существующий чат пользователя по типу агента."""
    chat_id = await db.find_chat_by_type(int(x_user_id), type_agent)
    logging.info(f"Пытались найти чат, для {x_user_id}, {type_agent}, {chat_id}")
    if chat_id:
        return {"chat_id": chat_id, "exists": True}
    return {"chat_id": None, "exists": False}


@app.get("/internal/resolve_file")
async def resolve_file(chat_id: str, message_id: str, file_name: str):
    """Только для gateway. Отдаёт путь на диске agent_service для скачивания."""
    row = await db.pool.fetchrow("""
        SELECT file_path, source
        FROM chat_files
        WHERE chat_id = $1 AND message_id = $2 AND file_name = $3
        LIMIT 1
    """, chat_id, message_id, file_name)

    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    if row["file_path"]:
        return {"path": row["file_path"]}
    if row["source"] == "user":
        safe_name = os.path.basename(file_name or "") or "file"
        safe_mid = os.path.basename(str(message_id or "")) or "msg"
        return {"path": f"/app/generated_files/{safe_mid}/{safe_name}"}
    raise HTTPException(status_code=404, detail="File not found")


@app.get("/internal/stats/overlay")
async def overlay_stats(gw: str = "main", top_n: int = 3):
    """Только для gateway. SQL-агрегаты для оверлея онлайна.
    Гейтвей сам подмешивает online/peak из Redis. gw пока не фильтрует
    выборку (топ — общий по системе), принимается для совместимости."""
    try:
        top_n = max(1, min(top_n, 10))
        stats = await db.get_overlay_stats(top_n=top_n)
        return stats
    except Exception as e:
        logging.error(f"Ошибка в overlay_stats: {e}")
        raise HTTPException(status_code=500, detail="overlay stats failed")


@app.get("/mobile/poll")
async def mobile_poll(chat_id: str, timeout: int = 25):
    """Long-poll обратного пути для мобильного клиента (AI_tracker).

    Телефон за NAT — сервер не может инициировать соединение к нему, поэтому
    мобилка сама периодически дёргает этот эндпоинт. Ответ агента кладёт сюда
    chats-consumer (endpoint_consumer.py) при gateway_target="mobile" в
    Redis-список mobile:reply:{chat_id}.

    Параметры:
      chat_id — тот же детерминированный UUIDv5(ANDROID_ID), что мобилка шлёт в
                /mobile/send_message.
      timeout — сколько секунд держать соединение в ожидании ответа (BLPOP),
                по умолчанию 25с (клиент ретраит сразу после).

    Возвращает:
      {"status":"ok","reply":{...}}   — пришёл ответ агента (text + опц. голос);
      {"status":"empty"}              — за timeout ничего не пришло, ретрайни;
      503                             — Redis недоступен.
    """
    if redis_client is None:
        raise HTTPException(status_code=503, detail="mobile reply channel offline (no redis)")
    # BLPOP держит соединение до timeout или до появления элемента.
    key = MOBILE_REPLY_KEY.format(chat_id=str(chat_id))
    # BLPOP-timeout держим строго меньше socket_timeout(=60) клиента,
    # иначе сокет рвёт чтение раньше. И не дольше nginx proxy_read_timeout.
    timeout = max(1, min(int(timeout), 50))
    try:
        item = await redis_client.blpop(key, timeout=timeout)
    except Exception as e:
        # Пустой BLPOP по таймауту НЕ должен прилетать сюда (redis сам вернёт None),
        # но на всякий случай различаем реальную поломку Redis и штатное отсутствие ответа.
        logging.error(f"[mobile] poll BLPOP error chat={chat_id}: {e}")
        raise HTTPException(status_code=503, detail="mobile reply channel error")
    if not item:
        return {"status": "empty"}
    # item = (key, value); value — JSON, который положил consumer
    try:
        reply = json.loads(item[1])
    except Exception:
        reply = {"text": item[1]}
    return {"status": "ok", "reply": reply}


@app.get("/budget/activity")
async def budget_activity(aggregation: str = "day"):
    """Фактический расход отдела из Cloud.ru Activity API. Кэш в Redis на BUDGET_CACHE_TTL.

    Activity обновляется ~раз в час, поэтому дёргать внешний API на каждом тике
    петли смысла нет. fail-open: при любой ошибке allowed=True, чтобы сбой панели
    не ронял работу агента.
    """
    from app.core.config import (
        ACTIVITY_URL, ACTIVITY_TOKEN, ACTIVITY_DEPARTMENT, BUDGET_CACHE_TTL,
    )
    import datetime as _dt

    cache_key = f"budget:activity:{ACTIVITY_DEPARTMENT}:{aggregation}"
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                return {**json.loads(cached), "cached": True}
        except Exception:
            pass

    if not ACTIVITY_TOKEN:
        return {"ok": False, "reason": "no_token", "current": None, "threshold": None}

    now_msk = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=3)))
    if aggregation == "day":
        period = now_msk.strftime("%Y-%m-%d")
    elif aggregation == "month":
        period = now_msk.strftime("%Y-%m")
    else:
        period = now_msk.strftime("%Y")

    try:
        resp = requests.get(
            ACTIVITY_URL,
            params={"aggregation": aggregation, "period": period,
                    "department": ACTIVITY_DEPARTMENT},
            headers={"Authorization": f"Bearer {ACTIVITY_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total") or {}
        pace = data.get("pace") or {}
        result = {
            "ok": True,
            "current": total.get("current"),
            "threshold": total.get("threshold"),
            "pace_current": pace.get("current"),
            "pace_threshold": pace.get("threshold"),
            "as_of": data.get("as_of"),
        }
        if redis_client is not None:
            try:
                await redis_client.set(cache_key, json.dumps(result), ex=BUDGET_CACHE_TTL)
            except Exception:
                pass
        return result
    except Exception as e:
        logging.warning(f"[budget] Activity API недоступен: {e}")
        return {"ok": False, "reason": "activity_error", "current": None, "threshold": None}


@app.get("/budget/check/{chat_id}")
async def budget_check(chat_id: str):
    """Агрегатор трёх условий бюджета для петли. Возвращает {allowed, reason, ...}.

    Петля продолжается только если прошли ВСЕ три:
      A) фактический расход отдела (Activity) < порога панели;
      B) наша оценка по токенам за сегодня (отдел) < того же порога;
      C) наша оценка < собственного жёсткого лимита петли (LOOP_HARD_LIMIT_RUB).
    fail-open по A/B: если данных нет — условие не блокирует. C считаем всегда.
    """
    from app.core.config import LOOP_HARD_LIMIT_RUB
    logging.info(f"[hard_limit_ru] {LOOP_HARD_LIMIT_RUB}")
    activity = await budget_activity(aggregation="day")
    estimate_dept = await db.estimate_spend_today(chat_id=None)
    estimate_loop = await db.estimate_spend_today(chat_id=chat_id)

    threshold = activity.get("threshold")
    actual = activity.get("current")

    checks = {"A_activity": True, "B_estimate": True, "C_loop_limit": True}
    if threshold is not None:
        if actual is not None and float(actual) >= float(threshold):
            checks["A_activity"] = False
        if estimate_dept >= float(threshold):
            checks["B_estimate"] = False
    if estimate_loop >= LOOP_HARD_LIMIT_RUB:
        checks["C_loop_limit"] = False

    allowed = all(checks.values())
    reason = "ok" if allowed else ",".join(k for k, v in checks.items() if not v)
    return {
        "allowed": allowed,
        "reason": reason,
        "actual": actual,
        "threshold": threshold,
        "estimate_dept": estimate_dept,
        "estimate_loop": estimate_loop,
        "loop_hard_limit": LOOP_HARD_LIMIT_RUB,
        "checks": checks,
    }


@app.get("/health")
async def health():
    status = "ok" if service_message_alive else "error"
    return {"service": "chats_service", "status": status}


# import uvicorn
# from utils.config import PORT_CHATS_SERVICE
#
# if __name__ == "__main__":
#     uvicorn.run("chats_service.app.app:app", host="127.0.0.1", port=int(PORT_CHATS_SERVICE), reload=True)




@app.get('/skills/liked')
async def get_liked_skills(user_id: int = 0, x_user_id: str | None = Header(default=None, alias="X-User-Id")):
    uid = int(user_id or (x_user_id if x_user_id and str(x_user_id).isdigit() else 0) or 0)
    if uid <= 0:
        raise HTTPException(status_code=400, detail="user_id required")
    await _flush_pending_skill_likes_from_redis(max_items=200)
    liked_ids = await db.get_liked_skill_ids(uid)
    state_obj = {
        'user_id': uid,
        'liked_ids': liked_ids,
        'count': len(liked_ids),
    }
    if redis_client is not None:
        try:
            await redis_client.set(
                _skill_liked_state_key(uid),
                json.dumps(state_obj, ensure_ascii=False),
                ex=SKILL_LIKE_STATE_TTL,
            )
        except Exception:
            pass
    return state_obj

@app.post('/skills/{skill_id}/like')
async def api_toggle_liked_skill(skill_id: int, payload: dict, request: Request):
    user_id = int(payload.get('user_id') or 0)
    if user_id <= 0: raise HTTPException(status_code=400)
    await _flush_pending_skill_likes_from_redis(max_items=10)
    result = await db.apply_skill_like(user_id=user_id, skill_id=skill_id, liked=payload.get('liked'))
    if not result.get('ok'):
        if result.get('reason') == 'not_found': raise HTTPException(status_code=404)
        raise HTTPException(status_code=400)
    return result
