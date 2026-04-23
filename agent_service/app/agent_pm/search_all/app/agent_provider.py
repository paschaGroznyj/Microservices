from functools import lru_cache
from langchain.chat_models import GigaChat
from langchain_gigachat.embeddings import GigaChatEmbeddings


@lru_cache
def get_model():
    return GigaChat(
        credentials="none",
        auth_url="http://localhost:9004/api/v2/oauth", # Данный адрес контейнера является единственным источником истины для получения токена внутри ВМ cloud
        verify_ssl_certs=False,
        model="GigaChat-2-Max"
    )

@lru_cache
def get_embedd_model():
    return GigaChatEmbeddings(
        credentials="somebody_once_told_me",
        auth_url="http://host.docker.internal:9004/api/v2/oauth",
        verify_ssl_certs=False,
        model="Embeddings-2"
    )

# ================== ДЛЯ ЛОКАЛЬНОЙ ОТЛАДКИ ================
# auth_url меняется на http://host.docker.internal:9004/api/v2/oauth при работе с докером
# auth_url меняется на http://localhost:9004/api/v2/oauth при работе без докера
# Условие по созданию проброса на порт 9004, должно быть выполнено