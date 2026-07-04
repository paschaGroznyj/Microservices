"""
Process Mining Relevance Analyzer
Модуль для оценки релевантности статей по Process Mining для внутреннего аудита банка
"""

from pydantic import BaseModel, Field
from typing import Dict, Any
from datetime import datetime
import re
from langchain.schema import SystemMessage, HumanMessage

class RelevanceScore(BaseModel):
    """Pydantic модель для структурированного вывода оценки релевантности"""
    criterion_1: float = Field(..., description="Прямое упоминание PM и методов (0.0-1.0)")
    criterion_2: float = Field(..., description="Фокус на бизнес-процессах и логах (0.0-1.0)")
    criterion_3: float = Field(..., description="Практическое применение и кейсы (0.0-1.0)")
    criterion_4: float = Field(..., description="Качество, свежесть, глубина (0.0-1.0)")
    
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
    
    async def assess_relevance(self, content: str, filename: str = None) -> Dict[str, Any]:
        """
        Оценивает релевантность статьи по 4 критериям Process Mining.
        
        Args:
            content: текст статьи для анализа
            filename: имя файла (для контекста)
            
        Returns:
            dict с полями: status, is_relevant, overall_score, scores, reasoning
        """
        system_prompt = """Тебе на вход поступает текст статьи из "{filename or 'неизвестный файл'}".

Оцени релевантность для **Process Mining** по строгим критериям:

## ОСНОВНЫЕ КРИТЕРИИ РЕЛЕВАНТНОСТИ (каждый 0.0-1.0):
1. **Прямое упоминание PM**: process mining, discovery, conformance checking, enhancement, 
   event logs, XES-формат, Celonis, UiPath, ProM
   
2. **Фокус на процессах**: бизнес-процессы, анализ логов событий, выявление отклонений, 
   метрики (время, стоимость), операционные/оцифрованные процессы
   
3. **Практическое применение**: кейсы внедрения, этапы (сбор данных, анализ качества, мониторинг), 
   ограничения (массовость, стандартизация)
   
4. **Качество и свежесть**: 2023-2026 гг., реальные примеры (финансы, логистика), 
   инсайты (узкие места, ML), а не общие фразы

## ИСКЛЮЧЕНИЯ (все оценки 0.0):
- Нет связи с process mining (BI, RPA без конкретики)
- Спекуляции, устаревшие данные, неструктурированный текст

## ПРАВИЛА:
- Факты ТОЛЬКО из текста
- Оценка с шагом 0.1: 0.0, 0.1, ..., 1.0
- overall_score = среднее по 4 критериям (округление до 0.1)
- is_relevant = true только если overall_score >= 0.6

## СТРОГО JSON:
{{
  "criterion_1": 0.8,
  "criterion_2": 0.7,
  "criterion_3": 0.6,
  "criterion_4": 0.9,
  "overall_score": 0.8,
  "is_relevant": true,
  "reasoning": "Кратко: 1-упомянут Celonis; 2-event logs; 3-кейс банка..."
}}

## Текст статьи:"""
        
        try:
            # Вызов LLM
            response = await self.manager.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=content[:3500]),
        ])
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