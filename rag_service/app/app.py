import re
import uuid
import json
import chromadb
from aiobotocore.session import AioSession
from botocore.client import Config
from fastapi import FastAPI, Response, status
from sqlalchemy import URL, Select, text, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from langchain_text_splitters import RecursiveCharacterTextSplitter
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
    EMB_MODEL,
    OBS_ACCESS_KEY,
    OBS_SECRET_KEY,
    OBS_REGION,
    OBS_BUCKET,
    OBS_ENDPOINT
)

s3_params = {
    "service_name": "s3",
    "aws_access_key_id": OBS_ACCESS_KEY,
    "aws_secret_access_key": OBS_SECRET_KEY,
    "region_name": OBS_REGION,
    "endpoint_url": OBS_ENDPOINT,
    "config": Config(s3={"addressing_style": "virtual"})
}

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
    
    @asynccontextmanager
    async def client():
        session = AioSession()
        async with session.create_client(**s3_params) as client:
            yield client
    
    emb_client = GigaChatEmbedding()
    app.state.emb_client = emb_client
    app.state.s3_client = client

    yield

    await engine.dispose()
    del emb_client
    del client


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

def extract_json_array(raw: str) -> str:
        start = raw.find("[")
        end = raw.rfind("]")

        if start == -1 or end == -1 or end < start:
            raise ValueError("JSON array not found in response")

        return raw[start:end + 1]


def clean_text(text: str) -> str:
        lines = text.splitlines()

        while lines:
            line = lines[0].strip()

            if not line:
                lines.pop(0)
                continue

            if re.fullmatch(r'[0-9a-fA-F]{3,8}', line):
                lines.pop(0)
                continue

            if len(line) < 10 and ' ' not in line:
                lines.pop(0)
                continue

            break

        text = '\n'.join(lines)
        text = re.sub(
            r'\n\d+\n0\nx-amz-checksum-crc32:.*$',
            '',
            text,
            flags=re.S
        )
        return text.strip()

@app.post('/run_rag')
async def run_rag():
    """
    Docstring для run_rag
    1. Получаем количество файлов
    2. Разделяем их на стримы
    3. В каждом стриме файлы заворачиваем в раг
    """

    params = {
        "Bucket": OBS_BUCKET,
        "Prefix": "articles/"
    }

    async with app.state.s3_client() as client:
        content_resp = await client.list_objects_v2(**params)

        md_reponse = await client.get_object(Bucket=OBS_BUCKET, Key='metadata/metadata.json')
        data = await md_reponse["Body"].read()
        raw_text = data.decode("utf-8", errors="ignore")
        clean_json = extract_json_array(raw_text)
        metadata = json.loads(clean_json)

        metadata = {item['file_article']: item for item in metadata}

        with open('metadata.json', "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

        chunks_count = 0
                
        for obj in content_resp["Contents"]:
            key = obj["Key"]

            file_name = key.split('/')[-1]
            doc_id = file_name.split('_')[-1].split('.')[0]

            if not key.endswith(".md"):
                continue

            response = await client.get_object(Bucket=OBS_BUCKET, Key=key)
            data = await response["Body"].read()
            text = data.decode("utf-8", errors="ignore")
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
            text_chunks = text_splitter.split_text(text)
            chunks_count += len(text_chunks)
            print(f'Chunks created: {len(text_chunks)}')
            db_objects = [Chunk(
                original_id = doc_id,
                original_name = 'test_name', # metadata[file_name]['title'],
                chunk_text = text_chunk,
                embedding = await app.state.emb_client.embed(text_chunk)
            ) for text_chunk in text_chunks]

            async with app.state.session() as session:
                session.add_all(db_objects)
                session.commit()
        
        print(f'Chunks added in session: {chunks_count}')

        return {'chunks_added': chunks_count}
