"""ChatOpenAI, который не теряет текст рассуждений из стрима.

Cloud.ru (OpenAI-совместимый endpoint) отдаёт reasoning отдельным полем
``delta.reasoning_content`` (у части провайдеров ``delta.reasoning``).
langchain-openai 0.3.x переносит из delta только content/role/tool_calls/
function_call, поэтому поле пропадало. Здесь оно кладётся в
``additional_kwargs["reasoning_content"]``: при сложении чанков строки
склеиваются, и event_loop читает его оттуда же.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

REASONING_KEYS = ("reasoning_content", "reasoning")


def delta_reasoning(chunk: dict) -> str:
    """Текст рассуждения из одного SSE-чанка или пустая строка."""
    choices = chunk.get("choices") or (chunk.get("chunk") or {}).get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    for key in REASONING_KEYS:
        val = delta.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


class ChatOpenAIWithReasoning(ChatOpenAI):
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen is None or not isinstance(gen.message, AIMessageChunk):
            return gen
        text = delta_reasoning(chunk)
        if text:
            # один ключ на все варианты: иначе при reasoning + reasoning_content текст задвоится
            gen.message.additional_kwargs["reasoning_content"] = text
        return gen
