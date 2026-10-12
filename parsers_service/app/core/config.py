import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

PORT_PARSERS_SERVICE = os.getenv("PORT_PARSERS_SERVICE")

