"""
tools/content/_image_sources.py
-------------------------------
Shared helpers to resolve image "sources" into base64 data-URIs, reused by
both generate_image and generate_video so the two tools accept images from the
same set of origins:

  - local disk paths / file names (whitelisted root: /app/generated_files)
  - image_key from the generated_images DB table
  - index of an image the user attached in the current request (from closure)
  - media_id from the per-chat media_store (Redis cache of recent images)

Each resolver is defensive: a bad/missing source is logged and skipped, never
raised, so one broken reference does not abort the whole generation call.
"""

import base64
import logging

from .tool_generate_image import _read_image_as_data_uri

log = logging.getLogger(__name__)


def resolve_path_to_data_uri(path_or_name: str) -> str | None:
    """Local disk path / file name -> data-URI (whitelisted roots only)."""
    return _read_image_as_data_uri(path_or_name)


async def resolve_image_key_to_data_uri(agent, image_key: str) -> str | None:
    """generated_images.image_key -> data-URI, or None."""
    db = getattr(agent, "db", None)
    pool = getattr(db, "pool", None) if db else None
    if pool is None:
        log.warning("_image_sources: no DB pool for image_key=%s", image_key)
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT image_data, mime_type FROM generated_images "
                "WHERE image_key::text = $1 LIMIT 1",
                str(image_key).strip(),
            )
        if not row or not row["image_data"]:
            log.warning("_image_sources: image_key=%s not found", image_key)
            return None
        raw = bytes(row["image_data"])
        mime = row["mime_type"] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception as e:
        log.warning("_image_sources: image_key=%s read error: %s", image_key, e)
        return None


def resolve_user_image_to_data_uri(images: list[dict] | None, idx: int) -> str | None:
    """Index into the user-attached images (closure list) -> data-URI."""
    images = images or []
    if not (0 <= idx < len(images)):
        log.warning("_image_sources: user image index %s out of range (have %d)",
                    idx, len(images))
        return None
    img = images[idx]
    b64 = (img.get("b64") or "").strip()
    if not b64:
        log.warning("_image_sources: user image #%s has empty b64", idx)
        return None
    mime = img.get("mime_type") or "image/png"
    return b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"


async def resolve_media_id_to_data_uri(agent, chat_id, media_id: str) -> str | None:
    """per-chat media_store media_id -> data-URI, or None."""
    if chat_id is None:
        log.warning("_image_sources: chat_id unknown, media_id=%s skipped", media_id)
        return None
    try:
        from ...media import media_store as _ms
    except Exception:
        log.warning("_image_sources: media_store import failed")
        return None
    try:
        raw, meta = await _ms.get_media(chat_id, media_id)
        if not raw:
            log.warning("_image_sources: media_id=%s not found", media_id)
            return None
        mime = (meta or {}).get("mime") or "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except Exception as e:
        log.warning("_image_sources: media_id=%s read error: %s", media_id, e)
        return None
