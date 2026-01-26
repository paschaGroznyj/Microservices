from db.app_db import DataBaseMessages
import asyncio
from typing import List
from giga_chat import LLMAgent
from llm_model.app.core.config import API_GIGACHAT
from rag.complex import MessageRAG

db = DataBaseMessages()

# Глобальное хранилище RAG-инстансов (можно заменить на кэш)
rag_instances = {}


async def rebuild_vectors(chat_ids: List[int]):
    await db.init_db_pool()

    for chat_id in chat_ids:
        # Получаем сообщения из БД
        meta = await db.fetch_messages_for_rag([chat_id])
        messages = [m['text'] for m in meta if m.get('text')]

        if not messages:
            print(f"Нет сообщений для chat_id={chat_id}")
            continue

        # Создаём/обновляем RAG
        rag = MessageRAG(chat_id=chat_id, data_dir="./data")
        rag.create_index(messages)
        rag.save_index()  # ← Сохраняем на диск!

        # Кэшируем для последующих запросов (опционально)
        rag_instances[chat_id] = rag

    await db.pool.close()  # Закрываем пул после использования


async def search_context(chat_ids: List[int], query: str, hybrid: bool = True):
    for chat_id in chat_ids:
        rag = MessageRAG(chat_id=chat_id, data_dir="./data")
        rag.load_index()

        if hybrid:
            context = rag.hybrid_search(query, k=5, alpha=0.6)  # чуть больше веса у векторов
        else:
            context = rag.retrieve(query, k=5)

        with open(f"print_{chat_id}.txt", "w", encoding="utf-8") as f:
            f.write(context)

from llm_model.app.core.config import API_GIGACHAT, KAFKA_BROKER
from typing import List
from db.app_db import DataBaseMessages

# Инициализация
llm = LLMAgent(API_GIGACHAT)

chat_id = 2229217118
response_text = llm.llm_agent_step(user_message="Какие навыки станут главными? Расскажи о ChatGPT", chat_id=chat_id)
with open(f"print_{chat_id}.txt", "w", encoding="utf-8") as f:
    f.write(response_text)

# async def main():
#     await search_context([2229217118], "Об онтологии создания LangChain")
#
# if __name__ == "__main__":
#     asyncio.run(main())