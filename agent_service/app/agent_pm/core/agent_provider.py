from functools import lru_cache
from langchain_gigachat.embeddings import GigaChatEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from .config import CLOUD_LLM_ACCESS_KEY


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
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )

@lru_cache
def get_llm_model_gpt():
    return ChatOpenAI(
        model="openai/gpt-5.3-codex",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )

@lru_cache
def get_llm_model_claude():
    return ChatOpenAI(
        model="anthropic/claude-opus-4.8",
        base_url="https://foundation-models.api.cloud.ru/v1",
        api_key=CLOUD_LLM_ACCESS_KEY,
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
        model_kwargs={
            "extra_headers": {
                "X-Enable-Prompt-Cache": "true"
            }
        }
    )

# ================== ДЛЯ ЛОКАЛЬНОЙ ОТЛАДКИ ================
# auth_url меняется на http://host.docker.internal:9004/api/v2/oauth при работе с докером
# auth_url меняется на http://localhost:9004/api/v2/oauth при работе без докера
# Условие по созданию проброса на порт 9004, должно быть выполнено