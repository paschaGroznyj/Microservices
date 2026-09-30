"""
tools/../media/media_store.py
─────────────────────────────
Пер-чат медиа-хранилище в Redis: картинки/файлы, которые пользователь
прислал в чат, переживают текущий запрос и доступны агенту в следующих
сообщениях (в т.ч. если во вложениях уже ничего нет).

ЗАЧЕМ. Картинка прилетает во вложении одного запроса и живёт только в нём.
В следующем сообщении без вложений analyze_image смотреть не на что. Этот
модуль кладёт бинарник в Redis сразу при upload (put_media), а analyze_image
дописывает к записи текстовое описание (attach_description). Реестр
подмешивается в system-промпт (list_media), и агент видит "что лежит" и
может достать нужное по media_id из любого сообщения в рамках чата.

SCOPE — chat_id (не user_id): медиа шарятся внутри одного чата.

МОДЕЛЬ ХРАНЕНИЯ (Redis, str-safe, работает с decode_responses=True):
  media:chat:{chat_id}:blob:{mid}  -> base64-строка бинарника (STRING, TTL)
  media:chat:{chat_id}:index       -> JSON-словарь реестра {mid: meta} (STRING, TTL)

meta = {
  "mid": str, "name": str, "mime": str, "size": int,
  "kind": "image"|"file", "desc": str, "ts": float,
}

ЛИМИТЫ:
  BLOB_MAX_BYTES = 5 MB — крупнее не кладём (только meta без blob смысла нет,
    поэтому просто пропускаем blob и не регистрируем).
  MAX_ITEMS = 20 на чат — LRU-вытеснение самых старых по ts.
  TTL = 24 часа (продлевается при каждой записи).

FAIL-OPEN: любые ошибки Redis глушим и возвращаем безопасный дефолт —
медиа-хранилище необязательный слой, оно не должно ронять основной поток.
"""

import os
import json
import time
import uuid
import base64
import logging

log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

BLOB_MAX_BYTES = 5 * 1024 * 1024   # 5 MB на объект
MAX_ITEMS = 20                     # объектов на чат (LRU)
TTL_SECONDS = 24 * 3600            # сутки

_redis = None
_redis_failed = False


async def _get_redis():
    """Ленивый async Redis-клиент (str, decode_responses=True). Fail-open."""
    global _redis, _redis_failed
    if _redis is not None:
        return _redis
    if _redis_failed:
        return None
    try:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        return _redis
    except Exception as e:
        _redis_failed = True
        log.warning("[media_store] Redis недоступен, медиа-хранилище отключено: %s", e)
        return None


def _k_blob(chat_id, mid: str) -> str:
    return "media:chat:" + str(chat_id) + ":blob:" + mid


def _k_index(chat_id) -> str:
    return "media:chat:" + str(chat_id) + ":index"


async def _load_index(r, chat_id) -> dict:
    try:
        raw = await r.get(_k_index(chat_id))
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("[media_store] load index failed chat=%s: %s", chat_id, e)
        return {}


async def _save_index(r, chat_id, index: dict) -> None:
    try:
        await r.set(_k_index(chat_id), json.dumps(index, ensure_ascii=False), ex=TTL_SECONDS)
    except Exception as e:
        log.warning("[media_store] save index failed chat=%s: %s", chat_id, e)


async def put_media(chat_id, raw: bytes, name: str, mime: str,
                    kind: str = "image", desc: str = "") -> str | None:
    """Положить бинарник в хранилище чата. Возвращает media_id или None.

    raw — сырые байты. Крупнее BLOB_MAX_BYTES не кладём (return None).
    При переполнении MAX_ITEMS вытесняем самые старые записи (LRU по ts).
    """
    if not raw:
        return None
    if len(raw) > BLOB_MAX_BYTES:
        log.info("[media_store] пропуск %s: %d байт > лимита %d", name, len(raw), BLOB_MAX_BYTES)
        return None
    r = await _get_redis()
    if r is None:
        return None
    mid = uuid.uuid4().hex[:12]
    try:
        b64 = base64.b64encode(raw).decode("ascii")
        await r.set(_k_blob(chat_id, mid), b64, ex=TTL_SECONDS)
        index = await _load_index(r, chat_id)
        index[mid] = {
            "mid": mid, "name": name or ("media_" + mid), "mime": mime or "application/octet-stream",
            "size": len(raw), "kind": kind, "desc": desc or "", "ts": time.time(),
        }
        # LRU-вытеснение по ts, если превысили лимит.
        if len(index) > MAX_ITEMS:
            ordered = sorted(index.values(), key=lambda m: m.get("ts", 0))
            for victim in ordered[: len(index) - MAX_ITEMS]:
                vmid = victim.get("mid")
                if vmid and vmid != mid:
                    index.pop(vmid, None)
                    try:
                        await r.delete(_k_blob(chat_id, vmid))
                    except Exception:
                        pass
        await _save_index(r, chat_id, index)
        log.info("[media_store] put chat=%s mid=%s name=%s size=%d kind=%s",
                 chat_id, mid, name, len(raw), kind)
        return mid
    except Exception as e:
        log.warning("[media_store] put failed chat=%s name=%s: %s", chat_id, name, e)
        return None


async def attach_description(chat_id, mid: str, desc: str) -> bool:
    """Дописать/обновить текстовое описание к записи (после analyze_image)."""
    if not mid or not desc:
        return False
    r = await _get_redis()
    if r is None:
        return False
    try:
        index = await _load_index(r, chat_id)
        meta = index.get(mid)
        if not meta:
            return False
        # Не раздуваем реестр: описание режем.
        meta["desc"] = desc.strip()[:600]
        index[mid] = meta
        await _save_index(r, chat_id, index)
        # Освежаем TTL blob-а — раз агент им пользуется, пусть живёт.
        try:
            await r.expire(_k_blob(chat_id, mid), TTL_SECONDS)
        except Exception:
            pass
        return True
    except Exception as e:
        log.warning("[media_store] attach_description failed chat=%s mid=%s: %s", chat_id, mid, e)
        return False


async def get_media(chat_id, mid: str) -> tuple[bytes | None, dict | None]:
    """Достать (raw_bytes, meta) по media_id. (None, None) если нет."""
    if not mid:
        return None, None
    r = await _get_redis()
    if r is None:
        return None, None
    try:
        b64 = await r.get(_k_blob(chat_id, str(mid).strip()))
        if not b64:
            return None, None
        raw = base64.b64decode(b64)
        index = await _load_index(r, chat_id)
        meta = index.get(str(mid).strip())
        return raw, meta
    except Exception as e:
        log.warning("[media_store] get failed chat=%s mid=%s: %s", chat_id, mid, e)
        return None, None


async def list_media(chat_id) -> list[dict]:
    """Список meta-записей чата, новые сверху. [] если пусто/недоступно."""
    r = await _get_redis()
    if r is None:
        return []
    index = await _load_index(r, chat_id)
    items = sorted(index.values(), key=lambda m: m.get("ts", 0), reverse=True)
    return items


async def build_media_prompt_block(chat_id) -> str:
    """Компактный реестр для подмешивания в system-промпт. "" если пусто."""
    items = await list_media(chat_id)
    if not items:
        return ""
    lines = [
        "\n\nМЕДИА-ХРАНИЛИЩE ЧАТА (файлы/картинки из недавних сообщений, живут ~24ч).",
        "Ты можешь работать с ними по media_id даже если в текущем сообщении вложений нет:",
        "- analyze_image(source_media_ids=[...]) — посмотреть картинку;",
        "- generate_image(source_media_ids=[...]) — image-to-image на её основе;",
        "- upload_to_workspace(media_ids=[...]) — залить файл/картинку на фабрику.",
    ]
    for m in items:
        size_kb = int(m.get("size", 0) / 1024)
        desc = m.get("desc") or ""
        desc_part = (" — " + desc) if desc else ""
        lines.append(
            "  [" + str(m.get("mid")) + "] " + str(m.get("kind")) + " \""
            + str(m.get("name")) + "\" (" + str(m.get("mime")) + ", "
            + str(size_kb) + "KB)" + desc_part
        )
    return "\n".join(lines)
