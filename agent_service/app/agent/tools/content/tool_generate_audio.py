"""
tools/content/tool_generate_audio.py
------------------------------------
Admin-only text-to-speech (TTS) via RouterAPI (routerai.ru).

RouterAPI is a SEPARATE paid provider (different base_url + auth token). Speech
synthesis uses the OpenAI-compatible endpoint POST /v1/audio/speech. The TTS
model (google/gemini-3.8-flash-tts) only returns RAW PCM (24 kHz, 16-bit, mono).
Raw PCM is not directly playable and Telegram sendVoice requires OGG/Opus, so
this tool ALWAYS requests pcm from the API and then transcodes it in-memory to
OGG (Opus) via ffmpeg (stdin -> stdout, no temp files). The resulting .ogg is a
native Telegram voice message (shows an audio waveform).

Supports a free-form `style` (speech_metadata.style) controlling tone/pace/emotion
for the whole utterance, plus inline tags inside `text` like <laugh>, <sigh>,
<short pause> for non-verbal sounds and pauses.

The produced .ogg is written to /app/generated_files so the transport can hand
it to the user (send_file_to_user).
"""

import os
import uuid
import json
import base64
import logging
import subprocess

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
DEFAULT_VOICE = "Algenib"

# Gemini TTS raw PCM parameters (fixed by the model).
PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1


def _pcm_to_ogg_opus(pcm_data: bytes) -> bytes:
    """Transcode raw PCM (24kHz, 16-bit signed LE, mono) to OGG/Opus in memory.

    Uses ffmpeg via subprocess (stdin -> stdout), no temp files on disk.
    Raises RuntimeError if ffmpeg is missing or fails.
    """
    command = [
        "ffmpeg",
        "-f", "s16le",
        "-ar", str(PCM_SAMPLE_RATE),
        "-ac", str(PCM_CHANNELS),
        "-i", "pipe:0",
        "-c:a", "libopus",
        "-b:a", "32k",
        "-f", "ogg",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found in the container. Add 'apt-get install -y ffmpeg' "
            "to agent_service Dockerfile and rebuild."
        )
    stdout, stderr = proc.communicate(input=pcm_data)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {stderr.decode('utf-8', 'ignore')[:300]}")
    return stdout


async def audio_generator(text: str, voice: str = DEFAULT_VOICE, style: str = ""):
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

    # Gemini TTS only returns raw PCM -> always request pcm, transcode later.
    payload = {
        "model": model,
        "voice": v,
        "input": text.strip(),
        "response_format": "pcm",
    }
    if (style or "").strip():
        payload["provider"] = {
            "options": {
                "google-ai-studio": {
                    "speech_metadata": {"style": style.strip()},
                },
            },
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
                return {"result": f"generate_audio error: HTTP {r.status_code}: {r.text[:300]}"}
            pcm_bytes = r.content
            if not pcm_bytes:
                return {"result": "generate_audio error: empty audio response."}
    except httpx.HTTPError as e:
        return {"result": f"generate_audio error: network failure: {e}"}
    except Exception as e:
        log.exception("generate_audio request error")
        return {"result": f"generate_audio error: {e}"}

    try:
        ogg_bytes = _pcm_to_ogg_opus(pcm_bytes)
    except RuntimeError as e:
        return {"result": f"generate_audio error: transcode failed: {e}"}
    if not ogg_bytes:
        return {"result": "generate_audio error: transcode produced empty output."}

    os.makedirs(GENERATED_ROOT, exist_ok=True)
    fname = f"voice_{uuid.uuid4().hex[:12]}.ogg"
    fpath = os.path.join(GENERATED_ROOT, fname)
    with open(fpath, "wb") as f:
        f.write(ogg_bytes)

    try:
        pend = getattr(agent, "_pending_generated_files", None)
        if isinstance(pend, list):
            pend.append(fpath)
    except Exception:
        pass

    size_kb = len(ogg_bytes) // 1024
    style_note = f", style='{style.strip()}'" if (style or "").strip() else ""
    return {"result": None, "voice": v, "style_note": style_note, "fpath": fpath, "size_kb": size_kb}


def make_generate_audio_tool(agent):
    """Factory: builds the admin-only generate_audio langchain tool."""

    @tool
    async def generate_audio(
        text: str,
        voice: str = DEFAULT_VOICE,
        style: str = "",
    ) -> str:
        """Synthesize speech from text via RouterAPI TTS (admin-only, paid).

        Converts the given text into a spoken voice message and saves it as an
        OGG/Opus file ready to be sent as a native Telegram voice message
        (send_file_to_user). Gemini TTS outputs raw PCM; the tool automatically
        transcodes it to OGG (Opus) via ffmpeg, so the output always plays back
        and shows an audio waveform in Telegram.

        Args:
            text: The text to speak (required).
            voice: Voice name. Default 'Algenib'. Available voices: Zephyr, Puck,
                Charon, Kore, Fenrir, Leda, Orus, Aoede, Callirrhoe, Autonoe,
                Enceladus, Iapetus, Umbriel, Algieba, Despina, Erinome, Algenib,
                Rasalgethi, Laomedeia, Achernar, Alnilam, Schedar, Gacrux,
                Pulcherrima, Achird, Zubenelgenubi, Vindemiatrix, Sadachbia,
                Sadaltager, Sulafat.
            style: Optional free-form description of the delivery manner for the
                WHOLE text: emotion, pace, tone, accent. Any language. Examples:
                "calm and relaxed, like an audiobook narrator", "whispered",
                "cheerful and friendly", "sad and slow", "bored and monotone",
                "excited", "out of breath", "like a news anchor". Leave empty for
                the model default. Maps to speech_metadata.style.

        INLINE TAGS (optional): you may embed non-verbal sounds and pauses
        directly inside `text` at the exact position they should occur, using
        angle brackets: <laugh>, <chuckle>, <sigh>, <gasp>, <breath>, <pant>,
        <cough>, <throat-clearing>, <short pause>, <long pause>. Example:
        "Well... <sigh> I guess you were right. <laugh> That is hilarious!"

        If you need multi-character dialogue or mixed delivery, generate the parts in separate 
        calls and concatenate the resulting audio files into a single voice message with ffmpeg.

        Returns a human-readable status with the local .ogg path on success, or
        a clear error message on failure.
        """
        voice_meta = await audio_generator(text, voice, style)
        if not voice_meta["result"]:
            return (f"Voice message generated (voice={voice_meta['voice']}{voice_meta['style_note']}, OGG/Opus). "
                    f"Saved to {voice_meta['fpath']} ({voice_meta['size_kb']} KB). Send it with send_file_to_user.")
        else:
            return voice_meta["result"]

    return generate_audio


# ══════════════════════════════════════════════════════════════════════════
# MUSIC GENERATION (text -> instrumental mp3) via RouterAPI google/lyria-3-pro
# ══════════════════════════════════════════════════════════════════════════
# Unlike TTS, music uses the STREAMING chat.completions endpoint with
# audio={"format":"mp3"}. The server emits SSE chunks; each chunk carries a
# base64-encoded mp3 fragment at delta.audio.data. We concatenate all base64
# strings, decode once and write a ready-to-play .mp3 (no ffmpeg transcode).

async def music_generator(prompt: str):
    """Call RouterAPI Lyria, stream mp3 chunks, return (mp3_bytes | error_str)."""
    cfg = get_router_api_config()
    api_key = cfg.get("api_key") or ""
    base_url = (cfg.get("base_url") or "").rstrip("/")
    model = cfg.get("music_model")
    if not api_key:
        return ("generate_music error: RouterAPI key is not configured "
                "(ROUTER_API_KEY). Ask the admin to set it in env.")
    if not model:
        return "generate_music error: music_model is not configured."
    if not (prompt or "").strip():
        return "generate_music error: prompt is empty."

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt.strip()}],
        "audio": {"format": "mp3"},
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    b64_parts: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions",
                headers=headers, json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    return (f"generate_music error: HTTP {resp.status_code}: "
                            f"{body[:300]}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    audio = delta.get("audio")
                    if isinstance(audio, dict) and audio.get("data"):
                        b64_parts.append(audio["data"])
    except httpx.HTTPError as e:
        return f"generate_music error: network failure: {e}"
    except Exception as e:
        log.exception("generate_music request error")
        return f"generate_music error: {e}"

    if not b64_parts:
        return "generate_music error: empty audio response (no chunks)."
    try:
        mp3_bytes = base64.b64decode("".join(b64_parts))
    except Exception as e:
        return f"generate_music error: base64 decode failed: {e}"
    if not mp3_bytes:
        return "generate_music error: decoded audio is empty."
    return mp3_bytes


def make_generate_music_tool(agent):
    """Factory: builds the admin-only generate_music langchain tool."""

    @tool
    async def generate_music(prompt: str) -> str:
        """Generate instrumental/background MUSIC from a text prompt via RouterAPI (admin-only, paid).

        Uses google/lyria-3-pro-preview. Produces an mp3 track from a free-form
        description of the desired music and saves it for delivery to the user
        (send_file_to_user). This is for MUSIC/soundscapes, NOT speech — for
        spoken voice use generate_audio instead.

        Args:
            prompt: Free-form description of the music to compose. Describe genre,
                mood, instruments, tempo, energy. Any language. Examples:
                "спокойная музыка в стиле ambient", "энергичный synthwave с
                драйвовым басом", "lo-fi hip-hop beat for studying, mellow piano",
                "epic orchestral trailer music, building to a climax".

        Returns a human-readable status with the local .mp3 path on success, or
        a clear error message on failure.
        """
        result = await music_generator(prompt)
        if isinstance(result, str):
            # error path (generator returns an error string)
            return result

        mp3_bytes = result
        os.makedirs(GENERATED_ROOT, exist_ok=True)
        fname = f"music_{uuid.uuid4().hex[:12]}.mp3"
        fpath = os.path.join(GENERATED_ROOT, fname)
        with open(fpath, "wb") as f:
            f.write(mp3_bytes)

        try:
            pend = getattr(agent, "_pending_generated_files", None)
            if isinstance(pend, list):
                pend.append(fpath)
        except Exception:
            pass

        size_kb = len(mp3_bytes) // 1024
        return (f"Music generated (mp3). Saved to {fpath} ({size_kb} KB). "
                f"Send it with send_file_to_user.")

    return generate_music
