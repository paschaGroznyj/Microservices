from functools import lru_cache
from langchain.chat_models import GigaChat
from langchain_gigachat.embeddings import GigaChatEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from .config import CLOUD_LLM_ACCESS_KEY


@lru_cache
def get_model(temperature: float = 0.5):
    return GigaChat(
        credentials="none",
        auth_url="http://token_service:9004/api/v2/oauth", # Данный адрес контейнера является единственным источником истины для получения токена внутри ВМ cloud
        verify_ssl_certs=False,
        model="GigaChat-2-Max",
        temperature=temperature
    )

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
def get_async_gemini():
    return ChatOpenAI(
        model="deepseek-ai/DeepSeek-V4-Pro",
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