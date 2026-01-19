import logging

from langchain_core.messages import SystemMessage
from langchain_gigachat.chat_models import GigaChat
from config import API_GIGACHAT, PORT_CRB_SERVICE
from rag.simple_rag import SimpleRAG
import time
import json

class GigaChatApi:
    def __init__(self, api):
        pass
        self.giga = GigaChat(
            # Для авторизации запросов используйте ключ, полученный в проекте GigaChat API
            credentials=api,
            verify_ssl_certs=False,
        )
        self.rag = SimpleRAG("rag/docs/Posobie_Yava_poslednyaya_versia.pdf")

    def take_answer(self, prompt):
        start = time.time()
        messages = [
            SystemMessage(
                content=prompt
            )
        ]
        print("Передача текста в модель")
        res = self.giga.invoke(messages)
        messages.append(res)
        summary = res.content
        finish = time.time()
        print(f"Время генерации ответа: {finish - start}")
        return summary

    @staticmethod
    def rag_prompt(user_query: str, context: str) -> str:
        return f"""
        Ты — экспертный ассистент, который отвечает на вопросы на основе предоставленного контекста.

        КОНТЕКСТ ИЗ ДОКУМЕНТОВ:
        {context}

        ИНСТРУКЦИИ:
        1. Отвечай ТОЛЬКО на основе предоставленного контекста
        2. Если в контексте нет информации для ответа, скажи: "В предоставленных документах нет информации по этому вопросу."
        3. Не придумывай информацию, которой нет в контексте
        4. Будь точным и информативным
        5. Если нужно, цитируй конкретные части контекста
        6. Формулируй ответ своими словами, но сохраняй смысл

        ВОПРОС ПОЛЬЗОВАТЕЛЯ: {user_query}

        ОТВЕТ:
        """

    @staticmethod
    def first_prompt(user_query: str) -> str:
        return f"""
    Ты — эксперт по Java-инженерии. Твоя задача — решить, можно ли ответить на вопрос пользователя **без обращения к внутренним документам**, или **требуется поиск в технической документации**.

    Правила:
    - Если вопрос общий, который не касается Java и программирования — отвечай сразу.
    - Всегда отвечай **ТОЛЬКО в формате JSON** без пояснений.

    Формат ответа:
    {{
      "need_rag": false,
      "answer": "Краткий и точный ответ на вопрос"
    }}
    ИЛИ
    {{
      "need_rag": true,
      "answer": null
    }}

    ВОПРОС: {user_query}

    ОТВЕТ:
    """

    @staticmethod
    def parse_llm_json(text: str):
        """Извлекает JSON из ответа LLM (даже если есть пояснения)"""
        try:
            # Ищем первую пару фигурных скобок
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end != -1:
                json_str = text[start:end]
                return json.loads(json_str)
        except:
            pass
        return {"action": "none"}

    def llm_agent_step(self, user_message: str):
        # Сначала определим тематику запроса
        classification_prompt = self.first_prompt(user_message)
        classification_response = self.take_answer(classification_prompt)

        decision = self.parse_llm_json(classification_response)
        print("Решение о RAG *************", decision)
        if not isinstance(decision, dict):
            decision = {"need_rag": True, "answer": None}

        need_rag = decision.get("need_rag", True)
        direct_answer = decision.get("answer", None)

        if not need_rag and direct_answer:
            return direct_answer
        # Иначе используем RAG
        context = self.rag.retrieve(user_message)
        print(f"КОНТЕКСТ: {context[:500]}")
        rag_prompt = self.rag_prompt(user_message, context)
        return self.take_answer(rag_prompt)

# llm_client = GigaChatApi(API_GIGACHAT)
# print(llm.take_answer("Привет"))
# print(llm_client.dummy_answer("Hello world"))
#
# print(llm_client.llm_agent_step("Принципы объектного ориентированного программирования из методички"))
