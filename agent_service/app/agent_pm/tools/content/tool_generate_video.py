"""
tools/content/tool_generate_video.py
------------------------------------
Admin-only video generation via RouterAPI (routerai.ru).

RouterAPI is a SEPARATE paid provider (different base_url + auth token). Video
generation is ASYNCHRONOUS: POST /v1/videos creates a task and returns an id +
polling_url; we then poll GET /v1/videos/{id} until the task is ready and fetch
the resulting mp4 via GET /v1/videos/{id}/content.

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


def make_generate_video_tool(agent):
    """Factory: builds the admin-only generate_video langchain tool."""

    @tool
    async def generate_video(
        prompt: str,
        aspect_ratio: str = "16:9",
        duration: int = 5,
        resolution: str = "480p",
        first_frame_image_url: str | None = None,
        reference_image_urls: list[str] | None = None,
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
            first_frame_image_url: Optional image URL (or data URI) used as the
                first frame for image-to-video.
            reference_image_urls: Optional list of image URLs (or data URIs) used
                as style/character/object references.
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

        # ── validate / clamp params ──────────────────────────────────────
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
        if first_frame_image_url and str(first_frame_image_url).strip():
            data["frame_images"] = [{
                "type": "image_url",
                "image_url": {"url": str(first_frame_image_url).strip()},
                "frame_type": "first_frame",
            }]
        refs = _coerce_url_list(reference_image_urls)
        if refs:
            data["input_references"] = [
                {"type": "image_url", "image_url": {"url": u}} for u in refs
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
                direct_url = (last.get("url") or last.get("video_url")
                              or (last.get("output") or {}).get("url")
                              if isinstance(last.get("output"), dict) else last.get("url"))
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
