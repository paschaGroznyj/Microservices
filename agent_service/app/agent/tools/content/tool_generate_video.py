"""
tools/content/tool_generate_video.py
------------------------------------
Admin-only video generation via RouterAPI (routerai.ru).

RouterAPI is a SEPARATE paid provider (different base_url + auth token). Video
generation is ASYNCHRONOUS: POST /v1/videos creates a task and returns an id +
polling_url; we then poll GET /v1/videos/{id} until the task is ready and fetch
the resulting mp4 via GET /v1/videos/{id}/content.

Image-to-video sources (first frame + references) mirror generate_image: an
image may come from a local path, a generated_images image_key, an index into
the user-attached images of the current request, or a media_id from the per-chat
media_store cache. Any of those is resolved to a base64 data URI before being
put into the RouterAPI payload (which accepts URL or data URI alike).

The produced mp4 is written to /app/generated_files so the transport can hand it
to the user (send_file_to_user). Everything talks raw httpx (OpenAI-compatible),
not the langchain wrapper.
"""

import os
import uuid
import time
import asyncio
import logging

import httpx
from langchain.tools import tool

from ...core.agent_provider import get_router_api_config
from ...core.user_secrets import resolve_router_api_key
from ...core import media_jobs, obs_file_storage
from ._image_sources import (
    resolve_path_to_data_uri,
    resolve_image_key_to_data_uri,
    resolve_user_image_to_data_uri,
    resolve_media_id_to_data_uri,
)

log = logging.getLogger(__name__)

GENERATED_ROOT = "/app/generated_files"

# Hard cap on clip duration. API supports up to 15s, but by product decision we
# cap at 10s to keep cost under control (admin's personal funds).
MAX_DURATION_SEC = 10

# ── Provider matrix ────────────────────────────────────────────────────────
# RouterAPI exposes several video backends behind the same /v1/videos async
# endpoint, but each backend accepts a different set of aspect/resolution/
# duration values. We pick the backend via the `provider` arg and validate the
# params against that backend's allowed set. Default stays heygen (cheapest).
#
# Each entry: model id + allowed aspect ratios / resolutions / durations +
# the fallback value used when the caller passes something unsupported.
PROVIDERS = {
    "heygen": {
        "model": "heygen/heygen-video-1",
        "aspect": {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"},
        "resolution": {"480p", "768p"},
        "duration": {5, 6, 7, 8, 9, 10},  # 11..15 excluded (cost cap 10s)
        "default_aspect": "16:9",
        "default_resolution": "480p",
        "default_duration": 5,
        "audio": False,  # heygen has no audio track knob here
        "frames": {"first_frame"},
        "refs": True,
        "max_refs": None,
    },
    "google": {
        "model": "google/veo-3.1-fast",
        "aspect": {"16:9", "9:16"},
        "resolution": {"720p", "1080p", "4K"},
        "duration": {4, 6, 8},
        "default_aspect": "16:9",
        "default_resolution": "720p",
        "default_duration": 8,
        "audio": True,  # veo generates an audio track by default
        "frames": {"first_frame", "last_frame"},
        "refs": True,
        "max_refs": 3,
    },
    # Veo 3.1 Lite: < 50% of veo-3.1-fast price per second, no 4K and no
    # reference images (RouterAPI /models: supports_reference_images=false).
    "google-lite": {
        "model": "google/veo-3.1-lite",
        "aspect": {"16:9", "9:16"},
        "resolution": {"720p", "1080p"},
        "duration": {4, 6, 8},
        "default_aspect": "16:9",
        "default_resolution": "720p",
        "default_duration": 8,
        "audio": True,
        "frames": {"first_frame", "last_frame"},
        "refs": False,
        "max_refs": 0,
    },
    # Talking-head avatar: animates ONE face image to speak. Either synthesizes
    # speech from `prompt` using a catalog voice_id, OR lip-syncs to a provided
    # audio track (audio_url / local audio path). Duration is driven by the
    # speech length, not a `duration` knob — so we don't send duration here.
    "avatar": {
        "model": "heygen/avatar-iv",
        "aspect": {"16:9", "9:16", "1:1", "4:3", "3:4"},
        "resolution": {"720p", "1080p"},
        "duration": set(),  # not applicable — length follows the speech
        "default_aspect": "16:9",
        "default_resolution": "720p",
        "default_duration": None,
        "audio": False,
        "frames": set(),  # face goes into input_references, not frame_images
        "refs": True,
        "max_refs": 1,
    },
}
DEFAULT_PROVIDER = "heygen"
# Spellings the LLM tends to use for the same backend.
PROVIDER_ALIASES = {
    "lite": "google-lite",
    "veo-lite": "google-lite",
    "veo-3.1-lite": "google-lite",
    "google_lite": "google-lite",
    "google/veo-3.1-lite": "google-lite",
    "veo": "google",
    "google/veo-3.1-fast": "google",
}
# Multilingual HeyGen catalog voice used when the caller doesn't pick one
# (works for Russian). Override per call via avatar_voice_id.
DEFAULT_AVATAR_VOICE_ID = "80441555167a467e967ab9487d844a30"

# Polling parameters for the async video task.
POLL_INTERVAL_SEC = 6
POLL_TIMEOUT_SEC = 1200  # фон досматривает задачу до 20 мин от создания
# Сколько сам тул ждёт готовности. Дальше — task_id и check_video_status.
VIDEO_WAIT_SEC = int(os.getenv("VIDEO_TOOL_WAIT_SEC", "150"))
CHECK_WAIT_MAX_SEC = 90


def _coerce_url_list(val):
    """Normalize image URL list arg (LLM may send JSON string / CSV / single)."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if not s:
        return []
    try:
        import json as _json
        parsed = _json.loads(s)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        return [str(parsed).strip()]
    except Exception:
        return [p.strip() for p in s.strip("[]").split(",") if p.strip()]


def _coerce_int_list(val):
    """Normalize int-list arg (indexes). Accepts list / JSON / CSV / single."""
    if val is None:
        return []
    if isinstance(val, int):
        return [val]
    if isinstance(val, list):
        out = []
        for x in val:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out
    s = str(val).strip()
    if not s:
        return []
    try:
        import json as _json
        parsed = _json.loads(s)
        return _coerce_int_list(parsed)
    except Exception:
        out = []
        for p in s.strip("[]").split(","):
            p = p.strip()
            if not p:
                continue
            try:
                out.append(int(p))
            except ValueError:
                continue
        return out


# Local audio file (produced by generate_audio / generate_music) -> data URI.
# Whitelisted roots only; used to feed an avatar lip-sync track without a public
# URL. Mirrors the image path resolver but for audio mime types.
_AUDIO_ROOTS = ("/app/generated_files",)
_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".flac": "audio/flac",
}


def _in_audio_roots(path: str) -> bool:
    real = os.path.realpath(path)
    return any(real.startswith(os.path.realpath(r) + os.sep) for r in _AUDIO_ROOTS)


def _resolve_audio_to_data_uri(path_or_name: str) -> str | None:
    """Local audio path / file name (whitelisted roots) -> data URI, or None."""
    import base64 as _b64
    p = (path_or_name or "").strip()
    if not p:
        return None
    candidate = None
    if os.path.isabs(p):
        if _in_audio_roots(p) and os.path.isfile(p):
            candidate = p
    else:
        for root in _AUDIO_ROOTS:
            for dirpath, _dirs, files in os.walk(root):
                if os.path.basename(p) in files:
                    candidate = os.path.join(dirpath, os.path.basename(p))
                    break
            if candidate:
                break
    if not candidate or not os.path.isfile(candidate) or not _in_audio_roots(candidate):
        return None
    ext = os.path.splitext(candidate)[1].lower()
    mime = _AUDIO_MIME.get(ext, "audio/mpeg")
    try:
        with open(candidate, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if not raw:
        return None
    return f"data:{mime};base64,{_b64.b64encode(raw).decode('ascii')}"


async def _resolve_frame(agent, images, chat_id, url=None, path=None,
                         image_key=None, user_image_index=None,
                         media_id=None) -> str | None:
    """One frame image from the first source that is set (priority order:
    url, path, image_key, user_image_index, media_id) -> URL/data URI or None."""
    if url and str(url).strip():
        return str(url).strip()
    if path and str(path).strip():
        return resolve_path_to_data_uri(str(path).strip())
    if image_key and str(image_key).strip():
        return await resolve_image_key_to_data_uri(agent, str(image_key).strip())
    if user_image_index is not None and str(user_image_index).strip() != "":
        try:
            return resolve_user_image_to_data_uri(images, int(user_image_index))
        except (TypeError, ValueError):
            return None
    if media_id and str(media_id).strip():
        return await resolve_media_id_to_data_uri(agent, chat_id, str(media_id).strip())
    return None


def _any_source(*vals) -> bool:
    return any(v is not None and str(v).strip() != "" for v in vals)


_DONE = ("completed", "succeeded", "success", "ready", "done")
_FAILED = ("failed", "error", "canceled", "cancelled")
_bg_tasks: set = set()


async def _poll_once(job: dict, api_key: str) -> tuple[str, dict | str]:
    """('done'|'failed'|'processing'|'error', provider json | error text)."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            pr = await client.get(job["poll_url"], headers=headers)
    except httpx.HTTPError as e:
        return "error", f"network failure: {e}"
    if pr.status_code >= 400:
        return "error", f"poll HTTP {pr.status_code}: {pr.text[:300]}"
    last = pr.json()
    st = (last.get("status") or "").lower()
    if st in _DONE:
        return "done", last
    if st in _FAILED:
        return "failed", last
    return "processing", last


async def _download(chat_id, job: dict, last: dict, api_key: str) -> dict:
    """mp4 -> generated_files/media_<uuid>/ (TTL 3 ч) -> сразу OBS. Обновляет job."""
    if not await media_jobs.acquire_lock(chat_id, job["task_id"]):
        return job  # качает кто-то другой (фон / параллельный check)
    try:
        fresh = await media_jobs.get_job(chat_id, job["task_id"]) or job
        if fresh.get("status") == "done" and fresh.get("obs_key"):
            return fresh
        out = last.get("output") if isinstance(last, dict) else None
        direct_url = ((last or {}).get("url") or (last or {}).get("video_url")
                      or (out.get("url") if isinstance(out, dict) else None))
        resp = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            if direct_url:
                try:
                    resp = await client.get(direct_url)
                except httpx.HTTPError:
                    resp = None
            if resp is None or resp.status_code >= 400:
                resp = await client.get(job["content_url"],
                                        headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code >= 400:
            job.update(status="error",
                       error=f"content download HTTP {resp.status_code}: {resp.text[:200]}")
            await media_jobs.save_job(chat_id, job)
            return job
        fpath = obs_file_storage.paid_media_path(f"video_{job['task_id'][:12]}.mp4")
        with open(fpath, "wb") as f:
            f.write(resp.content)
        obs_key = await obs_file_storage.upload_user_file(fpath, chat_id)
        job.update(status="done", file=fpath, obs_key=obs_key,
                   size_kb=len(resp.content) // 1024, error=None)
        await media_jobs.save_job(chat_id, job)
        return job
    except httpx.HTTPError as e:
        job.update(status="error", error=f"content download failed: {e}")
        await media_jobs.save_job(chat_id, job)
        return job
    finally:
        await media_jobs.release_lock(chat_id, job["task_id"])


async def _step(chat_id, job: dict, api_key: str) -> dict:
    """Один опрос провайдера + скачивание, если готово."""
    st, last = await _poll_once(job, api_key)
    if st == "done":
        return await _download(chat_id, job, last, api_key)
    if st == "failed":
        job.update(status="failed", error=str(last)[:300])
        await media_jobs.save_job(chat_id, job)
        await media_jobs.drop_fingerprint(chat_id, job.get("fp"))
    elif st == "error":
        job["error"] = str(last)[:300]  # сетевой сбой опроса: задача жива, статус не трогаем
    return job


async def _wait_job(chat_id, job: dict, api_key: str, timeout: float) -> dict:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        job = await _step(chat_id, job, api_key)
        if job.get("status") != "processing" or time.monotonic() >= deadline:
            return job
        await asyncio.sleep(POLL_INTERVAL_SEC)


def _spawn_background(chat_id, job: dict, api_key: str) -> None:
    """Досматривает задачу после выхода из тула, чтобы ролик лёг в OBS сам."""
    async def run():
        try:
            left = POLL_TIMEOUT_SEC - (time.time() - float(job.get("created_at") or time.time()))
            await _wait_job(chat_id, job, api_key, max(left, POLL_INTERVAL_SEC))
        except Exception:
            log.exception("generate_video background poll failed task=%s", job.get("task_id"))
    t = asyncio.create_task(run())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def _job_report(agent, chat_id, job: dict, api_key: str | None) -> str:
    """Текст для модели по состоянию задачи. Готовый файл кладёт в автодоставку."""
    tid = job.get("task_id")
    st = job.get("status")
    if st == "done":
        path = job.get("file")
        if not (path and os.path.isfile(path)) and job.get("obs_key"):
            data, err = await obs_file_storage.get_object_bytes(job["obs_key"], 200 * 1024 * 1024)
            if data:
                path = obs_file_storage.paid_media_path(os.path.basename(path or "video.mp4"))
                with open(path, "wb") as f:
                    f.write(data)
                await media_jobs.update_job(chat_id, tid, file=path)
        if not (path and os.path.isfile(path)):
            return (f"Status: done, but the local file is gone and OBS copy is unavailable "
                    f"(obs_key={job.get('obs_key')}). Try fetch_from_obs later.")
        try:
            pend = getattr(agent, "_pending_generated_files", None)
            if isinstance(pend, list) and path not in pend:
                pend.append(path)
        except Exception:
            pass
        return (f"Status: done. Saved to {path} ({os.path.getsize(path) // 1024} KB), "
                f"obs_key={job.get('obs_key')}. Send it with send_file_to_user; "
                f"if the local file expires later, use fetch_from_obs with this obs_key.")
    if st == "failed":
        return f"Status: failed at provider: {job.get('error')}. Nothing to download."
    if st == "error":
        return (f"Status: ready at provider but download failed: {job.get('error')}. "
                f"Call check_video_status(task_id='{tid}') to retry the download "
                f"(no new charge).")
    hint = f" Last poll error: {job['error']}." if job.get("error") else ""
    return (f"Status: still processing.{hint} The task keeps running and is paid for. "
            f"Do NOT call generate_video again. Call check_video_status(task_id='{tid}') "
            f"in a minute or two to get the file.")


def make_generate_video_tool(agent, images: list[dict] | None = None):
    """Factory: builds the generate_video langchain tool.

    images: the user-attached images of the current request (same list passed to
    analyze_image / generate_image): [{filename, mime_type, b64}]. Used so the
    video tool can reference an image the user just uploaded by its index.
    """

    @tool
    async def generate_video(
        prompt: str,
        provider: str = "heygen",
        aspect_ratio: str | None = None,
        duration: int | None = None,
        resolution: str | None = None,
        first_frame_image_url: str | None = None,
        first_frame_path: str | None = None,
        first_frame_image_key: str | None = None,
        first_frame_user_image_index: int | None = None,
        first_frame_media_id: str | None = None,
        last_frame_image_url: str | None = None,
        last_frame_path: str | None = None,
        last_frame_image_key: str | None = None,
        last_frame_user_image_index: int | None = None,
        last_frame_media_id: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_image_keys: list[str] | None = None,
        reference_user_image_indexes: list[int] | None = None,
        reference_media_ids: list[str] | None = None,
        generate_audio: bool | None = None,
        seed: int | None = None,
        avatar_voice_id: str | None = None,
        avatar_audio_url: str | None = None,
        avatar_audio_path: str | None = None,
    ) -> str:
        """Generate a short video from a text prompt via RouterAPI (paid, billed to the user's own RouterAPI key).

        Asynchronous: creates a generation task, waits a few minutes and saves the
        resulting mp4 for delivery to the user. Use short, cinematic prompts.
        If the result says "still processing", the task is already PAID: never
        call generate_video again for it, call check_video_status(task_id).
        Repeating the exact same request in the same chat returns the existing
        task instead of creating a new one.

        Args:
            prompt: Text description of the scene to animate (required).
            provider: Which video backend to use. One of:
                "heygen" (default, cheapest) -> heygen/heygen-video-1;
                    aspect 21:9|16:9|4:3|1:1|3:4|9:16, resolution 480p|768p,
                    duration 5..10, no audio track.
                "google" -> google/veo-3.1-fast; aspect 16:9|9:16, resolution
                    720p|1080p|4K, duration 4|6|8, generates an audio track by
                    default. Use google for cinematic prompts with sound.
                    Supports first AND last frame, up to 3 reference images.
                "google-lite" -> google/veo-3.1-lite; same as google but less
                    than half the price, resolution 720p|1080p only (no 4K) and
                    NO reference images. Supports first and last frame. Prefer
                    it for drafts and cheap iterations.
                "avatar" -> heygen/avatar-iv; a TALKING-HEAD avatar that animates
                    ONE face photo to speak. aspect 16:9|9:16|1:1|4:3|3:4,
                    resolution 720p|1080p. Length follows the speech (no duration
                    knob). REQUIRES a face image as the first frame (use any
                    first_frame_* source — a clear JPEG with one visible face).
                    Speech comes from EITHER avatar_voice_id (TTS reads `prompt`)
                    OR an audio track (avatar_audio_url / avatar_audio_path) that
                    the avatar lip-syncs to. For lip-sync, `prompt` becomes a
                    stage direction (e.g. "friendly, smiling, gestures").
            aspect_ratio: Aspect ratio. Allowed values depend on `provider`
                (see above). Unsupported values snap to the provider default.
                If omitted, the provider default is used.
            duration: Clip length in seconds. Allowed values depend on `provider`
                (heygen 5..10, google 4|6|8). Values above 10 are capped; an
                unsupported value snaps to the nearest allowed one. If omitted,
                the provider default is used.
            resolution: Output resolution. Allowed values depend on `provider`
                (heygen 480p|768p, google 720p|1080p|4K, google-lite
                720p|1080p). If omitted, the
                provider default is used.

            First frame (image-to-video). Provide AT MOST ONE of these; they are
            tried in this priority order:
              first_frame_image_url: a public image URL or a data URI.
              first_frame_path: a local path OR file name inside the allowed
                root (/app/generated_files).
              first_frame_image_key: an image_key of a previously GENERATED
                image (generated_images table).
              first_frame_user_image_index: index of an image the USER attached
                in the current request (0 = first).
              first_frame_media_id: a media_id from the per-chat MEDIA STORAGE
                (cache of recent images in this chat).

            Last frame (google / google-lite only): the clip ends on this image,
            the model interpolates between first and last frame. Same five
            sources and the same priority as first_frame_*:
              last_frame_image_url, last_frame_path, last_frame_image_key,
              last_frame_user_image_index, last_frame_media_id.
            Give a first frame too and use duration=8 for interpolation.
            Ignored (with a note in the result) for heygen and avatar.

            References (character / style / object). Each list is resolved and
            all resolved images are sent together (google: max 3, extra ones
            are dropped; google-lite: not supported, dropped with a note):
              reference_image_urls: list of image URLs or data URIs.
              reference_image_keys: list of generated_images image_keys.
              reference_user_image_indexes: indexes of user-attached images.
              reference_media_ids: list of media_ids from MEDIA STORAGE.

            generate_audio: Whether to also generate an audio track. Only honored
                by providers that support it (google/veo). If omitted, the
                provider default is used (veo: on). Ignored for heygen.
            seed: Optional integer for deterministic generation.

            Avatar-only (provider="avatar"):
              avatar_voice_id: catalog voice id for TTS. The avatar reads
                `prompt` aloud in this voice. For Russian use a multilingual
                voice. Mutually exclusive with the audio lip-sync args.
              avatar_audio_url: public URL of an audio track to lip-sync to.
              avatar_audio_path: local path/name of an audio file (e.g. one
                produced by generate_audio or generate_music, in
                /app/generated_files) to lip-sync to. Resolved to a
                data URI automatically. Use this to chain voice/music -> avatar.
              Provide EITHER avatar_voice_id OR one of the audio args.

        Returns a human-readable status with the local mp4 path on success, or a
        clear error message on failure.
        """
        cfg = get_router_api_config()
        api_key, key_err = await resolve_router_api_key(agent)
        if key_err:
            return f"generate_video error: {key_err}"
        base_url = (cfg.get("base_url") or "").rstrip("/")
        prov_key = (provider or DEFAULT_PROVIDER).strip().lower()
        prov_key = PROVIDER_ALIASES.get(prov_key, prov_key)
        if prov_key not in PROVIDERS:
            return (f"generate_video error: unknown provider {provider!r}. "
                    f"Choose one of: {', '.join(PROVIDERS)}.")
        prov = PROVIDERS[prov_key]
        model = prov["model"]
        if not api_key:
            return ("generate_video error: RouterAPI key is not configured "
                    "(ROUTER_API_KEY). Ask the admin to set it in env.")
        if not (prompt or "").strip():
            return "generate_video error: prompt is empty."

        chat_id = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))

        # -- validate / clamp params (per selected provider) --------------
        ar = aspect_ratio if aspect_ratio in prov["aspect"] else prov["default_aspect"]
        res = resolution if resolution in prov["resolution"] else prov["default_resolution"]
        dur = None
        if prov["duration"]:  # avatar has no duration knob -> leave dur=None
            if duration is None:
                dur = prov["default_duration"]
            else:
                try:
                    dur = int(duration)
                except (TypeError, ValueError):
                    dur = prov["default_duration"]
                if dur > MAX_DURATION_SEC:
                    dur = MAX_DURATION_SEC
                if dur not in prov["duration"]:
                    # snap to the closest allowed duration for this provider
                    dur = min(prov["duration"], key=lambda d: (abs(d - dur), d))

        notes: list[str] = []

        # -- resolve first / last frame (at most one source each) ---------
        first_frame_uri = await _resolve_frame(
            agent, images, chat_id, first_frame_image_url, first_frame_path,
            first_frame_image_key, first_frame_user_image_index,
            first_frame_media_id)
        last_frame_uri = None
        last_given = _any_source(
            last_frame_image_url, last_frame_path, last_frame_image_key,
            last_frame_user_image_index, last_frame_media_id)
        if last_given:
            if "last_frame" not in prov["frames"]:
                notes.append(f"last frame ignored: {prov_key} supports only "
                             f"the first frame")
            else:
                last_frame_uri = await _resolve_frame(
                    agent, images, chat_id, last_frame_image_url,
                    last_frame_path, last_frame_image_key,
                    last_frame_user_image_index, last_frame_media_id)
                if not last_frame_uri:
                    notes.append("last frame source could not be resolved, "
                                 "sent without it")

        # -- resolve references (all sources concatenated) ----------------
        reference_uris: list[str] = []
        for u in _coerce_url_list(reference_image_urls):
            reference_uris.append(u)
        for k in _coerce_url_list(reference_image_keys):
            uri = await resolve_image_key_to_data_uri(agent, k)
            if uri:
                reference_uris.append(uri)
        for idx in _coerce_int_list(reference_user_image_indexes):
            uri = resolve_user_image_to_data_uri(images, idx)
            if uri:
                reference_uris.append(uri)
        for mid in _coerce_url_list(reference_media_ids):
            uri = await resolve_media_id_to_data_uri(agent, chat_id, mid)
            if uri:
                reference_uris.append(uri)

        if reference_uris and prov_key != "avatar":
            if not prov["refs"]:
                notes.append(f"{len(reference_uris)} reference image(s) dropped: "
                             f"{model} does not accept references")
                reference_uris = []
            elif prov["max_refs"] and len(reference_uris) > prov["max_refs"]:
                notes.append(f"references cut from {len(reference_uris)} to "
                             f"{prov['max_refs']} (model limit)")
                reference_uris = reference_uris[:prov["max_refs"]]

        data = {
            "model": model,
            "prompt": prompt.strip(),
            "aspect_ratio": ar,
            "resolution": res,
        }
        if dur is not None:
            data["duration"] = dur

        if prov_key == "avatar":
            # Talking-head: the face image MUST go into input_references (not
            # frame_images). Prefer an explicit first_frame_* source; fall back
            # to the first reference image if that's where the caller put it.
            face_uri = first_frame_uri or (reference_uris[0] if reference_uris else None)
            if not face_uri:
                return ("generate_video error: avatar needs a face image. Pass a "
                        "clear JPEG with one visible face via any first_frame_* "
                        "source (path / image_key / user_image_index / media_id).")
            refs = [{"type": "image_url", "image_url": {"url": face_uri}}]

            # Speech source: EITHER a lip-sync audio track OR a catalog voice_id.
            audio_uri = None
            if avatar_audio_url and str(avatar_audio_url).strip():
                audio_uri = str(avatar_audio_url).strip()
            elif avatar_audio_path and str(avatar_audio_path).strip():
                audio_uri = _resolve_audio_to_data_uri(str(avatar_audio_path).strip())
            if audio_uri:
                refs.append({"type": "audio_url", "audio_url": {"url": audio_uri}})
            data["input_references"] = refs

            if not audio_uri:
                # No audio -> TTS reads `prompt` with a catalog voice. Default to
                # a multilingual voice (Kore) when the caller didn't pick one.
                vid = (avatar_voice_id or "").strip() or DEFAULT_AVATAR_VOICE_ID
                data["provider"] = {"options": {"heygen": {"voice_id": vid}}}
        else:
            # Audio track: only meaningful for providers that expose the knob
            # (veo). Fall back to the provider default when the caller is silent.
            if prov["audio"]:
                want_audio = prov["audio"] if generate_audio is None else bool(generate_audio)
                data["generate_audio"] = want_audio
            if seed is not None:
                try:
                    data["seed"] = int(seed)
                except (TypeError, ValueError):
                    pass
            frames = []
            if first_frame_uri:
                frames.append({
                    "type": "image_url",
                    "image_url": {"url": first_frame_uri},
                    "frame_type": "first_frame",
                })
            if last_frame_uri:
                frames.append({
                    "type": "image_url",
                    "image_url": {"url": last_frame_uri},
                    "frame_type": "last_frame",
                })
            if frames:
                data["frame_images"] = frames
            if reference_uris:
                data["input_references"] = [
                    {"type": "image_url", "image_url": {"url": u}} for u in reference_uris
                ]

        frames_info = ""
        if first_frame_uri or last_frame_uri:
            parts = [n for n, u in (("first", first_frame_uri), ("last", last_frame_uri)) if u]
            frames_info = f" Frames: {'+'.join(parts)}."
        notes_info = f" Notes: {'; '.join(notes)}." if notes else ""
        summary = f"{prov_key} ({model}): {dur}s, {ar}, {res}.{frames_info}"

        # -- dedup: те же параметры в этом чате -> та же (уже оплаченная) задача
        fp = media_jobs.fingerprint(data)
        prev = await media_jobs.find_by_fingerprint(chat_id, fp)
        if prev:
            log.info("generate_video: dedup hit chat=%s task=%s", chat_id, prev.get("task_id"))
            return ("Same video request already exists in this chat, NOT charged again. "
                    + await _job_report(agent, chat_id, prev, api_key))

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                r = await client.post(f"{base_url}/videos", headers=headers, json=data)
        except httpx.HTTPError as e:
            return f"generate_video error: network failure: {e}"
        if r.status_code >= 400:
            return (f"generate_video error: create task failed "
                    f"HTTP {r.status_code}: {r.text[:300]}")
        task = r.json()
        task_id = task.get("id")
        if not task_id:
            return f"generate_video error: no task id in response: {str(task)[:300]}"

        job = {
            "task_id": str(task_id),
            "poll_url": task.get("polling_url") or f"{base_url}/videos/{task_id}",
            "content_url": f"{base_url}/videos/{task_id}/content",
            "status": "processing",
            "provider": prov_key, "model": model, "summary": summary,
            "prompt": prompt.strip()[:200],
            "fp": fp,
            "created_at": time.time(),
        }
        await media_jobs.save_job(chat_id, job, fp=fp)

        # -- ждём недолго; дальше задача досматривается фоном, а агент берёт
        #    результат через check_video_status (без нового списания)
        job = await _wait_job(chat_id, job, api_key, VIDEO_WAIT_SEC)
        if job.get("status") == "processing":
            _spawn_background(chat_id, job, api_key)
        return f"Video task {task_id} ({summary}).{notes_info} " + await _job_report(
            agent, chat_id, job, api_key)

    return generate_video


def make_check_video_status_tool(agent):
    """Factory: check_video_status — статус/результат уже созданной задачи generate_video."""

    @tool
    async def check_video_status(task_id: str, wait_sec: int = 30) -> str:
        """Get the status or the finished mp4 of a video task created earlier by generate_video.

        FREE: it only polls the provider and downloads the result, it never
        starts a new (paid) generation. Use it whenever generate_video returned
        "still processing" or the download failed.

        Args:
            task_id: the task id from the generate_video report.
            wait_sec: how long to wait for completion in this call, 0..90 s
                (default 30). If still not ready, call again later.

        Returns the local mp4 path + obs_key when done, otherwise the status.
        """
        chat_id = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))
        tid = (task_id or "").strip()
        if not tid:
            return "check_video_status error: task_id is empty."
        job = await media_jobs.get_job(chat_id, tid)
        if job is None:
            return (f"check_video_status error: task {tid} not found in this chat "
                    f"(unknown id or older than 24 h). If the video was delivered, "
                    f"try fetch_from_obs.")
        if job.get("status") in ("done", "failed"):
            return await _job_report(agent, chat_id, job, None)
        api_key, key_err = await resolve_router_api_key(agent)
        if key_err:
            return f"check_video_status error: {key_err}"
        if job.get("status") == "error":
            job["status"] = "processing"  # провайдер уже готов — повторяем только скачивание
        try:
            w = max(0, min(int(wait_sec), CHECK_WAIT_MAX_SEC))
        except (TypeError, ValueError):
            w = 30
        job = await _wait_job(chat_id, job, api_key, w)
        return f"Video task {tid} ({job.get('summary', '')}). " + await _job_report(
            agent, chat_id, job, api_key)

    return check_video_status
