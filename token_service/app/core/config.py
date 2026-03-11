from dotenv import load_dotenv
import os

load_dotenv()

INTERNAL_TOKEN_API_URL = os.getenv("INTERNAL_TOKEN_API_URL")
PORT_TOKEN_GENERATE_SERVICE = int(os.getenv("PORT_TOKEN_GENERATE_SERVICE", 9004))
TOKEN_TTL = os.getenv("TOKEN_TTL")
IAM_API_KEY = os.getenv("IAM_API_KEY")