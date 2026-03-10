import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

PORT_PM_AGENT = os.getenv("PORT_PM_AGENT")


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