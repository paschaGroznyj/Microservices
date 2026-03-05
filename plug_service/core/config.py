import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")
PORT_PLUG_AGENT = os.getenv("PORT_PLUG_AGENT")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_KEY = os.getenv("REDIS_TOKEN_KEY", "gigachat:access_token")
