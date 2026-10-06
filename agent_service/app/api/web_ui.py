import os
import base64
import logging
import asyncio
from urllib.parse import quote
from agent.core.db import AgentDB
from agent.media import media_store
from agent.core import obs_file_storage
from agent.core.file_parsers import parse_file
from fastapi.responses import StreamingResponse

from schemas.common import StopMessagePayload
from fastapi import (FastAPI, HTTPException, Request, Response, 
APIRouter, Form, File, UploadFile)

logging.basicConfig(level=logging.INFO)

db = AgentDB()
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


@web_router.post("/web/upload_files")
async def upload_web_files(
    chat_id: str = Form(...),
    message_id: str = Form(...),
    user_id: str = Form(...),
    files: list[UploadFile] = File(...),
):
    parts = []
    images: list[dict] = []          # картинки JPEG/PNG для vision-тула
    image_media_ids: list[str] = []  # media_id положенных в Redis media_store (пер-чат)
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

        # Не-image файл: тоже кладём бинарник в media_store (kind=file) — чтобы
        # его можно было залить в workspace через upload_to_workspace(media_ids=[...]).
        _fmid = None
        try:
            _fmid = await media_store.put_media(
                chat_id, raw, f.filename,
                "application/octet-stream", kind="file"
            )
        except Exception as _e:
            logging.warning(f"[media_store] put_media(file) пропущен: {_e}")
        _fmid_hint = (f" [media_id={_fmid}]" if _fmid else "")
        try:
            extracted_text = await asyncio.to_thread(parse_file, f.filename, raw)
            parts.append(f"\n\n📎[FILE] [Прикрепленный файл: {f.filename}]{_fmid_hint}\nСодержимое файла:\n```\n{extracted_text}\n```")
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

@web_router.post("/internal/stop_message")
async def internal_stop_message(payload: StopMessagePayload):
    task = active_tasks.get(payload.request_id)
    if not task:
        return {"ok": False, "status": "not_found"}
    task.cancel()
    return {"ok": True, "status": "cancelling", "request_id": payload.request_id}