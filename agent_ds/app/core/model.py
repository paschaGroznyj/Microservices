from functools import lru_cache
from langchain_community.chat_models import GigaChat


@lru_cache
def get_model():
    return GigaChat(
        credentials="none",
        auth_url="http://token_service:9004/api/v2/oauth", # Данный адрес контейнера является единственным источником истины для получения токена внутри ВМ cloud
        verify_ssl_certs=False,
        model="GigaChat-2-Max"
    )

# ================== ДЛЯ ЛОКАЛЬНОЙ ОТЛАДКИ ================
# auth_url меняется на http://host.docker.internal:9004/api/v2/oauth при работе с докером
# auth_url меняется на http://localhost:9004/api/v2/oauth при работе без докера
# Условие по созданию проброса на порт 9004, должно быть выполнено