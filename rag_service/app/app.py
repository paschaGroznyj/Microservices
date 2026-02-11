import os
import uuid
import json
import chromadb
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from .embedding import KBEmbedding

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    vdb_client = chromadb.HttpClient(host='chromadb', port=8000)

    app.state.collection = vdb_client.get_or_create_collection(
        'kb_collection',
        embedding_function=KBEmbedding(os.environ['EMB_MODEL'], os.environ['HF_TOKEN'])
    )
    
    yield

    del vdb_client
    del embedding_client


app = FastAPI(title='RAG Service', lifespan=lifespan)

class Document(BaseModel):
    content: str

@app.post('/put')
def put(document: Document):
    app.state.collection.add(
        ids = [str(uuid.uuid4())],
        documents = [document.content]
    )
    return {
        "doc_length": len(document.content),
        "new_collection_length": app.state.collection.count()
    }

@app.post('/put_gc')
def put_gc(document: Document):

    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    payload = 'scope=GIGACHAT_API_PERS'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'RqUID': f'{uuid.uuid4()}',
        'Authorization': f'Basic {os.environ["GC_KEY"]}'
    }

    response = requests.request("POST", url, headers=headers, data=payload, verify=False)

    response_data = response.json()
    print(response_data)

    url = "https://gigachat.devices.sberbank.ru/api/v1/embeddings"

    payload = json.dumps({
        "model": "Embeddings",
        "input": document.content
    })

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {response_data["access_token"]}'
    }

    response = requests.request("POST", url, headers=headers, data=payload, verify=False)

    return response.json()

@app.get('/get')
def get(request):
    pass

@app.get('/healthcheck')
def healthcheck():
    return {'heartbeat': app.state.vdb_client.hearbeat()}
