import os
import time
import logging
import requests
import redis
from core.config import (REDIS_HOST,
                        REDIS_PORT,
                        REDIS_KEY,
                        IAM_API_KEY,
                        PORT_TOKEN_GENERATE_SERVICE)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


TOKEN_TTL = 1800     # секунды
MIN_SAFE_TTL = 60           
CHECK_INTERVAL = TOKEN_TTL  

def get_gigachat_token() -> str:
    response = requests.post(
        url="http://10.0.4.62:2000/token",
        headers={"IAM-User-ID": IAM_API_KEY}
    )
    return response.json()['token']

def get_token_ttl(r: redis.Redis, key: str) -> int:
    """Возвращает оставшееся время жизни токена в секундах.
    -2 если ключ не существует, -1 если нет TTL."""
    return r.ttl(key)

def should_refresh_token(r: redis.Redis, key: str, min_ttl: int) -> bool:
    """Решает, нужно ли запрашивать новый токен."""
    ttl = get_token_ttl(r, key)
    
    if ttl == -2:
        logger.info("🔍 Токен в Redis не найден — требуется обновление")
        return True
    elif ttl == -1:
        logger.warning("⚠️ У токена нет TTL — принудительное обновление")
        return True
    elif ttl < min_ttl:
        logger.info(f"🔍 TTL токена ({ttl} сек) < порога ({min_ttl} сек) — обновление")
        return True
    else:
        logger.info(f"✅ Токен валиден ещё {ttl} сек — пропускаем обновление")
        return False

def main():
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        r.ping()
        logger.info("✅ Подключено к Redis")
    except redis.ConnectionError as e:
        logger.error(f"❌ Не удалось подключиться к Redis: {e}")

    while True:
        cycle_start = time.time()
        
        try:
            if should_refresh_token(r, REDIS_KEY, MIN_SAFE_TTL):
                logger.info("🔄 Запрос нового токена у GigaChat...")
                token = get_gigachat_token()
                
                # Атомарно записываем с TTL
                r.setex(REDIS_KEY, TOKEN_TTL, token)
                logger.info(f"🔑 Токен обновлен и сохранён (TTL: {TOKEN_TTL} сек)")
            else:
                # Токен валиден, просто ждём
                pass
                
        except requests.RequestException as e:
            logger.error(f"❌ Ошибка запроса к GigaChat API: {e}")
        except redis.RedisError as e:
            logger.error(f"❌ Ошибка работы с Redis: {e}")
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка: {e}")
        
        elapsed = time.time() - cycle_start
        sleep_time = max(0, CHECK_INTERVAL - elapsed)
        
        if sleep_time > 0:
            logger.debug(f"💤 Сон на {sleep_time:.0f} сек")
            time.sleep(sleep_time)

if __name__ == "__main__":
    main()