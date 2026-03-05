from dotenv import load_dotenv
import os

load_dotenv()

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
PORT_TOKEN_GENERATE_SERVICE =os.getenv("PORT_TOKEN_GENERATE_SERVICE")
TOKEN_TTL = 20  # 26 минут 40 секунд (запас до 30 мин)