import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

API_GIGACHAT = os.getenv("API_GIGACHAT")

PORT_LLM_CONSUMER = os.getenv("PORT_LLM_CONSUMER")
PORT_CRB_SERVICE = os.getenv("PORT_CRB_SERVICE")