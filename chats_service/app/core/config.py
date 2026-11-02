import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

KAFKA_BROKER = os.getenv("KAFKA_BROKER")


PORT_DB_CONSUMER = os.getenv("PORT_DB_CONSUMER")
PORT_AUTH_SERVICE = os.getenv("PORT_AUTH_SERVICE")
PORT_CHATS_SERVICE = os.getenv("PORT_CHATS_SERVICE")
PORT_GATEWAY_PORT = os.getenv("PORT_GATEWAY_PORT")


ACTIVITY_URL = os.getenv("ACTIVITY_URL", "https://grafana.uva-advanced.ru/cloudru-activity")
ACTIVITY_TOKEN = os.getenv("ACTIVITY_TOKEN")
ACTIVITY_DEPARTMENT = os.getenv("ACTIVITY_DEPARTMENT", "cds")

LOOP_HARD_LIMIT_RUB = float(os.getenv("LOOP_HARD_LIMIT_RUB", "200.0"))
BUDGET_CACHE_TTL = int(os.getenv("BUDGET_CACHE_TTL", "900"))

LOOP_SERVICE_USER_ID = -100