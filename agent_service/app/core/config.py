import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

PORT_PM_AGENT = os.getenv("PORT_PM_AGENT")
PORT_CRB_SERVICE = os.getenv("PORT_CRB_SERVICE")

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT"))
REDIS_KEY = os.getenv("REDIS_TOKEN_KEY")

# �����
# ����
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
EMBED_MODEL_GLOSS = "paraphrase-multilingual-MiniLM-L12-v2"

# Path to glossary
PATH_TO_GLOSSARY = "Глоссарий РМ_cloud.md"

# �����������
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))

# OPENAI
QWEN_CREDENTIALS = os.getenv("OPENAI_TOKEN", '')

# HF_TOKEN
HF_TOKEN = os.getenv("HF_TOKEN", '')