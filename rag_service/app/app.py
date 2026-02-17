import os
import uuid
import chromadb
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from .embedding import DummyKBEmbedding, AsyncStandaloneEmbedding

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    vdb_client = chromadb.HttpClient(host='chromadb', port=8000)
    emb_client = AsyncStandaloneEmbedding(os.environ['EMB_MODEL'], os.environ['HF_TOKEN'])
    
    app.state.collection = vdb_client.get_or_create_collection(
        'kb_collection', 
        embedding_function=DummyKBEmbedding(os.environ['EMB_MODEL'])
    )
    app.state.vdb_client = vdb_client
    app.state.emb_client = emb_client

    yield

    del vdb_client
    del embedding_client


app = FastAPI(title='RAG Service', lifespan=lifespan)

class Document(BaseModel):
    content: str

@app.post('/put')
async def put(document: Document):
    app.state.collection.add(
        ids = [str(uuid.uuid4())],
        embeddings = await app.state.emb_client.embed(document.content),
        documents = [document.content]
    )
    return {
        "doc_length": len(document.content),
        "new_collection_length": app.state.collection.count()
    }

@app.get('/get')
async def get(document: Document):
    response = app.state.collection.query(
        query_embeddings = await app.state.emb_client.embed(document.content),
        n_results = 5
    )
    return response

@app.get('/healthcheck')
def healthcheck():
    return {'heartbeat': app.state.vdb_client.heartbeat()}
