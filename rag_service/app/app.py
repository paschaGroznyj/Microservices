import os
import uuid
import chromadb
import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, List
from asyncpg.protocol.record import Record

from .embedding import DummyKBEmbedding, AsyncStandaloneEmbedding

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    vdb_client = None
    if os.environ['VDB_TYPE'] == 'chroma':
        vdb_client = chromadb.HttpClient(host='chromadb', port=8000)
        app.state.collection = vdb_client.get_or_create_collection(
            'kb_collection', 
            embedding_function=DummyKBEmbedding(os.environ['EMB_MODEL'])
        )
        app.state.vdb_client = vdb_client

    elif os.environ['VDB_TYPE'] == 'pgvector':
        conn = await asyncpg.connect(
            user=os.environ['DB_USER'],
            password=os.environ['DB_PASSWORD'],
            host=os.environ['DB_HOST'],
            port=os.environ['DB_PORT']
        )

        await conn.execute('CREATE EXTENSION IF NOT EXISTS vector')
        await register_vector(conn)

        await conn.execute('CREATE TABLE IF NOT EXISTS rag_docs (id bigserial PRIMARY KEY, embedding vector(768), content varchar(5000))')
        app.state.vdb_client = conn

    emb_client = AsyncStandaloneEmbedding(os.environ['EMB_MODEL'], os.environ['HF_TOKEN'])
    app.state.emb_client = emb_client

    yield

    del vdb_client
    del emb_client


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

@app.post('/put_pv')
async def put_pv(document: Document):

    embedding = await app.state.emb_client.embed(document.content)
    await app.state.vdb_client.execute('INSERT INTO rag_docs (embedding, content) VALUES ($1, $2)', np.array(embedding), document.content)

    return {
        "doc_length": len(document.content),
        "new_collection_length": ''
    }

@app.get('/get_pv')
async def get_pv(document: Document):
    embedding = await app.state.emb_client.embed(document.content)
    response = await app.state.vdb_client.fetch('SELECT content FROM rag_docs ORDER BY embedding <-> $1 LIMIT 5', np.array(embedding))
    return [dict(qr) for qr in response]

@app.post('/del_pv/{id}')
async def del_pv(id):
    await app.state.vdb_client.execute('DELETE FROM rag_docs where id = $1', int(id))
    return {'deleted': id}

@app.get('/all')
async def get_all():
    query_result = await app.state.vdb_client.fetch('SELECT id, content FROM rag_docs')
    return [dict(qr) for qr in query_result]
