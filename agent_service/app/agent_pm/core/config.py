import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

PORT_PM_AGENT = os.getenv("PORT_PM_AGENT")
PORT_GATEWAY_PORT = os.getenv("PORT_GATEWAY_PORT")
PORT_PARSERS_SERVICE = os.getenv("PORT_PARSERS_SERVICE")

STORAGE_PATH = os.getenv("STORAGE_PATH", "./knowledge_base")
ARTICLES_FOLDER = "articles"
METADATA_FILE = "metadata.json"
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./faiss_index")

# Embedding
EMBEDDING_MODEL = "sentence-transformers/LaBSE"
EMBED_MODEL_GLOSS = "paraphrase-multilingual-MiniLM-L12-v2"

# Path to glossary
PATH_TO_GLOSSARY = "Глоссарий РМ_cloud.md"

# �����������
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))

# SMTP Email
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.yandex.ru")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_LOGIN = os.getenv("SMTP_LOGIN", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")
