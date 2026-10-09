"""LITE-раннер для мобильного оркестратора.

Дешёвая модель (Haiku) обрабатывает ВСЕ запросы мобилки по умолчанию,
без тулов и без тяжёлого event-loop'а. Задача раннера — один вызов LLM
с LITE_INSTRUCTION, с цепочкой фолбэков (haiku -> gpt-mini -> gemini-flash),
и возврат сырого текста (JSON-строки). Парсинг и валидация — в
вызывающем коде через action_parser.parse_mobile_response.
Сам агент (process_query) СЮДА НЕ ВОВЛЕКАЕТСЯ.
"""
import logging

logger = logging.getLogger(__name__)


async def run_lite(user_text: str, persona: str = None) -> str:
    """Один вызов лайт-модели. Возвращает сырой текст ответа (ожидается JSON).

    persona — ключ персоны из payload (peyto/atlantic). Её характер-блок
    клеится в LITE-инструкцию, чтобы дешёвая модель говорила тем же голосом,
    что и полный агент (единый герой).

    Цепочка фолбэков зеркалит event_loop: haiku -> gpt-mini -> gemini-flash.
    При полном отказе всех моделей возвращает fallback-JSON (голос не теряется).
    """
    from agent.mobile.mobile_settings import build_lite_instruction
    from agent.mobile.personas import get_persona_prompt
    from agent.core.agent_provider import (
        get_llm_lite_model,
        get_llm_lite_model_gpt_mini,
        get_llm_lite_model_gemini_flash,
    )

    instruction = build_lite_instruction(get_persona_prompt(persona))

    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": user_text or ""},
    ]

    chain = [
        ("haiku", get_llm_lite_model),
        ("gpt-mini", get_llm_lite_model_gpt_mini),
        ("gemini-flash", get_llm_lite_model_gemini_flash),
    ]

    last_err = None
    for name, factory in chain:
        try:
            llm = factory()
            resp = await llm.ainvoke(messages)
            content = getattr(resp, "content", None)
            if isinstance(content, list):
                # некоторые провайдеры отдают content блоками
                content = "".join(
                    (c.get("text", "") if isinstance(c, dict) else str(c)) for c in content
                )
            content = (content or "").strip()
            if content:
                if name != "haiku":
                    logger.warning("[mobile-lite] ответила фолбэк-модель %s", name)
                return content
        except Exception as e:
            last_err = e
            logger.warning("[mobile-lite] модель %s отказала: %s", name, e)
            continue

    logger.error("[mobile-lite] все лайт-модели отказали: %s", last_err)
    # fallback-JSON: голос не теряем, агента не зовём
    return (
        '{"v":1,"speech":"Извините, сейчас не могу ответить. Попробуйте ещё раз.",'
        '"actions":[],"needs_agent":false,"call_agent":false}'
    )
