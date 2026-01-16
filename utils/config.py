import os
import yaml
from pathlib import Path
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

API_GIGACHAT = os.getenv("API_KEY")

PORT_LLM_CONSUMER = os.getenv("PORT_LLM_CONSUMER")
PORT_DB_CONSUMER = os.getenv("PORT_DB_CONSUMER")
PORT_AUTH_SERVICE = os.getenv("PORT_AUTH_SERVICE")
PORT_CHATS_SERVICE = os.getenv("PORT_CHATS_SERVICE")
PORT_CRB_SERVICE = os.getenv("PORT_CRB_SERVICE")
PORT_GATEWAY_PORT = os.getenv("PORT_GATEWAY_PORT")

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")