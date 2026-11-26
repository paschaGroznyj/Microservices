"""Платные медиа: fetch_from_obs, TTL 3 ч, дедуп generate_video + check_video_status,
обрезка хвоста TTS, заливка audio/music в OBS. Сеть, OBS и Redis подменены."""
import asyncio
import base64
import math
import os
import struct
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
os.environ.setdefault("FERNET_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

from agent.core import media_jobs, obs_file_storage as obs  # noqa: E402
from agent.tools.content import tool_generate_audio as tga  # noqa: E402
from agent.tools.content import tool_generate_video as tgv  # noqa: E402
from agent.tools.storage import tool_obs_fetch as tof  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def get(self, k):
        return self.d.get(k)

    async def delete(self, *ks):
        return sum(1 for k in ks if self.d.pop(k, None) is not None)


class FakeAgent:
    db = None

    def __init__(self, chat_id="c1", admin=False):
        self.current_chat_id = chat_id
        self._ctx = {"chat_id": chat_id, "media_key_owner": {"user_id": 7, "is_admin": admin}}
        self._pending_generated_files = []

    def _ctx_get(self, key, default=None):
        return self._ctx.get(key, default)


@pytest.fixture
def env(monkeypatch, tmp_path):
    fake = FakeRedis()

    async def _r():
        return fake
    monkeypatch.setattr(media_jobs, "_r", _r)
    monkeypatch.setattr(obs, "GENERATED_FILES_ROOT", str(tmp_path))
    store = {}

    async def upload(path, chat_id):
        key = obs.build_obs_key(path, chat_id)
        store[key] = open(path, "rb").read()
        return key

    async def get_bytes(key, max_bytes):
        if key not in store:
            return None, "not_found"
        if len(store[key]) > max_bytes:
            return None, f"too_large:{len(store[key])}"
        return store[key], None
    monkeypatch.setattr(obs, "upload_user_file", upload)
    monkeypatch.setattr(obs, "get_object_bytes", get_bytes)
    return {"redis": fake, "store": store, "root": tmp_path}


def run(coro):
    return asyncio.run(coro)


def test_fetch_by_key_to_generated_files(env):
    env["store"]["catchpm/c1/media_ab__video_x.mp4"] = b"MP4"
    out = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke(
        {"obs_key": "catchpm/c1/media_ab__video_x.mp4"}))
    assert "Fetched" in out, out
    p = [w for w in out.split() if w.startswith(str(env["root"]))][0].rstrip(".")
    assert open(p, "rb").read() == b"MP4"
    assert Path(p).parent.name.startswith(obs.PAID_MEDIA_DIR_PREFIX)


def test_fetch_other_chat_denied_for_user(env):
    env["store"]["catchpm/c2/r__a.txt"] = b"x"
    t = tof.make_obs_fetch_tool(FakeAgent())
    assert "error" in run(t.ainvoke({"obs_key": "catchpm/c2/r__a.txt"}))
    assert "error" in run(t.ainvoke({"obs_key": "catchpm/c2/r__a.txt", "chat_id": "c2"}))


def test_fetch_other_chat_allowed_for_admin(env):
    env["store"]["catchpm/c2/r__a.txt"] = b"x"
    out = run(tof.make_obs_fetch_tool(FakeAgent(admin=True)).ainvoke(
        {"obs_key": "catchpm/c2/r__a.txt", "chat_id": "c2"}))
    assert "Fetched" in out, out


@pytest.mark.parametrize("bad", ["catchpm/c1/../c2/r__a.txt", "other/c1/r__a.txt", "catchpm/c10/r__a"])
def test_fetch_key_traversal_and_prefix(env, bad):
    out = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke({"obs_key": bad}))
    assert "error" in out


def test_fetch_by_old_local_path(env, tmp_path):
    p = tmp_path / "run123" / "pic.png"
    p.parent.mkdir()
    p.write_bytes(b"PNG")
    key = run(obs.upload_user_file(str(p), "c1"))
    p.unlink()
    out = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke({"file_path": str(p)}))
    assert key in out and "Fetched" in out, out


def test_fetch_by_public_url(env, monkeypatch):
    monkeypatch.setenv("OBS_PUBLIC_BASE", "https://bucket.obs.test")
    env["store"]["catchpm/c1/r__v.mp4"] = b"V"
    out = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke(
        {"url": "https://bucket.obs.test/catchpm/c1/r__v.mp4"}))
    assert "Fetched catchpm/c1/r__v.mp4" in out, out


def test_fetch_missing(env):
    out = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke({"obs_key": "catchpm/c1/r__nope.mp4"}))
    assert "нет объекта" in out


def test_fetch_into_workspace(env, monkeypatch):
    env["store"]["catchpm/c1/r__v.mp4"] = b"V"
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"ok": True})
    real = httpx.AsyncClient
    monkeypatch.setattr(tof.httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    out = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke(
        {"obs_key": "catchpm/c1/r__v.mp4", "workspace_id": "ws123abc", "dest": "assets/video"}))
    assert "workspace ws123abc: assets/video/v.mp4" in out, out
    assert seen["url"].endswith("/workspace/ws123abc/upload")
    bad = run(tof.make_obs_fetch_tool(FakeAgent()).ainvoke(
        {"obs_key": "catchpm/c1/r__v.mp4", "workspace_id": "ws123abc", "dest": "../x"}))
    assert "error" in bad


def test_sweep_keeps_paid_media_3h(tmp_path):
    old = time.time() - 31 * 60
    plain = tmp_path / "run1" / "a.png"
    paid = tmp_path / "media_abc" / "video.mp4"
    very_old = tmp_path / "media_def" / "video.mp4"
    for p in (plain, paid, very_old):
        p.parent.mkdir()
        p.write_bytes(b"x")
    os.utime(plain, (old, old))
    os.utime(paid, (old, old))
    vo = time.time() - 3 * 3600 - 60
    os.utime(very_old, (vo, vo))
    removed = obs._sweep_dir(str(tmp_path), 30 * 60)
    assert removed == 2
    assert paid.exists() and not plain.exists() and not very_old.exists()
    assert not very_old.parent.exists()


@pytest.fixture
def video(env, monkeypatch):
    calls = {"post": 0, "status": "in_progress"}

    def handler(req: httpx.Request):
        if req.method == "POST":
            calls["post"] += 1
            return httpx.Response(200, json={"id": f"task{calls['post']}"})
        if req.url.path.endswith("/content"):
            return httpx.Response(200, content=b"\x00mp4")
        return httpx.Response(200, json={"status": calls["status"]})
    real = httpx.AsyncClient
    monkeypatch.setattr(tgv.httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(tgv, "get_router_api_config",
                        lambda: {"api_key": "k", "base_url": "https://router.test/api/v1"})
    monkeypatch.setattr(tgv, "resolve_router_api_key", AsyncMock(return_value=("k", None)))
    monkeypatch.setattr(tgv, "POLL_INTERVAL_SEC", 0)
    monkeypatch.setattr(tgv, "VIDEO_WAIT_SEC", 0)
    monkeypatch.setattr(tgv, "_spawn_background", lambda *a, **kw: None)
    agent = FakeAgent()
    return calls, agent, tgv.make_generate_video_tool(agent, []), tgv.make_check_video_status_tool(agent)


def test_video_timeout_returns_task_and_no_double_charge(video):
    calls, agent, gen, check = video
    out1 = run(gen.ainvoke({"prompt": "a cat"}))
    assert "check_video_status(task_id='task1')" in out1 and "generate_video again" in out1, out1
    out2 = run(gen.ainvoke({"prompt": "a cat"}))
    assert calls["post"] == 1 and "NOT charged again" in out2, out2
    calls["status"] = "completed"
    out3 = run(check.ainvoke({"task_id": "task1", "wait_sec": 0}))
    assert calls["post"] == 1
    assert "catchpm/c1/media_" in out3 and "fetch_from_obs" in out3, out3
    out4 = run(gen.ainvoke({"prompt": "a cat"}))
    assert calls["post"] == 1 and "NOT charged again" in out4, out4


def test_video_other_prompt_is_new_task(video):
    calls, _, gen, _ = video
    run(gen.ainvoke({"prompt": "a cat"}))
    run(gen.ainvoke({"prompt": "a dog"}))
    assert calls["post"] == 2


def test_check_status_unknown_task(video):
    _, _, _, check = video
    assert "not found" in run(check.ainvoke({"task_id": "nope"}))


def test_check_status_other_chat_cannot_see_task(video):
    _, _, gen, _ = video
    run(gen.ainvoke({"prompt": "a cat"}))
    other = tgv.make_check_video_status_tool(FakeAgent(chat_id="c2"))
    assert "not found" in run(other.ainvoke({"task_id": "task1"}))


def test_api_key_not_stored_in_redis(video, env):
    _, _, gen, _ = video
    run(gen.ainvoke({"prompt": "a cat"}))
    assert env["redis"].d
    assert all('"k"' not in v and "Bearer" not in v for v in env["redis"].d.values())


SR = 24000


def tone(sec, amp, f=220):
    return b"".join(struct.pack("<h", int(amp * 32767 * math.sin(2 * math.pi * f * i / SR)))
                    for i in range(int(sec * SR)))


def wav(pcm, extra=b"", sr=SR, ch=1, bits=16):
    fmt = struct.pack("<HHIIHH", 1, ch, sr, sr * ch * bits // 8, ch * bits // 8, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
    body += b"data" + struct.pack("<I", len(pcm)) + pcm + extra
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_extract_pcm_drops_header_and_c2pa():
    pcm = tone(.5, .2)
    c2pa = b"C2PA" + struct.pack("<I", 7) + b"\xff" * 7 + b"\x00"  # нечётный -> pad
    assert tga.extract_pcm(wav(pcm, c2pa)) == pcm


def test_extract_pcm_passthrough_raw():
    pcm = tone(.1, .2)
    assert tga.extract_pcm(pcm) == pcm
    assert tga.extract_pcm(b"") == b""


def test_extract_pcm_rejects_other_format():
    with pytest.raises(ValueError):
        tga.extract_pcm(wav(tone(.1, .2), sr=44100))
    with pytest.raises(ValueError):
        tga.extract_pcm(b"RIFF" + struct.pack("<I", 4) + b"WAVE")


def test_persist_paid_media_moves_and_uploads(env, tmp_path):
    src = tmp_path / "voice_x.ogg"
    src.write_bytes(b"OGG")
    agent = FakeAgent()
    agent._pending_generated_files.append(str(src))
    dst, key = run(tga.persist_paid_media(agent, str(src)))
    assert Path(dst).parent.name.startswith("media_") and not src.exists()
    assert agent._pending_generated_files == [dst]
    assert key.startswith("catchpm/c1/media_") and env["store"][key] == b"OGG"
    assert "fetch_from_obs(obs_key=" in tga._obs_note(key)


def test_new_tools_registered_and_stateful():
    from agent.core import settings
    from agent import event_loop as el
    assert "check_video_status" in settings.TOOLS_SETTINGS["media_gen"]
    assert "fetch_from_obs" in settings.UNIVERSAL_TOOLS
    for n in ("generate_video", "check_video_status", "generate_audio", "generate_music", "fetch_from_obs"):
        assert n in el.STATEFUL_TOOLS
