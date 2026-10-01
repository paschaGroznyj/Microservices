"""Agent-media dispatcher for Telegram outbound files.

Decides HOW an agent-produced file should be delivered to a Telegram chat and
encapsulates the actual send_* call. Kept as a standalone module so bot.py does
not grow a tangle of extension checks.

Media classes:
  - voice     : OGG/Opus (or .opus/.oga) -> bot.send_voice -> native voice bubble
                with a waveform. This is what generate_audio produces.
  - audio     : mp3/m4a/aac/flac/wav -> bot.send_audio -> music/track player.
  - video     : mp4/mov/webm/mkv -> bot.send_video (generate_video output).
  - photo     : png/jpg/jpeg/webp -> bot.send_photo.
  - document  : everything else -> bot.send_document.

The decision uses the explicit ``kind`` hint coming from agent_service first
(e.g. kind="voice" / "audio" / "video" / "image"), then falls back to the file
extension. ``kind`` is already carried end-to-end (agent -> Kafka -> consumer ->
push), so a tool can force a class regardless of extension.
"""
from __future__ import annotations

import logging
import pathlib
from io import BytesIO

log = logging.getLogger(__name__)

# Extension -> media class. Lowercase, with leading dot.
VOICE_EXTS = {".ogg", ".oga", ".opus"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
PHOTO_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Explicit kind hint -> media class (takes priority over extension).
_KIND_MAP = {
    "voice": "voice",
    "audio": "audio",
    "video": "video",
    "image": "photo",
    "photo": "photo",
}


def classify_media(name: str | None, kind: str | None = None) -> str:
    """Return one of: voice | audio | video | photo | document.

    kind hint wins; otherwise decide by file extension; default document.
    NB: a bare .ogg from generate_audio is treated as *voice*, not audio, so it
    renders as a native Telegram voice message (waveform) rather than a track.
    """
    k = (kind or "").strip().lower()
    if k in _KIND_MAP:
        return _KIND_MAP[k]
    ext = pathlib.Path(name or "").suffix.lower()
    if ext in VOICE_EXTS:
        return "voice"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PHOTO_EXTS:
        return "photo"
    return "document"


async def send_agent_media(bot, chat_id, raw: bytes, name: str,
                           kind: str | None = None, caption: str = "") -> str:
    """Send ``raw`` bytes to ``chat_id`` picking the right Telegram method.

    Returns the chosen media class string (for logging/caller). Raises on
    transport errors (caller already wraps send in try/except).
    """
    media = classify_media(name, kind)
    bio = BytesIO(raw)
    bio.name = name or "file"
    cap = caption or ""

    if media == "voice":
        # Caption is not shown on voice messages by all clients, but PTB accepts it.
        await bot.send_voice(chat_id=chat_id, voice=bio, caption=cap)
    elif media == "audio":
        await bot.send_audio(chat_id=chat_id, audio=bio, caption=cap)
    elif media == "video":
        await bot.send_video(chat_id=chat_id, video=bio, caption=cap)
    elif media == "photo":
        await bot.send_photo(chat_id=chat_id, photo=bio, caption=cap)
    else:
        await bot.send_document(chat_id=chat_id, document=bio,
                                filename=name or "file", caption=cap)
    log.info(f"[tg-media] chat={chat_id} name={name!r} kind={kind!r} -> {media}")
    return media
