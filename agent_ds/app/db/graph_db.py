from typing import List, Dict, Any, Optional
from neo4j import AsyncGraphDatabase
import re



class GraphStore:
    def __init__(self, uri: str, user: str, password: str):
        self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    async def close(self):
        await self.driver.close()

    async def merge_document(self, tx, document_id: int):
        await tx.run("""
        MERGE (d:Document {id: $document_id})
        """, document_id=document_id)

    async def merge_chunk(self, tx, chunk_id: int, chunk_index: int, document_id: int):
        await tx.run("""
        MERGE (c:Chunk {id: $chunk_id})
        SET c.chunk_index = $chunk_index

        MERGE (d:Document {id: $document_id})
        MERGE (c)-[:PART_OF]->(d)
        """, chunk_id=chunk_id, chunk_index=chunk_index, document_id=document_id)

    async def merge_entity(self, tx, name: str, entity_type: str):
        await tx.run("""
        MERGE (e:Entity {normalized_name:$normalized})
        SET e.type = $entity_type
        SET e.name = $name
        """, name=name, entity_type=entity_type, normalized=normalize_entity_name(name))

    async def link_entity_chunk(self, tx, name: str, chunk_id: int):
        await tx.run("""
        MATCH (e:Entity {normalized_name: $name})
        MATCH (c:Chunk {id: $chunk_id})

        MERGE (e)-[:MENTIONED_IN]->(c)
        """, name=normalize_entity_name(name), chunk_id=chunk_id)

    async def merge_relation(self, tx, source: str, target: str, relation_type: str):
        await tx.run(f"""
        MATCH (a:Entity {{normalized_name: $source}})
        MATCH (b:Entity {{normalized_name: $target}})
        MERGE (a)-[:{relation_type}]->(b)
        """, source=normalize_entity_name(source), target=normalize_entity_name(target))

    async def get_all_entities(self) -> List[Dict[str, Any]]: # Получение всех сущностей из Neo
        async with self.driver.session() as session:
            result = await session.run("""
            MATCH (e:Entity)
            RETURN e.name AS name,
                   e.type AS type
            ORDER BY name
            """)

            entities = []

            async for record in result:
                entities.append({
                    "name": record["name"],
                    "type": record["type"]
                })

            return entities

    async def get_chunks_by_entity(self, entity_name: str) -> list[int]:
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (e:Entity {normalized_name: $name})-[:MENTIONED_IN]->(c:Chunk)
                RETURN c.id AS chunk_id
            """, name=normalize_entity_name(entity_name))

            return [record["chunk_id"] async for record in result]

    async def get_chunks_by_relation(self, source: str, target: str) -> list[int]:
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (e1:Entity {normalized_name: $source})-[r]-(e2:Entity {normalized_name: $target})
                MATCH (e1)-[:MENTIONED_IN]->(c:Chunk)
                MATCH (e2)-[:MENTIONED_IN]->(c)
                RETURN DISTINCT c.id AS chunk_id
            """, source=source, target=target)

            return [record["chunk_id"] async for record in result]

    async def get_chunks_by_relation_type(
            self,
            source: str,
            target: str,
            rel_type: str
    ) -> list[int]:
        """
        Чанки по паттерну сущность → тип_связи → ?
        Работает для трёх случаев:
          1. source + rel_type          (ChatGPT -CREATED_BY-> ?)
          2. target + rel_type          (? -CREATED_BY-> OpenAI)
          3. source + target + rel_type (полный триплет)
        """
        async with self.driver.session() as session:

            if source and not target:
                # (ChatGPT)-[:CREATED_BY]->(?)
                query = f"""
                    MATCH (e:Entity {{normalized_name: $source}})-[:{rel_type}]->(neighbor:Entity)
                    MATCH (e)-[:MENTIONED_IN]->(c:Chunk)
                    RETURN DISTINCT c.id AS chunk_id, neighbor.name AS neighbor
                """
                params = {"source": source.lower()}

            elif target and not source:
                # (?)-[:CREATED_BY]->(OpenAI)
                query = f"""
                    MATCH (neighbor:Entity)-[:{rel_type}]->(e:Entity {{normalized_name: $target}})
                    MATCH (e)-[:MENTIONED_IN]->(c:Chunk)
                    RETURN DISTINCT c.id AS chunk_id, neighbor.name AS neighbor
                """
                params = {"target": target.lower()}

            else:
                # полный триплет
                query = f"""
                    MATCH (e1:Entity {{normalized_name: $source}})-[:{rel_type}]->(e2:Entity {{normalized_name: $target}})
                    MATCH (e1)-[:MENTIONED_IN]->(c:Chunk)
                    MATCH (e2)-[:MENTIONED_IN]->(c)
                    RETURN DISTINCT c.id AS chunk_id, e2.name AS neighbor
                """
                params = {"source": source.lower(), "target": target.lower()}

            result = await session.run(query, **params)
            rows = [record async for record in result]

            neighbors = list({r["neighbor"] for r in rows})
            print(f"get_chunks_by_relation_type [{rel_type}]: найдены соседи {neighbors}")

            return [record["chunk_id"] for record in rows]

    async def get_all_chunks(self):
        async with self.driver.session() as session:
            result = await session.run("""
            MATCH (c:Chunk) RETURN c.id
            """)

            chunk_ids = []

            async for record in result:
                chunk_ids.append(record["c.id"])

            return chunk_ids

def normalize_entity_name(name: str) -> str:
    name = name.lower()
    name = name.strip()

    # убрать лишние пробелы
    name = re.sub(r"\s+", " ", name)

    # убрать спецсимволы
    name = re.sub(r"[^\w\s]", "", name)

    return name
