import json
import re

from langchain_core.messages import SystemMessage, HumanMessage
from .core.agent_provider import get_model



class LLMProcessor:
    def __init__(self, model=None):
        self.model = model or get_model()

    def _extract_json(self, text: str) -> dict:
        text = (text or "").strip()

        try:
            return json.loads(text)
        except Exception:
            pass

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))

        raise ValueError(f"LLM did not return valid JSON: {text}")

    def translate_abstract(self, title: str, abstract: str) -> str:
        system_prompt = """
Ты переводишь abstract научной статьи на русский язык.
Верни только чистый перевод без пояснений и markdown.
Сохраняй научный стиль.
Сохраняй термины, определения, названия инструментов на оригинальном языке
"""

        user_prompt = f"""
Название статьи:
{title}

Abstract:
{abstract}
"""

        response = self.model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])

        return (response.content or "").strip()

    def judge_relevance(self, user_query: str, title: str, translated_abstract: str) -> bool:
        system_prompt = """
Ты определяешь, релевантна ли статья запросу пользователя.
Верни только JSON без markdown.

Формат:
{"is_relevant": true}

или

{"is_relevant": false}
"""

        user_prompt = f"""
Запрос пользователя:
{user_query}

Название статьи:
{title}

Перевод abstract:
{translated_abstract}
"""

        response = self.model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])

        data = self._extract_json(response.content)
        return bool(data.get("is_relevant", False))

    def process_article(self, user_query: str, title: str, abstract: str) -> dict:
        translated_text = self.translate_abstract(title=title, abstract=abstract)
        print(translated_text)
        is_relevant = self.judge_relevance(
            user_query=user_query,
            title=title,
            translated_abstract=translated_text,
        )

        return {
            "translated_text": translated_text,
            "is_relevant": is_relevant,
        }