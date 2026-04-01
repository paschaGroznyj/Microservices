import re
import uuid
import json
import asyncio
import chromadb
from datetime import datetime
from aiobotocore.session import AioSession
from botocore.client import Config
from fastapi import FastAPI, Response, status
from sqlalchemy import URL, Select, text, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from langchain_text_splitters import RecursiveCharacterTextSplitter
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from .models import Chunk, GetChunk, PutDocument, DelChunk
from .embedding import GigaChatEmbedding
from .core.config import (
    DB_USER,
    DB_PASSWORD,
    DB_HOST,
    DB_PORT,
    DB_NAME,
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

    url = URL.create(
        'postgresql+asyncpg',
        username=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME
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
        text = text.replace(r'\x00', '')
        return text.strip()

@app.post('/run_rag')
async def run_rag():
    """
    Docstring для run_rag
    Эндпоинт обращается к S3-бакету, загружает оттуда метаданные и содержимое файлов.
    Содержимое нарезается на чанки, чанки делятся на батчи по 10 штук.
    Для каждого чанка в батче создается корутина, они заворачиваются в gather, результаты через zip скрепляются с текстом чанков.
    Формируются ORM объекты и записываются в БД в едином запросе.
    """

    params = {
        "Bucket": OBS_BUCKET,
        "Prefix": "articles/"
    }

    load_id = str(uuid.uuid4())
    start = datetime.now()

    async with app.state.session() as session:
        stmt = Select(Chunk.original_id)

        query = await session.execute(stmt)
        ids = set(query.scalars().all())
    
    async with app.state.s3_client() as client:
        content_resp = await client.list_objects_v2(**params)

        md_reponse = await client.get_object(Bucket=OBS_BUCKET, Key='metadata/metadata.json')
        data = await md_reponse["Body"].read()
        raw_text = data.decode("utf-8", errors="ignore")
        clean_json = extract_json_array(raw_text)
        metadata = json.loads(clean_json)

        metadata = {item['file_article']: item for item in metadata}

        chunks_count = 0
        all_chunks = []
        
        for obj in content_resp["Contents"]:
            key = obj["Key"]

            file_name = key.split('/')[-1]

            if metadata[file_name]['external_id'] in ids:
                continue
            
            if not key.endswith(".md"):
                continue

            response = await client.get_object(Bucket=OBS_BUCKET, Key=key)
            data = await response["Body"].read()

            data = data.replace(b'\x00', b'')
            text = data.decode("utf-8", errors="ignore")
            text = clean_text(text)
            metadata[file_name]['text'] = text
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
            text_chunks = text_splitter.split_text(text)
            chunks_count += len(text_chunks)
            print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} Created {len(text_chunks)} chunks for document {file_name}')

            chunk_batches = [text_chunks[i:i+10] for i in range(0, len(text_chunks), 10)]

            chunk_embedding_map = {}

            for batch in chunk_batches:
                coros = [app.state.emb_client.embed(chunk) for chunk in batch]
                results = await asyncio.gather(*coros)
                chunk_embedding_map = chunk_embedding_map | dict(zip(batch, results))
            
            db_objects = [Chunk(
                original_id = metadata[file_name]['external_id'],
                original_name = file_name,
                chunk_text = text,
                embedding = embedding,
                load_id = load_id
            ) for text, embedding in chunk_embedding_map.items()]

            all_chunks.extend(db_objects)
        
        with open('metadata.json', "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} Chunking done in {datetime.now() - start}, starting DB query.')        
        async with app.state.session() as session:
            session.add_all(all_chunks)
            await session.commit()
        
        print(f'{chunks_count} chunks added.')

        return {'chunks_added': chunks_count}
