import threading
import time
import os
import httpx
from fastapi import (FastAPI, HTTPException, Request, Response, 
APIRouter, Form, File, UploadFile)
from fastapi.responses import StreamingResponse
import uvicorn
import json
import logging
import base64
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from contextlib import asynccontextmanager
# from agent_pm.async_agent import KnowledgeAgent
from agent_pm.langchain_agent import KnowledgeAgent, GATEWAY_URL
from langchain.schema import HumanMessage, SystemMessage
from agent_pm.summary_period import extract_json
import asyncio
from httpx import AsyncClient
# from agent_pm.pm_parser.arxiv_parser import ARXIVParser
from agent_pm.core.config import (KAFKA_BROKER, PORT_PM_AGENT, 
PORT_PARSERS_SERVICE, PORT_GATEWAY_PORT,
TG_BOT_PUSH_URL)

from agent_pm.search_bot.searcher import searcher_main
from agent_pm.core.db import AgentDB
from agent_pm.core.file_parsers import parse_file
from agent_pm.scheduler_digest import start_digest_scheduler, start_interval_digest_scheduler
import websockets
from agent_pm.core.botocore_client import database
from agent_pm.core import obs_file_storage
from urllib.parse import quote
logging.basicConfig(level=logging.INFO)


def _content_disposition(filename: str, disposition: str = "attachment") -> str:
    """
    Собрать корректный заголовок Content-Disposition для имён с не-ASCII
    символами (кириллица и т.п.) по RFC 6266.

    Проблема: Starlette/uvicorn кодируют HTTP-заголовки в latin-1
    (raw_headers = ...v.encode("latin-1")). Имя вроде "LFR_Метрики_2026.pptx"
    в latin-1 не влезает -> UnicodeEncodeError -> 500 на /web/file.

    Решение: ASCII-фолбэк в filename="..." (для старых клиентов) + filename*
    с percent-encoding в UTF-8 (современные браузеры берут именно его).
    """
    fallback = filename.encode("ascii", "replace").decode("ascii")
    quoted = quote(filename, safe="")
    return (
        f"{disposition}; "
        f'filename="{fallback}"; '
        f"filename*=UTF-8''{quoted}"
    )


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
pending_files: dict[str, dict] = {}

class StopMessagePayload(BaseModel):
    request_id: str
    user_id: Optional[int] = None

# Глобальные переменные для producer/consumer
producer: AIOKafkaProducer | None = None
consumer: AIOKafkaConsumer | None = None
consumer_alive = False
agent: KnowledgeAgent | None = None
active_tasks: dict[str, asyncio.Task] = {}

FILE_WAIT_TIMEOUT = 20.0  # сек, сколько kafka-хендлер готов ждать файлы

async def get_producer() -> AIOKafkaProducer:
    global producer
    if producer is None:
        producer = AIOKafkaProducer(
            bootstrap_servers=[KAFKA_BROKER],
            key_serializer=lambda k: str(k).encode("utf-8"),
            value_serializer=lambda m: json.dumps(m).encode("utf-8"),
        )
        await producer.start()
    return producer


web_router = APIRouter()

@web_router.post("/web/upload_files")
async def upload_web_files(
    chat_id: str = Form(...),
    message_id: str = Form(...),
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    parts = []
    images: list[dict] = []          # картинки JPEG/PNG для vision-тула
    IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

    # Серверный предохранитель: не более 10 файлов на сообщение (дубль
    # фронтового лимита MAX_FILES — чтобы нельзя было обойти мимо UI).
    MAX_FILES = 10
    if len(files) > MAX_FILES:
        logging.warning(
            "upload_files: получено %d файлов, обрезаю до %d (message_id=%s)",
            len(files), MAX_FILES, message_id,
        )
        files = files[:MAX_FILES]

    for f in files:
        raw = await f.read()
        ext = os.path.splitext(f.filename or "")[1].lower()

        # Картинки не парсим в текст — кладём base64 отдельно, чтобы vision-тул
        # дёргал строку из pending_files, а контекст модели не засорялся.
        if ext in IMAGE_MIME:
            b64 = base64.b64encode(raw).decode("ascii")
            idx = len(images)
            images.append({
                "filename": f.filename,
                "mime_type": IMAGE_MIME[ext],
                "b64": b64,
            })
            # В текст сообщения — только лёгкий маркер с индексом.
            parts.append(
                f"\n\n🖼️[IMAGE #{idx}: {f.filename}] "
                f"(изображение загружено, для анализа вызови тул analyze_image "
                f"с image_index={idx})"
            )
            continue

        try:
            extracted_text = await asyncio.to_thread(parse_file, f.filename, raw)
            parts.append(f"\n\n📎[FILE] [Прикрепленный файл: {f.filename}]\nСодержимое файла:\n```\n{extracted_text}\n```")
        except Exception as e:
            logging.error("Ошибка парсинга файла %s: %s", f.filename, e)
            parts.append(f"\n\n[Не удалось прочитать прикрепленный файл {f.filename}: {e}]")

    combined_text = "".join(parts)
    entry = pending_files.setdefault(
        message_id, {"event": asyncio.Event(), "text": "", "images": []}
    )
    entry["text"] = combined_text
    entry["images"] = images
    entry["event"].set()

    logging.info(
        f"Файлы для message_id={message_id} обработаны, chat_id={chat_id}, "
        f"картинок={len(images)}"
    )
    return {"status": "ok", "files_count": len(files), "images_count": len(images)}


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

# Whitelist корней, из которых разрешено отдавать файлы с диска.
# Любой запрошенный путь обязан лежать ВНУТРИ одного из этих корней —
# иначе 403 (защита от path-traversal вида ../../etc/passwd).
ALLOWED_FILES_ROOTS = [
    os.path.realpath("/app/generated_files"),
    os.path.realpath("/sandbox"),
]
# Обратная совместимость: часть кода ссылается на единичный корень.
ALLOWED_FILES_ROOT = ALLOWED_FILES_ROOTS[0]


def _is_within_allowed_roots(real_path: str) -> bool:
    """True, если real_path лежит внутри одного из whitelist-корней."""
    for root in ALLOWED_FILES_ROOTS:
        if real_path == root or real_path.startswith(root + os.sep):
            return True
    return False


def _resolve_in_whitelist(file_name: str) -> str | None:
    """Ищет файл по basename в whitelist-корнях (рекурсивно).

    Возвращает абсолютный путь первого совпадения внутри разрешённых
    корней либо None. basename берётся принудительно, чтобы в имени
    нельзя было протащить разделители пути / обход директорий.
    """
    import glob as _glob

    safe_name = os.path.basename((file_name or "").strip())
    if not safe_name:
        return None
    for root in ALLOWED_FILES_ROOTS:
        for cand in _glob.glob(os.path.join(root, "**", safe_name), recursive=True):
            real = os.path.realpath(cand)
            if _is_within_allowed_roots(real) and os.path.isfile(real):
                return real
    return None


@web_router.get("/web/file")
async def get_generated_file(path: str, chat_id: str | None = None):
    real_path = os.path.realpath(path)

    # Защита: файл обязан лежать внутри одного из разрешённых корней.
    if not _is_within_allowed_roots(real_path):
        raise HTTPException(status_code=403, detail="Forbidden path")

    # Файлы на хосте эфемерны (живут 30 мин, чистятся клинером).
    # Если файл уже вычищен — тянем его обратно из OBS (источник истины)
    # по детерминированному ключу catchpm/{chat_id}/{run_id}__{basename}.
    if not os.path.isfile(real_path):
        restored = False
        if chat_id is not None:
            try:
                obs_key = obs_file_storage.build_obs_key(real_path, chat_id)
                local = await obs_file_storage.download_to_local(obs_key, real_path)
                restored = bool(local) and os.path.isfile(real_path)
            except Exception as _obs_err:
                logging.error(f"OBS restore failed for {real_path}: {_obs_err}")
        if not restored:
            raise HTTPException(status_code=404, detail="File not found")

    filename = os.path.basename(real_path)

    def iterfile():
        with open(real_path, "rb") as f:
            yield from iter(lambda: f.read(1024 * 1024), b"")

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": _content_disposition(filename, "attachment")},
    )


@web_router.get("/web/db_image")
async def get_db_image(
    message_id: str | None = None,
    file_name: str | None = None,
    image_id: int | None = None,
    image_key: str | None = None,
):
    """Отдаёт бинарник картинки НАПРЯМУЮ из таблицы generated_images (BYTEA).

    Источник истины — БД, а НЕ диск. Эфемерный файл в generated_files живёт
    ~30 мин и чистится по TTL, поэтому превью в чате обязано браться отсюда,
    иначе после уборки в галерее будет пусто (404 от /web/file).

    Поиск строки (file_name в WHERE НЕ участвует — он всегда generated_0.jpg
    и для картинок бесполезен ни как якорь, ни как уникальное имя):
      - image_key (точный, приоритетный), либо
      - image_id (точный), либо
      - message_id — последняя по времени (created_at DESC), что и нужно фронту:
        одно сообщение бота = одна актуальная картинка.

    Уникальный ключ image_key возвращаем в заголовке X-Image-Key — фронт из него
    строит уникальное имя файла для скачивания (не завися от WS-пейлоада, куда
    image_key может не доехать). Разрешение фронт снимает сам (naturalWidth).
    """
    logging.info(
        f"/web/db_image: message_id={message_id}, image_id={image_id}, "
        f"image_key={image_key} (file_name={file_name} — игнорируется для картинок)"
    )
    pool = getattr(db, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="DB pool not ready")

    row = None
    try:
        async with pool.acquire() as conn:
            if image_key:
                row = await conn.fetchrow(
                    "SELECT file_name, mime_type, image_data, image_key "
                    "FROM generated_images WHERE image_key::text = $1 LIMIT 1",
                    str(image_key).strip(),
                )
            elif image_id is not None:
                row = await conn.fetchrow(
                    "SELECT file_name, mime_type, image_data, image_key "
                    "FROM generated_images WHERE id = $1",
                    image_id,
                )
            elif message_id:
                # Точное совпадение по message_id; если картинок несколько —
                # берём последнюю по времени генерации.
                row = await conn.fetchrow(
                    "SELECT file_name, mime_type, image_data, image_key "
                    "FROM generated_images "
                    "WHERE message_id = $1 "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    message_id,
                )
    except Exception as e:
        logging.error(f"/web/db_image: ошибка запроса: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="DB query failed")

    if not row or not row["image_data"]:
        # ── DISK FALLBACK ──────────────────────────────────────────────
        # Картинка НЕ найдена в generated_images. Это нормальный кейс для
        # изображений, которые агент положил только на диск (в /sandbox или
        # /app/generated_files) и вернул как обычный файл-вложение, БЕЗ записи
        # в БД. Такие файлы фронт всё равно просит через /image/... (по
        # растровому расширению), поэтому раньше они ловили 404.
        #
        # Резолвим файл по basename СТРОГО в whitelist-корнях и стримим его.
        # Никакого произвольного path здесь нет — только имя файла, поиск
        # ограничен ALLOWED_FILES_ROOTS.
        disk_path = _resolve_in_whitelist(file_name)
        if disk_path:
            logging.info(f"/web/db_image: not in DB, disk fallback -> {disk_path}")
            ext = os.path.splitext(disk_path)[1].lower().lstrip(".")
            mime_map = {
                "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif",
            }
            disk_mime = mime_map.get(ext, "application/octet-stream")
            disk_name = os.path.basename(disk_path)

            def iterfile():
                with open(disk_path, "rb") as f:
                    yield from iter(lambda: f.read(1024 * 1024), b"")

            return StreamingResponse(
                iterfile(),
                media_type=disk_mime,
                headers={
                    "Content-Disposition": _content_disposition(disk_name, "inline"),
                    "Cache-Control": "public, max-age=86400",
                    "X-Image-Source": "disk",
                    "Access-Control-Expose-Headers": "X-Image-Key, X-Image-Source",
                },
            )
        raise HTTPException(status_code=404, detail="Image not found in DB or disk whitelist")

    raw = bytes(row["image_data"])
    mime = row["mime_type"] or "image/png"
    fname = row["file_name"] or "image.png"
    img_key = str(row["image_key"]) if row["image_key"] else ""
    return Response(
        content=raw,
        media_type=mime,
        headers={
            "Content-Disposition": _content_disposition(fname, "inline"),
            "Cache-Control": "public, max-age=86400",
            "X-Image-Key": img_key,
            "Access-Control-Expose-Headers": "X-Image-Key",
        },
    )


async def handle_message(payload: dict, prod: AIOKafkaProducer):
    """Обработка одного сообщения — запускается конкурентно."""
    start = time.perf_counter()

    user_id = payload.get("user_id")
    chat_id = payload.get("chat_id")
    text = payload.get("message")
    is_from_bot = payload.get("is_from_bot", False)
    type_agent = payload.get("type_agent")
    request_id = payload.get("request_id")
    tb_name = payload.get("tb_name")
    username = payload.get("username")
    selected_tools = payload.get("selected_tools")
    context_size = payload.get("context_size")
    message_id = payload.get("message_id")
    has_files = payload.get("has_files", False)
    agent_model = payload.get("agent_model")
    gateway_target = payload.get("gateway_target", "main")

    # Проактивная инициация (будильники завтрак/обед/ужин + poke). Бот шлёт
    # спец-payload без "живого" текста от человека: initiated=True + слот/адресность.
    tg_initiated = bool(payload.get("initiated", False))
    tg_slot = payload.get("slot")                 # morning|lunch|dinner|poke
    tg_addressed = bool(payload.get("addressed", False))
    tg_target_username = payload.get("target_username")
    tg_sad = bool(payload.get("sad_followup", False))
    tg_custom_prompt = payload.get("custom_prompt")


    if is_from_bot or user_id == -1:
        return
    # Для проактивной инициации текст в payload может быть пустым — генерим сами.
    if (not text) and (not tg_initiated):
        pass
    if (not text and not tg_initiated) or chat_id is None:
        logging.warning(f"Некорректное сообщение: {payload}")
        return
    print("ВНУТРИ АГЕНТА", payload)
    # Маршрутизация: ouroboros → Ouroboros API, pm → KnowledgeAgent

    if type_agent != "pm":
        return

    # message = f"[CatchPM, ТабельНомер: 23423423, Пользователь: Иванов Иванон Иваныч, user_id=-1234]\n" + text
    is_telegram = (gateway_target == "telegram")
    if is_telegram:
        # Источник Telegram: своя шапка (без CatchPM/табельного).
        from agent_pm.telegram.tg_integration import (
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
        if not agent_model:
            agent_model = DEFAULT_TG_MODEL
    else:
        message = f"[CatchPM, ТабельНомер: {tb_name}, Пользователь: {username}, chat_id: {chat_id}, user_id: {user_id}]\n" + text

    # Скиллы юзера — только для CatchPM (в Telegram user_id это tg-id, не наш users).
    if not is_telegram:
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
        response = await agent.process_query(user_input=message, 
                                            request_id=request_id, user_id=user_id, 
                                            chat_id=chat_id, database=db, selected_tools=selected_tools,
                                            context_size=context_size,
                                            source=("telegram" if is_telegram else "CatchPM"),
                                            agent_model=agent_model,
                                            images=images,
                                            gateway_target=gateway_target)

        response_text = response["message"]
        # from datetime import datetime
        # now = datetime.now()
        # response = {'action': "test"}
        # formatted_time = now.strftime("%H:%M:%S")
        # response_text = f"Идет внедрение новой фичи! Cпасибо за понимание. Текущее время: **{formatted_time}**, {message}"
        files_to_send = response.get("files_to_send")  # [{"path": "...", ...}, ...]

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
            "action": response['action'],
            "attached_files": attached_files,
            "tool_trace": response.get("tool_trace"), # str,
            "agent_model": response.get("agent_model"),
            "tokens_usage": response.get("tokens_usage"),
            "gateway_target": gateway_target,
            "auto_title": response.get("auto_title"),
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
                "gateway_target": gateway_target
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
                "gateway_target": gateway_target
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


@web_router.post("/internal/stop_message")
async def internal_stop_message(payload: StopMessagePayload):
    task = active_tasks.get(payload.request_id)
    if not task:
        return {"ok": False, "status": "not_found"}
    task.cancel()
    return {"ok": True, "status": "cancelling", "request_id": payload.request_id}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent, digest_scheduler

    await db.init_db_pool()
    await db.init_agent_admin_pool() 
    await db.init_agent_base_pool()

    from agent_pm.consumers.task_events_consumer import start_task_events_consumer


    # result = await db.search_code_library("tabler icons", tags=["tabler"])
    # print(result)
    # from agent_pm.core.botocore_client import OBSWorker
    # obs = OBSWorker()
    # await obs.upload_all_md()

    kafka_task = asyncio.create_task(kafka_consumer())
    task_events_task = asyncio.create_task(start_task_events_consumer())
    # Клинер эфемерных файлов generated_files (TTL 30 мин):
    # чистит и свежесозданные файлы, и скачанные из OBS. Источник истины — OBS.
    obs_cleaner_task = asyncio.create_task(obs_file_storage.start_generated_files_cleaner())


    agent = KnowledgeAgent(database=db, skip_startup_autoprocess=False)

    connect_service_routers(agent, db)
    # await agent.make_reviews()
    # # Получаем сам объект scheduler, а не таск
    # digest_scheduler = await start_interval_digest_scheduler()

    yield

    # Graceful shutdown
    logging.info("Shutting down...")

    # # 1. Останавливаем планировщик правильно
    # if digest_scheduler and digest_scheduler.running:
    #     digest_scheduler.shutdown(wait=False)
    #     logging.info("Digest scheduler stopped.")

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


async def push_to_telegram(chat_id: int, text: str, edit_thinking: bool = True) -> None:
    """Fire-and-forget отправка — agent_service не ждёт полного ответа от tg_bot."""
    try:
        print(TG_BOT_PUSH_URL)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                TG_BOT_PUSH_URL,
                json={"chat_id": chat_id, "text": text, "edit_thinking": edit_thinking},
            )
            if resp.status_code != 200:
                logging.warning(f"push_to_telegram: tg_bot вернул {resp.status_code}: {resp.text[:300]}")
    except Exception as e:
        logging.error(f"push_to_telegram failed для chat_id={chat_id}: {e}")

async def send_file_to_telegram(chat_id: int, file_path: str, caption: str = "") -> None:
    file_path = Path(file_path)
    logging.info(f"ПУТЬ ВНУТРИ ФУНКЦИИ ОТПРАВКИ {file_path}, {type(file_path)}")
    with open(file_path, "rb") as f:
        content = f.read()
    file_b64 = base64.b64encode(content).decode("utf-8")
    filename = os.path.basename(file_path)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                TG_BOT_PUSH_URL.replace("/push_message", "/push_file"),
                json={"chat_id": chat_id, "file_b64": file_b64, "filename": filename, "caption": caption},
            )
    except Exception as e:
        log.error(f"send_file_to_telegram failed: {e}")


@tg_router.post("/message", response_model=TgMessageResponse)
async def handle_tg_message(req: TgMessageRequest) -> TgMessageResponse:
    """
    Принимает сообщение из Telegram-бота, прогоняет через PM Agent,
    возвращает текстовый ответ.
    """
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    
    try:
        # Сразу дергаем ID пользователя из БД:
        user_id_database = await _db.ensure_user_exists(username=req.user_tag, source="telegram")
    except Exception as e:
        logging.warning("Не удалось автоматически зарегистрировать TG юзера %s: %s", req.username, e)
        raise HTTPException(status_code=500, detail="Failed to resolve user in database")

    # Обогащаем текст мета-информацией (как делал ouroboros-мост)
    enriched = (
        f"[TG | user_id={req.user_id} | name={req.username} | chat={req.chat_type}] | chat_id={req.chat_id} | tag={req.user_tag}\n"
        f"{req.message}"
    )
    
    # Если передан файл в base64 — декодируем и парсим
    if req.file_b64 and req.file_name:
        logging.info(f"ПОЛУЧЕН ФАЙЛ ->")
        try:
            # 1. Декодируем base64 в байты
            file_bytes = base64.b64decode(req.file_b64)
            
            # 2. Выносим тяжелый синхронный парсинг в separate thread (Task/Executor)
            extracted_text = await asyncio.to_thread(parse_file, req.file_name, file_bytes)
            
            # 3. Формируем контекст для агента
            file_prompt = f"\n\n📎[FILE] [Прикрепленный файл: {req.file_name}]\nСодержимое файла:\n```\n{extracted_text}\n```"
            enriched += file_prompt
            logging.info(f"СОДЕРЖИМОЕ ФАЙЛА: {extracted_text[:300]}")
        except Exception as e:
            logging.error("Ошибка при декодировании/парсинге файла %s: %s", req.file_name, e)
            enriched += f"\n\n[Не удалось прочитать прикрепленный файл {req.file_name}: {e}]"

    logging.info(
        "TG message | user_id=%s name=%s chat_id=%s type=%s | %.60s",
        req.user_id, req.username, req.chat_id, req.chat_type, req.message,
    )
    files_to_send = None
    try:
        result = await _agent.process_query(
            user_input=enriched,
            request_id=None,       # статусы через Kafka не нужны
            user_id=user_id_database,
            chat_id=req.chat_id,          # чат в БД не создаём
            database=_db,
            source="telegram"
        )
        reply = result.get("message", "Агент не вернул ответ.")
        files_to_send = result.get("files_to_send")
        
    except Exception as e:
        logging.error("process_query failed for TG: %s", e, exc_info=True)
        reply = "Произошла техническая ошибка при обработке запроса. Уже залогировано, попробуй ещё раз чуть позже."

        # raise HTTPException(status_code=500, detail=str(e))   

    await push_to_telegram(chat_id=req.chat_id, text=reply, edit_thinking=True)

    # from pathlib import Path
    # files_to_send = [{"path": Path("/app/generated_files/a41c88a6a5184a139ec3680e0f49e4a7/pmych_quality.png")}]
    if files_to_send:
        for file in files_to_send:
            caption = os.path.basename(file["path"])
            await send_file_to_telegram(req.chat_id, file["path"], caption=caption)

    return TgMessageResponse(reply=enriched)
    # return TgMessageResponse(reply=reply)
    # return TgMessageResponse(reply="NOTHING")

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
        from agent_pm.telegram.tg_integration import should_respond, get_tg_chat_settings
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
        from agent_pm.telegram.tg_integration import analyze_reaction
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
        from agent_pm.telegram.tg_integration import build_reactions_feedback
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
        from agent_pm.telegram.tg_integration import build_reaction_proactive_prompt
        return TgReactionPromptResponse(prompt=build_reaction_proactive_prompt(req.kind, req.reactions or []))
    except Exception as e:
        logging.error(f"build_reaction_prompt failed: {e}")
        return TgReactionPromptResponse()


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


@app.post("/make_reviews")
async def make_reviews(parsing_status: ParsingMessage):
    if parsing_status.status == "success":
        logging.info("Получена команда на запуск генерации ревью. Запускаем в фоне...")

        try:
            # Создадим фоновую задачу, не дожидаемся ее выполнения, сразу передаем ответ обратно
            # в parsers_service. Типа выстрелил и забыл
            task = asyncio.create_task(agent.make_reviews())

            def handle_task_exception(t):
                if t.exception():
                    logging.error(f"Фоновая задача генерации ревью упала с ошибкой: {t.exception()}")

            task.add_done_callback(handle_task_exception)

            # Отправим запрос сразу в сервис парсинга
            return {
                "status": "accepted",
                "message": "Задача поставлена в очередь на выполнение"
            }
        except Exception as e:
            logging.error(f"Ошибка в агенте: {e}")
            raise HTTPException(status_code=500, detail=f"Agent failed: {str(e)}")

    return {"status": "ignored", "message": "Статус не success"}



SERVICES = {
    "parsers": {
         "url": f"http://parsers_service:{PORT_PARSERS_SERVICE}",
         },
    "gateway": {
        "url": f"http://api_gateway:{PORT_GATEWAY_PORT}"
    }
}

@app.post("/api/translate")
async def translate(request: Request):
    data = await request.json()
    article_id = int(data["article_id"])

    # Проверяем есть ли уже перевод в БД
    row = await db.get_article_translation(article_id)

    if row and row["translate"]:
        # Перевод уже есть — отдаём из БД
        return {"translated_abstract": row["translate"], "cached": True}

    if not row or not row["abstract"]:
        raise HTTPException(status_code=404, detail="Article not found")

    # Перевода нет — отправляем в LLM
    translated = translator(
        title=row.get("title", ""),
        abstract=row["abstract"]
    )
    logging.info(translated)
    # Сохраняем перевод в БД
    await db.save_translation(article_id, translated)

    return {"translated_abstract": translated, "cached": False}


@app.post("/api/search")
async def searching(request: Request):
    data = await request.json()
    logging.info(data)

    info = await db.insert_query(username=data["user_name"], query=data["query"])
    request_id = info["request_id"]
    count_search = info["count_search"]
    token = info["token"]
    existing_tags, existing_keywords = await asyncio.gather(
        db.get_tags(),
        db.get_keywords()
    )

    intent = parse_user_intent(data["query"], existing_tags, existing_keywords)
    logging.info(f"INTENT = {intent}")
    # Пользователь просто поздоровался
    if not intent["is_search"]:
        await db.update_status(request_id=request_id, llm_answer=intent["chat_response"])
        return {"type": "agent", "message": intent["chat_response"]}

    new_tags = list(set(intent["categories"]) - set(existing_tags))
    new_keywords = list(set(intent["keywords"]) - set(existing_keywords))
    if new_tags:
        await db.insert_tags(new_tags)
    if new_keywords:
        await db.insert_keywords(new_keywords)
    logging.info(f"Новые теги: {new_tags}, новые keywords: {new_keywords}, search_now: {intent['search_now']}")

    # Иначе — идём в парсер с готовыми keywords
    post_body = {
        "user_name": data["user_name"],
        "query": data["query"],
        "params": {"keywords": intent["keywords"], "categories": intent["categories"]},
        "request_id": request_id,
        "search_now": intent["search_now"],
        "count_search": count_search,
        "token": token,
    }
    logging.info(post_body)
    try:
        async with httpx.AsyncClient() as client:
            # 1. Парсим статьи
            response = await client.post(
                f"{SERVICES['parsers']['url']}/search/articles",
                json=post_body,
                timeout=30
            )
            if response.status_code == 200:
                parser_data = response.json()
                # 2. Считаем эмбеддинги и ищем релевантные — фоново
                asyncio.create_task(
                    run_search_and_notify(
                        user_name=data["user_name"],
                        request_id=int(request_id),
                    )
                )
                # 3. Сразу отдаём все статьи фронту
                return parser_data
            else:
                error_msg = "Не удалось выполнить поиск. Попробуйте позже."

                try:
                    error_detail = response.json().get("detail", "")
                    if response.status_code == 429:
                        error_msg = "Слишком частые запросы к arxiv. Подождите минуту и попробуйте снова.😉"
                    elif response.status_code == 502:
                        error_msg = "Сервис поиска временно недоступен.😶‍"
                    elif error_detail:
                        error_msg = f"Ошибка поиска"
                except:
                    pass

                logging.warning(f"Parser returned error {response.status_code}: {error_msg}")

                return {
                    "type": "arxiv_error",
                    "message": error_msg
                }

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Parsers service unreachable: {str(e)}")

async def run_search_and_notify(user_name: str, request_id: int):
    """Фоновая задача — эмбеддинги + поиск похожих → пуш на gateway"""
    logging.info("СОЗДАНИЕ ЭМБЕДДИНГОВ")
    try:
        searcher_body = {
            "user_name": user_name,
            "request_id": request_id,
            "match_threshold": 0.8
        }
        result = await searcher_main(searcher_body, db=db)
        relevant_ids = [i["article_id"] for i in result["articles"]]

        # Пушим на gateway чтобы он передал фронту через SSE
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SERVICES['gateway']['url']}/internal/sse-push",
                json={
                    "username": user_name,
                    "data": {
                        "type": "embeddings_ready",
                        "request_id": request_id,
                        "article_ids": relevant_ids,
                        "count": len(relevant_ids)
                    }
                }
            )
    except Exception as e:
        logging.error(f"Background search failed: {e}")

@app.post("/make_style_report")
async def make_style_report(request: Request):
    data =  await request.json()
    style = data["style"]
    text = data["text"]
    return await make_report(text=text, style=style)

from agent_pm.tools.make_graph.tool_knowledge_graph import GRAPH_SQL

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
# Модели тоггла (coder/designer/universal) физически ходят в разные
# провайдеры через один шлюз cloud.ru. Фронт (ModelPicker) показывает
# бейдж доступности, чтобы юзер понимал: если модель не отвечает — это
# провайдер лёг, а не агент завис. Пингуем каждую модель минимальным
# chat-запросом (1 токен), результат кешируем на _PROVIDERS_TTL сек,
# чтобы не долбить шлюз на каждый polling фронта.
from agent_pm.core.agent_provider import (
    get_llm_model_gpt, get_llm_model_claude, get_llm_model_gemini,
)

_PROVIDERS_TTL = 30.0  # сек — как часто реально перепроверяем провайдеров
_providers_cache: dict = {"ts": 0.0, "data": None}
_providers_lock = asyncio.Lock()

# id модели в UI -> человекочитаемое имя + фабрика llm
_PROVIDER_MODELS = {
    "coder":     ("openai/gpt-5.3-codex",           get_llm_model_gpt),
    "designer":  ("anthropic/claude-opus-4.8",      get_llm_model_claude),
    "universal": ("google/gemini-3.1-pro-preview",  get_llm_model_gemini),
}


async def _ping_provider(model_id: str, model_name: str, factory) -> dict:
    """Минимальный вызов модели. up=True если провайдер ответил без ошибки."""
    try:
        llm = factory()
        # 1 токен, короткий таймаут — нам важен только факт живости
        await asyncio.wait_for(
            llm.ainvoke(
                [HumanMessage(content="ping")],
                max_tokens=1,
            ),
            timeout=8.0,
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


class SearchIntent(BaseModel):

    is_search: bool = Field(
        description="True если пользователь хочет найти статьи, False если просто общается"
    )
    keywords: list[str] = Field(
        default=[],
        description="Ключевые слова для поиска на arXiv, на английском"
    )
    categories: list[str] = Field(
        default=[],
        description="Категории arXiv если применимо: cs.AI, cs.LG, stat.ML и т.д."
    )
    search_now: Optional[bool] = Field(
        default=True,
        description="Ответ модели, нужно ли запускать парсер или будем искать статьи в БД"
    )
    chat_response: Optional[str] = Field(
        default=None,
        description="Ответ модели если is_search=False (приветствие, вопрос не по теме и т.д.)"
    )

def translator(title: str, abstract: str) -> str:
    system_prompt = f"""
Ты — научный переводчик. Переводишь тексты академических статей с английского на русский.

Правила:
- Сохраняй научный стиль и точность формулировок
- Термины и названия методов оставляй на английском в скобках: «векторное представление (embedding)»
- Названия инструментов, библиотек, моделей не переводи: LSTM, arXiv, Python, LangChain
- Не добавляй пояснений, вступлений и комментариев — только перевод
- Не используй markdown

Название статьи: {title}
"""

    response = agent.llm_lite.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=abstract),
    ])

    result = (response.content or "").strip()

    if not result:
        logging.warning("Переводчик вернул пустой ответ")
        return abstract  # fallback — возвращаем оригинал

    return result

def parse_user_intent(user_query: str, existing_tags: list[str], keywords: list[str]) -> dict:
    tags_str = ", ".join(existing_tags) if existing_tags else "список пуст"
    keywords = ', '.join(keywords) if keywords else 'база пуста'
    logging.info(f"Теги и ключевые слова из БД {tags_str}, keywords: {keywords}")

    system_prompt = f"""
    Ты анализируешь запрос пользователя и определяешь его намерение.
    Верни только JSON без markdown.

    Существующие теги в базе:
    {tags_str}

    Существующие ключевые слова в базе:
    {keywords}

    Если пользователь хочет найти статьи:
    1. Извлеки ключевые слова из запроса (на английском)
    2. Сравни их с существующими тегами и ключевыми словами
    3. Если все ключевые слова есть в бд — search_now = false (статьи уже есть в БД)
    4. Если предложенного тобой тега или ключевого слова нет в БД — search_now = true (нужно парсить arXiv)
    5. Если пользователь пишет о свежих статьях - всегда возвращай всегда search_now = true независимо от тегов

    Верни:
    {{
      "is_search": true,
      "keywords": ["process mining", "conformance checking"],
      "categories": ["cs.AI", "cs.LG"],
      "search_now": true,
      "chat_response": null
    }}

    Если пользователь просто общается — верни:
    {{
      "is_search": false,
      "keywords": [],
      "categories": [],
      "search_now": false,
      "chat_response": "Привет! Я помогаю искать научные статьи. Спросите меня о теме."
    }}

    Ключевые слова всегда на английском.
    Категории arXiv — точные коды: cs.AI, cs.AR, cs.CE, cs.CL, cs.CR, cs.CV, cs.CY, cs.DB, cs.DC, cs.DL, cs.DS, cs.ET, cs.FL, cs.GR, cs.GT, cs.HC, cs.IR, cs.IT, cs.LG, cs.LO, cs.MA, cs.MM, cs.NE, cs.NI, cs.OS, cs.PL, cs.RO, cs.SC, cs.SE, cs.SI
    Если тема явно относится к категории — включи её в categories.
    keywords — свободные слова для поиска по заголовку и аннотации.
    """

    response = agent.llm_lite.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ])

    try:
        data = json.loads(extract_json(response.content))
    except Exception:
        logging.info("Не удалось распарсить JSON — AGENT SERVICE")
        data = {
            "is_search": False,
            "keywords": [],
            "categories": [],
            "search_now": False,
            "chat_response": "Технические шоколадки. Повторим запрос?😉",
        }

    return SearchIntent(
        is_search=data.get("is_search", False),
        keywords=data.get("keywords", []),
        categories=data.get("categories", []),
        search_now=data.get("search_now", True),
        chat_response=data.get("chat_response"),
    ).model_dump()

STYLE_PROMPTS = {
    "pushkin":    "Пиши возвышенно и лирично, в духе Пушкина. Используй поэтичные обороты, обращения «душа моя», «друг мой». Без архаизмов, но с элегантностью.",
    "esenin":     "Пиши как Есенин: тепло, немного тоскливо, с образами деревни, природы, берёз. Простой язык, душевность, лёгкая меланхолия.",
    "chekhov":    "Пиши кратко и с мягкой иронией, как Чехов. Никакого пафоса. Чуть грустно, чуть смешно, по-человечески.",
    "dostoevsky": "Пиши надрывно и философски. В стиле Достоевского",
    "tolstoy":    "Пиши обстоятельно и с моралью. В стиле Льва Толстого",
    "gogol":      "Пиши с абсурдом и народным юмором в духе Гоголя. Немного чертовщины, яркие образы, живой язык.",
    "soviet":     "Пиши в стиле советского плаката: чётко, пафосно, «товарищ», коллективизм, уверенность в светлом будущем.",
    "babushka":   "Пиши ласково и по-домашнему: «касатик», «голубчик», пословицы, забота. Как письмо от бабушки из деревни.",
    "normal":     "Пиши естественно и дружелюбно.",
    "formal":     "Пиши официально и по делу, без лишних эмоций.",
    "pirate":     "Пиши как пират: «йо-хо-хо», морские метафоры, дерзко и весело.",
    "strict":     "Пиши строго и требовательно, как недовольный клиент или проверяющий.",
    "gen_z":      "Пиши на молодёжном сленге: кринж, краш, вайб, имба. Коротко, с эмодзи.",
}

async def make_report(text: str, style: str):
    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["normal"])
    system_prompt = f"""Ты помощник, который переформулирует пользовательские отзывы.

    Стиль переформулирования: {style}:{style_instruction}

    Правила:
    - Не увеличивай объём текста, опирайся только на исходный отзыв
    - Сохраняй смысл, меняй только подачу под стиль
    - Определи категорию отзыва в одно слово, например: Идея, Баг, Проблема, Респект или другие

    Ответь СТРОГО в формате JSON, без markdown, без пояснений:
    {{
    "category": "твоя категория", 
    "text": "переформулированный отзыв"
    }}"""

    response = agent.llm_lite.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=text),
    ])
    logging.info(response.content)
    try:
        data = json.loads(response.content)
    except Exception as e:
        logging.info(f"Не удалось распарсить JSON — AGENT SERVICE, {e}")
        data = {
            "category": "",
            "text": text,
        }

    return data

if __name__ == "__main__":
    # threading.Thread(target=kafka_consumer, daemon=True).start() # Поднятие консьюмера
    uvicorn.run("app:app", host="0.0.0.0", port=int(PORT_PM_AGENT), log_level="info", reload=False)
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PM_AGENT), log_level="info")

