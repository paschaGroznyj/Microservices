"""Превью картинок для сетки «Медиа» панели вложений (таблица file_thumbs).

WebP, длинная сторона до THUMB_SIDE (384) px, с учётом EXIF-поворота. Делается один раз:
  - общий чат: при загрузке (group_chat.upload), байты уже в памяти;
  - чаты агента: после записи chat_files chats-consumer дёргает POST /internal/thumbs/chat_message,
    chats_service строит в фоне (schedule_message);
    оригинал из generated_images (image_key) или agent_service /web/file;
  - старые файлы без превью: при первом запросе (thumbs.py), потом из таблицы.
data IS NULL — превью сделать нельзя (не картинка, битый файл): повторно не пытаемся.
Сетевые ошибки и недоступный оригинал в таблицу не пишутся, следующий запрос попробует снова.

Уменьшение идёт в потоке под семафором (2 одновременно на процесс), JPEG декодируется
сразу в уменьшенном масштабе через Image.draft().
"""
from __future__ import annotations

import asyncio
import io
import logging
import os

import httpx

logger = logging.getLogger(__name__)

THUMB_SIDE = 384
MAX_SRC_BYTES = 25 * 1024 * 1024
IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tif", "tiff"}
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://agent_service:9001")

DDL = """
CREATE TABLE IF NOT EXISTS file_thumbs (
    id            BIGSERIAL PRIMARY KEY,
    chat_file_id  INTEGER UNIQUE REFERENCES chat_files(id) ON DELETE CASCADE,
    group_file_id BIGINT  UNIQUE REFERENCES group_message_files(id) ON DELETE CASCADE,
    mime          TEXT,
    width         INTEGER,
    height        INTEGER,
    data          BYTEA,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT file_thumbs_one_src CHECK (num_nonnulls(chat_file_id, group_file_id) = 1)
)
"""
COLS = {"chat": "chat_file_id", "group": "group_file_id"}

_sem = asyncio.Semaphore(2)
_inflight: dict[tuple[str, int], asyncio.Future] = {}
_bg: set[asyncio.Task] = set()


async def ensure_schema(pool) -> None:
    """Таблица есть в postgres/sql/02_chats/06_file_thumbs.sql; здесь — для базы без пересоздания postgres."""
    async with pool.acquire() as c:
        await c.execute(DDL)


def ext_of(name: str | None) -> str:
    n = name or ""
    return n.rsplit(".", 1)[-1].lower() if "." in n else ""


def is_image(name: str | None, mime: str | None = None, kind: str | None = None) -> bool:
    m = (mime or "").lower()
    if "svg" in m or ext_of(name) == "svg":
        return False
    return kind == "image" or m.startswith("image/") or ext_of(name) in IMAGE_EXTS


def make_thumb(raw: bytes, side: int = THUMB_SIDE) -> tuple[bytes, int, int] | None:
    """Байты картинки → (webp, w, h). Не картинка или битый файл → None."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        try:
            im.seek(0)                       # GIF/APNG: первый кадр
        except Exception:
            pass
        im.draft("RGB", (side, side))        # JPEG: декод сразу в 1/2…1/8, не меньше side
        im = ImageOps.exif_transpose(im)
        im.thumbnail((side, side), Image.LANCZOS)
        if im.mode not in ("RGB", "RGBA"):
            alpha = im.mode in ("LA", "PA") or "transparency" in im.info
            im = im.convert("RGBA" if alpha else "RGB")
        out = io.BytesIO()
        im.save(out, "WEBP", quality=78, method=4)
        return out.getvalue(), im.width, im.height
    except Exception as e:
        logger.info(f"[file_thumbs] не картинка: {e}")
        return None


async def shrink(raw: bytes) -> tuple[bytes, int, int] | None:
    if not raw or len(raw) > MAX_SRC_BYTES:
        return None
    async with _sem:
        return await asyncio.to_thread(make_thumb, raw)


async def get(pool, src: str, file_id: int):
    """Строка превью: None — превью ещё не делали; row["data"] None — сделать нельзя."""
    return await pool.fetchrow(f"SELECT data, mime FROM file_thumbs WHERE {COLS[src]} = $1", file_id)


async def put(pool, src: str, file_id: int, res: tuple[bytes, int, int] | None) -> None:
    data, w, h = res if res else (None, None, None)
    await pool.execute(
        f"INSERT INTO file_thumbs ({COLS[src]}, mime, width, height, data) VALUES ($1, $2, $3, $4, $5) "
        f"ON CONFLICT ({COLS[src]}) DO NOTHING",
        file_id, "image/webp" if data else None, w, h, data)


# ── Оригиналы ────────────────────────────────────────────────────────────────

async def _chat_original(pool, client: httpx.AsyncClient, f) -> bytes | None:
    """Оригинал файла чата агента: generated_images по image_key, иначе файл через agent_service."""
    if f["image_key"]:
        d = await pool.fetchval("SELECT image_data FROM generated_images WHERE image_key::text = $1 LIMIT 1",
                                str(f["image_key"]))
        if d:
            return bytes(d)
    path = f["file_path"]
    if not path and f["source"] == "user":
        path = (f"/app/generated_files/{os.path.basename(str(f['message_id']))}/"
                f"{os.path.basename(f['file_name'] or '') or 'file'}")
    if path:
        r = await client.get(f"{AGENT_SERVICE_URL}/web/file",
                             params={"path": path, "chat_id": f["chat_id"], "download": 0})
        if r.status_code == 200 and r.content:
            return r.content
    r = await client.get(f"{AGENT_SERVICE_URL}/web/db_image",
                         params={"message_id": f["message_id"], "file_name": f["file_name"], "chat_id": f["chat_id"]})
    return r.content if r.status_code == 200 and r.content else None


async def _build_chat(pool, file_id: int) -> bytes | None:
    f = await pool.fetchrow("""
        SELECT id, chat_id, message_id, file_name, file_ext, file_kind, file_path, source, image_key
        FROM chat_files WHERE id = $1""", file_id)
    if not f:
        return None
    if not is_image(f["file_name"], None, f["file_kind"]):
        await put(pool, "chat", file_id, None)
        return None
    async with httpx.AsyncClient(timeout=30.0) as client:
        raw = await _chat_original(pool, client, f)
    if not raw:
        return None                                   # оригинал недоступен: не помечаем, попробуем позже
    res = await shrink(raw)
    await put(pool, "chat", file_id, res)
    return res[0] if res else None


async def _build_group(pool, file_id: int) -> bytes | None:
    from app.core import obs_file_storage
    f = await pool.fetchrow("SELECT obs_key, file_name, mime_type, is_image FROM group_message_files WHERE id = $1",
                            file_id)
    if not f:
        return None
    if not (f["is_image"] or is_image(f["file_name"], f["mime_type"])) or "svg" in (f["mime_type"] or ""):
        await put(pool, "group", file_id, None)
        return None
    raw = await obs_file_storage.download_bytes(f["obs_key"])
    if not raw:
        return None
    res = await shrink(raw)
    await put(pool, "group", file_id, res)
    return res[0] if res else None


async def ensure(pool, src: str, file_id: int) -> bytes | None:
    """Готовое превью или сделать сейчас. Один и тот же файл одновременно строится один раз."""
    row = await get(pool, src, file_id)
    if row is not None:
        return bytes(row["data"]) if row["data"] is not None else None
    key = (src, file_id)
    fut = _inflight.get(key)
    if fut is None:
        build = _build_chat if src == "chat" else _build_group
        fut = asyncio.ensure_future(build(pool, file_id))
        _inflight[key] = fut
        fut.add_done_callback(lambda _f: _inflight.pop(key, None))
    try:
        return await asyncio.shield(fut)
    except Exception as e:
        logger.warning(f"[file_thumbs] {src}/{file_id}: {e}")
        return None


async def store_group_upload(pool, file_id: int, body: bytes, mime: str, name: str) -> None:
    """Загрузка в общий чат: превью из уже прочитанных байтов, без похода в OBS."""
    try:
        if not is_image(name, mime):
            await put(pool, "group", file_id, None)
            return
        await put(pool, "group", file_id, await shrink(body))
    except Exception as e:
        logger.warning(f"[file_thumbs] group upload {file_id}: {e}")


def has_images(files: list | None) -> bool:
    return any(is_image(f.get("name"), None, f.get("kind")) for f in (files or []) if isinstance(f, dict))


def schedule_message(pool, chat_id: str, message_id: str) -> None:
    """Превью картинок сообщения в фоне (event loop chats_service).

    chats-consumer сам строить не может: его цикл блокируется на синхронном KafkaConsumer,
    и фоновая задача там стояла бы до следующего сообщения. Поэтому он дёргает
    POST /internal/thumbs/chat_message (thumbs.py), а строится уже здесь."""

    async def run():
        try:
            rows = await pool.fetch("SELECT id, file_name, file_kind FROM chat_files WHERE chat_id = $1 AND message_id = $2",
                                    chat_id, message_id)
            for r in rows:
                if is_image(r["file_name"], None, r["file_kind"]):
                    await ensure(pool, "chat", r["id"])
        except Exception as e:
            logger.warning(f"[file_thumbs] {chat_id}/{message_id}: {e}")

    t = asyncio.get_running_loop().create_task(run())
    _bg.add(t)
    t.add_done_callback(_bg.discard)
