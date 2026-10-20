import threading
import time
import os
import httpx
import uvicorn
import json
import logging
import base64
import uuid
import asyncio
import websockets
from urllib.parse import quote
from httpx import AsyncClient
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from contextlib import asynccontextmanager
from fastapi import (FastAPI, HTTPException, Request, Response, 
APIRouter, Form, File, UploadFile)
from agent.event_loop import HarnessAgent
from langchain.schema import HumanMessage, SystemMessage
from agent.core.config import (KAFKA_BROKER, PORT_PM_AGENT, 
PORT_PARSERS_SERVICE, PORT_GATEWAY_PORT,
TG_BOT_PUSH_URL, TRANSCRIBE_PROVIDER)
from agent.mobile.lite_runner import run_lite
from agent.mobile.action_parser import parse_mobile_response
from agent.tools.content.tool_generate_audio import audio_generator
from agent.core.db import AgentDB
from agent.core.file_parsers import parse_file
from agent.core import obs_file_storage
from api.web_ui import web_router, pending_files, set_db as _set_web_ui_db, set_active_tasks as _set_web_ui_tasks


logging.basicConfig(level=logging.INFO)



class _DropInternalBotStreamLogs(logging.Filter):
    """Suppress noisy httpx per-chunk logs for /internal/bot_stream only."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "HTTP Request:" in msg and "/internal/bot_stream" in msg:
            return False
        return True


_httpx_filter = _DropInternalBotStreamLogs()
logging.getLogger("httpx").addFilter(_httpx_filter)
logging.getLogger("httpcore").addFilter(_httpx_filter)

db = AgentDB()

class StopMessagePayload(BaseModel):
    request_id: str
    user_id: Optional[int] = None

# Глобальные переменные для producer/consumer
producer: AIOKafkaProducer | None = None
consumer: AIOKafkaConsumer | None = None
consumer_alive = False
agent: HarnessAgent | None = None
active_tasks: dict[str, asyncio.Task] = {}

FILE_WAIT_TIMEOUT = 20.0  # сек, сколько kafka-хендлер готов ждать файлы

async def get_producer() -> AIOKafkaProducer:
    global producer
    if producer is None:
        producer = AIOKafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            key_serializer=lambda k: str(k).encode("utf-8"),
            value_serializer=lambda m: json.dumps(m).encode("utf-8"),
            max_request_size=10485760,
        )
        await producer.start()
    return producer


async def wait_for_files(message_id: str, timeout: float = FILE_WAIT_TIMEOUT) -> tuple[str, list[dict]]:
    """Дождаться файлов для message_id.

    Возвращает кортеж (text, images):
      text   — извлечённый текст текстовых/офисных файлов (или пустая строка),
      images — список картинок JPEG/PNG с base64 для vision-тула (или []).
    По таймауту возвращает ("", []).
    """
    entry = pending_files.setdefault(
        message_id, {"event": asyncio.Event(), "text": "", "images": []}
    )
    try:
        await asyncio.wait_for(entry["event"].wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logging.warning(f"Файлы для message_id={message_id} не пришли за {timeout}s, продолжаем без них")
    finally:
        pending_files.pop(message_id, None)  # чистим память в любом случае
    return entry.get("text", ""), entry.get("images", [])




async def handle_message(payload: dict, prod: AIOKafkaProducer):
    """Обработка одного сообщения — запускается конкурентно."""
    start = time.perf_counter()

    user_id = payload.get("user_id")
    chat_id = payload.get("chat_id")
    text = payload.get("message", "")
    is_from_bot = payload.get("is_from_bot", False)
    type_agent = payload.get("type_agent")
    request_id = payload.get("request_id")
    tb_name = payload.get("tb_name")
    username = payload.get("username")
    selected_tools = payload.get("selected_tools")
    context_size = payload.get("context_size")
    message_id = payload.get("message_id")
    has_files = payload.get("has_files", False)
    agent_model = payload.get("agent_model", "coder")
    gateway_target = payload.get("gateway_target", "main")
    # Физический telegram chat id — адрес доставки ответа. chat_id (выше) для
    # telegram теперь = session_id (ключ памяти), а push ответа идёт на tg_chat_id.
    # Прокидываем его обратно в bot_message, чтобы endpoint_consumer знал, куда слать.
    tg_chat_id = payload.get("tg_chat_id")
    # Mobile-оркестратор: роутер в chats_service проставляет уровень обработки.
    #   agent_tier = "lite"  -> дешёвая модель (Haiku) отвечает сама (дефолт);
    #   agent_tier = "agent" -> полный агент с тулами (юзер явно позвал).
    # agent_farewell=True -> это ПОСЛЕДНИЙ ход agent-режима (счётчик дошёл до 0):
    #   сервер подмешивает в конец speech прощание. Сам агент про счётчик не знает.
    agent_tier = payload.get("agent_tier", "lite")
    agent_farewell = bool(payload.get("agent_farewell", False))
    # Персона голосового героя (Пейто/Атлантик). Прилетает из приложения;
    # по ней жёстко мапятся голос+стиль TTS и блок личности в промпте.
    mobile_persona = payload.get("persona")

    # Проактивная инициация (будильники завтрак/обед/ужин + poke). Бот шлёт
    # спец-payload без "живого" текста от человека: initiated=True + слот/адресность.
    tg_initiated = bool(payload.get("initiated", False))
    tg_slot = payload.get("slot")                 # morning|lunch|dinner|poke
    tg_addressed = bool(payload.get("addressed", False))
    tg_target_username = payload.get("target_username")
    tg_sad = bool(payload.get("sad_followup", False))
    tg_custom_prompt = payload.get("custom_prompt")

    # САМОЦИКЛ АГЕНТА: тик петли приходит от consumer с user_id=-100, loop_tick=True.
    # loop_instruction — установка (consumer подтянул её из БД и положил в message).
    loop_flag = bool(payload.get("loop", False))
    loop_tick = bool(payload.get("loop_tick", False))
    loop_instruction = payload.get("loop_instruction")


    # Служебный тик петли (loop_tick, user_id=-100) ДОЛЖЕН пройти гейт: это
    # новый вход от сервиса, не ответ бота. Остальные bot/‑сообщения и user_id=-1 — отсекаем.
    if (is_from_bot or user_id == -1) and not loop_tick:
        return
    # # Для проактивной инициации текст в payload может быть пустым — генерим сами.
    # if (not text) and (not tg_initiated):
    #     pass
    # if (not text and not tg_initiated) or chat_id is None:
    #     logging.warning(f"Некорректное сообщение: {payload}")
    #     return
    print("ВНУТРИ АГЕНТА", payload)
    # Маршрутизация: ouroboros → Ouroboros API, pm → HarnessAgent

    # if type_agent != "pm":
    #     return

    # message = f"[CatchPM, ТабельНомер: 23423423, Пользователь: Иванов Иванон Иваныч, user_id=-1234]\n" + text
    is_telegram = (gateway_target == "telegram")
    is_max = (gateway_target == "max")
    is_mobile = (gateway_target == "mobile") # Если это мобильный клиент
    if is_telegram:
        # Источник Telegram: своя шапка (без CatchPM/табельного).
        from agent.telegram.tg_integration import (
            build_tg_prefix, DEFAULT_TG_MODEL,
            build_initiation_prompt, build_sad_followup_prompt,
            build_custom_trigger_prompt,
        )
        _chat_type = "group" if str(chat_id).startswith("-") else "private"
        # slot от бота: для preset-будильников это breakfast|lunch|dinner,
        # для broadcast-совместимости также morning|lunch|dinner, для тычка poke.
        _preset_slots = {"breakfast", "lunch", "dinner", "morning"}
        if tg_initiated:
            # Проактив: текст задачи агент генерит из промпта инициации/грусти.
            if tg_slot == "reaction" and tg_custom_prompt:
                # Триггер по реакциям: промпт уже готов (build_reaction_proactive_prompt) — идёт как есть.
                message = tg_custom_prompt
            elif tg_sad:
                message = build_sad_followup_prompt(tg_target_username)
            elif (tg_custom_prompt and tg_custom_prompt.strip()) or (tg_slot in _preset_slots):
                # Триггер по расписанию: либо custom_prompt, либо preset-будильник
                # (breakfast/lunch/dinner) с пустым промптом -> дефолтная задача по preset.
                message = build_custom_trigger_prompt(
                    tg_custom_prompt or "", tg_target_username,
                    chat_type=_chat_type, addressed=tg_addressed,
                    preset_key=tg_slot,
                )
            else:
                message = build_initiation_prompt(
                    tg_slot or "poke", tg_target_username,
                    chat_type=_chat_type, addressed=tg_addressed,
                )
        else:
            message = build_tg_prefix(username, chat_id, _chat_type) + text
    elif is_max:
        # Источник MAX: простая шапка (без CatchPM/табельного, по аналогии с TG).
        from agent.max.max_integration import build_max_prefix
        _chat_type_max = "group" if str(chat_id).startswith("-") else "private"
        message = build_max_prefix(username, chat_id, _chat_type_max) + text
    elif loop_tick:
        # Самоцикл: служебный тик. Текст уже содержит установку (из consumer).
        # Даём спец-шапку вместо табельного (tb_name/username у сервиса пустые).
        _loop_instr = (loop_instruction or text or "").strip()
        message = (
            f"[AGENT SELF-LOOP — служебный тик самоцикла, chat_id: {chat_id}]\n"
            "Это автоматическое продолжение работы без нового сообщения от пользователя. "
            "Действуй самостоятельно согласно установке ниже, продолжая начатое.\n\n"
            f"[УСТАНОВКА]\n{_loop_instr}"
        )
    else:
        message = f"[CatchPM, ТабельНомер: {tb_name}, Пользователь: {username}, chat_id: {chat_id}, user_id: {user_id}]\n" + text

    if is_mobile:
        from agent.mobile.mobile_settings import (build_agent_instruction, LIMIT_VOICE_TEXT,
            text_compressor)
        from agent.mobile.personas import get_persona_prompt
        # Персона приклеивается и к agent-инструкции, и к lite (внутри run_lite),
        # чтобы герой был один на обоих мозгах. В agent-режиме здесь собираем
        # полную инструкцию с характером; lite соберёт свою в run_lite.
        message = build_agent_instruction(get_persona_prompt(mobile_persona)) + text
    # Скиллы юзера — только для CatchPM (в Telegram/MAX user_id — id мессенджера, не наш users).
    if not is_telegram and not is_max:
        selected_skills = await db.get_selected_skills_for_user(int(user_id))
        if selected_skills:
            selected_chunks = []
            for idx, sk in enumerate(selected_skills, start=1):
                tools = ", ".join(sk.get("tools") or []) or "не указаны"
                selected_chunks.append(
                    f"#{idx} [id={sk.get('id')}] {sk.get('theme')}\nИнструменты: {tools}\n{sk.get('body')}"
                )
            message += "\n\n[SELECTED_SKILLS_CONTEXT] Ниже скиллы, выбранные пользователем (до 5). Учитывай их как приоритетные инструкции, если они релевантны запросу:\n" + "\n\n".join(selected_chunks)

    images: list[dict] = []
    if has_files and message_id:
        file_text, images = await wait_for_files(message_id)
        if file_text:
            message += file_text

    current_task = asyncio.current_task()
    if request_id and current_task:
        active_tasks[request_id] = current_task

    try:
        logging.info(f"ПОЛЬЗОВАТЕЛЬ ВЫБРАЛ: {selected_tools}, с контекстом {context_size}")
        # ── MOBILE LITE-ВЕТКА ───────────────────────────────────────────────
        # Дешёвая модель (Haiku) отвечает сама, БЕЗ тулов и event-loop'а.
        # process_query НЕ вызывается. Если Haiku в JSON вернёт call_agent=true,
        # ниже (после парсинга) переключаем Redis-режим на agent (режим X:
        # Haiku всё равно отвечает этим же ответом «подключаю агента»).
        is_lite = is_mobile and agent_tier == "lite"
        if is_lite:
            response = {}
            lite_raw = await run_lite(text, persona=mobile_persona)
            lite_parsed = parse_mobile_response(lite_raw)

            # ВАРИАНТ Y (бесшовная эскалация): если Haiku в JSON пометила, что
            # вопрос вне её компетенций (needs_agent) ИЛИ юзер явно позвал
            # агента (call_agent) — НЕ выходим, а в ЭТОМ ЖЕ проходе дёргаем
            # полного агента. Реплику-мостик Haiku ("дай-ка подумать…")
            # склеиваем в начало ответа агента -> один голос, без стыка и без
            # упоминания слова «агент». Заодно включаем agent-режим в Redis
            # (следующие ходы поедут на агента без эскалации).
            if lite_parsed.get("needs_agent") or lite_parsed.get("call_agent"):
                if lite_parsed.get("call_agent"):
                    try:
                        from agent.mobile.mode_store import enable_agent_mode
                        await enable_agent_mode(chat_id)
                        logging.info(f"[mobile] chat_id={chat_id}: эскалация -> agent-режим (5 ходов)")
                    except Exception as _mode_err:
                        logging.error(f"[mobile] не удалось включить agent-режим: {_mode_err}")

                # БЕСШОВНЫЙ МОСТИК: реплику Haiku ("дай-ка подумать…") озвучиваем
                # голосом ТЕКУЩЕЙ персоны и СРАЗУ кладём напрямую в Redis
                # mobile:reply (мимо Kafka и БД — это промежуточная реплика).
                # Юзер слышит голос героя мгновенно, пока агент работает;
                # финальный ответ приедет вторым сообщением штатным путём.
                bridge = (lite_parsed.get("speech") or "").strip()
                if bridge:
                    try:
                        from agent.mobile.personas import get_voice_and_style
                        from agent.mobile.mode_store import push_bridge_reply
                        b_voice, b_style = get_voice_and_style(mobile_persona)
                        bridge_meta = await audio_generator(text=bridge, voice=b_voice, style=b_style)
                        if not bridge_meta["result"]:
                            await push_bridge_reply(chat_id, bridge_meta["fpath"])
                        else:
                            logging.warning("[mobile] не удалось озвучить мостик, эскалирую без раннего голоса")
                    except Exception as _bridge_err:
                        logging.error(f"[mobile] ошибка озвучки/отправки мостика: {_bridge_err}")

                response = await agent.process_query(user_input=message,
                                                request_id=request_id, user_id=user_id,
                                                chat_id=chat_id, database=db, selected_tools=selected_tools,
                                                context_size=context_size,
                                                source="CatchPM",
                                                agent_model=agent_model,
                                                images=images,
                                                gateway_target=gateway_target,
                                                tg_chat_id=tg_chat_id)
                response_text = response["message"]
                is_lite = False  # дальше обрабатываем как полноценный ответ агента
            else:
                response_text = lite_raw
        else:
            response = await agent.process_query(user_input=message,
                                            request_id=request_id, user_id=user_id,
                                            chat_id=chat_id, database=db, selected_tools=selected_tools,
                                            context_size=context_size,
                                            source=("telegram" if is_telegram else ("max" if is_max else "CatchPM")),
                                            agent_model=agent_model,
                                            images=images,
                                            gateway_target=gateway_target,
                                            tg_chat_id=tg_chat_id)
            response_text = response["message"]
        # Mobile: ответ приходит СТРОГИМ JSON {"v","speech","actions","style",...}.
        # Парсер fallback-safe: при кривом JSON весь текст уходит в speech.
        # Вариант A: в историю чата кладём чистый speech, actions/style едут
        # отдельными полями на телефон (Redis long-poll).
        mobile_speech = None
        mobile_actions = []
        mobile_style = None
        if is_mobile:
            
            parsed = parse_mobile_response(response_text)
            mobile_speech = parsed["speech"]
            mobile_actions = parsed["actions"]
            mobile_style = parsed.get("style")

            # Мостик-реплика Haiku при эскалации уже ушла ОТДЕЛЬНЫМ голосовым
            # сообщением в Redis (push_bridge_reply) ПЕРЕД вызовом агента —
            # здесь к ответу агента её НЕ клеим (иначе прозвучит дважды).

            # AGENT: последний ход режима (счётчик дошёл до 0) -> прощаемся голосом.
            # Подмешивает СЕРВЕР; агент про счётчик не осведомлён.
            if (agent_tier != "lite") and agent_farewell:
                from agent.mobile.mobile_settings import AGENT_FAREWELL_SUFFIX
                mobile_speech = (mobile_speech or "") + AGENT_FAREWELL_SUFFIX

            # дальше по пайплайну (TTS + история чата) используем чистый текст
            response_text = mobile_speech
        # from datetime import datetime
        # now = datetime.now()
        # response = {'action': "test"}
        # formatted_time = now.strftime("%H:%M:%S")
        # response_text = f"Идет внедрение новой фичи! Cпасибо за понимание. Текущее время: **{formatted_time}**, {message}"
        files_to_send = response.get("files_to_send")  # [{"path": "...", ...}, ...]

        
        if is_mobile: # Генерируем голос по speech. Пустой speech -> команда без озвучки.
            from agent.mobile.personas import get_voice_and_style
            voice_id, persona_style = get_voice_and_style(mobile_persona)
            # Модель может уточнить эмоцию в поле style -> домешиваем к базовому
            # стилю персоны; если не прислала — остаётся базовый стиль героя.
            eff_style = f"{persona_style}, {mobile_style}" if mobile_style else persona_style

            speech_clean = (response_text or "").strip()
            if not speech_clean:
                # Пустой speech = команда (фонарик/громкость/трек): TTS НЕ генерим,
                # на телефон уедут только actions. Мобилка выполнит молча / свой ack.
                logging.info(f"[mobile] speech пуст -> пропускаю TTS, только actions ({len(mobile_actions)})")
            else:
                voice_text = speech_clean
                if len(speech_clean) > LIMIT_VOICE_TEXT:
                    voice_text = text_compressor(speech_clean)
                voice_metadata = await audio_generator(text=voice_text, voice=voice_id, style=eff_style)
                if not voice_metadata["result"]:
                    files_to_send = [{"kind": "voice", "path": voice_metadata["fpath"]}]
                else:
                    pass # Нужно вернуть сигнал об ошибке. Дефолтные ГС на мобильном клиенте

        attached_files = None
        if files_to_send:
            attached_files = []
            for f in files_to_send:
                if f.get("kind") == "image":
                    entry = {
                        "name": f.get("name") or "image.jpg",
                        "path": f.get("path"),
                        "kind": "image",
                        "image_key": f.get("image_key"),
                    }
                else:
                    # Не-image файл: заливаем в OBS (obs://{OBS_BUCKET}/catchpm/{chat_id}/...).
                    # Ключ детерминирован от (chat_id + local_path), поэтому
                    # /web/file сможет восстановить тот же ключ без маппинга в БД.
                    try:
                        await obs_file_storage.upload_user_file(f["path"], chat_id)
                    except Exception as _obs_err:
                        logging.error(f"OBS upload failed for {f.get('path')}: {_obs_err}")
                    entry = {
                        "name": os.path.basename(f["path"]),
                        "path": f["path"],
                        "kind": f.get("kind"),
                    }

                # Для Telegram host-бот изолирован от ФС агента (разные контейнеры),
                # поэтому os.path.exists(path) на его стороне вернёт False и файл
                # молча не отправится. Дочитываем байты ЗДЕСЬ и кладём base64 в
                # payload — бот возьмёт file_b64 без обращения к диску. Лимит TG 20MB.
                if is_telegram:
                    try:
                        _fpath = entry.get("path")
                        if _fpath and os.path.exists(_fpath):
                            _size = os.path.getsize(_fpath)
                            # Мелкие файлы — inline base64 (быстро, без второго HTTP).
                            # Крупные (> TG_INLINE_B64_MAX) НЕ кладём в Kafka как base64
                            # (MessageSizeTooLargeError, лимит брокера ~1 МБ). Вместо байтов
                            # кладём file_url — бот (host-режим, порт 9001) сам скачает файл
                            # с /web/file, который отдаёт его с диска или из OBS.
                            _inline_max = int(os.getenv("TG_INLINE_B64_MAX", str(700 * 1024)))
                            if entry.get("kind") != "image" and _size <= 20 * 1024 * 1024:
                                if _size <= _inline_max:
                                    with open(_fpath, "rb") as _fh:
                                        entry["file_b64"] = base64.b64encode(_fh.read()).decode("ascii")
                                else:
                                    entry["file_url"] = (
                                        f"/web/file?path={quote(_fpath)}"
                                        f"&chat_id={quote(str(chat_id))}"
                                    )
                                    logging.info(
                                        f"[telegram] файл {_fpath} = {_size} байт > {_inline_max}, "
                                        f"уходит через file_url (бот скачает с /web/file, Kafka лёгкая)"
                                    )
                            elif entry.get("kind") == "image" and _size <= 20 * 1024 * 1024:
                                with open(_fpath, "rb") as _fh:
                                    entry["file_b64"] = base64.b64encode(_fh.read()).decode("ascii")
                            else:
                                logging.warning(
                                    f"[telegram] файл {_fpath} = {_size} байт > 20MB, "
                                    f"не влезает в лимит TG, пропускаю"
                                )
                        else:
                            # Сгенерированные картинки (generate_image) лежат НЕ на диске,
                            # а в БД generated_images (bytea), path=None.
                            # ВАЖНО: base64 картинки (~1+ МБ) В payload ломает Kafka
                            # (MessageSizeTooLargeError, дефолтный лимит 1 МБ). Поэтому
                            # бинарник СЮДА НЕ кладём — оставляем только image_key.
                            # chats-consumer перед отправкой в Telegram сам достанет
                            # картинку из generated_images по image_key и b64-закодирует.
                            _img_key = entry.get("image_key")
                            if entry.get("kind") == "image" and _img_key:
                                logging.info(
                                    f"[telegram] картинка image_key={_img_key} пойдёт через image_key "
                                    f"(бинарник тянет chats-consumer, Kafka лёгкая)"
                                )
                            else:
                                logging.warning(f"[telegram] файл не найден на диске агента: {_fpath}")
                    except Exception as _b64_err:
                        logging.error(f"[telegram] не удалось прочитать файл в base64: {_b64_err}")

                attached_files.append(entry)

        message_id = f"msg-{int(time.time() * 1000)}-{str(uuid.uuid4())[:8]}"

        # Дозаполняем message_id в generated_images по image_key.
        # message_id бота генерится ЗДЕСЬ (после process_query), поэтому тул
        # физически не мог записать его в generated_images (там оставался NULL).
        # Связываем постфактум по image_key — он создаётся в туле и доходит до
        # обоих концов (generated_images и chat_files). Без этого фолбэк-поиск
        # картинки по паре (message_id, file_name) в /web/db_image давал 404.
        if attached_files:
            image_keys = [
                f.get("image_key")
                for f in attached_files
                if f.get("kind") == "image" and f.get("image_key")
            ]
            if image_keys:
                try:
                    async with db.pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE generated_images SET message_id = $1 "
                            "WHERE image_key::text = ANY($2::text[]) "
                            "AND message_id IS NULL",
                            message_id, image_keys,
                        )
                    logging.info(
                        f"generated_images: message_id={message_id} проставлен "
                        f"для image_key={image_keys}"
                    )
                except Exception as link_err:
                    logging.error(
                        f"Не удалось привязать message_id к generated_images: {link_err}"
                    )

        bot_message = {
            "user_id": -1,
            "chat_id": chat_id,
            "message": response_text.strip(),
            "message_id": message_id,
            "is_from_bot": True,
            "type_agent": "pm",
            "selected_tools": selected_tools,
            "action": response.get('action'),
            "attached_files": attached_files,
            "tool_trace": response.get("tool_trace"), # str,
            "agent_model": response.get("agent_model"),
            "tokens_usage": response.get("tokens_usage"),
            "gateway_target": gateway_target,
            "tg_chat_id": tg_chat_id,
            "auto_title": response.get("auto_title"),
            # Mobile device actions (белый список): едут на телефон через
            # endpoint_consumer.push_to_mobile -> Redis -> long-poll мобилки.
            # Для не-mobile всегда [] — поле нейтральное.
            "mobile_actions": mobile_actions,
            # САМОЦИКЛ: пробрасываем флаг и установку, чтобы consumer после
            # логирования сделал реентри (новый тик) — только для web (gateway main).
            "loop": loop_flag,
            "loop_instruction": loop_instruction,
        }

        await prod.send_and_wait("chat-messages", value=bot_message)
        logging.info(f"Ответ в чат {chat_id}: {response_text[:50]}...")
        logging.info(f"Время обработки: {time.perf_counter() - start:.4f}s")

    except asyncio.CancelledError:
        logging.info(f"Run cancelled by user: request_id={request_id} chat_id={chat_id}")
        try:
            stop_message = {
                "user_id": -1,
                "chat_id": chat_id,
                "message": "Остановлено пользователем…",
                "message_id": f"msg-stop-{int(time.time() * 1000)}",
                "is_from_bot": True,
                "type_agent": "pm",
                "action": "stopped",
                "gateway_target": gateway_target,
                "tg_chat_id": tg_chat_id
            }
            await prod.send_and_wait("chat-messages", value=stop_message)
        except Exception as send_err:
            logging.error(f"Не удалось отправить stop-сообщение в Kafka: {send_err}")
        raise

    except Exception as e:
        logging.error(f"Ошибка обработки сообщения: {e}")
        try:
            err_message = {
                "user_id": -1,
                "chat_id": chat_id,
                "message": "Произошла внутренняя ошибка при обработке запроса агентом. Попробуйте еще раз.",
                "message_id": f"msg-err-{int(time.time() * 1000)}",
                "is_from_bot": True,
                "type_agent": "pm",
                "action": "error",
                "gateway_target": gateway_target,
                "tg_chat_id": tg_chat_id
            }
            await prod.send_and_wait("chat-messages", value=err_message)
        except Exception as send_err:
            logging.error(f"Не удалось отправить сообщение об ошибке в Kafka: {send_err}")
    finally:
        if request_id:
            active_tasks.pop(request_id, None)


async def _process_service_message(message_id: int, enriched_text: str):
    """Обрабатывает сообщение сервиса в фоне, пишет ответ в service_chats."""
    try:
        await _db.update_service_status(message_id)
 
        result = await _agent.process_query(
            user_input=enriched_text,
            request_id=None,
            user_id=None,           # нет пользователя — это сервис
            chat_id=None,
            database=_db,
            source="service",
        )
        reply = result.get("message", "Агент не вернул ответ.")
        status = "done"
 
    except Exception as e:
        logging.error("Ошибка обработки service message id=%s: %s", message_id, e, exc_info=True)
        reply  = f"Ошибка агента: {e}"
        status = "error"
 
    async with _db.pool.acquire() as conn:
        await conn.execute(_SQL_UPDATE_DONE, reply, status, message_id)
 
    logging.info("service_chats id=%s → status=%s | %.60s", message_id, status, reply)


async def kafka_consumer():
    global consumer_alive

    consumer = AIOKafkaConsumer(
        "chat-messages",
        bootstrap_servers=[KAFKA_BROKER],
        group_id="llm-bot-group",
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        session_timeout_ms=30000,  # Ждем 60 сек перед тем как убить клиента
        heartbeat_interval_ms=5000,  # Шлем пульс каждые 10 сек
        max_poll_interval_ms=300000,  # Даем 5 минут на обработку одного сообщения
        max_poll_records=1,
        max_partition_fetch_bytes=10485760
    )

    await consumer.start()
    consumer_alive = True
    logging.info("LLM-бот запущен и ждёт сообщения...")

    prod = await get_producer()

    tasks: set[asyncio.Task] = set()

    try:
        async for message in consumer:
            # Каждое сообщение — отдельная задача, не ждём завершения
            task = asyncio.create_task(handle_message(message.value, prod))

            # Держим ссылки на таски, чтобы GC их не собрал
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    except asyncio.CancelledError:
        logging.info("Kafka consumer остановлен, ждём завершения активных задач...")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        consumer_alive = False
        await consumer.stop()

async def shutdown():
    global producer
    if producer:
        await producer.stop()

async def kafka_moment():
    try:
        await kafka_consumer()
    finally:
        await shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, digest_scheduler

    await db.init_db_pool()
    await db.init_agent_admin_pool() 
    await db.init_agent_base_pool()
    # web_ui использует ТОТ ЖЕ прогретый db (иначе его локальный pool=None -> 503)
    _set_web_ui_db(db)
    _set_web_ui_tasks(active_tasks)   # web_ui.stop_message видит тот же словарь задач

    from agent.consumers.task_events_consumer import start_task_events_consumer


    kafka_task = asyncio.create_task(kafka_consumer())
    task_events_task = asyncio.create_task(start_task_events_consumer())
    # Клинер эфемерных файлов generated_files (TTL 30 мин):
    # чистит и свежесозданные файлы, и скачанные из OBS. Источник истины — OBS.
    obs_cleaner_task = asyncio.create_task(obs_file_storage.start_generated_files_cleaner())
    agent = HarnessAgent(database=db)
    connect_service_routers(agent, db)

    yield

    # Graceful shutdown
    logging.info("Shutting down...")

    # 2. Kafka
    kafka_task.cancel()
    await asyncio.gather(kafka_task, return_exceptions=True)
    
    task_events_task.cancel()
    await asyncio.gather(task_events_task, return_exceptions=True)

    obs_cleaner_task.cancel()
    await asyncio.gather(obs_cleaner_task, return_exceptions=True)


    await shutdown()
    await db.close()

app = FastAPI(lifespan=lifespan)

tg_router = APIRouter(prefix="/api/tg", tags=["telegram"])
service_router = APIRouter(prefix="/service", tags=["service"])



"""
agent_service/api/tg_endpoint.py
─────────────────────────────────
Эндпоинт POST /api/tg/message для приёма сообщений из tg_bot контейнера.
Подключается к app.py через app.include_router(tg_router).
"""


class TgMessageRequest(BaseModel):
    message:   str
    user_id:   int
    username:  str
    user_tag: str
    chat_id:   int
    chat_type: str = "private"   # "private" | "group"
    file_b64: Optional[str] = None   # Base64 строка с содержимым файла
    file_name: Optional[str] = None  # Имя файла (например, "document.pdf")


class TgMessageResponse(BaseModel):
    reply: str

class ParsingMessage(BaseModel):
    status: str

class ServiceMessageRequest(BaseModel):
    service_name: str           # "parsers_service"
    service_port: Optional[int] = None   # 8003
    message: str                # текст команды для агента
 
 
class ServiceMessageResponse(BaseModel):
    status: str                 # "accepted"
    message_id: int             # ID записи в service_chats

_agent = None
_db    = None


def connect_service_routers(agent, db):
    global _agent, _db
    _agent = agent
    _db    = db


class TgShouldRespondRequest(BaseModel):
    chat_id: int
    buffer_lines: list[str] = []   # ["Ник: текст", ...] из Redis-буфера бота


class TgShouldRespondResponse(BaseModel):
    respond: bool


@tg_router.post("/should_respond", response_model=TgShouldRespondResponse)
async def tg_should_respond(req: TgShouldRespondRequest) -> TgShouldRespondResponse:
    """
    Оркестратор на llm_lite: по буферу последних сообщений группы
    решает, обращаются ли к агенту. Буфер готовит бот (из Redis),
    БД для контекста не трогаем. Fail-open в сторону молчания.
    """
    if _agent is None:
        return TgShouldRespondResponse(respond=False)
    try:
        from agent.telegram.tg_integration import should_respond, get_tg_chat_settings
        # подтянем имя агента из настроек чата (fail-open: None → дефолтный промпт)
        agent_name = None
        try:
            cfg = await get_tg_chat_settings(getattr(_agent, "db", None), str(req.chat_id))
            agent_name = cfg.get("agent_name")
        except Exception:
            pass
        ok = await should_respond(_agent, req.buffer_lines, agent_name)
        return TgShouldRespondResponse(respond=bool(ok))
    except Exception as e:
        logging.error(f"tg_should_respond failed: {e}")
        return TgShouldRespondResponse(respond=False)


class TgReactionRequest(BaseModel):
    chat_id: int
    agent_message_id: str | None = None   # agent uuid сообщения бота (для БД-аналитики)
    emoji: str = ""
    action: str = "add"                     # add | remove
    user_id: int | None = None
    username: str | None = None
    agent_text: str = ""                    # текст сообщения бота, на которое реакция


class TgReactionResponse(BaseModel):
    sentiment: str = "neutral"
    note: str = ""


@tg_router.post("/analyze_reaction", response_model=TgReactionResponse)
async def tg_analyze_reaction(req: TgReactionRequest) -> TgReactionResponse:
    """Лайт-анализ реакции на сообщение бота + запись в БД (аналитика).

    Бот дёргает этот эндпоинт когда прилетает message_reaction. Возвращает
    sentiment (pos|neg|neutral) + note, чтобы бот решил про проактив.
    Сам анализ — на llm_lite. Fail-open → neutral.
    """
    if _agent is None:
        return TgReactionResponse()
    try:
        from agent.telegram.tg_integration import analyze_reaction
        text = req.agent_text or ""
        # если текст не пришёл — подтянем из БД по agent_message_id
        if not text and req.agent_message_id and getattr(_agent, "db", None):
            try:
                text = await _agent.db.get_bot_message_text(req.agent_message_id) or ""
            except Exception:
                pass
        res = await analyze_reaction(_agent, req.emoji, req.action, text)
        # запись реакции в БД (аналитика) — fail-open
        if req.agent_message_id and getattr(_agent, "db", None):
            try:
                await _agent.db.append_reaction(req.agent_message_id, {
                    "user_id": req.user_id,
                    "username": req.username,
                    "emoji": req.emoji,
                    "action": req.action,
                    "sentiment": res.get("sentiment", "neutral"),
                    "note": res.get("note", ""),
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                logging.warning(f"append_reaction fail: {e}")
        return TgReactionResponse(sentiment=res.get("sentiment", "neutral"), note=res.get("note", ""))
    except Exception as e:
        logging.error(f"tg_analyze_reaction failed: {e}")
        return TgReactionResponse()


class TgReactionsFeedbackRequest(BaseModel):
    reactions: list = []


class TgReactionsFeedbackResponse(BaseModel):
    feedback: str = ""


@tg_router.post("/build_reactions_feedback", response_model=TgReactionsFeedbackResponse)
async def tg_build_reactions_feedback(req: TgReactionsFeedbackRequest) -> TgReactionsFeedbackResponse:
    """Собирает feedback-блок по реакциям для большого агента."""
    try:
        from agent.telegram.tg_integration import build_reactions_feedback
        return TgReactionsFeedbackResponse(feedback=build_reactions_feedback(req.reactions or []))
    except Exception as e:
        logging.error(f"build_reactions_feedback failed: {e}")
        return TgReactionsFeedbackResponse()


class TgReactionPromptRequest(BaseModel):
    kind: str = "pos"
    reactions: list = []
    count: int = 0


class TgReactionPromptResponse(BaseModel):
    prompt: str = ""


@tg_router.post("/build_reaction_prompt", response_model=TgReactionPromptResponse)
async def tg_build_reaction_prompt(req: TgReactionPromptRequest) -> TgReactionPromptResponse:
    """Проактив-промпт по реакциям (pos: развить тему / neg: шутливо уточнить, что не так)."""
    try:
        from agent.telegram.tg_integration import build_reaction_proactive_prompt
        return TgReactionPromptResponse(prompt=build_reaction_proactive_prompt(req.kind, req.reactions or []))
    except Exception as e:
        logging.error(f"build_reaction_prompt failed: {e}")
        return TgReactionPromptResponse()


class TgTranscribeRequest(BaseModel):
    audio_b64: str
    fmt: str = "ogg"
    language: str = "ru"
    provider: str | None = None  # None -> env TG_ASR_PROVIDER -> cloud_ru


class TgTranscribeResponse(BaseModel):
    ok: bool = False
    text: str = ""
    error: str = ""


@tg_router.post("/transcribe", response_model=TgTranscribeResponse)
async def tg_transcribe(req: TgTranscribeRequest) -> TgTranscribeResponse:
    """Распознавание голосового сообщения Telegram (ogg/opus) -> текст.

    Провайдер: req.provider | env TG_ASR_PROVIDER | "cloud_ru" (whisper).
    """
    import base64 as _b64
    from agent.transcribe import transcribe_audio
    try:
        audio = _b64.b64decode(req.audio_b64)
    except Exception as e:
        return TgTranscribeResponse(error=f"bad base64: {e}")
    if not audio:
        return TgTranscribeResponse(error="empty audio")
    provider = (req.provider or TRANSCRIBE_PROVIDER)
    logging.info(f"[TRANSCRIBE] {provider}")
    try:
        text = await transcribe_audio(audio, provider=provider, language=req.language or "ru", fmt=req.fmt or "ogg")
        if not text:
            return TgTranscribeResponse(ok=True, text="")
        return TgTranscribeResponse(ok=True, text=text)
    except Exception as e:
        logging.error(f"tg_transcribe failed (provider={provider}): {e}")
        return TgTranscribeResponse(error=str(e)[:300])


@service_router.post("/message", response_model=ServiceMessageResponse, status_code=202)
async def handle_service_message(req: ServiceMessageRequest):
    """
    Принимает команду от внешнего сервиса, сохраняет в service_chats,
    запускает обработку агентом в фоне и сразу возвращает 202 Accepted.
 
    Пример тела от parsers_service:
        {
          "service_name": "parsers_service",
          "service_port": 8003,
          "message": "Я сервис парсинга на порту 8003, подготовил для тебя 42 новые статьи.
                      Посмотри papers_metadata и отбери релевантные (is_relevant IS NULL).
                      Для каждой проставь is_relevant=true/false и relevance_score."
        }
    """
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
 
    # 1. Записываем входящее сообщение в service_chats
    row = await _db.add_service_message(req.service_name, req.service_port, req.message)
    message_id = row["id"]
 
    log.info(
        "service_chats: новое сообщение id=%s от %s:%s | %.80s",
        message_id, req.service_name, req.service_port, req.message,
    )
 
    # 2. Обогащаем текст для агента
    enriched = (
        f"[SERVICE | from={req.service_name} port={req.service_port} "
        f"msg_id={message_id}]\n{req.message}"
    )
 
    # 3. Fire-and-forget — сразу возвращаем 202
    task = asyncio.create_task(_process_service_message(message_id, enriched))
 
    def _on_done(t: asyncio.Task):
        if t.exception():
            log.error("_process_service_message упал: %s", t.exception())
 
    task.add_done_callback(_on_done)
 
    return ServiceMessageResponse(status="accepted", message_id=message_id)


routers = [tg_router, service_router, web_router]

for rout in routers:
    app.include_router(rout)


@app.post("/make_style_report")
async def make_style_report(request: Request):
    data =  await request.json()
    style = data["style"]
    text = data["text"]
    return await make_report(text=text, style=style)

from agent.tools.make_graph.tool_knowledge_graph import GRAPH_SQL

@app.get("/internal/graph")
async def internal_get_graph():
    try:
        async with _db.pool.acquire() as conn:
            row = await conn.fetchrow(GRAPH_SQL)
            raw = row["graph_json"] if row else {}
            # asyncpg возвращает строку из jsonb — парсим
            if isinstance(raw, str):
                import json
                raw = json.loads(raw)
            return raw
    except Exception as e:
        logging.error("internal_get_graph failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    status = "ok" if consumer_alive else "error"
    return {"service": "llm-bot", "status": status}


# ── Доступность LLM-провайдеров ────────────────────────────────────
from agent.core.agent_provider import (
    get_llm_model_gpt, get_llm_model_claude, get_llm_model_gemini, get_llm_model_glm
)

_PROVIDERS_TTL = 30.0  # сек — как часто реально перепроверяем провайдеров
_providers_cache: dict = {"ts": 0.0, "data": None}
_providers_lock = asyncio.Lock()

# id модели в UI -> человекочитаемое имя + фабрика llm
_PROVIDER_MODELS = {
    "coder":     ("openai/gpt-5.4",           get_llm_model_gpt),
    "designer":  ("anthropic/claude-opus-4.8",      get_llm_model_claude),
    "universal": ("zai-org/GLM-5.2",  get_llm_model_glm),
    "gemini": ("google/gemini-3.1-pro-preview",  get_llm_model_gemini),
}


async def _ping_provider(model_id: str, model_name: str, factory) -> dict:
    """Минимальный вызов модели. up=True если провайдер ответил без ошибки."""
    try:
        llm = factory()
        # 1 токен, короткий таймаут — нам важен только факт живости
        await asyncio.wait_for(
            llm.ainvoke(
                [HumanMessage(content="ping")],
            ),
            timeout=20.0,
        )
        return {"id": model_id, "name": model_name, "up": True}
    except asyncio.TimeoutError:
        return {"id": model_id, "name": model_name, "up": False, "reason": "timeout"}
    except Exception as e:
        return {"id": model_id, "name": model_name, "up": False, "reason": str(e)[:120]}




@app.get('/api/skills')
async def api_list_skills(query: str = '', sort_by: str = 'new', tool: str | None = None, limit: int = 100):
    skills = await db.list_skills(query=query, sort_by=sort_by, tool=tool, limit=limit)
    return {'skills': skills}


@app.post('/api/skills')
async def api_create_skill(payload: dict):
    if not (payload.get('theme') or '').strip() or not (payload.get('body') or '').strip():
        raise HTTPException(status_code=400, detail='theme and body required')
    row = await db.create_skill(
        theme=payload.get('theme'),
        body=payload.get('body'),
        published_by_user_id=payload.get('published_by_user_id'),
        published_by_name=payload.get('published_by_name'),
        tools=payload.get('tools') or [],
    )
    return {'skill': row}


@app.get('/api/skills/selected')
async def api_get_selected_skills(user_id: int):
    if int(user_id or 0) <= 0:
        raise HTTPException(status_code=400, detail='user_id required')
    selected_ids = await db.get_selected_skill_ids(int(user_id))
    return {'selected_ids': selected_ids, 'count': len(selected_ids), 'max_selected': 5}


@app.post('/api/skills/{skill_id}/select')
async def api_toggle_selected_skill(skill_id: int, payload: dict):
    user_id = int(payload.get('user_id') or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail='user_id required')

    result = await db.toggle_skill_selected(skill_id=skill_id, user_id=user_id, max_selected=5)
    if not result.get('ok'):
        if result.get('reason') == 'not_found':
            raise HTTPException(status_code=404, detail='Skill not found')
        if result.get('reason') == 'limit':
            raise HTTPException(status_code=400, detail='Max 5 selected skills per user')
        raise HTTPException(status_code=400, detail='Cannot toggle selected skill')
    return result


@app.post('/api/skills/{skill_id}/like')
async def api_like_skill(skill_id: int, payload: dict):
    user_id = int(payload.get('user_id') or 0)
    if user_id <= 0:
        raise HTTPException(status_code=400, detail='user_id required')
    result = await db.toggle_skill_like(skill_id=skill_id, user_id=user_id)
    return result


@app.get('/api/skills/{skill_id}/prompt')
async def api_skill_prompt(skill_id: int):
    row = await db.get_skill_by_id(skill_id)
    if not row:
        raise HTTPException(status_code=404, detail='Skill not found')
    prompt = (
        f"Тема скилла: {row['theme']}\n"
        f"Рекомендуемые инструменты: {', '.join(row.get('tools') or []) or 'не указаны'}\n\n"
        f"Тело скилла:\n{row['body']}"
    )
    return {'skill_id': skill_id, 'prompt': prompt, 'skill': row}


@app.put('/api/skills/{skill_id}')
async def api_update_skill(skill_id: int, payload: dict):
    user_id = int(payload.get('user_id') or 0)
    theme = (payload.get('theme') or '').strip()
    body = (payload.get('body') or '').strip()
    tools = payload.get('tools') or []

    if user_id <= 0:
        raise HTTPException(status_code=400, detail='user_id required')
    if not theme or not body:
        raise HTTPException(status_code=400, detail='theme and body required')

    row = await db.update_skill(
        skill_id=skill_id,
        user_id=user_id,
        theme=theme,
        body=body,
        tools=tools,
    )
    if not row:
        exists = await db.get_skill_by_id(skill_id)
        if exists:
            raise HTTPException(status_code=403, detail='Only skill author can edit it')
        raise HTTPException(status_code=404, detail='Skill not found')
    return {'ok': True, 'skill': row}


@app.delete('/api/skills/{skill_id}')
async def api_delete_skill(skill_id: int):
    deleted = await db.delete_skill(skill_id)
    if not deleted:
        raise HTTPException(status_code=404, detail='Skill not found')
    return {'ok': True, 'skill_id': skill_id}

@app.get("/internal/providers_status")
async def internal_providers_status():
    """Доступность провайдеров под каждой моделью тоггла.

    Отдаёт {"providers": {model_id: {up: bool, name, reason?}}, "cached_at": ts}.
    Результат кешируется на _PROVIDERS_TTL сек (двойная проверка под локом,
    чтобы параллельные polling-запросы не порождали пачку пингов)."""
    now = time.time()
    if _providers_cache["data"] and (now - _providers_cache["ts"]) < _PROVIDERS_TTL:
        return {"providers": _providers_cache["data"], "cached_at": _providers_cache["ts"]}

    async with _providers_lock:
        now = time.time()
        if _providers_cache["data"] and (now - _providers_cache["ts"]) < _PROVIDERS_TTL:
            return {"providers": _providers_cache["data"], "cached_at": _providers_cache["ts"]}

        results = await asyncio.gather(*[
            _ping_provider(mid, name, factory)
            for mid, (name, factory) in _PROVIDER_MODELS.items()
        ])
        providers = {r["id"]: r for r in results}
        _providers_cache["data"] = providers
        _providers_cache["ts"] = time.time()
        return {"providers": providers, "cached_at": _providers_cache["ts"]}



if __name__ == "__main__":
    # Важно ставить reload=False, т.к. при записи файлов в разрешенные папки, контейнер начинает перезапускаться
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(PORT_PM_AGENT), log_level="info", reload=False)

