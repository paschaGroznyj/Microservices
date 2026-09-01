"""
tools/vision/tool_analyze_image.py
───────────────────────────────────
Тул анализа изображений (JPEG/PNG) через мультимодальный LLM (claude-opus-4.8).

Ключевая идея: base64 картинок НЕ таскается через контекст модели.
Эндпоинт /web/upload_files кладёт base64 в pending_files, handle_message
прокидывает список картинок в process_query(..., images=[...]), а тот —
в _build_tools(..., images=images). Тул захватывает images в замыкание.

Модель при вызове пишет ТОЛЬКО промпт (и опц. индекс картинки) —
сама base64-строка дёргается из замыкания, контекст не засоряется
и base64 не режется по числу символов.

Подключение в langchain_agent.py::_build_tools:
    from .tools.vision.tool_analyze_image import make_analyze_image_tool
    analyze_image = make_analyze_image_tool(agent, images)
    # добавить analyze_image в base_tools
"""

import logging
from langchain.tools import tool
from langchain.schema import HumanMessage

log = logging.getLogger(__name__)


def make_analyze_image_tool(agent, images: list[dict] | None = None,
                            agent_model: str | None = None):
    """
    Фабрика тула анализа изображений.

    :param agent: экземпляр KnowledgeAgent — источник AGENT_MODELS.
    :param images: список картинок текущего запроса, каждая:
        {"filename": str, "mime_type": "image/png"|"image/jpeg", "b64": "<base64>"}
        base64 БЕЗ префикса data: (или с ним — обработаем оба случая).
    :param agent_model: активная модель основного цикла ('coder' | 'designer' |
        'universal'). Определяет, какой vision-моделью распознавать картинку:
          - coder      → gpt (get_llm_model_gpt): если пользователь работает в
                         gpt-режиме, распознаём тоже через gpt;
          - universal  → gemini (умеет в картинки);
          - designer   → claude (мультимодальный opus).
        Если модель неизвестна/не задана — фолбэк на designer (claude).
    """
    images = images or []

    # Выбор vision-модели по активной модели основного цикла.
    # coder — свой gpt; universal — gemini; всё остальное — claude (designer).
    VISION_BY_MODEL = {
        "coder": "coder",
        "universal": "universal",
        "designer": "designer",
    }
    vision_key = VISION_BY_MODEL.get(agent_model or "", "designer")
    vision_llm = agent.AGENT_MODELS.get(vision_key) or agent.AGENT_MODELS["designer"]
    log.info("analyze_image: agent_model=%s → vision-модель '%s'",
             agent_model, vision_key)

    # Краткая опись картинок для подсказки модели в docstring не вставить (он статичен),
    # поэтому кладём в имена — модель видит их из текста сообщения ([IMAGE #N: ...]).
    n_images = len(images)

    @tool
    async def analyze_image(prompt: str, image_index: int = 0) -> str:
        """
        Анализирует изображение (JPEG/PNG), приложенное пользователем к текущему
        сообщению, и отвечает на вопрос о нём: описать, распознать текст/диаграмму,
        извлечь данные из графика/таблицы, оценить содержимое схемы и т.п.

        Картинки уже загружены на сервер — тебе НЕ нужно передавать base64.
        В тексте сообщения они помечены маркерами вида [IMAGE #0: file.png].
        Ты передаёшь ТОЛЬКО:
          prompt      — что именно сделать с изображением (вопрос/задача для vision-модели)
          image_index — номер картинки из маркера (0 — первая, по умолчанию 0)

        Если картинок несколько — вызывай тул отдельно на каждый нужный индекс.
        """
        if not images:
            return ("К текущему сообщению не приложено ни одного изображения "
                    "(JPEG/PNG). Попроси пользователя загрузить картинку.")

        if image_index < 0 or image_index >= n_images:
            avail = ", ".join(
                f"#{i} ({img.get('filename', '?')})" for i, img in enumerate(images)
            )
            return (f"Нет картинки с индексом {image_index}. "
                    f"Доступны: {avail}.")

        img = images[image_index]
        b64 = (img.get("b64") or "").strip()
        if not b64:
            return f"Картинка #{image_index} пустая (нет base64-данных)."

        mime = img.get("mime_type") or "image/png"
        data_uri = b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"

        try:
            msg = HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ])
            resp = await vision_llm.ainvoke([msg])
            content = resp.content if hasattr(resp, "content") else str(resp)
            fname = img.get("filename", f"image_{image_index}")
            log.info("analyze_image: обработана картинка %s (%d байт b64)",
                     fname, len(b64))
            return content
        except Exception as e:
            log.error("analyze_image failed: %s", e, exc_info=True)
            return f"Ошибка анализа изображения: {e}"

    return analyze_image
