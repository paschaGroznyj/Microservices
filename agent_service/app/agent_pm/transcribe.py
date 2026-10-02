"""Распознавание речи (ASR) для голосовых сообщений из Telegram.

Два провайдера, переключаются аргументом provider=:
  - "cloud_ru"  (дефолт): foundation-models.api.cloud.ru, openai/whisper-large-v3
  - "routerapi":          routerai.ru, microsoft/mai-transcribe-2

Оба эндпоинта OpenAI-совместимые POST /audio/transcriptions (multipart/form-data).
Telegram отдаёт голосовые в opus-in-ogg (.oga). Сначала шлём ogg как есть;
если провайдер отбивает по формату — фоллбэк: ffmpeg ogg->mp3 и повтор.
"""
import asyncio
import logging

import httpx

from .core.config import (
    CLOUD_LLM_ACCESS_KEY,
    ROUTER_API_KEY,
    ROUTER_API_BASE_URL,
)

log = logging.getLogger("transcribe")

CLOUD_RU_BASE_URL = "https://foundation-models.api.cloud.ru/v1"

# provider -> (base_url, api_key_getter, model)
_PROVIDERS = {
    "cloud_ru": {
        "base_url": CLOUD_RU_BASE_URL,
        "model": "openai/whisper-large-v3",
        "response_format": "text",
    },
    "routerapi": {
        "base_url": (ROUTER_API_BASE_URL or "https://routerai.ru/api/v1"),
        "model": "microsoft/mai-transcribe-2",
        # mai-transcribe-2 НЕ принимает response_format=text, только json/verbose_json
        "response_format": "json",
    },
}

DEFAULT_PROVIDER = "cloud_ru"


def _api_key(provider: str) -> str:
    if provider == "routerapi":
        return ROUTER_API_KEY or ""
    return CLOUD_LLM_ACCESS_KEY or ""


async def _ffmpeg_ogg_to_mp3(audio_bytes: bytes) -> bytes:
    """Конвертация ogg/opus -> mp3 через ffmpeg (stdin->stdout, без temp-файлов)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0", "-f", "mp3", "-ar", "16000", "-ac", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(input=audio_bytes)
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ffmpeg ogg->mp3 failed: {err.decode('utf-8','ignore')[:300]}")
    return out


async def _post_transcription(
    *, base_url: str, api_key: str, model: str, response_format: str,
    audio_bytes: bytes, filename: str, mime: str, language: str,
) -> httpx.Response:
    files = {"file": (filename, audio_bytes, mime)}
    data = {
        "model": model,
        "response_format": response_format,
        "language": language,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        return await client.post(
            f"{base_url.rstrip('/')}/audio/transcriptions",
            headers=headers, data=data, files=files,
        )


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    provider: str = DEFAULT_PROVIDER,
    language: str = "ru",
    fmt: str = "ogg",
) -> str:
    """Распознать речь. Возвращает текст или кидает RuntimeError.

    provider: "cloud_ru" | "routerapi" (переключение аргументом).
    """
    provider = (provider or DEFAULT_PROVIDER).lower()
    cfg = _PROVIDERS.get(provider)
    if not cfg:
        raise ValueError(f"unknown ASR provider: {provider!r}; allowed: {list(_PROVIDERS)}")
    api_key = _api_key(provider)
    if not api_key:
        raise RuntimeError(f"ASR provider {provider!r}: API key is not configured")

    base_url, model = cfg["base_url"], cfg["model"]
    response_format = cfg.get("response_format", "text")

    # Попытка 1: ogg как есть (Telegram opus-in-ogg).
    mime = "audio/ogg" if fmt in ("ogg", "oga", "opus") else f"audio/{fmt}"
    fname = f"audio.{fmt}"
    try:
        r = await _post_transcription(
            base_url=base_url, api_key=api_key, model=model,
            response_format=response_format,
            audio_bytes=audio_bytes, filename=fname, mime=mime, language=language,
        )
        if r.status_code < 400:
            return _extract_text(r)
        # Возможно провайдер не любит ogg — пробуем фоллбэк.
        log.warning(f"[asr] {provider} ogg HTTP {r.status_code}: {r.text[:200]} — пробую ffmpeg->mp3")
    except httpx.HTTPError as e:
        log.warning(f"[asr] {provider} ogg network error: {e} — пробую ffmpeg->mp3")

    # Попытка 2 (фоллбэк): ffmpeg ogg->mp3, повторный запрос.
    mp3 = await _ffmpeg_ogg_to_mp3(audio_bytes)
    r = await _post_transcription(
        base_url=base_url, api_key=api_key, model=model,
        response_format=response_format,
        audio_bytes=mp3, filename="audio.mp3", mime="audio/mpeg", language=language,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"ASR {provider} HTTP {r.status_code}: {r.text[:300]}")
    return _extract_text(r)


def _extract_text(r: httpx.Response) -> str:
    """response_format=text — обычно plain text; но на всякий случай разбираем JSON."""
    ctype = r.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            j = r.json()
            return (j.get("text") or j.get("transcript") or "").strip()
        except Exception:
            pass
    return (r.text or "").strip()
