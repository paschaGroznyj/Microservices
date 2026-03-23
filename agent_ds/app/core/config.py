import os
from dotenv import load_dotenv
import json

# Load environment variables from .env file
load_dotenv()

DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")

NEO4J_URI = "bolt://localhost:7687"
# NEO4J_URI = "bolt://my_neo4j_graph:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "password"

KAFKA_BROKER = os.getenv("KAFKA_BROKER")

PORT_DS_AGENT = os.getenv("PORT_DS_AGENT")
