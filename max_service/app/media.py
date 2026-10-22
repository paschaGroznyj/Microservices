"""
max_service/app/media.py — приём и доставка ВХОДЯЩИХ вложений пользователя из MAX.

Отличие от telegram_service: у MAX вложения приходят уже готовой CDN-ссылкой
(attachment.payload.url), 2-шаговый download по file_id не нужен — обычный GET.

Что делает:
  • extract_attachments(update) — вытаскивает список входящих вложений из
    message_created (body.attachments[]), классифицирует тип;
  • download_attachment(url) — тянет байты файла по CDN-URL (ретраи);
  • collect_message_items(update) — собирает готовый список items
    [{file_b64, file_name, kind}] для одного апдейта (несколько картинок/файлов);
  • AlbumBuffer — склейка «альбома»: MAX может прислать один пост несколькими
    message_created-апдейтами (с общим link/похожим timestamp). Дебаунс по тишине;
  • upload_items_to_agent(...) — multipart-POST в agent_service /web/upload_files
    (тот же контракт, что в telegram_service).

Подключается в bot.py: в ветке message_created, до dispatch_to_agent.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import pathlib
import time

import httpx

log = logging.getLogger("max_bot.media")

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 МБ — лимит на входящий файл
MAX_ITEMS = 10                     # жёсткий лимит вложений на сообщение (как в агенте)
ALBUM_DEBOUNCE_SEC = 1.2           # окно тишины для склейки альбома
DOWNLOAD_RETRIES = 3
DOWNLOAD_TIMEOUT = 60

# MAX attachment.type -> наш "kind" для маркеров/классификации в агенте.
_KIND_BY_MAX_TYPE = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "file": "file",
}

# Расширение по MAX-типу, если имя файла не пришло.
_DEFAULT_EXT = {
    "image": ".jpg",
    "video": ".mp4",
    "audio": ".ogg",
    "file": ".bin",
}


def _att_url(att: dict) -> str | None:
    """Прямая ссылка на скачивание из payload (MAX отдаёт CDN-url)."""
    payload = att.get("payload") or {}
    # Разные типы: image/video/audio -> url; file -> url; иногда token без url.
    url = payload.get("url")
    if url:
        return url
    # image может прийти как {photos:{<key>:{url,...}}}
    photos = payload.get("photos")
    if isinstance(photos, dict):
        for ph in photos.values():
            if isinstance(ph, dict) and ph.get("url"):
                return ph["url"]
    return None


def _att_filename(att: dict, idx: int) -> str:
    payload = att.get("payload") or {}
    fname = payload.get("filename") or payload.get("name")
    if fname:
        return str(fname)
    atype = att.get("type") or "file"
    ext = _DEFAULT_EXT.get(atype, ".bin")
    return f"max_{atype}_{idx}{ext}"


def extract_attachments(update: dict) -> list[dict]:
    """Список attachment-объектов из message_created (body.attachments[])."""
    msg = update.get("message") or {}
    body = msg.get("body") or {}
    atts = body.get("attachments")
    return atts if isinstance(atts, list) else []


async def download_attachment(url: str) -> bytes | None:
    """GET файла по CDN-ссылке MAX с ретраями на сетевые сбои."""
    for attempt in range(DOWNLOAD_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT,
                                         follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.content
        except Exception as e:
            wait = 1.5 * (attempt + 1)
            log.warning(f"[download] attempt {attempt+1}/{DOWNLOAD_RETRIES} "
                        f"failed ({type(e).__name__}): {e}; retry in {wait}s")
            await asyncio.sleep(wait)
    return None


async def collect_message_items(update: dict) -> list[dict]:
    """Скачивает все вложения ОДНОГО апдейта → items [{file_b64,file_name,kind}].

    Пропускает вложения без url и слишком большие. Режет до MAX_ITEMS.
    """
    atts = extract_attachments(update)
    if not atts:
        return []
    items: list[dict] = []
    for idx, att in enumerate(atts):
        if len(items) >= MAX_ITEMS:
            break
        atype = att.get("type") or "file"
        kind = _KIND_BY_MAX_TYPE.get(atype, "file")
        url = _att_url(att)
        if not url:
            log.info(f"[media] attachment без url пропущен (type={atype})")
            continue
        raw = await download_attachment(url)
        if raw is None:
            log.warning(f"[media] не скачал вложение type={atype} url={url[:80]}")
            continue
        if len(raw) > MAX_FILE_SIZE:
            log.warning(f"[media] вложение >{MAX_FILE_SIZE}B пропущено "
                        f"(type={atype}, {len(raw)}B)")
            continue
        items.append({
            "file_b64": base64.b64encode(raw).decode("ascii"),
            "file_name": _att_filename(att, idx),
            "kind": kind,
        })
    return items


# ── Склейка альбома ────────────────────────────────────────────────────────────
# MAX может прислать несколько файлов одним message_created (тогда они уже в
# body.attachments — альбом внутри одного апдейта, collect_message_items их
# соберёт сам). Но на случай, когда клиент шлёт N отдельных апдейтов по одному
# файлу подряд — буферизуем по chat_id с дебаунсом тишины.
class AlbumBuffer:
    def __init__(self, debounce: float = ALBUM_DEBOUNCE_SEC):
        self._buf: dict[int, dict] = {}   # chat_id -> {items, text, user_id, username, message_id, last_ts, task}
        self._debounce = debounce
        self._lock = asyncio.Lock()

    async def add(self, chat_id: int, info: dict, items: list[dict],
                  flush_cb) -> None:
        """Добавляет порцию в буфер чата; (пере)взводит таймер дебаунса.

        flush_cb(chat_id, text, user_id, username, message_id, items) — корутина,
        вызывается после тишины debounce.
        """
        async with self._lock:
            slot = self._buf.get(chat_id)
            if slot is None:
                slot = {
                    "items": [], "text": "", "user_id": info.get("user_id"),
                    "username": info.get("username"),
                    "message_id": info.get("message_id"),
                    "task": None,
                }
                self._buf[chat_id] = slot
            slot["items"].extend(items)
            # Текст берём первый непустой (caption поста).
            if info.get("text") and not slot["text"]:
                slot["text"] = info["text"]
            slot["last_ts"] = time.monotonic()
            if slot["task"] and not slot["task"].done():
                slot["task"].cancel()
            slot["task"] = asyncio.create_task(
                self._wait_and_flush(chat_id, flush_cb))

    async def _wait_and_flush(self, chat_id: int, flush_cb) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return
        async with self._lock:
            slot = self._buf.pop(chat_id, None)
        if not slot:
            return
        try:
            await flush_cb(chat_id, slot["text"], slot["user_id"],
                           slot["username"], slot["message_id"],
                           slot["items"][:MAX_ITEMS])
        except Exception as e:
            log.error(f"[media] album flush fail chat={chat_id}: {e}")


# ── Доставка вложений агенту ─────────────────────────────────────────────────────
_CTYPE_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".pdf": "application/pdf",
}


async def upload_items_to_agent(agent_url: str, message_id, chat_id, user_id,
                                items: list[dict]) -> None:
    """Multipart-POST вложений в agent_service /web/upload_files (как в TG)."""
    if not items:
        return
    try:
        files = []
        for it in items:
            raw = base64.b64decode(it["file_b64"])
            fname = it.get("file_name") or "file"
            ext = pathlib.Path(fname).suffix.lower()
            ctype = _CTYPE_BY_EXT.get(ext, "application/octet-stream")
            files.append(("files", (fname, raw, ctype)))
        data = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "user_id": str(user_id),
        }
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as c:
            resp = await c.post(f"{agent_url}/web/upload_files",
                                data=data, files=files)
            resp.raise_for_status()
        log.info(f"[media] uploaded to agent chat={chat_id} msg={message_id} "
                 f"count={len(files)}")
    except Exception as e:
        log.error(f"[media] upload to agent fail chat={chat_id} "
                  f"msg={message_id}: {e}")
