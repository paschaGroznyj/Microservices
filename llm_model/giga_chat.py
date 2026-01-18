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

    # def take_answer(self, text_page):
    #     char_list = list(text_page)  # Convert string to a list of characters
    #     # random.shuffle(char_list)  # Shuffle the list in place
    #     return "".join(char_list)  # Join the characters back into a string
    @staticmethod
    def get_action_prompt(user_query: str) -> str:
        return f"""
    Ты — умный ассистент, который понимает намерения пользователя.
    Проанализируй запрос и определи, нужно ли вызывать внешний инструмент.

    Доступные инструменты:
    - get_exchange_rate: если запрос про курс валюты (доллар, евро, юань и т.д.)

    Правила:
    - Если запрос НЕ про курс — верни {{ "action": "none" }}
    - Если про курс — определи код валюты:
        "USD" для доллара, "EUR" для евро, "CNY" для юаня
    - Всегда используй валюту "RUB" как целевую (to_currency)
    - Отвечай ТОЛЬКО валидным JSON, без пояснений.

    Примеры:
    Запрос: "Привет!" → {{ "action": "none" }}
    Запрос: "Сколько доллар?" → {{ "action": "get_exchange_rate", "params": {{ "from_currency": "USD", "to_currency": "RUB" }} }}
    Запрос: "Евро в рублях" → {{ "action": "get_exchange_rate", "params": {{ "from_currency": "EUR", "to_currency": "RUB" }} }}

    Запрос: "{user_query}"
    """

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
    def get_final_answer_prompt(user_query: str, tool_result: dict) -> str:
        rate = tool_result["rate"]
        from_curr = tool_result["from"]
        date = tool_result.get("date", "сегодня")
    #     return f"""
    # Ты — дружелюбный финансовый ассистент.
    # Пользователь спросил: "{user_query}"
    # Ты знаешь: курс ЦБ РФ на {date}: 1 {from_curr} = {rate} RUB.
    #
    # Сформулируй краткий, точный и вежливый ответ.
    # Не упоминай технические детали (ЦБ, API и т.д.), просто дай ответ.
    # """
        return f"""
    Спросил "{user_query}"
    Курс ЦБ РФ на {date}: 1 {from_curr} = {rate} RUB.

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

    # def llm_agent_step(self, user_message: str):
    #     prompt1 = GigaChatApi.get_action_prompt(user_message)
    #     #action_response = '{ "action": "get_exchange_rate", "params": { "from_currency": "USD", "to_currency": "RUB" } }'  # Заглушка
    #     action_response = self.take_answer(prompt1)
    #     print(action_response)
    #     action = GigaChatApi.parse_llm_json(action_response)
    #     print(user_message)
    #     if action.get("action") != "get_exchange_rate":
    #         return self.take_answer(user_message)
    #     print("STEP 2")
    #     # === ШАГ 2: Выполняем инструмент ===
    #     from_curr = action["params"]["from_currency"]
    #     try:
    #         tool_resp = requests.get(f"http://localhost:{PORT_CRB_SERVICE}/rate?from={from_curr}", timeout=5)
    #         if tool_resp.status_code == 200:
    #             tool_data = tool_resp.json()
    #             if "rate" in tool_data:
    #                 prompt2 = GigaChatApi.get_final_answer_prompt(user_message, tool_data)
    #                 return self.take_answer(prompt2)
    #             else:
    #                 return "Не удалось определить курс валюты."
    #         else:
    #             return "Сервис курсов временно недоступен."
    #     except Exception as e:
    #         return "У нас отвалился сервис актуализации курса ЦБ."

    def llm_agent_step(self, user_message: str):
        context = self.rag.retrieve(user_message)
        print(context)
        rag_result = GigaChatApi.rag_prompt(user_message, context)
        action_response = self.take_answer(rag_result)
        return action_response

# llm_client = GigaChatApi(API_GIGACHAT)
# # print(llm.take_answer("Привет"))
# # print(llm_client.dummy_answer("Hello world"))
# #
# print(llm_client.llm_agent_step("О многопоточности в Java?"))