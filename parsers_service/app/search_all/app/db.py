import os
import psycopg
from psycopg.rows import dict_row


def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST", "db"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "research_db"),
        user=os.getenv("DB_USER", "research_user"),
        password=os.getenv("DB_PASSWORD", "research_pass"),
        row_factory=dict_row,
    )
