import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

API_GIGACHAT = os.getenv("API_GIGACHAT")

PORT_LLM_CONSUMER = os.getenv("PORT_LLM_CONSUMER")
PORT_CRB_SERVICE = os.getenv("PORT_CRB_SERVICE")