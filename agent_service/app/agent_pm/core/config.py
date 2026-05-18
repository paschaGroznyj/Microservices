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

DIGEST_RECIPIENTS = os.getenv("DIGEST_RECIPIENTS", "")
# mon,tue,wed,thu,fri,sat,sun или *, mon-fri и т.д.
DIGEST_SCHEDULE_DAY_OF_WEEK = os.getenv('DIGEST_SCHEDULE_DAY_OF_WEEK', 'mon')
DIGEST_SCHEDULE_HOUR = os.getenv('DIGEST_SCHEDULE_HOUR', 9)
DIGEST_SCHEDULE_MINUTE = os.getenv('DIGEST_SCHEDULE_MINUTE', 0)
DIGEST_TIMEZONE = os.getenv('DIGEST_TIMEZONE' , 'Europe/Moscow')

# Включение / отключение автодайджеста
DIGEST_ENABLED = os.getenv('DIGEST_ENABLED', 'true')

# Режим запуска:
# - once       -> однократный запуск для быстрой проверки
# - scheduler  -> постоянный процесс с APScheduler
DIGEST_RUN_MODE = os.getenv('DIGEST_RUN_MODE', 'scheduler')

# Загружать ли итоговый дайджест в OBS-хранилище
DIGEST_UPLOAD_TO_OBS = os.getenv('DIGEST_UPLOAD_TO_OBS', 'true')

FERNET_KEY = os.getenv("FERNET_KEY")
