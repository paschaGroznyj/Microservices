"""
tools/content/tool_generate_audio.py
------------------------------------
Admin-only text-to-speech (TTS) via RouterAPI (routerai.ru).

RouterAPI is a SEPARATE paid provider (different base_url + auth token). Speech
synthesis uses the OpenAI-compatible endpoint POST /v1/audio/speech and returns
raw audio bytes. We talk raw httpx (not the openai SDK) to avoid an extra
dependency and stay consistent with the image/video tools.

The produced audio file is written to /app/generated_files so the transport can
hand it to the user (send_file_to_user).
"""

import os
import uuid
import logging

import httpx
from langchain.tools import tool

from ...core.agent_provider import get_router_api_config

log = logging.getLogger(__name__)

GENERATED_ROOT = "/app/generated_files"

# Available synthesis voices (RouterAPI google/gemini-3.8-flash-tts).
AVAILABLE_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]
DEFAULT_VOICE = "Zephyr"

# Output format -> file extension. 'mp3' is the friendliest default for playback;
# 'pcm' is raw and needs a header to play, so we steer to mp3 by default.
ALLOWED_FORMATS = {"mp3", "wav", "opus", "aac", "flac", "pcm"}
FORMAT_EXT = {
    "mp3": "mp3", "wav": "wav", "opus": "opus",
    "aac": "aac", "flac": "flac", "pcm": "pcm",
}


def make_generate_audio_tool(agent):
    """Factory: builds the admin-only generate_audio langchain tool."""

    @tool
    async def generate_audio(
        text: str,
        voice: str = DEFAULT_VOICE,
        response_format: str = "mp3",
    ) -> str:
        """Synthesize speech from text via RouterAPI TTS (admin-only, paid).

        Converts the given text into spoken audio and saves the file for delivery
        to the user.

        Args:
            text: The text to speak (required).
            voice: Voice name. Default 'Zephyr'. Available voices: Zephyr, Puck,
                Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe,
                Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib,
                Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux,
                Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia,
                Sadaltager, Sulafat.
            response_format: Audio format: mp3 (default), wav, opus, aac, flac, pcm.

        Returns a human-readable status with the local audio path on success, or
        a clear error message on failure.
        """
        cfg = get_router_api_config()
        api_key = cfg.get("api_key") or ""
        base_url = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("audio_model")
        if not api_key:
            return ("generate_audio error: RouterAPI key is not configured "
                    "(ROUTER_API_KEY). Ask the admin to set it in env.")
        if not (text or "").strip():
            return "generate_audio error: text is empty."

        v = voice if voice in AVAILABLE_VOICES else DEFAULT_VOICE
        fmt = response_format if response_format in ALLOWED_FORMATS else "mp3"

        payload = {
            "model": model,
            "voice": v,
            "input": text.strip(),
            "response_format": fmt,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                r = await client.post(
                    f"{base_url}/audio/speech", headers=headers, json=payload,
                )
                if r.status_code >= 400:
                    return (f"generate_audio error: HTTP {r.status_code}: "
                            f"{r.text[:300]}")
                audio_bytes = r.content
                if not audio_bytes:
                    return "generate_audio error: empty audio response."
        except httpx.HTTPError as e:
            return f"generate_audio error: network failure: {e}"
        except Exception as e:
            log.exception("generate_audio unexpected error")
            return f"generate_audio error: {e}"

        os.makedirs(GENERATED_ROOT, exist_ok=True)
        ext = FORMAT_EXT.get(fmt, "mp3")
        fname = f"speech_{uuid.uuid4().hex[:12]}.{ext}"
        fpath = os.path.join(GENERATED_ROOT, fname)
        with open(fpath, "wb") as f:
            f.write(audio_bytes)

        try:
            pend = getattr(agent, "_pending_generated_files", None)
            if isinstance(pend, list):
                pend.append(fpath)
        except Exception:
            pass

        size_kb = len(audio_bytes) // 1024
        return (f"Audio generated (voice={v}, {fmt}). Saved to {fpath} "
                f"({size_kb} KB). Send it with send_file_to_user.")

    return generate_audio
