import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.environ['DB_USER']
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_HOST = os.environ['DB_HOST']
DB_PORT = os.environ['DB_PORT']

GC_AUTH_URL = os.environ['GC_AUTH_URL']
GC_MODEL = os.environ['GC_MODEL']

OBS_BUCKET=os.environ['OBS_BUCKET']
OBS_ACCESS_KEY=os.environ['OBS_ACCESS_KEY']
OBS_SECRET_KEY=os.environ['OBS_SECRET_KEY']
OBS_REGION=os.environ['OBS_REGION']
OBS_ENDPOINT=os.environ['OBS_ENDPOINT']
