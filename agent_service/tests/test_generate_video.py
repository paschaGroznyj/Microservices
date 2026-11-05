"""generate_video: провайдер google-lite (veo-3.1-lite) и последний кадр.

Сеть подменена через httpx.MockTransport: POST /videos -> id, поллинг ->
completed, /content -> байты mp4. Проверяется payload, ушедший в RouterAPI.
"""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
os.environ.setdefault("FERNET_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

from agent.tools.content import tool_generate_video as tgv  # noqa: E402

FIRST = "https://img.test/first.png"
LAST = "https://img.test/last.png"


class FakeAgent:
    db = None
    current_chat_id = 1

    def __init__(self):
        self._pending_generated_files = []

    def _ctx_get(self, key, default=None):
        return default


@pytest.fixture
def gen(monkeypatch, tmp_path):
    sent = []

    def handler(req: httpx.Request):
        if req.method == "POST":
            sent.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "task1"})
        if req.url.path.endswith("/content"):
            return httpx.Response(200, content=b"\x00mp4")
        return httpx.Response(200, json={"status": "completed"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def client(*a, **kw):
        kw.pop("transport", None)
        return real_client(*a, transport=transport, **kw)

    monkeypatch.setattr(tgv.httpx, "AsyncClient", client)
    monkeypatch.setattr(tgv, "get_router_api_config",
                        lambda: {"api_key": "k", "base_url": "https://router.test/api/v1"})
    monkeypatch.setattr(tgv, "GENERATED_ROOT", str(tmp_path))
    monkeypatch.setattr(tgv, "POLL_INTERVAL_SEC", 0)

    agent = FakeAgent()
    tool = tgv.make_generate_video_tool(
        agent, images=[{"b64": "QkJCQg==", "mime_type": "image/jpeg"}])

    def call(**kw):
        out = asyncio.run(tool.ainvoke({"prompt": "a cat walks", **kw}))
        return out, (sent[-1] if sent else None)

    call.sent = sent
    return call


def frame_types(payload):
    return [(f["frame_type"], f["image_url"]["url"]) for f in payload.get("frame_images", [])]


# ---------------------------------------------------------------- provider
@pytest.mark.parametrize("name", ["google-lite", "lite", "veo-lite", " LITE ",
                                  "google_lite", "google/veo-3.1-lite"])
def test_lite_aliases_hit_lite_model(gen, name):
    out, p = gen(provider=name)
    assert p["model"] == "google/veo-3.1-lite"
    assert "google-lite" in out


@pytest.mark.parametrize("name", ["google", "veo", "google/veo-3.1-fast"])
def test_google_stays_fast(gen, name):
    _, p = gen(provider=name)
    assert p["model"] == "google/veo-3.1-fast"


def test_unknown_provider_lists_lite(gen):
    out, p = gen(provider="sora")
    assert p is None
    assert "google-lite" in out


def test_lite_defaults_and_clamps(gen):
    _, p = gen(provider="google-lite")
    assert (p["aspect_ratio"], p["resolution"], p["duration"]) == ("16:9", "720p", 8)
    assert p["generate_audio"] is True

    _, p = gen(provider="google-lite", resolution="4K", duration=10,
                     aspect_ratio="1:1")
    assert (p["aspect_ratio"], p["resolution"], p["duration"]) == ("16:9", "720p", 8)

    _, p = gen(provider="google-lite", resolution="1080p", duration=5,
                     aspect_ratio="9:16", generate_audio=False)
    assert (p["aspect_ratio"], p["resolution"], p["duration"]) == ("9:16", "1080p", 4)
    assert p["generate_audio"] is False


def test_google_fast_keeps_4k(gen):
    _, p = gen(provider="google", resolution="4K")
    assert p["resolution"] == "4K"


# ---------------------------------------------------------------- frames
@pytest.mark.parametrize("prov", ["google", "google-lite"])
def test_first_and_last_frame(gen, prov):
    out, p = gen(provider=prov, first_frame_image_url=FIRST,
                       last_frame_image_url=LAST, duration=8)
    assert frame_types(p) == [("first_frame", FIRST), ("last_frame", LAST)]
    assert all(f["type"] == "image_url" for f in p["frame_images"])
    assert "Frames: first+last" in out
    assert "Notes:" not in out


def test_last_frame_only(gen):
    _, p = gen(provider="google-lite", last_frame_image_url=LAST)
    assert frame_types(p) == [("last_frame", LAST)]


def test_last_frame_from_user_image_index_zero(gen):
    _, p = gen(provider="google-lite", first_frame_image_url=FIRST,
                     last_frame_user_image_index=0)
    assert frame_types(p)[1] == ("last_frame", "data:image/jpeg;base64,QkJCQg==")


def test_last_frame_url_wins_over_other_sources(gen):
    _, p = gen(provider="google", last_frame_image_url=LAST,
                     last_frame_user_image_index=0)
    assert frame_types(p) == [("last_frame", LAST)]


def test_last_frame_unresolvable_is_noted(gen):
    out, p = gen(provider="google-lite", first_frame_image_url=FIRST,
                       last_frame_user_image_index=7)
    assert frame_types(p) == [("first_frame", FIRST)]
    assert "last frame source could not be resolved" in out


@pytest.mark.parametrize("empty", ["", "   "])
def test_blank_last_frame_is_not_given(gen, empty):
    out, p = gen(provider="heygen", last_frame_image_url=empty)
    assert "frame_images" not in p
    assert "Notes:" not in out


def test_heygen_ignores_last_frame(gen):
    out, p = gen(provider="heygen", first_frame_image_url=FIRST,
                       last_frame_image_url=LAST)
    assert frame_types(p) == [("first_frame", FIRST)]
    assert "last frame ignored" in out


def test_avatar_ignores_last_frame(gen):
    out, p = gen(provider="avatar", first_frame_image_url=FIRST,
                       last_frame_image_url=LAST)
    assert "frame_images" not in p
    assert p["input_references"][0]["image_url"]["url"] == FIRST
    assert "last frame ignored" in out


def test_no_frames_no_key(gen):
    out, p = gen(provider="google")
    assert "frame_images" not in p
    assert "Frames:" not in out


# ---------------------------------------------------------------- references
def test_lite_drops_references(gen):
    out, p = gen(provider="google-lite", first_frame_image_url=FIRST,
                       reference_image_urls=["https://r/1.png", "https://r/2.png"])
    assert "input_references" not in p
    assert frame_types(p) == [("first_frame", FIRST)]
    assert "2 reference image(s) dropped" in out


def test_google_caps_references_at_three(gen):
    urls = [f"https://r/{i}.png" for i in range(5)]
    out, p = gen(provider="google", reference_image_urls=urls)
    assert [r["image_url"]["url"] for r in p["input_references"]] == urls[:3]
    assert "references cut from 5 to 3" in out


def test_google_three_references_untouched(gen):
    urls = [f"https://r/{i}.png" for i in range(3)]
    out, p = gen(provider="google", reference_image_urls=urls)
    assert len(p["input_references"]) == 3
    assert "Notes:" not in out


def test_heygen_references_uncapped(gen):
    urls = [f"https://r/{i}.png" for i in range(5)]
    _, p = gen(provider="heygen", reference_image_urls=urls)
    assert len(p["input_references"]) == 5


# ---------------------------------------------------------------- matrix
def test_provider_matrix_shape():
    for key, prov in tgv.PROVIDERS.items():
        assert {"model", "frames", "refs", "max_refs"} <= prov.keys(), key
        assert prov["default_aspect"] in prov["aspect"], key
        assert prov["default_resolution"] in prov["resolution"], key
    for alias, target in tgv.PROVIDER_ALIASES.items():
        assert target in tgv.PROVIDERS, alias
    lite = tgv.PROVIDERS["google-lite"]
    assert lite["model"] == "google/veo-3.1-lite"
    assert "4K" not in lite["resolution"]
    assert lite["refs"] is False
