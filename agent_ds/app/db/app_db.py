import asyncio
import asyncpg
from core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
import logging
from typing import List, Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class DataBaseMessages:
    async def init_db_pool(self):
        """Инициализация пула подключений к базе данных."""
        max_retries = 5
        retry_delay = 2.0

        for attempt in range(1, max_retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    user=DB_USER,
                    password=DB_PASSWORD,
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME,
                    min_size=5,
                    max_size=10,
                    max_queries=50000,
                    max_inactive_connection_lifetime=300,
                    command_timeout=60,
                    timeout=60,
                    statement_cache_size=100
                )

                # Инициализация структуры базы данных
                async with self.pool.acquire() as conn:
                    await DataBaseMessages.__init_db(conn)
                    return

            except Exception as e:
                print(f"Пока не удалось подключиться по причине {e}")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                else:
                    raise

    async def close(self):
        await self.pool.close()

    @staticmethod
    async def __init_db(conn):
        await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id BIGINT PRIMARY KEY,
                    channel TEXT,
                    date TIMESTAMP,
                    author TEXT,
                    source TEXT,
                    raw_text TEXT,
                    links TEXT[],
                    hashtags TEXT[],
                    metadata JSONB
                );
                           
                CREATE TABLE IF NOT EXISTS chunks (
                    id BIGSERIAL PRIMARY KEY,
                    document_id BIGINT,
                    chunk_index INT,
                    content TEXT,
                    embedding VECTOR(1024),
                    tsv tsvector
                        GENERATED ALWAYS AS (
                            to_tsvector('simple', content)
                        ) STORED,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                           
                    CREATE INDEX IF NOT EXISTS chunks_embedding_idx
                    ON chunks
                    USING hnsw (embedding vector_cosine_ops);
                    
                    CREATE INDEX IF NOT EXISTS chunks_tsv_idx
                    ON chunks USING GIN(to_tsvector('simple', content));
                           
                    CREATE UNIQUE INDEX IF NOT EXISTS unique_doc_chunk 
                    ON chunks (document_id, chunk_index);
                    
                    CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN(tsv);
                           """)

    async def insert_raw_data(self, messages: List[Dict[str, Any]]):
        if not messages:
            return 0

        records = [] # Список для кортежей
        for msg in messages:
            records.append((
                msg.get("id"),
                msg.get("channel", ''),
                msg.get("date", ''),
                msg.get("author", ''),
                msg.get("source", ''),
                msg.get("text", ''),
                msg.get("links", []),
                msg.get("hashtags", [])
            ))

        try:
            async with self.pool.acquire() as conn:
                # Массовая вставка
                await conn.executemany("""
                       INSERT INTO chunks (
                           id, channel, date, author, source,
                           raw_text, links, hashtags
                       ) VALUES (
                           $1, $2, $3, $4, $5, $6,
                           $7, $8
                       )
                       ON CONFLICT (id) DO NOTHING;
                   """, records)


        except Exception as e:
            logging.error(f"Ошибка при вставке сообщений: {e}")

    async def insert_chunks_data(self, records: List[tuple]):
        if not records:
            return 0

        try:
            async with self.pool.acquire() as conn:
                # Массовая вставка
                await conn.executemany("""
               INSERT INTO chunks (document_id, chunk_index, content, embedding)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (document_id, chunk_index) DO NOTHING;
               """, records)


        except Exception as e:
            logging.error(f"Ошибка при вставке чанков: {e}")


    async def select_raw_data(self):
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                SELECT id, raw_text 
                FROM documents;
                """)
                return rows
        except Exception as e:
            logging.error(f"Ошибка при получении сырых данных", e)
            raise

    async def select_chunks(self):
        """Для извлечения чанков с последующей векторизацией"""
        try:
            async with self.pool.acquire() as conn:
                # rows = await conn.fetch("""
                #                         SELECT id, content, document_id, chunk_index
                #                         FROM chunks
                #                         WHERE embedding IS NULL
                #                         ORDER BY document_id LIMIT 50;
                #                         """)
                rows = await conn.fetch("""
                                        SELECT c.id, c.content, c.document_id, c.chunk_index, d.date
                                        FROM chunks as c
                                                 JOIN documents as d
                                                      ON c.document_id = d.id
                                        WHERE d.date BETWEEN '2025-01-01' AND '2026-03-12'
                                        ORDER BY c.document_id DESC LIMIT 64
                                        OFFSET 2100;
                                        """)  # LIMIT от 20
                return rows
        except Exception as e:
            logging.error("Ошибка при получении чанков для эмбеддингов", e)
            raise

    async def select_text_chunks(self, chunks: List[int]) -> List[Dict]:
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                                        SELECT c.id,
                                               c.document_id,
                                               c.chunk_index,
                                               c.content,
                                               d.links
                                        FROM chunks c
                                                 JOIN documents d ON c.document_id = d.id
                                        WHERE c.id = ANY ($1::int[])
                                        """, list(chunks))

                return [
                    {
                        "chunk_id": r["id"],
                        "document_id": r["document_id"],
                        "chunk_index": r["chunk_index"],
                        "content": r["content"],
                        "links": r["links"] or [],
                        "search_type": "graph"
                    }
                    for r in rows
                ]
        except Exception as e:
            logging.error(f"Ошибка при получении чанков: {e}")
            raise

    async def select_chunks_graph(self):
        """Для извлечения чанков с последующей векторизацией"""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                                        SELECT c.id, c.content, c.document_id, c.chunk_index, d.date
                                        FROM chunks as c JOIN documents as d
                                        ON c.document_id = d.id
                                        WHERE d.date BETWEEN '2025-01-01' AND '2026-03-12'
                                        ORDER BY c.document_id DESC
                                        LIMIT 300 OFFSET 2100; 
                                        """) # LIMIT от 20
                return rows
        except Exception as e:
            logging.error("Ошибка при получении чанков для графа", e)
            raise

    async def update_embeddings(self, ids, vectors):

        records = [
            (str(vectors[i]), ids[i])
            for i in range(len(ids))
        ]

        async with self.pool.acquire() as conn:
            await conn.executemany("""
            UPDATE chunks
            SET embedding = $1
            WHERE id = $2
            """, records)

    async def search_vector(
            self,
            query_embedding: List[float],
            limit: int = 5,
            threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                                    WITH corpus AS (SELECT c.id
                                                    FROM chunks c
                                                             JOIN documents d ON c.document_id = d.id
                                                    WHERE d.date BETWEEN '2025-01-01' AND '2026-03-12'
                                                    ORDER BY c.document_id DESC
                                        LIMIT 300
                                    OFFSET 2100 )
                                    SELECT c.id,
                                           c.document_id,
                                           c.chunk_index,
                                           c.content,
                                           d.links,
                                           1 - (c.embedding <=> $1) as score
                                    FROM chunks c
                                             JOIN documents d ON c.document_id = d.id
                                    WHERE c.embedding IS NOT NULL
                                      AND c.id IN (SELECT id FROM corpus)
                                      AND (1 - (c.embedding <=> $1)) >= $3
                                    ORDER BY c.embedding <=> $1
                                        LIMIT $2
                                    """, str(query_embedding), limit, threshold)

            return [
                {
                    "chunk_id": r["id"],
                    "document_id": r["document_id"],
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "links": r["links"] or [],
                    "score": float(r["score"]),
                    "search_type": "vector"
                }
                for r in rows
            ]

    async def search_bm25(
            self,
            query_text: str,
            limit: int = 5,
    ) -> List[Dict[str, Any]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                                    WITH corpus AS (SELECT c.id
                                                    FROM chunks c
                                                             JOIN documents d ON c.document_id = d.id
                                                    WHERE d.date BETWEEN '2025-01-01' AND '2026-03-12'
                                                    ORDER BY c.document_id DESC
                                        LIMIT 300
                                    OFFSET 2100 )
                                    SELECT c.id,
                                           c.document_id,
                                           c.chunk_index,
                                           c.content,
                                           d.links,
                                           ts_rank_cd(c.tsv, websearch_to_tsquery('simple', $1)) as score
                                    FROM chunks c
                                             JOIN documents d ON c.document_id = d.id
                                    WHERE c.tsv @@ websearch_to_tsquery('simple'
                                        , $1)
                                      AND c.id IN (SELECT id FROM corpus)
                                    ORDER BY score DESC
                                        LIMIT $2
                                    """, query_text, limit)

            return [
                {
                    "chunk_id": r["id"],
                    "document_id": r["document_id"],
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "links": r["links"] or [],
                    "score": float(r["score"]),
                    "search_type": "bm25"
                }
                for r in rows
            ]

    async def search_hybrid(
            self,
            query_text: str,
            query_embedding: List[float],
            limit: int = 5,
            alpha: float = 0.5,  # Вес векторного поиска (0.5 = равный вклад)
    ) -> List[Dict[str, Any]]:
        """
        Гибридный поиск с объединением результатов (RRF - Reciprocal Rank Fusion).
        alpha: 0.0 = только BM25, 1.0 = только вектор
        """
        vector_results = await self.search_vector(query_embedding, limit=limit * 2)
        bm25_results = await self.search_bm25(query_text, limit=limit * 2)
        k = 60
        fusion_scores = {}

        for rank, item in enumerate(vector_results):
            chunk_id = item['chunk_id']
            # Векторный скор уже есть, но для RRF важнее ранг
            rrf_score = 1 / (k + rank + 1)

            if chunk_id not in fusion_scores:
                fusion_scores[chunk_id] = {**item, 'rrf_score': 0, 'vector_rank': rank + 1, 'bm25_rank': None}
            fusion_scores[chunk_id]['rrf_score'] += rrf_score * (1 - alpha)  # Вес вектора

        for rank, item in enumerate(bm25_results):
            chunk_id = item['chunk_id']
            rrf_score = 1 / (k + rank + 1)

            if chunk_id not in fusion_scores:
                fusion_scores[chunk_id] = {**item, 'rrf_score': 0, 'vector_rank': None, 'bm25_rank': rank + 1}
            fusion_scores[chunk_id]['rrf_score'] += rrf_score * alpha  # Вес BM25
            # Обновляем контент и мета-данные, если чанк пришел только из вектора
            if fusion_scores[chunk_id].get('search_type') == 'vector':
                fusion_scores[chunk_id].update({k: v for k, v in item.items() if k not in ['score']})
                fusion_scores[chunk_id]['search_type'] = 'hybrid'

        # 4. Сортируем по финальному скоры и возвращаем топ
        sorted_results = sorted(
            fusion_scores.values(),
            key=lambda x: x['rrf_score'],
            reverse=True
        )[:limit]

        return sorted_results

    async def get_context_window(
            self,
            chunk_id: int,
            window_size: int = 1
    ) -> List[Dict[str, Any]]:
        """
        Получает соседние чанки того же документа для расширения контекста.
        Например, если нашли чанк #5, вернет чанки #4, #5, #6.
        """
        async with self.pool.acquire() as conn:
            # Сначала находим document_id и chunk_index искомого чанка
            anchor = await conn.fetchrow(
                "SELECT document_id, chunk_index FROM chunks WHERE id = $1",
                chunk_id
            )
            if not anchor:
                return []

            doc_id = anchor['document_id']
            start_idx = max(0, anchor['chunk_index'] - window_size)
            end_idx = anchor['chunk_index'] + window_size

            rows = await conn.fetch("""
                                    SELECT id, chunk_index, content
                                    FROM chunks
                                    WHERE document_id = $1
                                      AND chunk_index BETWEEN $2 AND $3
                                    ORDER BY chunk_index ASC
                                    """, doc_id, start_idx, end_idx)

            return [
                {"chunk_index": r["chunk_index"], "content": r["content"]}
                for r in rows
            ]