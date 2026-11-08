import os
import base64
import logging
import asyncio
import httpx
from urllib.parse import quote
from agent.core.db import AgentDB
from agent.media import media_store
from agent.core import obs_file_storage
from fastapi.responses import StreamingResponse

from schemas.common import StopMessagePayload
from fastapi import (FastAPI, HTTPException, Request, Response, 
APIRouter, Form, File, UploadFile)

logging.basicConfig(level=logging.INFO)

# db-инстанс проставляется из main.py (lifespan) через set_db() —
# это ТОТ ЖЕ объект, чей пул прогрет в init_db_pool(). Отдельный AgentDB()
# здесь держал бы self.pool=None -> /web/db_image отдавал 503.
db = AgentDB()

def set_db(shared_db):
    """Подменяет модульный db прогретым инстансом из main.lifespan."""
    global db
    db = shared_db

# active_tasks — ТОТ ЖЕ словарь, что в main.py (связывается через set_active_tasks()
# из lifespan). main пишет [request_id]=task / .pop, web_ui читает для /stop_message.
# Общая ссылка на один объект; main словарь целиком не переприсваивает, только мутирует.
active_tasks: dict = {}

def set_active_tasks(shared_tasks):
    """Связывает модульный active_tasks с общим словарём из main.lifespan."""
    global active_tasks
    active_tasks = shared_tasks

web_router = APIRouter()

ALLOWED_FILES_ROOTS = [
    os.path.realpath("/app/generated_files"),
    os.path.realpath("/sandbox"),
]
pending_files: dict[str, dict] = {}

def _is_within_allowed_roots(real_path: str) -> bool:
    """True, если real_path лежит внутри одного из whitelist-корней."""
    for root in ALLOWED_FILES_ROOTS:
        if real_path == root or real_path.startswith(root + os.sep):
            return True
    return False


def _content_disposition(filename: str, disposition: str = "attachment") -> str:
    """
    Собрать корректный заголовок Content-Disposition для имён с не-ASCII
    символами (кириллица и т.п.) по RFC 6266.
    """
    fallback = filename.encode("ascii", "replace").decode("ascii")
    quoted = quote(filename, safe="")
    return (
        f"{disposition}; "
        f'filename="{fallback}"; '
        f"filename*=UTF-8''{quoted}"
    )


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} Б"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n / (1024 * 1024):.1f} МБ"


@web_router.post("/web/upload_files")
async def upload_web_files(
    chat_id: str = Form(...),
    message_id: str = Form(...),
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
    file_modes: list[str] = Form(default=[]),
):
    """Раскладывает вложения сообщения. Ничего не парсит.

    file_modes[i] выставляет gateway (api_gateway/file_parsers.classify):
      "inline" — короткий текстовый файл, кладём его текст в промпт;
      "file"   — всё остальное: бинарник в media_store (Redis) + диск/OBS,
                 в промпт идёт только маркер с media_id.
    Если режима нет (TG/MAX-боты шлют файлы напрямую), файл считается "file".
    """
    parts = []
    images: list[dict] = []          # картинки JPEG/PNG для vision-тула
    image_media_ids: list[str] = []  # media_id положенных в Redis media_store (пер-чат)
    IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    AUDIO_EXT = {".mp3", ".mpga", ".wav", ".wave", ".ogg", ".oga", ".opus", ".m4a",
                 ".aac", ".flac", ".weba", ".caf", ".aif", ".aiff", ".amr", ".wma"}

    # Серверный предохранитель: не более 10 файлов на сообщение (дубль
    # фронтового лимита MAX_FILES — чтобы нельзя было обойти мимо UI).
    MAX_FILES = 10
    if len(files) > MAX_FILES:
        logging.warning(
            "upload_files: получено %d файлов, обрезаю до %d (message_id=%s)",
            len(files), MAX_FILES, message_id,
        )
        files = files[:MAX_FILES]

    for f_idx, f in enumerate(files):
        raw = await f.read()
        ext = os.path.splitext(f.filename or "")[1].lower()
        mode = file_modes[f_idx] if f_idx < len(file_modes) else "file"
        user_fpath = None

        # Сохраняем на диск/OBS все файлы, включая картинки: по этому пути
        # /api/chats/{chat}/files/{msg}/{name} отдаёт превью в истории.
        safe_fname = os.path.basename(f.filename or "file") or "file"
        safe_mid = os.path.basename(str(message_id or "msg")) or "msg"
        try:
            user_dir = os.path.join("/app/generated_files", safe_mid)
            os.makedirs(user_dir, exist_ok=True)
            user_fpath = os.path.join(user_dir, safe_fname)
            with open(user_fpath, "wb") as _uf:
                _uf.write(raw)
            await obs_file_storage.upload_user_file(user_fpath, chat_id)
        except Exception as _uerr:
            logging.warning(f"[upload_files] не удалось сохранить файл на диск/OBS: {_uerr}")

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
            # Кладём входящую картинку в пер-чат media-хранилище СРАЗУ (до анализа),
            # чтобы картинка пережила текущий запрос и была доступна в следующих.
            media_id = None
            try:
                media_id = await media_store.put_media(
                    chat_id, raw, f.filename, IMAGE_MIME[ext], kind="image"
                )
            except Exception as _e:
                logging.warning(f"[media_store] put_media пропущен: {_e}")
            if media_id:
                image_media_ids.append(media_id)
                images[idx]["media_id"] = media_id  # чтобы analyze_image мог дописать desc
            mid_hint = (f", media_id={media_id}" if media_id else "")
            # В текст сообщения — только лёгкий маркер с индексом.
            parts.append(
                f"\n\n🖼️[IMAGE #{idx}: {f.filename}] "
                f"(изображение загружено, для анализа вызови тул analyze_image "
                f"с image_index={idx}{mid_hint})"
            )
            continue

        VIDEO_MIME = {
            ".mp4": "video/mp4",
            ".m4v": "video/mp4",
            ".webm": "video/webm",
            ".mov": "video/quicktime",
            ".ogv": "video/ogg",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
        }

        # Не-image файл: тоже кладём бинарник в media_store (kind=file) — чтобы
        # его можно было залить в workspace через upload_to_workspace(media_ids=[...]).
        _fmid = None
        try:
            _fmid = await media_store.put_media(
                chat_id, raw, f.filename,
                VIDEO_MIME.get(ext) or _WEB_FILE_MIME_MAP.get(ext.lstrip("."), "application/octet-stream"),
                kind="file"
            )
        except Exception as _e:
            logging.warning(f"[media_store] put_media(file) пропущен: {_e}")
        _fmid_hint = (f" [media_id={_fmid}]" if _fmid else "")
        if ext in VIDEO_MIME or ext in AUDIO_EXT:
            # Видео и аудио не парсим: расшифровка — тулом transcribe_media.
            # Файлы > 5 МБ в media_store не попадают, тогда даём путь на диске.
            _src = (f"source_media_id={_fmid}" if _fmid else
                    (f"local_path={user_fpath}" if user_fpath else None))
            _tag, _what = (("🎬[VIDEO]", "видео") if ext in VIDEO_MIME else ("🎧[AUDIO]", "аудио"))
            _hint = (f" (для расшифровки с таймкодами вызови transcribe_media с {_src}; "
                     "тул в категории «Контент и файлы», если его нет — попроси включить)"
                     if _src else "")
            parts.append(
                f"\n\n{_tag} [Прикрепленное {_what}: {f.filename}, {_human_size(len(raw))}]"
                f"{_fmid_hint}{_hint}"
            )
            continue
        if mode == "inline":
            # gateway уже проверил: это UTF-8 текст не длиннее лимита.
            inline_text = raw.decode("utf-8-sig", errors="replace")
            parts.append(
                f"\n\n📎[FILE] [Прикрепленный файл: {f.filename}]{_fmid_hint}"
                f"\nСодержимое файла:\n```\n{inline_text}\n```"
            )
            continue
        # Документ не кладём в контекст: только маркер, анализ — внутри
        # workspace (upload_to_workspace по media_id или путь на диске).
        where = (f"media_id={_fmid}" if _fmid else
                 (f"path={user_fpath}" if user_fpath else "не сохранён"))
        parts.append(
            f"\n\n📄[DOCUMENT] [Прикрепленный файл: {f.filename}, {_human_size(len(raw))}] "
            f"({where}). Содержимое НЕ включено в контекст. Чтобы прочитать или "
            f"проанализировать файл, залей его в workspace через "
            f"upload_to_workspace(media_ids=[...]) или local_path и разбери кодом."
        )

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



_WEB_FILE_MIME_MAP = {
    "mp4": "video/mp4",
    "m4v": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "ogv": "video/ogg",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "mp3": "audio/mpeg",
    "mpga": "audio/mpeg",
    "wav": "audio/wav",
    "wave": "audio/wav",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "weba": "audio/webm",
    "caf": "audio/x-caf",
    "aif": "audio/aiff",
    "aiff": "audio/aiff",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
}
_WEB_STREAM_MAX_CHUNK = 2 * 1024 * 1024  # 2 МБ (sendfile_max_chunk 2m)


def _parse_web_byte_range(range_header: str, total_size: int, max_chunk: int = _WEB_STREAM_MAX_CHUNK) -> tuple[int, int] | None:
    if not range_header or not range_header.strip().lower().startswith("bytes=") or total_size <= 0:
        return None
    spec = range_header.strip()[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    start_s, end_s = start_s.strip(), end_s.strip()
    try:
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                return None
            start = max(0, total_size - suffix)
            end = total_size - 1
        else:
            start = int(start_s)
            if start < 0 or start >= total_size:
                return None
            if end_s == "":
                end = min(start + max_chunk - 1, total_size - 1)
            else:
                end = min(int(end_s), total_size - 1)
        if start > end:
            return None
        return start, end
    except ValueError:
        return None


def _chats_service_url() -> str:
    return os.getenv("CHATS_SERVICE_URL") or f"http://chats_service:{os.getenv('PORT_CHATS_SERVICE', '8001')}"


async def _resolve_chat_file_path(chat_id, message_id, file_name) -> str | None:
    """Точный file_path вложения из chat_files (chats_service /internal/resolve_file)."""
    if not (chat_id and message_id and file_name):
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            rf = await client.get(
                f"{_chats_service_url()}/internal/resolve_file",
                params={"chat_id": chat_id, "message_id": message_id, "file_name": file_name},
            )
        if rf.status_code == 200:
            return (rf.json() or {}).get("path") or None
    except Exception as e:
        logging.warning(f"resolve_file chat={chat_id} msg={message_id} {file_name}: {e}")
    return None


async def _serve_file(
    real_path: str,
    request: Request,
    chat_id: str | None,
    download: int = 0,
    cache_control: str = "public, max-age=3600",
    source_header: str = "X-File-Source",
):
    """Отдаёт файл чата: сначала OBS (источник истины), потом локальный диск.

    OBS первым, только если ключ уникален (generated_files/{run_id}/...): локальная
    копия по тому же пути может оказаться другим файлом. Для /sandbox и корня
    generated_files ключ общий на все запросы чата, там первым идёт диск.
    Поддерживает HTTP Range (206) для обоих источников.
    """
    filename = os.path.basename(real_path)
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    media_type = _WEB_FILE_MIME_MAP.get(ext, "application/octet-stream")
    is_inline_media = media_type.startswith(("video/", "audio/", "image/"))
    disposition = "attachment" if (download or not is_inline_media) else "inline"

    source, obs_key, file_size = None, None, None
    if chat_id is not None and obs_file_storage.has_unique_obs_key(real_path):
        obs_key = obs_file_storage.build_obs_key(real_path, chat_id)
        file_size = await obs_file_storage.stat_object(obs_key)
        if file_size is not None:
            source = "obs"
    if source is None and os.path.isfile(real_path):
        source, file_size = "disk", os.path.getsize(real_path)
    if source is None and obs_key is None and chat_id is not None:
        # Неуникальный ключ и локальной копии нет: последний шанс — OBS.
        obs_key = obs_file_storage.build_obs_key(real_path, chat_id)
        file_size = await obs_file_storage.stat_object(obs_key)
        if file_size is not None:
            source = "obs"
    if source is None:
        raise HTTPException(status_code=404, detail="File not found")

    def _disk_iter(start: int, length: int):
        with open(real_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    def _body(start: int = 0, end: int | None = None):
        if source == "obs":
            return obs_file_storage.iter_object(obs_key, start, end)
        last = file_size - 1 if end is None else end
        return _disk_iter(start, last - start + 1)

    base_headers = {
        "Content-Disposition": _content_disposition(filename, disposition),
        "Accept-Ranges": "bytes",
        "Cache-Control": cache_control,
        source_header: source,
        "Access-Control-Expose-Headers": f"{source_header}, X-Image-Key",
    }

    range_hdr = request.headers.get("range")
    if range_hdr and not download and file_size > 0:
        parsed = _parse_web_byte_range(range_hdr, file_size)
        if parsed is None:
            return Response(
                status_code=416,
                headers={**base_headers, "Content-Range": f"bytes */{file_size}"},
            )
        start, end = parsed
        return StreamingResponse(
            _body(start, end),
            status_code=206,
            media_type=media_type,
            headers={
                **base_headers,
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(end - start + 1),
            },
        )

    return StreamingResponse(
        _body(),
        media_type=media_type,
        headers={**base_headers, "Content-Length": str(file_size)},
    )


@web_router.get("/web/file")
async def get_generated_file(
    path: str,
    request: Request,
    chat_id: str | None = None,
    download: int = 0,
):
    real_path = os.path.realpath(path)

    # Защита: файл обязан лежать внутри одного из разрешённых корней.
    if not _is_within_allowed_roots(real_path):
        raise HTTPException(status_code=403, detail="Forbidden path")

    # Файлы на хосте эфемерны (30 мин) и при повторе имени в другом запросе
    # могут оказаться чужими, поэтому источник истины — OBS по ключу
    # catchpm/{chat_id}/{run_id}__{basename}, диск — запасной вариант.
    return await _serve_file(real_path, request, chat_id, download)


@web_router.get("/web/db_image")
async def get_db_image(
    request: Request,
    message_id: str | None = None,
    file_name: str | None = None,
    image_id: int | None = None,
    image_key: str | None = None,
    chat_id: str | None = None,
):
    """Картинка для превью в чате.

    Порядок поиска:
      1. generated_images по image_key или image_id (точные ключи);
      2. точный file_path вложения из chat_files (chat_id + message_id + file_name)
         → OBS по уникальному ключу, потом диск (_serve_file);
      3. generated_images по message_id — последняя картинка сообщения
         (старые записи без image_key).
    Поиска по одному имени файла по всему диску нет: при повторе имени
    (slide-1.png) в другом запросе он отдавал чужую картинку.

    image_key возвращается в X-Image-Key — фронт строит из него имя файла.
    """
    logging.info(
        f"/web/db_image: message_id={message_id}, image_id={image_id}, "
        f"image_key={image_key}, file_name={file_name}"
    )
    pool = getattr(db, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="DB pool not ready")

    async def _fetch(sql: str, *args):
        try:
            async with pool.acquire() as conn:
                return await conn.fetchrow(sql, *args)
        except Exception as e:
            logging.error(f"/web/db_image: ошибка запроса: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="DB query failed")

    def _db_response(row):
        raw = bytes(row["image_data"])
        img_key = str(row["image_key"]) if row["image_key"] else ""
        return Response(
            content=raw,
            media_type=row["mime_type"] or "image/png",
            headers={
                "Content-Disposition": _content_disposition(row["file_name"] or "image.png", "inline"),
                "Cache-Control": "public, max-age=86400",
                "X-Image-Key": img_key,
                "X-Image-Source": "db",
                "Access-Control-Expose-Headers": "X-Image-Key, X-Image-Source",
            },
        )

    _cols = "SELECT file_name, mime_type, image_data, image_key FROM generated_images "
    row = None
    if image_key:
        row = await _fetch(_cols + "WHERE image_key::text = $1 LIMIT 1", str(image_key).strip())
    elif image_id is not None:
        row = await _fetch(_cols + "WHERE id = $1", image_id)
    if row and row["image_data"]:
        return _db_response(row)

    real_fp = await _resolve_chat_file_path(chat_id, message_id, file_name)
    if real_fp:
        real_fp = os.path.realpath(real_fp)
        if _is_within_allowed_roots(real_fp):
            try:
                return await _serve_file(
                    real_fp, request, chat_id,
                    cache_control="public, max-age=86400",
                    source_header="X-Image-Source",
                )
            except HTTPException as e:
                if e.status_code != 404:
                    raise

    if not image_key and image_id is None and message_id:
        row = await _fetch(
            _cols + "WHERE message_id = $1 ORDER BY created_at DESC, id DESC LIMIT 1",
            message_id,
        )
        if row and row["image_data"]:
            return _db_response(row)

    raise HTTPException(status_code=404, detail="Image not found in DB, disk or OBS")

@web_router.post("/internal/stop_message")
async def internal_stop_message(payload: StopMessagePayload):
    # САМОЦИКЛ: стоп-кнопка гасит не только текущий тик (task.cancel), но и саму
    # петлю (is_active=false) — иначе consumer тут же запустит следующий тик.
    if payload.chat_id:
        try:
            await db.deactivate_loop_task(payload.chat_id)
        except Exception as e:
            logging.error(f"[self-loop] не удалось погасить петлю chat={payload.chat_id}: {e}")
    task = active_tasks.get(payload.request_id)
    if not task:
        return {"ok": False, "status": "not_found", "loop_stopped": bool(payload.chat_id)}
    task.cancel()
    return {"ok": True, "status": "cancelling", "request_id": payload.request_id, "loop_stopped": bool(payload.chat_id)}