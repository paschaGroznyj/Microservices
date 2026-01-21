import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

PORT_LLM_CONSUMER = os.getenv("PORT_LLM_CONSUMER")
PORT_CRB_SERVICE = os.getenv("PORT_CRB_SERVICE")


# АГЕНТ
# Пути
STORAGE_PATH = os.getenv("STORAGE_PATH", "./knowledge_base")
ARTICLES_FOLDER = "articles"
METADATA_FILE = "metadata.json"
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./faiss_index")

# GigaChat
GIGACHAT_CREDENTIALS = os.getenv("API_GIGACHAT")
GIGACHAT_AUTHORIZATION = os.getenv("GIGACHAT_AUTHORIZATION")
GIGACHAT_MODEL = "GigaChat:latest"

# Embedding
EMBEDDING_MODEL = "sentence-transformers/LaBSE"

# Планировщик
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))

# OPENAI
QWEN_CREDENTIALS = os.getenv("OPENAI_TOKEN", '')