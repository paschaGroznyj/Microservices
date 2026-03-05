import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

KAFKA_BROKER = os.getenv("KAFKA_BROKER")
PORT_PLUG_AGENT = os.getenv("PORT_PLUG_AGENT")
