import os
import re
import base64
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Optional, List, Tuple
from urllib.parse import urlparse, parse_qs

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image

BASE_URL = os.getenv("CLOUD_LLM_BASE", "https://foundation-models.api.cloud.ru/v1").rstrip("/")
API_KEY = os.getenv("CLOUD_LLM_ACCESS_KEY", "") or os.getenv("IMAGE_API_KEY", "")

app = FastAPI(title="video-engine")


class RenderReq(BaseModel):
    prompt: str = (
        "Cinematic battle scene between Optimus Prime and Bumblebee in an epic ruined city, "
        "high energy action, realistic motion, dramatic light."
    )
    model: str = "google/gemini-3.1-flash-image-preview"
    seconds: int = 5
    frames: int = 10
    temperature: float = 0.35
    aspect_ratio: str = "16:9"
    seed: Optional[int] = 777
    style_image_url: Optional[str] = None
    output_name: str = "transformers_battle_5s_10f.mp4"
    max_motion_refs: int = 10


def _decode_data_uri(data_uri: str) -> bytes:
    m = re.match(r"^data:[^;]+;base64,(.+)$", data_uri, re.DOTALL)
    if not m:
        raise ValueError("bad data uri")
    return base64.b64decode(m.group(1))


def _extract_data_uri(resp_json: dict) -> str:
    msg = ((resp_json.get("choices") or [{}])[0].get("message") or {})
    imgs = msg.get("images") or []
    if imgs:
        url = ((imgs[0].get("image_url") or {}).get("url") or "")
        if url.startswith("data:"):
            return url
    content = msg.get("content")
    if isinstance(content, str):
        mm = re.search(r"data:image/\w+;base64,[A-Za-z0-9+/=\n\r]+", content)
        if mm:
            return mm.group(0)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                txt = block.get("text") or ""
                if isinstance(txt, str):
                    mm = re.search(r"data:image/\w+;base64,[A-Za-z0-9+/=\n\r]+", txt)
                    if mm:
                        return mm.group(0)
    raise ValueError("image not found in response")


def _to_data_uri(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _extract_gdrive_file_id(url: str) -> Optional[str]:
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    if "id" in q and q["id"]:
        return q["id"][0]
    return None


def _download_style_image(style_url: str, target_path: Path) -> None:
    file_id = _extract_gdrive_file_id(style_url)
    if file_id:
        dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    else:
        dl_url = style_url

    s = requests.Session()
    r = s.get(dl_url, timeout=120, allow_redirects=True)
    r.raise_for_status()

    ctype = (r.headers.get("content-type") or "").lower()
    content = r.content
    if "text/html" in ctype:
        text = r.text
        m = re.search(r"confirm=([0-9A-Za-z_]+)", text)
        if m and file_id:
            confirm = m.group(1)
            r2 = s.get(
                f"https://drive.google.com/uc?export=download&confirm={confirm}&id={file_id}",
                timeout=120,
                allow_redirects=True,
            )
            r2.raise_for_status()
            content = r2.content
        else:
            raise HTTPException(400, "Не удалось скачать style image: Google Drive вернул HTML")

    if len(content) < 1024:
        raise HTTPException(400, "Style image слишком маленький или пустой")

    target_path.write_bytes(content)


def _target_resolution(aspect_ratio: str) -> Tuple[int, int]:
    # Фиксированный target для полной консистентности видео
    if aspect_ratio == "16:9":
        return (1364, 768)
    if aspect_ratio == "9:16":
        return (768, 1364)
    if aspect_ratio == "1:1":
        return (1024, 1024)
    if aspect_ratio == "4:3":
        return (1364, 1024)
    if aspect_ratio == "3:4":
        return (1024, 1364)
    return (1364, 768)


def _fit_cover(img: Image.Image, target: Tuple[int, int]) -> Image.Image:
    tw, th = target
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale), int(sh * scale)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max((nw - tw) // 2, 0)
    top = max((nh - th) // 2, 0)
    return img.crop((left, top, left + tw, top + th))


def _normalize_frame(raw: bytes, target: Tuple[int, int]) -> bytes:
    img = Image.open(BytesIO(raw)).convert("RGB")
    img = _fit_cover(img, target)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@app.get("/health")
def health():
    return {"ok": True, "base": BASE_URL, "has_key": bool(API_KEY)}


@app.post("/render")
def render(req: RenderReq):
    if req.frames <= 1:
        raise HTTPException(400, "frames must be > 1")

    out_dir = Path("/app/output")
    frames_dir = out_dir / "frames"
    style_path = out_dir / "style_template.jpg"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    for old in frames_dir.glob("frame_*.jpg"):
        try:
            old.unlink()
        except Exception:
            pass

    if req.style_image_url:
        _download_style_image(req.style_image_url, style_path)
    else:
        raise HTTPException(400, "Нужен style_image_url")

    target_size = _target_resolution(req.aspect_ratio)
    style_raw = _normalize_frame(style_path.read_bytes(), target_size)

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    fps = req.frames / float(req.seconds)

    beats = [
        "Establishing frame: Optimus Prime and Bumblebee face each other before impact.",
        "Bumblebee shifts stance, Optimus steps in aggressively.",
        "Optimus starts right-hand swing; Bumblebee braces defensively.",
        "Impact moment builds: sparks and debris, strong motion cues.",
        "Optimus right hand clearly lands a heavy strike.",
        "After impact recoil: both keep same identities and armor details.",
        "Bumblebee counter-moves; dynamic camera angle slightly changes.",
        "Optimus pushes forward, dust and fragments in air.",
        "Close combat continuation with clear limb motion and consistency.",
        "Final cinematic frame: both in powerful post-impact positions.",
    ]

    motion_history: List[bytes] = []
    max_refs = max(1, min(int(req.max_motion_refs), 10))

    for i in range(req.frames):
        beat = beats[i] if i < len(beats) else (
            f"Frame {i+1}: continue battle action with consistent identities and armor, "
            "Optimus attacking with right hand emphasis."
        )

        user_content = [
            {
                "type": "text",
                "text": (
                    f"{req.prompt}\n\n{beat}\n"
                    "STYLE LOCK: Keep visual style, color palette, armor materials, and character identity aligned with the style template image. "
                    "MOTION RULE: Continue movement from the latest motion frame while preserving scene continuity. "
                    "No text, no watermark, no logo."
                )
            },
            {"type": "text", "text": "Style reference (must be preserved across all frames):"},
            {"type": "image_url", "image_url": {"url": _to_data_uri(style_raw)}},
        ]

        if motion_history:
            refs = motion_history[-max_refs:]
            user_content.append({"type": "text", "text": f"Motion references ({len(refs)} frames, chronological):"})
            for idx, fr in enumerate(refs, start=1):
                user_content.append({"type": "text", "text": f"Motion ref #{idx}"})
                user_content.append({"type": "image_url", "image_url": {"url": _to_data_uri(fr)}})
            user_content.append({"type": "text", "text": "Primary motion continuation source (latest frame):"})
            user_content.append({"type": "image_url", "image_url": {"url": _to_data_uri(motion_history[-1])}})

        payload = {
            "model": req.model,
            "temperature": req.temperature,
            "top_p": 0.9,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a master cinematographer for photorealistic action sequences. "
                        "Preserve style from style reference in every frame. "
                        "Preserve identities of Optimus Prime and Bumblebee. "
                        "Optimus strikes with his right hand during key beats."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "extra_body": {"aspect_ratio": req.aspect_ratio},
        }
        if req.seed is not None:
            payload["seed"] = int(req.seed + i)

        r = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=240)
        if r.status_code != 200:
            raise HTTPException(502, f"frame {i+1} failed: HTTP {r.status_code} {r.text[:300]}")

        data_uri = _extract_data_uri(r.json())
        raw = _decode_data_uri(data_uri)
        raw = _normalize_frame(raw, target_size)

        frame_path = frames_dir / f"frame_{i:03d}.jpg"
        frame_path.write_bytes(raw)

        motion_history.append(raw)
        if len(motion_history) > max_refs:
            motion_history = motion_history[-max_refs:]

    out_mp4 = out_dir / req.output_name
    cmd = [
        "ffmpeg", "-y",
        "-framerate", f"{fps}",
        "-i", str(frames_dir / "frame_%03d.jpg"),
        "-c:v", "libx264",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg failed: {proc.stderr[-700:]}")

    return {
        "ok": True,
        "video": str(out_mp4),
        "frames": req.frames,
        "seconds": req.seconds,
        "fps": fps,
        "model": req.model,
        "style_image_url": req.style_image_url,
        "target_resolution": {"width": target_size[0], "height": target_size[1]},
        "max_motion_refs": max_refs,
    }
