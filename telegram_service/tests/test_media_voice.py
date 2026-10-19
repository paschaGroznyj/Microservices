"""Unit tests for app.media_voice: media classification + dispatch."""
from unittest.mock import AsyncMock

import pytest

from app.media_voice import classify_media, send_agent_media


@pytest.mark.parametrize("name,expected", [
    ("note.ogg", "voice"),
    ("sound.oga", "voice"),
    ("speech.opus", "voice"),
    ("track.mp3", "audio"),
    ("song.m4a", "audio"),
    ("rec.wav", "audio"),
    ("clip.mp4", "video"),
    ("movie.mkv", "video"),
    ("pic.png", "photo"),
    ("photo.JPG", "photo"),
    ("scan.webp", "photo"),
    ("report.pdf", "document"),
    ("archive.zip", "document"),
    ("noext", "document"),
])
def test_classify_by_extension(name, expected):
    assert classify_media(name) == expected


@pytest.mark.parametrize("kind,expected", [
    ("voice", "voice"),
    ("audio", "audio"),
    ("video", "video"),
    ("image", "photo"),
    ("photo", "photo"),
    ("  Voice  ", "voice"),
])
def test_kind_hint_wins_over_extension(kind, expected):
    # extension says document, but kind hint should win
    assert classify_media("file.bin", kind=kind) == expected


def test_unknown_kind_falls_back_to_extension():
    assert classify_media("a.mp3", kind="bogus") == "audio"


def test_none_name_is_document():
    assert classify_media(None) == "document"


def _bot():
    b = AsyncMock()
    return b


def test_send_voice_routes_to_send_voice():
    import asyncio
    b = _bot()
    media = asyncio.run(send_agent_media(b, 42, b"abc", "v.ogg", caption="hi"))
    assert media == "voice"
    b.send_voice.assert_awaited_once()
    _, kwargs = b.send_voice.call_args
    assert kwargs["chat_id"] == 42
    assert kwargs["caption"] == "hi"


def test_send_photo_routes_to_send_photo():
    import asyncio
    b = _bot()
    media = asyncio.run(send_agent_media(b, 1, b"img", "p.png"))
    assert media == "photo"
    b.send_photo.assert_awaited_once()


def test_send_document_fallback_passes_filename():
    import asyncio
    b = _bot()
    media = asyncio.run(send_agent_media(b, 7, b"data", "x.bin"))
    assert media == "document"
    _, kwargs = b.send_document.call_args
    assert kwargs["filename"] == "x.bin"


def test_kind_overrides_dispatch():
    import asyncio
    b = _bot()
    media = asyncio.run(send_agent_media(b, 3, b"d", "f.bin", kind="video"))
    assert media == "video"
    b.send_video.assert_awaited_once()
