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

ALLOWED_ASPECT = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
ALLOWED_RESOLUTION = {"480p", "768p"}
ALLOWED_DURATION = {5, 6, 7, 8, 9, 10}  # 11..15 intentionally excluded (cap 10s)

# Polling parameters for the async video task.
POLL_INTERVAL_SEC = 6
POLL_TIMEOUT_SEC = 600  # 10 min hard ceiling


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


def make_generate_video_tool(agent, images: list[dict] | None = None):
    """Factory: builds the admin-only generate_video langchain tool.

    images: the user-attached images of the current request (same list passed to
    analyze_image / generate_image): [{filename, mime_type, b64}]. Used so the
    video tool can reference an image the user just uploaded by its index.
    """

    @tool
    async def generate_video(
        prompt: str,
        aspect_ratio: str = "16:9",
        duration: int = 5,
        resolution: str = "480p",
        first_frame_image_url: str | None = None,
        first_frame_path: str | None = None,
        first_frame_image_key: str | None = None,
        first_frame_user_image_index: int | None = None,
        first_frame_media_id: str | None = None,
        reference_image_urls: list[str] | None = None,
        reference_image_keys: list[str] | None = None,
        reference_user_image_indexes: list[int] | None = None,
        reference_media_ids: list[str] | None = None,
        generate_audio: bool = False,
        seed: int | None = None,
    ) -> str:
        """Generate a short video from a text prompt via RouterAPI (admin-only, paid).

        Asynchronous: creates a generation task, polls until ready and saves the
        resulting mp4 for delivery to the user. Use short, cinematic prompts.

        Args:
            prompt: Text description of the scene to animate (required).
            aspect_ratio: One of 21:9, 16:9, 4:3, 1:1, 3:4, 9:16. Default 16:9.
            duration: Clip length in seconds, 5..10 (values above 10 are clamped
                to 10). Default 5.
            resolution: 480p or 768p. Default 480p (cheaper).

            First frame (image-to-video). Provide AT MOST ONE of these; they are
            tried in this priority order:
              first_frame_image_url: a public image URL or a data URI.
              first_frame_path: a local path OR file name inside the allowed
                roots (/sandbox, /app/generated_files).
              first_frame_image_key: an image_key of a previously GENERATED
                image (generated_images table).
              first_frame_user_image_index: index of an image the USER attached
                in the current request (0 = first).
              first_frame_media_id: a media_id from the per-chat MEDIA STORAGE
                (cache of recent images in this chat).

            References (character / style / object). Each list is resolved and
            all resolved images are sent together:
              reference_image_urls: list of image URLs or data URIs.
              reference_image_keys: list of generated_images image_keys.
              reference_user_image_indexes: indexes of user-attached images.
              reference_media_ids: list of media_ids from MEDIA STORAGE.

            generate_audio: Whether to also generate an audio track. Default False.
            seed: Optional integer for deterministic generation.

        Returns a human-readable status with the local mp4 path on success, or a
        clear error message on failure.
        """
        cfg = get_router_api_config()
        api_key = cfg.get("api_key") or ""
        base_url = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("video_model")
        if not api_key:
            return ("generate_video error: RouterAPI key is not configured "
                    "(ROUTER_API_KEY). Ask the admin to set it in env.")
        if not (prompt or "").strip():
            return "generate_video error: prompt is empty."

        chat_id = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))

        # -- validate / clamp params --------------------------------------
        ar = aspect_ratio if aspect_ratio in ALLOWED_ASPECT else "16:9"
        res = resolution if resolution in ALLOWED_RESOLUTION else "480p"
        try:
            dur = int(duration)
        except (TypeError, ValueError):
            dur = 5
        if dur > MAX_DURATION_SEC:
            dur = MAX_DURATION_SEC
        if dur < 5:
            dur = 5
        if dur not in ALLOWED_DURATION:
            dur = 5

        # -- resolve first frame (at most one, priority order) ------------
        first_frame_uri = None
        if first_frame_image_url and str(first_frame_image_url).strip():
            first_frame_uri = str(first_frame_image_url).strip()
        elif first_frame_path and str(first_frame_path).strip():
            first_frame_uri = resolve_path_to_data_uri(str(first_frame_path).strip())
        elif first_frame_image_key and str(first_frame_image_key).strip():
            first_frame_uri = await resolve_image_key_to_data_uri(
                agent, str(first_frame_image_key).strip())
        elif first_frame_user_image_index is not None:
            try:
                first_frame_uri = resolve_user_image_to_data_uri(
                    images, int(first_frame_user_image_index))
            except (TypeError, ValueError):
                first_frame_uri = None
        elif first_frame_media_id and str(first_frame_media_id).strip():
            first_frame_uri = await resolve_media_id_to_data_uri(
                agent, chat_id, str(first_frame_media_id).strip())

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

        data = {
            "model": model,
            "prompt": prompt.strip(),
            "aspect_ratio": ar,
            "duration": dur,
            "resolution": res,
            "generate_audio": bool(generate_audio),
        }
        if seed is not None:
            try:
                data["seed"] = int(seed)
            except (TypeError, ValueError):
                pass
        if first_frame_uri:
            data["frame_images"] = [{
                "type": "image_url",
                "image_url": {"url": first_frame_uri},
                "frame_type": "first_frame",
            }]
        if reference_uris:
            data["input_references"] = [
                {"type": "image_url", "image_url": {"url": u}} for u in reference_uris
            ]

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                # 1) create task
                r = await client.post(f"{base_url}/videos", headers=headers, json=data)
                if r.status_code >= 400:
                    return (f"generate_video error: create task failed "
                            f"HTTP {r.status_code}: {r.text[:300]}")
                task = r.json()
                task_id = task.get("id")
                polling_url = task.get("polling_url")
                if not task_id:
                    return f"generate_video error: no task id in response: {str(task)[:300]}"

                # 2) poll until ready
                poll_endpoint = polling_url or f"{base_url}/videos/{task_id}"
                deadline = time.monotonic() + POLL_TIMEOUT_SEC
                status = None
                last = {}
                while time.monotonic() < deadline:
                    await asyncio.sleep(POLL_INTERVAL_SEC)
                    pr = await client.get(poll_endpoint, headers=headers)
                    if pr.status_code >= 400:
                        return (f"generate_video error: poll failed "
                                f"HTTP {pr.status_code}: {pr.text[:300]}")
                    last = pr.json()
                    status = (last.get("status") or "").lower()
                    if status in ("completed", "succeeded", "success", "ready", "done"):
                        break
                    if status in ("failed", "error", "canceled", "cancelled"):
                        return (f"generate_video error: task {status}: "
                                f"{str(last)[:300]}")
                else:
                    return (f"generate_video error: timed out after "
                            f"{POLL_TIMEOUT_SEC}s (last status={status}).")

                # 3) download mp4 content
                os.makedirs(GENERATED_ROOT, exist_ok=True)
                fname = f"video_{uuid.uuid4().hex[:12]}.mp4"
                fpath = os.path.join(GENERATED_ROOT, fname)

                # Prefer a direct url if the API returned one; else /content.
                out = last.get("output")
                direct_url = (last.get("url") or last.get("video_url")
                              or (out.get("url") if isinstance(out, dict) else None))
                content_resp = None
                if direct_url:
                    content_resp = await client.get(direct_url, timeout=httpx.Timeout(120.0))
                if content_resp is None or content_resp.status_code >= 400:
                    content_resp = await client.get(
                        f"{base_url}/videos/{task_id}/content",
                        headers=headers, timeout=httpx.Timeout(120.0),
                    )
                if content_resp.status_code >= 400:
                    return (f"generate_video error: content download failed "
                            f"HTTP {content_resp.status_code}: {content_resp.text[:200]}")
                with open(fpath, "wb") as f:
                    f.write(content_resp.content)

        except httpx.HTTPError as e:
            return f"generate_video error: network failure: {e}"
        except Exception as e:
            log.exception("generate_video unexpected error")
            return f"generate_video error: {e}"

        # Register for auto-delivery if the agent supports a pending list.
        try:
            pend = getattr(agent, "_pending_generated_files", None)
            if isinstance(pend, list):
                pend.append(fpath)
        except Exception:
            pass

        size_kb = os.path.getsize(fpath) // 1024
        return (f"Video generated ({dur}s, {ar}, {res}). Saved to {fpath} "
                f"({size_kb} KB). Send it with send_file_to_user.")

    return generate_video
