import json
import re

from langchain.schema import SystemMessage, HumanMessage
from core.agent_provider import get_model



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

    def process_article(self, user_query: str, title: str, abstract: str) -> dict:
        system_prompt = """
    Ты обрабатываешь abstract научной статьи.
    Верни только JSON без markdown и пояснений.
    
    Формат ответа:
    {
      "translated_abstract": "перевод abstract на русский язык",
      "is_relevant": true
    }
    
    Правила перевода:
    - Сохраняй научный стиль
    - Термины, названия инструментов оставляй на оригинальном языке
    
    Правила определения релевантности:
    - true если статья относится к запросу пользователя
    - false если не относится
    """

        user_prompt = f"""
    Запрос пользователя:
    {user_query}
    
    Название статьи:
    {title}
    
    Abstract:
    {abstract}
    """

        response = self.model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])

        data = self._extract_json(response.content)

        return {
            "translated_abstract": data.get("translated_abstract", ""),
            "is_relevant": bool(data.get("is_relevant", False)),
        }

annotation = "The emerging deep learning (DL) technology has recently exhibited great potential in data-driven short-term voltage stability (SVS) assessment of complex power grids. However, without sufficient attention to the time-varying topological structures of today's power grids, the majority of existing DL-based SVS assessment schemes could experience severe performance degradation in practice. To address this drawback, this paper proposes an adaptive spatial-temporal graph learning-enabled SVS assessment approach that can adapt well to various topological changes. First, considering the time-varying topological conditions of a given power grid, an adaptive graph representation matrix is automatically learned to effectively capture the complicated spatial correlations between individual buses within the grid. Then, to help better capture regional SVS features for subsequent learning processes, the adaptive graph representation matrix is properly adjusted by introducing a spatial attention mechanism. Further, with post-fault system trajectory data linked together via attention-based graph representation, a residual spatiotemporal graph convolutional network is carefully built with Optuna-based optimization to deeply mine system-wide spatiotemporal features and thus achieve structure-adaptive SVS assessment. Numerical test results on two representative sub-systems of a realistic provincial power grid in South China demonstrate the efficacy of the proposed approach under various changing topological conditions."
query = 'Графовые подходы это что такое?'
title = 'Adaptive Spatial-Temporal Graph Learning-Enabled Short-Term Voltage Stability Assessment against Time-Varying Topological Conditions'

llm = LLMProcessor()
print(llm.process_article(user_query=query, title=title, abstract=annotation))