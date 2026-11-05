"""transcribe_media: разбор таймкодов, нарезка ffmpeg, склейка фрагментов, запросы к шлюзу."""
import asyncio
import json
import os
import shutil
import subprocess

import httpx
import pytest

import agent.core.agent_provider as ap
from agent.tools.vision import tool_transcribe_media as tm

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")


# ───────────────────────── чистые функции ─────────────────────────

@pytest.mark.parametrize("raw, sec", [
    ("00:05", 5), ("01:02.5", 62.5), ("1:00:01", 3601), ("12,5", 12.5), ("7", 7),
])
def test_parse_ts(raw, sec):
    assert tm.parse_ts(raw) == pytest.approx(sec)


def test_fmt_ts():
    assert tm.fmt_ts(62.5) == "01:02"
    assert tm.fmt_ts(3725) == "1:02:05"
    assert tm.fmt_ts(3725.25, srt=True) == "01:02:05,250"


def test_parse_transcript_formats_and_offset():
    text = (
        "Вот расшифровка:\n"
        "```\n"
        "[00:00.0 - 00:04.5] Спикер 1: Привет, коллеги.\n"
        "- **[00:04.5 - 00:09.0]** Спикер 2: Добрый день\n"
        "и продолжение без таймкода\n"
        "[00:09.0 - 00:12.0] [музыка]\n"
        "[00:12.0 - 00:15.0] [КАДР] слайд «План»\n"
        "[00:15] Спикер 1: без конца\n"
        "```\n"
    )
    segs, warn = tm.parse_transcript(text, chunk_len=20, offset=600)
    assert [s["start"] for s in segs] == [600, 604.5, 609, 612, 615]
    assert segs[0]["speaker"] == "Спикер 1" and segs[0]["text"] == "Привет, коллеги."
    assert segs[1]["text"] == "Добрый день и продолжение без таймкода"
    assert segs[2]["kind"] == "event"
    assert segs[3]["kind"] == "visual" and segs[3]["text"] == "слайд «План»"
    assert segs[4]["end"] == pytest.approx(617)      # нет конца -> +2 с
    assert warn == []


def test_parse_transcript_clamps_and_falls_back():
    segs, warn = tm.parse_transcript("[00:50 - 00:55] Спикер 1: мимо", chunk_len=10, offset=0)
    assert segs[0]["start"] == 10 and segs[0]["end"] == 10 and warn
    segs, warn = tm.parse_transcript("просто текст без таймкодов", chunk_len=30, offset=60)
    assert len(segs) == 1 and segs[0]["start"] == 60 and segs[0]["end"] == 90 and warn


@pytest.mark.parametrize("dur, chunk, plan", [
    (25, 10, [(0, 10), (10, 10), (20, 5)]),
    (630, 600, [(0, 630)]),                  # хвост 30 с < 60 приклеен
    (900, 600, [(0, 600), (600, 300)]),
    (0, 600, [(0, 0)]),
])
def test_plan_chunks(dur, chunk, plan, monkeypatch):
    monkeypatch.setattr(tm, "MIN_TAIL_SEC", 60 if chunk >= 600 else 1)
    assert tm.plan_chunks(dur, chunk) == plan


def test_render_srt_skips_visual():
    segs = [{"start": 0, "end": 1.5, "speaker": "Спикер 1", "kind": "speech", "text": "а"},
            {"start": 1.5, "end": 2, "speaker": None, "kind": "visual", "text": "кадр"}]
    srt = tm.render_srt(segs)
    assert "00:00:00,000 --> 00:00:01,500" in srt and "кадр" not in srt
    assert "[КАДР] кадр" in tm.render_txt(segs)


# ───────────────────────── тул целиком ─────────────────────────

class Agent:
    def __init__(self):
        self.tracked = []

    def track_tokens(self, source, *, tool=None, model=None, extra=None):
        self.tracked.append((tool, model, extra, source.get("usage")))
        return {}


def _gen(path, seconds, video=False):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc=size=320x240:rate=10:duration={seconds}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest"]
    subprocess.run(cmd + [str(path)], check=True)


@pytest.fixture
def env(monkeypatch, tmp_path, fast_sleep):
    root = tmp_path / "gen"
    root.mkdir()
    monkeypatch.setattr(tm, "ALLOWED_MEDIA_ROOTS", [os.path.realpath(root)])
    monkeypatch.setattr(tm, "OUTPUT_ROOT", str(root / "out"))
    monkeypatch.setattr(ap, "CLOUD_LLM_ACCESS_KEY", "k")
    requests = []
    state = {"handler": None}

    def handler(req):
        body = json.loads(req.content)
        requests.append(body)
        return state["handler"](body, len(requests))

    real = httpx.AsyncClient

    class A(real):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", A)
    return root, requests, state


def ok(text, finish="stop"):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 250, "completion_tokens": 40}})


def run(tool, **args):
    return asyncio.run(tool.ainvoke(args))


@needs_ffmpeg
def test_audio_chunks_are_stitched_with_offsets(env, monkeypatch):
    root, requests, state = env
    monkeypatch.setattr(tm, "AUDIO_CHUNK_SEC", 10)
    monkeypatch.setattr(tm, "MIN_TAIL_SEC", 1)
    _gen(root / "call.mp3", 25)

    def handler(body, n):
        prompt = body["messages"][0]["content"][0]["text"]
        idx = int(prompt.split("Это фрагмент ")[1].split(" ")[0])
        return ok(f"[00:01.0 - 00:03.0] Спикер 1: часть {idx}\n[00:03.0 - 00:04.0] Спикер 2: ответ {idx}")

    state["handler"] = handler
    agent = Agent()
    out = run(tm.make_transcribe_media_tool(agent, chat_id="c"), local_path="call.mp3")

    assert len(requests) == 3
    for b in requests:
        media = b["messages"][0]["content"][1]
        assert media["type"] == "input_audio" and media["input_audio"]["format"] == "mp3"
        assert b["model"] == "google/gemini-3.1-pro-preview"
        assert "temperature" not in b and "top_p" not in b
    assert "фрагментов 3" in out and "Спикер 1, Спикер 2" in out
    txt_path = next(l[2:] for l in out.splitlines() if l.startswith("- ") and l.endswith(".txt"))
    lines = open(txt_path, encoding="utf-8").read().splitlines()
    assert lines[0] == "[00:01 - 00:03] Спикер 1: часть 1"
    assert lines[2] == "[00:11 - 00:13] Спикер 1: часть 2"
    assert lines[4] == "[00:21 - 00:23] Спикер 1: часть 3"
    data = json.load(open(txt_path[:-4] + ".json", encoding="utf-8"))
    assert data["chunks"] == 3 and data["mode"] == "audio"
    assert "00:00:21,000 --> 00:00:23,000" in open(txt_path[:-4] + ".srt", encoding="utf-8").read()
    assert len(agent.tracked) == 3 and all(t[0] == "transcribe_media" for t in agent.tracked)
    assert sum(t[2]["media_seconds"] for t in agent.tracked) == 25


@needs_ffmpeg
@pytest.mark.parametrize("visual", [False, True])
def test_video_mode_depends_on_include_visual(env, visual):
    root, requests, state = env
    _gen(root / "demo.mp4", 3, video=True)
    state["handler"] = lambda b, n: ok("[00:00.0 - 00:02.0] [КАДР] таблица настроек\n"
                                       "[00:00.0 - 00:03.0] [гудок]")
    out = run(tm.make_transcribe_media_tool(Agent(), chat_id="c"),
              local_path=str(root / "demo.mp4"), include_visual=visual)
    content = requests[0]["messages"][0]["content"]
    if visual:
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:video/mp4;base64,")
        assert "[КАДР]" in content[0]["text"] and "звук + кадры" in out
    else:
        assert content[1]["type"] == "input_audio" and "только звук" in out


@needs_ffmpeg
def test_retry_on_503_and_no_retry_on_400(env):
    root, requests, state = env
    _gen(root / "a.wav", 2)
    state["handler"] = lambda b, n: httpx.Response(503, text="busy") if n < 3 else ok("[00:00 - 00:01] Спикер 1: да")
    out = run(tm.make_transcribe_media_tool(Agent(), chat_id="c"), local_path="a.wav")
    assert len(requests) == 3 and "Спикер 1: да" in out

    requests.clear()
    state["handler"] = lambda b, n: httpx.Response(400, text="invalid_parameter")
    out = run(tm.make_transcribe_media_tool(Agent(), chat_id="c"), local_path="a.wav")
    assert len(requests) == 1 and out.startswith("Расшифровка не удалась") and "400" in out


@needs_ffmpeg
def test_truncated_chunk_is_reported(env):
    root, _, state = env
    _gen(root / "a.mp3", 2)
    state["handler"] = lambda b, n: ok("[00:00 - 00:01] Спикер 1: обрыв", finish="length")
    out = run(tm.make_transcribe_media_tool(Agent(), chat_id="c"), local_path="a.mp3")
    assert "обрезан по max_tokens" in out


@needs_ffmpeg
def test_source_from_media_store(env, monkeypatch):
    root, requests, state = env
    _gen(root / "voice.ogg", 2)
    raw = (root / "voice.ogg").read_bytes()
    from agent.media import media_store

    async def fake_get(chat_id, mid):
        assert (chat_id, mid) == ("c", "abc")
        return raw, {"name": "voice.ogg"}

    monkeypatch.setattr(media_store, "get_media", fake_get)
    state["handler"] = lambda b, n: ok("[00:00 - 00:02] Спикер 1: голосовое")
    out = run(tm.make_transcribe_media_tool(Agent(), chat_id="c"), source_media_id="abc")
    assert "«voice.ogg»" in out and "голосовое" in out


def test_bad_sources(env, tmp_path):
    root, requests, _ = env
    tool = tm.make_transcribe_media_tool(Agent(), chat_id="c")
    assert "Не указан источник" in run(tool)
    outside = tmp_path / "secret.mp3"
    outside.write_bytes(b"x")
    assert "не найден" in run(tool, local_path=str(outside))
    assert "не найден" in run(tool, local_path="../../etc/passwd")
    (root / "notes.txt").write_text("x")
    assert "не похоже на аудио" in run(tool, local_path="notes.txt")
    (root / "broken.mp3").write_bytes(b"not audio")
    assert "не читается" in run(tool, local_path="broken.mp3")
    assert requests == []


def test_no_key(env, monkeypatch):
    monkeypatch.setattr(ap, "CLOUD_LLM_ACCESS_KEY", "")
    assert "CLOUD_LLM_ACCESS_KEY" in run(tm.make_transcribe_media_tool(Agent()), local_path="a.mp3")
