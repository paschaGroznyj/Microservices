"""
agent_pm/judge.py

LLM-as-judge для финального ответа агента.

Живёт рядом с langchain_agent.py. Задача — в финальной точке process_query
(модель вернула ответ без tool_calls) быстро проверить: соответствует ли
финальный ответ исходному запросу пользователя.

Свойства:
- судит ТОЛЬКО финальный ответ + исходный запрос (+ короткий tool_trace);
- использует ТУ ЖЕ модель, что и основной агент (передаётся llm снаружи);
- завернуть ответ можно максимум ОДИН раз (лимит контролируется снаружи);
- fail-open: любая ошибка судьи → вердикт pass, чтобы не ломать выдачу.
"""

import json
import logging
import re

from langchain.schema import HumanMessage, SystemMessage

from .prompts.judge_prompt import JUDGE_SYSTEM_PROMPT, JUDGE_USER_TEMPLATE

logger = logging.getLogger(__name__)

_MAX_ANSWER_CHARS = 6000
_MAX_TRACE_CHARS = 2000


def _extract_json(text: str):
    """Достаёт JSON-вердикт из ответа модели, устойчиво к markdown-обёртке."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("\x60\x60\x60"):
        text = re.sub(r"^\x60\x60\x60[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?\x60\x60\x60$", "", text).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


async def judge_final_answer(llm, user_input, final_response, tool_trace=None):
    """Оценивает финальный ответ агента.

    :param llm: модель для судьи (та же, что у основного агента).
    :param user_input: исходный запрос пользователя.
    :param final_response: финальный ответ агента (без tool_calls).
    :param tool_trace: краткий след использованных инструментов.
    :return: dict {"verdict": "pass"|"revise", "reason": str, "missing": str}.
             При любой ошибке — verdict=pass (fail-open).
    """
    if not (final_response or "").strip():
        return {
            "verdict": "revise",
            "reason": "Финальный ответ пустой — вероятно, цикл оборвался до выдачи результата.",
            "missing": "Сформировать содержательный ответ на запрос пользователя.",
        }

    answer = final_response.strip()[:_MAX_ANSWER_CHARS]
    trace_text = "\n".join(tool_trace or [])[:_MAX_TRACE_CHARS] or "(инструменты не использовались)"

    user_msg = JUDGE_USER_TEMPLATE.format(
        user_input=(user_input or "").strip()[:_MAX_ANSWER_CHARS],
        tool_trace=trace_text,
        final_response=answer,
    )

    try:
        response = await llm.ainvoke([
            SystemMessage(content=JUDGE_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)

        if not parsed or "verdict" not in parsed:
            logger.warning(f"[judge] не удалось распарсить вердикт, raw={raw[:300]!r} → fail-open pass")
            return {"verdict": "pass", "reason": "Судья вернул неразборчивый ответ, ответ пропущен.", "missing": ""}

        verdict = str(parsed.get("verdict", "pass")).lower().strip()
        if verdict not in ("pass", "revise"):
            verdict = "pass"

        result = {
            "verdict": verdict,
            "reason": str(parsed.get("reason", "")).strip(),
            "missing": str(parsed.get("missing", "")).strip(),
        }
        logger.info(f"[judge] verdict={result['verdict']} reason={result['reason'][:200]}")
        return result

    except Exception as e:
        logger.error(f"[judge] ошибка вызова судьи: {type(e).__name__}: {e} → fail-open pass", exc_info=True)
        return {"verdict": "pass", "reason": f"Судья недоступен ({type(e).__name__}), ответ пропущен.", "missing": ""}
