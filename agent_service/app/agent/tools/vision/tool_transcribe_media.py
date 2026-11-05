"""
tools/vision/tool_transcribe_media.py
─────────────────────────────────────
Точная расшифровка аудио и видео с таймкодами (google/gemini-3.1-pro-preview).

Что проверено на живом шлюзе cloud.ru (чат 6e2e3692, 05.11.2026):
  - аудио принимается ТОЛЬКО как {"type": "input_audio", "input_audio": {data, format}};
  - видео со звуком — как image_url c data:video/mp4;base64 (video_url -> 400,
    type=file молча отбрасывается);
  - flash-модели звук внутри видео не слышат и выдумывают речь, поэтому модель — pro;
  - тело запроса ~21 МБ проходит, ~30 МБ -> 413; битрейт на токены не влияет;
  - temperature/top_p не передаём; reasoning_tokens входят в max_tokens.

Конвейер: ffprobe -> ffmpeg режет запись на фрагменты (аудио mono 16 кГц mp3 по
10 мин, видео 480p/5fps по 5 мин) -> до 3 параллельных запросов с ретраями ->
таймкоды фрагментов сдвигаются на offset и склеиваются в .txt/.srt/.json.
Полный текст лежит в файлах, в контекст агента идёт сводка и превью.
"""

import asyncio
import base64
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid

import httpx
from langchain.tools import tool

log = logging.getLogger(__name__)

ALLOWED_MEDIA_ROOTS = [
    os.path.realpath("/app/generated_files"),
    os.path.realpath("/sandbox"),
]
OUTPUT_ROOT = "/app/generated_files"

AUDIO_EXT = {".mp3", ".mpga", ".wav", ".wave", ".ogg", ".oga", ".opus", ".m4a",
             ".aac", ".flac", ".weba", ".caf", ".aif", ".aiff", ".amr", ".wma"}
VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov", ".ogv", ".avi", ".mkv", ".3gp",
             ".flv", ".wmv", ".mpeg", ".mpg", ".ts"}
MEDIA_EXT = AUDIO_EXT | VIDEO_EXT

def _env_num(name: str, default: float) -> float:
    try:
        v = float(os.getenv(name, "") or default)
        return v if v > 0 else default
    except ValueError:
        return default


MAX_INPUT_BYTES = int(_env_num("TRANSCRIBE_MAX_INPUT_MB", 500) * 1024 * 1024)   # исходник
MAX_DURATION_SEC = int(_env_num("TRANSCRIBE_MAX_DURATION_MIN", 240) * 60)       # 4 часа
# Слишком большой исходник удаляем только из загрузок агента. /sandbox — черновики
# Павла, их не трогаем.
DELETABLE_ROOTS = [os.path.realpath("/app/generated_files")]
AUDIO_CHUNK_SEC = 600                    # ~25 ток/с -> ~15k токенов на вход
VIDEO_CHUNK_SEC = 300                    # ~66 ток/с с кадрами
MIN_TAIL_SEC = 60                        # хвост короче — приклеиваем к прошлому
MAX_B64_BYTES = 18 * 1024 * 1024         # запас до 413 (~21 МБ тела прошло)
MAX_PARALLEL = 3
RETRIES = 3
REQUEST_TIMEOUT = 600.0
MAX_TOKENS = 32000
PREVIEW_CHARS = 6000

_TS = r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
_RANGE_RE = re.compile(r"^\[?\s*" + _TS + r"\s*(?:-->|–|—|-)\s*" + _TS + r"\s*\]?\s*[:\-–—]?\s*(.*)$")
_START_RE = re.compile(r"^\[\s*" + _TS + r"\s*\]\s*[:\-–—]?\s*(.*)$")
_SPEAKER_RE = re.compile(r"^((?:Спикер|Speaker|Диктор|Голос)\s*\w*|[A-ZА-ЯЁ][\w .\-]{0,30}?)\s*:\s+(.+)$")
_VISUAL_RE = re.compile(r"^\[(?:КАДР|ЭКРАН|FRAME|VISUAL)\]\s*(.*)$", re.I)


# ───────────────────────── разбор ответа модели ─────────────────────────

def parse_ts(value: str) -> float:
    s = str(value).strip().replace(",", ".")
    parts = s.split(":")
    sec = 0.0
    for p in parts:
        sec = sec * 60 + float(p)
    return sec


def fmt_ts(sec: float, srt: bool = False) -> str:
    sec = max(0.0, float(sec))
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", line)
    return line.replace("**", "").strip()


def parse_transcript(text: str, chunk_len: float, offset: float = 0.0) -> tuple[list[dict], list[str]]:
    """Разбирает строки "[MM:SS.s - MM:SS.s] Спикер 1: текст" в сегменты.

    Таймкоды ответа — от начала фрагмента; к ним прибавляется offset.
    Строки без таймкода приклеиваются к предыдущему сегменту.
    """
    warnings: list[str] = []
    segs: list[dict] = []
    orphan: list[str] = []
    for raw in (text or "").splitlines():
        line = _clean_line(raw)
        if not line or line.startswith("```"):
            continue
        m = _RANGE_RE.match(line)
        start = end = None
        body = None
        if m:
            try:
                start, end, body = parse_ts(m.group(1)), parse_ts(m.group(2)), m.group(3)
            except ValueError:
                m = None
        if not m:
            m2 = _START_RE.match(line)
            if m2:
                try:
                    start, body = parse_ts(m2.group(1)), m2.group(2)
                except ValueError:
                    m2 = None
            if not m2:
                if segs:
                    segs[-1]["text"] = (segs[-1]["text"] + " " + line).strip()
                else:
                    orphan.append(line)
                continue
        body = (body or "").strip()
        kind, speaker = "speech", None
        vm = _VISUAL_RE.match(body)
        if vm:
            kind, body = "visual", vm.group(1).strip()
        elif body.startswith("[") and body.endswith("]"):
            kind = "event"
        else:
            sm = _SPEAKER_RE.match(body)
            if sm:
                speaker, body = sm.group(1).strip(), sm.group(2).strip()
        segs.append({"start": start, "end": end, "speaker": speaker, "kind": kind, "text": body})

    for i, s in enumerate(segs):
        if s["end"] is None or s["end"] < s["start"]:
            nxt = segs[i + 1]["start"] if i + 1 < len(segs) else None
            s["end"] = nxt if nxt is not None and nxt >= s["start"] else s["start"] + 2.0
    if chunk_len and chunk_len > 0:
        over = [s for s in segs if s["start"] > chunk_len + 5]
        if over:
            warnings.append(f"{len(over)} сегм. с таймкодом за пределами фрагмента — обрезаны до его конца")
        for s in segs:
            s["start"] = min(s["start"], chunk_len)
            s["end"] = min(max(s["end"], s["start"]), chunk_len)
    for s in segs:
        s["start"] = round(s["start"] + offset, 2)
        s["end"] = round(s["end"] + offset, 2)
    if not segs and orphan:
        warnings.append("модель не поставила таймкоды — фрагмент сохранён одним блоком")
        segs = [{"start": round(offset, 2), "end": round(offset + (chunk_len or 0), 2),
                 "speaker": None, "kind": "speech", "text": " ".join(orphan)}]
    return segs, warnings


def render_txt(segs: list[dict]) -> str:
    out = []
    for s in segs:
        head = f"[{fmt_ts(s['start'])} - {fmt_ts(s['end'])}]"
        if s["kind"] == "visual":
            out.append(f"{head} [КАДР] {s['text']}")
        elif s["speaker"]:
            out.append(f"{head} {s['speaker']}: {s['text']}")
        else:
            out.append(f"{head} {s['text']}")
    return "\n".join(out)


def render_srt(segs: list[dict]) -> str:
    out = []
    n = 0
    for s in segs:
        if s["kind"] == "visual":
            continue
        n += 1
        txt = f"{s['speaker']}: {s['text']}" if s["speaker"] else s["text"]
        out.append(f"{n}\n{fmt_ts(s['start'], True)} --> {fmt_ts(s['end'], True)}\n{txt}\n")
    return "\n".join(out)


def plan_chunks(duration: float, chunk_sec: float) -> list[tuple[float, float]]:
    """[(offset, length)] без коротких хвостов."""
    if duration <= 0:
        return [(0.0, 0.0)]
    bounds = []
    t = 0.0
    while t < duration - 1e-6:
        bounds.append([t, min(chunk_sec, duration - t)])
        t += chunk_sec
    if len(bounds) > 1 and bounds[-1][1] < MIN_TAIL_SEC:
        tail = bounds.pop()
        bounds[-1][1] += tail[1]
    return [(round(a, 3), round(b, 3)) for a, b in bounds]


# ───────────────────────── ffmpeg ─────────────────────────

def _run(cmd: list[str], timeout: int = 900) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} rc={p.returncode}: {(p.stderr or '').strip()[-400:]}")
    return p.stdout


def probe(path: str) -> dict:
    """{duration, has_audio, has_video} (обложка mp3 видео не считается)."""
    out = _run(["ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", path], timeout=120)
    j = json.loads(out or "{}")
    has_audio = has_video = False
    for s in j.get("streams", []):
        if s.get("codec_type") == "audio":
            has_audio = True
        elif s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic"):
            has_video = True
    try:
        duration = float((j.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"duration": duration, "has_audio": has_audio, "has_video": has_video}


def _cut_audio(src: str, dst: str, start: float | None = None, length: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if length is not None:
        cmd += ["-t", f"{length:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", dst]
    _run(cmd)


def _cut_video(src: str, dst: str, start: float, length: float, height: int, crf: int,
               with_audio: bool) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{length:.3f}",
           "-vf", f"scale=-2:'min({height},ih)',fps=5", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", str(crf), "-pix_fmt", "yuv420p"]
    cmd += (["-c:a", "aac", "-ac", "1", "-ar", "16000", "-b:a", "48k"] if with_audio else ["-an"])
    cmd += ["-movflags", "+faststart", dst]
    _run(cmd)


def prepare_chunks(src: str, workdir: str, visual: bool, info: dict,
                   audio_chunk: float | None = None,
                   video_chunk: float | None = None) -> list[dict]:
    """Режет исходник на фрагменты. [{path, offset, length, mode, mime, fmt}]."""
    audio_chunk = audio_chunk or AUDIO_CHUNK_SEC
    video_chunk = video_chunk or VIDEO_CHUNK_SEC
    chunks: list[dict] = []
    if not visual:
        full = os.path.join(workdir, "full.mp3")
        _cut_audio(src, full)            # полная перекодировка даёт честную длительность
        duration = probe(full)["duration"] or info.get("duration") or 0
        if duration > MAX_DURATION_SEC:
            raise ValueError(f"запись {fmt_ts(duration)} длиннее лимита {fmt_ts(MAX_DURATION_SEC)}")
        plan = plan_chunks(duration, audio_chunk)
        for i, (off, ln) in enumerate(plan):
            dst = os.path.join(workdir, f"a{i:03d}.mp3")
            if len(plan) == 1:
                shutil.copyfile(full, dst)
            else:
                _cut_audio(full, dst, off, ln)
            chunks.append({"path": dst, "offset": off, "length": ln, "mode": "audio",
                           "mime": "audio/mpeg", "fmt": "mp3"})
        return chunks

    duration = info.get("duration") or 0
    if duration <= 0:
        remux = os.path.join(workdir, "remux.mkv")
        _run(["ffmpeg", "-y", "-v", "error", "-i", src, "-c", "copy", remux])
        duration = probe(remux)["duration"]
        src = remux
    if duration <= 0:
        raise ValueError("не удалось определить длительность видео")
    if duration > MAX_DURATION_SEC:
        raise ValueError(f"видео {fmt_ts(duration)} длиннее лимита {fmt_ts(MAX_DURATION_SEC)}")
    for i, (off, ln) in enumerate(plan_chunks(duration, video_chunk)):
        dst = os.path.join(workdir, f"v{i:03d}.mp4")
        _cut_video(src, dst, off, ln, 480, 30, info.get("has_audio", True))
        if os.path.getsize(dst) * 4 / 3 > MAX_B64_BYTES:
            _cut_video(src, dst, off, ln, 360, 36, info.get("has_audio", True))
        if os.path.getsize(dst) * 4 / 3 > MAX_B64_BYTES:
            raise ValueError(f"фрагмент {i + 1} не ужимается под лимит запроса шлюза")
        chunks.append({"path": dst, "offset": off, "length": ln, "mode": "video",
                       "mime": "video/mp4", "fmt": "mp4"})
    return chunks


# ───────────────────────── промпт и запрос ─────────────────────────

def build_prompt(chunk: dict, total: int, idx: int, visual: bool, language: str | None,
                 context: str | None, instruction: str | None = None) -> str:
    lines = [
        "Сделай ТОЧНУЮ дословную расшифровку речи из этой записи.",
        f"Это фрагмент {idx + 1} из {total}, длительность {fmt_ts(chunk['length'])}. "
        "Таймкоды считай от начала ЭТОГО фрагмента (первая секунда = 00:00).",
        "",
        "Формат: каждая реплика — отдельной строкой, без вступлений, markdown и пояснений:",
        "[MM:SS.s - MM:SS.s] Спикер N: текст реплики",
        "",
        "Правила:",
        "- дословно, без пересказа, сокращений и исправления оговорок; слова-паразиты оставляй;",
        "- новая строка при смене говорящего и не реже, чем раз в ~15 секунд речи;",
        "- спикеров различай по голосу: Спикер 1, Спикер 2…; если имя явно прозвучало, пиши имя;",
        "- неразборчивое — [неразборчиво], паузы длиннее 5 с и значимые звуки — "
        "отдельной строкой в квадратных скобках, например [00:12.0 - 00:20.0] [музыка];",
        "- если речи нет совсем, ответь одной строкой [00:00.0 - MM:SS.s] [речи нет];",
        "- ничего не выдумывай: если звука не слышно, так и напиши, не угадывай по картинке.",
    ]
    if visual:
        lines += [
            "",
            "Дополнительно отмечай значимые визуальные события (смена сцены, текст и слайды "
            "на экране, действия) отдельными строками:",
            "[MM:SS.s - MM:SS.s] [КАДР] описание",
        ]
    if language:
        lines.append(f"Основной язык записи: {language}. Иноязычные вставки пиши на языке оригинала.")
    else:
        lines.append("Пиши на языке оригинала, не переводи.")
    if context:
        lines += ["", f"Контекст и словарь терминов от пользователя: {context.strip()[:2000]}"]
    if instruction and instruction.strip():
        lines += ["", "Дополнительное указание (выполняй его, но формат строк с таймкодами "
                  f"не меняй и речь не пропускай): {instruction.strip()[:2000]}"]
    return "\n".join(lines)


def _extract_text(data: dict) -> tuple[str, str | None]:
    choices = data.get("choices") or []
    if not choices:
        return "", None
    ch = choices[0] or {}
    msg = ch.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return (content or "").strip(), ch.get("finish_reason")


async def _post_with_retries(client: httpx.AsyncClient, url: str, payload: dict,
                             headers: dict) -> dict:
    last = None
    for attempt in range(RETRIES):
        try:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (408, 409, 425, 429, 500, 502, 503, 504):
                log.warning("transcribe_media: попытка %d/%d %s, без повтора", attempt + 1, RETRIES, last[:200])
                break
        except (httpx.TransportError, ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        log.warning("transcribe_media: попытка %d/%d не удалась: %s", attempt + 1, RETRIES, (last or "")[:200])
        if attempt + 1 < RETRIES:
            await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(last or "пустой ответ")


# ───────────────────────── источники ─────────────────────────

def _within_roots(real: str) -> bool:
    return any(real == r or real.startswith(r + os.sep) for r in ALLOWED_MEDIA_ROOTS)


def resolve_local_media(path_or_name: str) -> str | None:
    raw = (path_or_name or "").strip()
    if not raw:
        return None
    real = os.path.realpath(raw)
    if _within_roots(real) and os.path.isfile(real):
        return real
    name = os.path.basename(raw)
    if not name:
        return None
    for root in ALLOWED_MEDIA_ROOTS:
        for cand in glob.glob(os.path.join(root, "**", glob.escape(name)), recursive=True):
            real = os.path.realpath(cand)
            if _within_roots(real) and os.path.isfile(real):
                return real
    return None


def _mb(n: float) -> str:
    return f"{n / (1024 * 1024):.1f} МБ"


def discard_source(real: str) -> bool:
    """Удаляет исходник, если он лежит в DELETABLE_ROOTS. Пустую папку загрузки тоже."""
    real = os.path.realpath(real)
    if not any(real.startswith(r + os.sep) for r in DELETABLE_ROOTS) or not os.path.isfile(real):
        return False
    try:
        os.remove(real)
    except OSError as e:
        log.warning("transcribe_media: не удалось удалить %s: %s", real, e)
        return False
    parent = os.path.dirname(real)
    if parent not in DELETABLE_ROOTS:
        try:
            os.rmdir(parent)          # только если пустая
        except OSError:
            pass
    log.info("transcribe_media: удалён слишком большой исходник %s", real)
    return True


def too_big_message(name: str, reason: str, deleted: bool, size: int, duration: float) -> str:
    """Текст для агента: что не так, что с файлом и как нарезать."""
    parts = 2
    if size > MAX_INPUT_BYTES:
        parts = max(parts, -(-size // MAX_INPUT_BYTES))
    if duration > MAX_DURATION_SEC:
        parts = max(parts, -(-int(duration) // MAX_DURATION_SEC))
    seg = int(duration / parts) if duration > 0 else MAX_DURATION_SEC // 2
    seg = max(60, min(seg, MAX_DURATION_SEC))
    ext = os.path.splitext(name)[1] or ".mp4"
    fate = ("Файл удалён из области агента, чтобы не занимать диск." if deleted
            else "Файл не удалялся (он лежит вне загрузок агента).")
    return (f"«{name}» не расшифрован: {reason}. {fate}\n"
            f"Сообщи пользователю, что файл слишком большой, и попроси нарезать его минимум на "
            f"{parts} части (каждая до {_mb(MAX_INPUT_BYTES)} и до {fmt_ts(MAX_DURATION_SEC)}) "
            f"и прислать по очереди. Команда для нарезки без перекодирования:\n"
            f"ffmpeg -i \"{name}\" -c copy -map 0 -f segment -segment_time {seg} "
            f"-reset_timestamps 1 part_%02d{ext}\n"
            "Если важен только звук, можно сжать запись в mp3: "
            f"ffmpeg -i \"{name}\" -vn -ac 1 -ar 16000 -b:a 48k audio.mp3")


def _safe_stem(name: str) -> str:
    stem = os.path.splitext(os.path.basename(name or "media"))[0]
    stem = re.sub(r"[^\w\-.]+", "_", stem, flags=re.U).strip("._")
    return (stem or "media")[:60]


# ───────────────────────── тул ─────────────────────────

def make_transcribe_media_tool(agent, chat_id=None, output_root: str | None = None):
    """Фабрика тула transcribe_media. Модель — get_transcribe_config()."""
    from ...core.agent_provider import get_transcribe_config

    @tool
    async def transcribe_media(
        source_media_id: str | None = None,
        local_path: str | None = None,
        include_visual: bool = False,
        language: str | None = None,
        context: str | None = None,
        instruction: str | None = None,
    ) -> str:
        """
        Exact verbatim transcription of AUDIO or VIDEO with timestamps and speakers
        (google/gemini-3.1-pro-preview). Use it for any audio/video the user sent
        ([VIDEO]/[AUDIO] markers) or a media file on disk: meetings, calls, voice
        notes, lectures, screencasts.

        Source (exactly one):
          - source_media_id — media_id of the file from the chat MEDIA STORAGE
            (shown in the attachment marker or in the media registry);
          - local_path — path or file name inside /app/generated_files or /sandbox
            (large uploads > 5 MB have only a path, not a media_id). Files from a
            factory workspace must be downloaded first via download_workspace_file.

        include_visual — for video: also add [КАДР] lines with on-screen events
            (scene changes, slides, text). Default False = speech only (cheaper).
        language — main language hint, e.g. "ru", "en". Optional.
        context — names, terms, glossary to spell correctly. Optional.
        instruction — your own extra request to the transcription model, e.g.
            "отметь, где обсуждают сроки" or "speaker 1 is Павел". The timestamped
            line format stays fixed. Optional, up to 2000 chars.

        Size limits: source up to TRANSCRIBE_MAX_INPUT_MB (500 MB by default) and
        TRANSCRIBE_MAX_DURATION_MIN (240 min). A larger uploaded file is deleted from
        the agent area and the answer explains how the user should split it.

        Long recordings are split automatically (audio 10 min, video 5 min chunks)
        and stitched back with absolute timestamps. The FULL transcript is saved to
        .txt, .srt and .json files; the answer contains a summary, file paths and
        a preview. To give the user the transcript, call send_file_to_user with
        the returned paths — do not retype it from the preview.
        """
        cfg = get_transcribe_config()
        if not cfg.get("api_key"):
            return "Расшифровка недоступна: не задан ключ CLOUD_LLM_ACCESS_KEY."
        mid = (source_media_id or "").strip()
        lp = (local_path or "").strip()
        if not mid and not lp:
            return ("Не указан источник: передай source_media_id (из маркера [VIDEO]/[AUDIO] "
                    "или реестра медиа) либо local_path файла в /app/generated_files или /sandbox.")

        workdir = tempfile.mkdtemp(prefix="transcribe_")
        t_start = time.monotonic()
        try:
            # ── 1. исходник в workdir ──
            if mid:
                try:
                    from agent.media import media_store as _ms
                except Exception:
                    _ms = None
                if _ms is None or chat_id is None:
                    return "Медиа-хранилище недоступно в этом контексте, передай local_path."
                raw, meta = await _ms.get_media(chat_id, mid)
                if not raw:
                    return (f"media_id={mid} не найден в хранилище (истёк TTL или неверный id). "
                            "Если файл больше 5 МБ, он лежит только на диске — передай local_path.")
                name = (meta or {}).get("name") or f"media_{mid}"
                src = os.path.join(workdir, "src" + (os.path.splitext(name)[1].lower() or ".bin"))
                with open(src, "wb") as f:
                    f.write(raw)
                label = f"media_id={mid}"
            else:
                real = resolve_local_media(lp)
                if not real:
                    return (f"Файл «{lp}» не найден в /app/generated_files или /sandbox (или путь "
                            "недопустим). Файл из workspace сначала скачай через download_workspace_file.")
                name = os.path.basename(real)
                if os.path.splitext(name)[1].lower() not in MEDIA_EXT:
                    return f"«{name}»: расширение не похоже на аудио или видео."
                src = real
                label = f"local:{name}"
            size = os.path.getsize(src)
            log.info("transcribe_media: старт %s, %s, include_visual=%s, language=%s, "
                     "context=%d симв., instruction=%d симв.", label, _mb(size), include_visual,
                     language, len(context or ""), len(instruction or ""))
            if size > MAX_INPUT_BYTES:
                deleted = discard_source(src) if not mid else False
                log.warning("transcribe_media: %s %s больше лимита %s, удалён=%s",
                            label, _mb(size), _mb(MAX_INPUT_BYTES), deleted)
                return too_big_message(name, f"размер {_mb(size)} больше лимита {_mb(MAX_INPUT_BYTES)}",
                                       deleted, size, 0.0)

            # ── 2. probe + нарезка ──
            try:
                info = await asyncio.to_thread(probe, src)
            except Exception as e:
                return f"«{name}» не читается как медиафайл: {e}"
            log.info("transcribe_media: %s длительность %s, звук=%s, видео=%s", label,
                     fmt_ts(info["duration"]), info["has_audio"], info["has_video"])
            if info["duration"] > MAX_DURATION_SEC:
                deleted = discard_source(src) if not mid else False
                log.warning("transcribe_media: %s длительность %s больше лимита %s, удалён=%s",
                            label, fmt_ts(info["duration"]), fmt_ts(MAX_DURATION_SEC), deleted)
                return too_big_message(name, f"длительность {fmt_ts(info['duration'])} больше лимита "
                                       f"{fmt_ts(MAX_DURATION_SEC)}", deleted, size, info["duration"])
            if not info["has_audio"] and not info["has_video"]:
                return f"«{name}»: в файле нет ни звуковой, ни видеодорожки."
            warnings: list[str] = []
            visual = bool(include_visual and info["has_video"])
            if include_visual and not info["has_video"]:
                warnings.append("видеодорожки нет — расшифрован только звук")
            if not info["has_audio"]:
                if not info["has_video"]:
                    return f"«{name}»: нечего расшифровывать."
                visual = True
                warnings.append("в файле нет звука — описаны только визуальные события")
            t_cut = time.monotonic()
            try:
                chunks = await asyncio.to_thread(prepare_chunks, src, workdir, visual, info)
            except Exception as e:
                log.warning("transcribe_media: нарезка %s упала: %s", label, e)
                return f"Не удалось подготовить «{name}»: {e}"
            log.info("transcribe_media: %s нарезан на %d фрагм. (%s) за %.1f с, модель %s", label,
                     len(chunks), "видео" if visual else "аудио", time.monotonic() - t_cut, cfg["model"])

            # ── 3. запросы по фрагментам ──
            url = cfg["base_url"].rstrip("/") + "/chat/completions"
            headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
            sem = asyncio.Semaphore(MAX_PARALLEL)
            track = getattr(agent, "track_tokens", None)

            async def run_chunk(client, i, ch):
                async with sem:
                    b64 = await asyncio.to_thread(
                        lambda: base64.b64encode(open(ch["path"], "rb").read()).decode("ascii"))
                    prompt = build_prompt(ch, len(chunks), i, visual, language, context, instruction)
                    if ch["mode"] == "audio":
                        media = {"type": "input_audio", "input_audio": {"data": b64, "format": ch["fmt"]}}
                    else:
                        media = {"type": "image_url", "image_url": {"url": f"data:{ch['mime']};base64,{b64}"}}
                    payload = {"model": cfg["model"], "max_tokens": MAX_TOKENS,
                               "messages": [{"role": "user",
                                             "content": [{"type": "text", "text": prompt}, media]}]}
                    t0 = time.monotonic()
                    try:
                        data = await _post_with_retries(client, url, payload, headers)
                    except Exception as e:
                        log.warning("transcribe_media: %s фрагмент %d/%d (%s) упал за %.1f с: %s", label,
                                    i + 1, len(chunks), fmt_ts(ch["offset"]), time.monotonic() - t0, e)
                        raise
                    u = data.get("usage") or {}
                    log.info("transcribe_media: %s фрагмент %d/%d с %s, %s за %.1f с, in=%s out=%s finish=%s",
                             label, i + 1, len(chunks), fmt_ts(ch["offset"]), _mb(len(b64)),
                             time.monotonic() - t0, u.get("prompt_tokens"), u.get("completion_tokens"),
                             ((data.get("choices") or [{}])[0] or {}).get("finish_reason"))
                    if callable(track):
                        track(data, tool="transcribe_media", model=cfg["model"],
                              extra={"media_seconds": round(ch["length"])})
                    return _extract_text(data)

            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                results = await asyncio.gather(
                    *(run_chunk(client, i, ch) for i, ch in enumerate(chunks)),
                    return_exceptions=True)

            segments: list[dict] = []
            failed = 0
            for i, (ch, res) in enumerate(zip(chunks, results)):
                span = f"{fmt_ts(ch['offset'])}–{fmt_ts(ch['offset'] + ch['length'])}"
                if isinstance(res, Exception):
                    failed += 1
                    warnings.append(f"фрагмент {i + 1} ({span}) не расшифрован: {res}")
                    segments.append({"start": ch["offset"], "end": ch["offset"] + ch["length"],
                                     "speaker": None, "kind": "event",
                                     "text": "[фрагмент не расшифрован: ошибка модели]"})
                    continue
                text, finish = res
                if finish == "length":
                    warnings.append(f"фрагмент {i + 1} ({span}) обрезан по max_tokens, конец может отсутствовать")
                if not text:
                    warnings.append(f"фрагмент {i + 1} ({span}): модель вернула пустой ответ")
                    continue
                segs, w = parse_transcript(text, ch["length"], ch["offset"])
                segments.extend(segs)
                warnings.extend(f"фрагмент {i + 1}: {x}" for x in w)
            if failed == len(chunks):
                return "Расшифровка не удалась: " + "; ".join(warnings)[:1500]
            segments.sort(key=lambda s: (s["start"], s["end"]))

            # ── 4. файлы ──
            duration = chunks[-1]["offset"] + chunks[-1]["length"]
            out_dir = os.path.join(output_root or OUTPUT_ROOT, uuid.uuid4().hex)
            os.makedirs(out_dir, exist_ok=True)
            stem = _safe_stem(name) + "_transcript"
            txt = render_txt(segments)
            paths = {
                "txt": os.path.join(out_dir, stem + ".txt"),
                "srt": os.path.join(out_dir, stem + ".srt"),
                "json": os.path.join(out_dir, stem + ".json"),
            }
            with open(paths["txt"], "w", encoding="utf-8") as f:
                f.write(txt + "\n")
            with open(paths["srt"], "w", encoding="utf-8") as f:
                f.write(render_srt(segments))
            with open(paths["json"], "w", encoding="utf-8") as f:
                json.dump({"source": name, "model": cfg["model"], "duration_sec": round(duration, 2),
                           "mode": "video+frames" if visual else "audio", "chunks": len(chunks),
                           "warnings": warnings, "segments": segments}, f, ensure_ascii=False, indent=1)

            speakers = sorted({s["speaker"] for s in segments if s["speaker"]})
            head = [
                f"Расшифровка «{name}» готова: {fmt_ts(duration)}, фрагментов {len(chunks)}, "
                f"сегментов {len(segments)}, модель {cfg['model']}, "
                f"режим {'звук + кадры' if visual else 'только звук'}.",
                ("Спикеры: " + ", ".join(speakers)) if speakers else "Спикеры не размечены.",
                "Файлы (полный текст, отдать пользователю через send_file_to_user):",
                f"- {paths['txt']}", f"- {paths['srt']}", f"- {paths['json']}",
            ]
            if warnings:
                head.append("Предупреждения: " + "; ".join(warnings)[:1500])
            preview = txt if len(txt) <= PREVIEW_CHARS else (
                txt[:PREVIEW_CHARS] + f"\n… (ещё {len(txt) - PREVIEW_CHARS} символов в файле .txt)")
            log.info("transcribe_media: готово %s -> %d сегм., %d фрагм., ошибок %d, предупреждений %d, "
                     "%.1f с, %s", label, len(segments), len(chunks), failed, len(warnings),
                     time.monotonic() - t_start, out_dir)
            return "\n".join(head) + "\n\nРасшифровка:\n" + preview
        except Exception as e:
            log.error("transcribe_media failed: %s", e, exc_info=True)
            return f"Ошибка расшифровки: {e}"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return transcribe_media
