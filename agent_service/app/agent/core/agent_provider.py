from functools import lru_cache
from langchain_gigachat.embeddings import GigaChatEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from .config import CLOUD_LLM_ACCESS_KEY, ROUTER_API_KEY, ROUTER_API_BASE_URL


@lru_cache
def get_embedd_model():
    # return GigaChatEmbeddings(
    #     credentials="somebody_once_told_me",
    #     auth_url="http://token_service:9004/api/v2/oauth",
    #     verify_ssl_certs=False,
    #     model="Embeddings-2"
    # )
    return OpenAIEmbeddings(
    model="BAAI/bge-m3",
    api_key=CLOUD_LLM_ACCESS_KEY,
    base_url="https://foundation-models.api.cloud.ru/v1"
)

@lru_cache
def get_llm_lite_model():
    return ChatOpenAI(
        model="anthropic/claude-haiku-4.5",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=8192,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )


@lru_cache
def get_llm_lite_model_gpt_mini():
    """Фолбэк №1 для лайт-модели, если haiku отказал."""
    return ChatOpenAI(
        model="openai/gpt-5-mini",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=8192,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )


@lru_cache
def get_llm_lite_model_gemini_flash():
    """Фолбэк №2 для лайт-модели, если и haiku, и gpt-mini отказали."""
    return ChatOpenAI(
        model="google/gemini-3-flash-preview",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=8192,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )

@lru_cache
def get_llm_model():
    return ChatOpenAI(
        # model="openai/gpt-5.3-codex",
        model="anthropic/claude-opus-4.8",
        # model="google/gemini-3.1-pro-preview",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=16384,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )

@lru_cache
def get_llm_model_gpt():
    return ChatOpenAI(
        # model="openai/gpt-5.3-codex",
        model="openai/gpt-5.4",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=16384,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            },
            # Просим reasoning-summary у gpt-5.3: cloud.ru отдаёт его в
            # delta.reasoning (langchain кладёт в additional_kwargs["reasoning"]).
            "extra_body": {
                "reasoning_effort": "low"
            }
        }
    )

@lru_cache
def get_llm_model_claude():
    return ChatOpenAI(
        model="anthropic/claude-opus-4.8",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=16384,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )

@lru_cache
def get_llm_model_gemini():
    return ChatOpenAI(
        model="google/gemini-3.1-pro-preview",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=16384,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            },
            # Просим reasoning у gemini-3.1-pro: cloud.ru отдаёт его в
            # delta.reasoning_content (additional_kwargs["reasoning_content"]).
            "extra_body": {
                "reasoning_effort": "low"
            }
        }
    )

@lru_cache
def get_llm_model_glm():
    return ChatOpenAI(
        model="zai-org/GLM-5.2",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=16384,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            },
        }
    )

# ── Генерация изображений ──────────────────────────────────────────
# Модель и пресет подтверждены карточкой знаний #127 (HQ-baroque preset):
#   google/gemini-3.1-flash-image-preview + temperature 0.4 + quality-промпт.
# Работаем НЕ через langchain-обёртку, а сырым OpenAI-совместимым POST
# на /v1/chat/completions, т.к. картинка возвращается в
# choices[0].message.images[0].image_url.url (base64/data-URL), а
# ChatOpenAI это поле не отдаёт.
IMAGE_GEN_MODEL = "google/gemini-3.1-flash-image-preview"
IMAGE_GEN_BASE_URL = "https://foundation-models.api.cloud.ru/v1"


def get_image_gen_config() -> dict:
    """Конфиг клиента генерации изображений (endpoint + ключ + модель)."""
    return {
        "model": IMAGE_GEN_MODEL,
        "base_url": IMAGE_GEN_BASE_URL,
        "api_key": CLOUD_LLM_ACCESS_KEY,
    }

# ================== ДЛЯ ЛОКАЛЬНОЙ ОТЛАДКИ ================
# auth_url меняется на http://host.docker.internal:9004/api/v2/oauth при работе с докером
# auth_url меняется на http://localhost:9004/api/v2/oauth при работе без докера
# Условие по созданию проброса на порт 9004, должно быть выполнено


# ══ RouterAPI (routerai.ru) — media generation (video/audio) ══

# Separate PAID provider, different base_url and auth token. Used ONLY by the
# admin-only media tools (generate_video / generate_audio). We talk to it with
# raw httpx POST (OpenAI-compatible), NOT through the langchain wrapper, because
# video generation is async (task + polling) and audio returns raw binary.
ROUTER_VIDEO_MODEL = "heygen/heygen-video-1"
ROUTER_AUDIO_MODEL = "google/gemini-3.8-flash-lite-tts"
# Instrumental/background music generation (text -> mp3). Streaming
# chat.completions with audio={"format":"mp3"}; chunks arrive base64-encoded.
ROUTER_MUSIC_MODEL = "google/lyria-3-pro-preview"
# Talking-avatar video (image + voice/audio -> lipsynced mp4). Async /videos.
ROUTER_AVATAR_MODEL = "heygen/avatar-iv"


def get_router_api_config() -> dict:
    """Config for the RouterAPI media provider (base_url + key + models)."""
    return {
        "base_url": ROUTER_API_BASE_URL,
        "api_key": ROUTER_API_KEY,
        "video_model": ROUTER_VIDEO_MODEL,
        "audio_model": ROUTER_AUDIO_MODEL,
        "music_model": ROUTER_MUSIC_MODEL,
        "avatar_model": ROUTER_AVATAR_MODEL,
    }


def get_real_model_name(model_or_key) -> str:
    """Возвращает точное название модели из провайдера (например 'openai/gpt-5.4')."""
    if hasattr(model_or_key, "model_name") and getattr(model_or_key, "model_name"):
        return str(model_or_key.model_name)
    if hasattr(model_or_key, "model") and getattr(model_or_key, "model"):
        return str(model_or_key.model)
    key = str(model_or_key or "").strip()
    builders = {
        "coder": get_llm_model_gpt,
        "designer": get_llm_model_claude,
        "universal": get_llm_model_glm,
        "gemini": get_llm_model_gemini,
        "lite": get_llm_lite_model,
    }
    fn = builders.get(key)
    if fn is not None:
        llm = fn()
        return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or key)
    return key
