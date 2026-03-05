import os
import time
import logging
import requests
import redis
from core.config import (REDIS_HOST,
                        REDIS_PORT,
                        TOKEN_TTL,
                        IAM_API_KEY,
                        PORT_TOKEN_GENERATE_SERVICE)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)




def get_gigachat_token() -> str:
    response = requests.post(
        url="http://10.0.4.62:2000/token",
        headers={"IAM-User-ID": IAM_API_KEY}
    )
    return response.json()['token']


def main():
    # Подключение к Redis
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    logger.info("✅ Подключено к Redis")

    while True:
        try:
            token = get_gigachat_token()
            # Ключ 'gigachat:access_token', значение, время жизни (сек)
            r.setex("gigachat:access_token", TOKEN_TTL, token)
            logger.info(f"🔑 Токен обновлен (TTL: {TOKEN_TTL} сек)")
        except Exception as e:
            logger.error(f"❌ Ошибка обновления токена: {e}")

        time.sleep(TOKEN_TTL)  # Ждем перед следующим обновлением


if __name__ == "__main__":
    main()