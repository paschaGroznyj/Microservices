import uuid
import chromadb
from fastapi import FastAPI, Response, status
from sqlalchemy import URL, Select, text, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from contextlib import asynccontextmanager
from typing import AsyncGenerator


from .models import Chunk, GetChunk, PutDocument, DelChunk
from .embedding import DummyKBEmbedding, GigaChatEmbedding
from .core.config import (
    DB_USER,
    DB_PASSWORD,
    DB_HOST,
    DB_PORT,
    VDB_TYPE,
    EMB_MODEL
)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    vdb_client = None
    if VDB_TYPE == 'chroma':
        vdb_client = chromadb.HttpClient(host='chromadb', port=8000)
        app.state.collection = vdb_client.get_or_create_collection(
            'kb_collection', 
            embedding_function=DummyKBEmbedding(EMB_MODEL)
        )
        app.state.vdb_client = vdb_client

    elif VDB_TYPE == 'pgvector':
        url = URL.create(
            'postgresql+asyncpg',
            username=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )

        engine = create_async_engine(url)
        app.state.session = async_sessionmaker(engine, expire_on_commit=False)

        async with app.state.session() as session:
            await session.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
            await session.commit()

    emb_client = GigaChatEmbedding()
    app.state.emb_client = emb_client

    yield

    await engine.dispose()
    del emb_client


app = FastAPI(title='RAG Service', lifespan=lifespan)


@app.post('/put')
async def put(document: PutDocument):
    app.state.collection.add(
        ids = [str(uuid.uuid4())],
        embeddings = await app.state.emb_client.embed(document.text),
        documents = [document.content]
    )
    return {
        "doc_length": len(document.content),
        "new_collection_length": app.state.collection.count()
    }

@app.get('/get')
async def get(document: GetChunk):
    response = app.state.collection.query(
        query_embeddings = await app.state.emb_client.embed(document.content),
        n_results = 5
    )
    return response

@app.post('/put_pv')
async def put_pv(document: PutDocument):

    embedding = await app.state.emb_client.embed(document.text)
    c = Chunk(
        original_id = document.id,
        original_name = document.name,
        chunk_text = document.text,
        embedding = embedding
    )

    async with app.state.session() as session:
        session.add(c)
        await session.commit()

    return {
        "name": document.name,
        "chunks_added": ''
    }

@app.get('/get_pv')
async def get_pv(document: GetChunk):
    embedding = await app.state.emb_client.embed(document.text)

    async with app.state.session() as session:
        stmt = Select(Chunk.id, Chunk.original_name, Chunk.chunk_text).order_by(Chunk.embedding.l2_distance(embedding)).limit(document.top_n)
        
        queryset = await session.execute(stmt)

    return queryset.mappings().all()

@app.post('/del_pv')
async def del_pv(chunk_pointer: DelChunk, response: Response):
    """
    Docstring для del_pv
    Удаляем либо по ID оригинального документа, либо по ID чанка (если вдруг надо удалить одиночный чанк)
    """
    async with app.state.session() as session:
        stmt = delete(Chunk).where(Chunk.id == chunk_pointer.original_id if chunk_pointer.original_id else Chunk.id == chunk_pointer.db_id)

        result = await session.execute(stmt)

        if result.rowcount == 0:
            response.status_code = status.HTTP_404_NOT_FOUND
            return

        await session.commit()

    return {'rows_deleted': result.rowcount}

@app.get('/all')
async def get_all():

    async with app.state.session() as session:
        stmt = Select(Chunk.id, Chunk.original_id, Chunk.original_name, Chunk.chunk_text)
        queryset = await session.execute(stmt)
    
    return queryset.mappings().all()
