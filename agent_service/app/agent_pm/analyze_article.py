"""
Process Mining Relevance Analyzer
Модуль для оценки релевантности статей по Process Mining для внутреннего аудита банка
"""

from pydantic import BaseModel, Field
from typing import Dict, Any
from datetime import datetime
import re
from langchain_core.messages import SystemMessage

class RelevanceScore(BaseModel):
    """Pydantic модель для структурированного вывода оценки релевантности"""
    criterion_1: float = Field(..., description="Новый подход/алгоритм и воспроизводимость (0.0-1.0)")
    criterion_2: float = Field(..., description="Практические примеры и воспроизводимость (0.0-1.0)")
    criterion_3: float = Field(..., description="Код/репозитории (0.0-1.0)")
    criterion_4: float = Field(..., description="Применимость к аудиту банка (0.0-1.0)")
    
    overall_score: float = Field(..., description="Общая оценка релевантности (0.0-1.0, шаг 0.1)")
    is_relevant: bool = Field(..., description="True если overall_score >= 0.6")
    reasoning: str = Field(..., description="Краткие обоснования по каждому критерию")

class RelevanceAnalyzer:
    """
    Анализатор релевантности статей для Process Mining и внутреннего аудита банка
    """
    
    def __init__(self, llm_manager):
        """
        llm_manager: объект LLM (LangChain manager или аналог)
        """
        self.manager = llm_manager
    
    def assess_relevance(self, content: str, filename: str = None) -> Dict[str, Any]:
        """
        Оценивает релевантность статьи по 4 критериям Process Mining.
        
        Args:
            content: текст статьи для анализа
            filename: имя файла (для контекста)
            
        Returns:
            dict с полями: status, is_relevant, overall_score, scores, reasoning
        """
        prompt = SystemMessage(content=f"""Тебе на вход поступает текст статьи из "{filename or 'неизвестный файл'}".

Твоя задача: оценить, насколько статья относится к **process mining** и интересна для подробного разбора службой внутреннего аудита банка.

## КРИТЕРИИ ОЦЕНКИ (каждый 0.0-1.0, шаг 0.1):
1. **Новый подход/алгоритм**: Есть ли оригинальный метод в process mining? Можно ли воспроизвести алгоритм по описанию?
2. **Практические примеры**: Есть ли case studies по process mining? Можно ли повторить на своих данных?
3. **Код/репозитории**: Есть ли код, GitHub ссылки, ProM плагины или конкретные инструменты?
4. **Аудит банка**: Применим ли подход к анализу банковских процессов (кредитование, compliance, операции)?

## ПРАВИЛА:
- Факты БЕРИ ТОЛЬКО ИЗ ТЕКСТА статьи
- Оценка с шагом 0.1: 0.0, 0.1, 0.2, ..., 1.0
- overall_score = среднее арифметическое по 4 критериям (округление до 0.1)
- is_relevant = true ТОЛЬКО если overall_score >= 0.6

## СТРОГО JSON формат ответа:
{{
  "criterion_1": 0.7,
  "criterion_2": 0.4, 
  "criterion_3": 0.9,
  "criterion_4": 0.6,
  "overall_score": 0.7,
  "is_relevant": true,
  "reasoning": "Краткие обоснования по пунктам: ..."
}}

## Текст статьи для анализа:
{content}""")
        
        try:
            # Вызов LLM
            response = self.manager.invoke([prompt])
            print("ОТВЕТ", response.content)
            raw_output = response.content.strip()
            
            # Извлекаем JSON из ответа
            json_match = re.search(r'\{.*\}', raw_output, re.DOTALL)
            if not json_match:
                raise ValueError("LLM не вернул корректный JSON")
            
            # Валидация через Pydantic
            parsed = RelevanceScore.model_validate_json(json_match.group())
            
            return {
                "status": "success",
                "filename": filename,
                "assessed_at": datetime.now().isoformat(),
                "is_relevant": parsed.is_relevant,
                "overall_score": round(float(parsed.overall_score), 1),
                "scores": {
                    "new_method": round(float(parsed.criterion_1), 1),
                    "practical_examples": round(float(parsed.criterion_2), 1),
                    "code_repos": round(float(parsed.criterion_3), 1),
                    "bank_audit": round(float(parsed.criterion_4), 1)
                },
                "reasoning": parsed.reasoning,
                "raw_llm_output": raw_output[:500] + "..." if len(raw_output) > 500 else raw_output
            }
            
        except Exception as e:
            return {
                "status": "error",
                "filename": filename,
                "error": str(e),
                "assessed_at": datetime.now().isoformat(),
                "fallback": {
                    "is_relevant": False, 
                    "overall_score": 0.0,
                    "reasoning": "Ошибка обработки LLM ответа"
                }
            }


# ИСПОЛЬЗОВАНИЕ

# gigachat = GigaChat(
#     access_token="",
#     verify_ssl_certs=False,
#     model="GigaChat-2-Max"
#     # ...
# )

# analyzer = RelevanceAnalyzer(gigachat)

# with open('agent_pm/knowledge_base/articles/2026-02-21_62a35fede9c1.md', 'r', encoding='utf-8') as file:
#     text = file.read()

# # print(text)

# result = analyzer.assess_relevance(text, '2026-02-21_62a35fede9c1.md')

# print(result)