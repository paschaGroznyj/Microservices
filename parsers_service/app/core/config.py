import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

PORT_PARSERS_SERVICE = os.getenv("PORT_PARSERS_SERVICE")
STORAGE_PATH = os.getenv("STORAGE_PATH", "./knowledge_base")
ARTICLES_FOLDER = "articles"
PDFS_FOLDER = "pdfs"
METADATA_FILE = "metadata.json"
# VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./faiss_index")

SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "15"))
SYNC_INTERVAL_SCHEDULER = os.getenv("SYNC_INTERVAL_SCHEDULER", "10")

PORT_LLM_CONSUMER = os.getenv("PORT_PM_AGENT", "9001")

DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")

SCRAPINGDOG_API_KEY = os.getenv("SCRAPINGDOG_API_KEY")