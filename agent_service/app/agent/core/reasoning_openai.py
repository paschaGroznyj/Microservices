"""ChatOpenAI, который не теряет текст рассуждений из стрима.

Cloud.ru (OpenAI-совместимый endpoint) отдаёт reasoning отдельным полем
``delta.reasoning_content`` (у части провайдеров ``delta.reasoning``).
langchain-openai 0.3.x переносит из delta только content/role/tool_calls/
function_call, поэтому поле пропадало. Здесь оно кладётся в
``additional_kwargs["reasoning_content"]``: при сложении чанков строки
склеиваются, и event_loop читает его оттуда же. Без стрима (фолбэк на
ainvoke) поле берётся из ``message.reasoning`` / ``message.reasoning_content``.

prompt_tokens_exclude_cached: у части моделей (GigaChat 4) prompt_tokens
приходит БЕЗ закешированной части, а остальной код считает, что input_tokens
её включает. Флаг досчитывает cached_tokens в prompt_tokens/total_tokens.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
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
            # без чистки: strip() на чанке съедает пробелы и переносы на стыках;
            # markdown снимает ReasoningCleaner в event_loop на склеенном тексте
            return val
    return ""


def include_cached_in_prompt(usage: Any) -> Any:
    """Копия usage, где prompt_tokens и total_tokens включают cached_tokens."""
    if not isinstance(usage, dict):
        return usage
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    if not isinstance(cached, int) or cached <= 0:
        return usage
    fixed = dict(usage)
    fixed["prompt_tokens"] = int(usage.get("prompt_tokens") or 0) + cached
    if "total_tokens" in usage:
        fixed["total_tokens"] = int(usage.get("total_tokens") or 0) + cached
    return fixed


class ChatOpenAIWithReasoning(ChatOpenAI):
    prompt_tokens_exclude_cached: bool = False

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        if self.prompt_tokens_exclude_cached and isinstance(chunk.get("usage"), dict):
            chunk = {**chunk, "usage": include_cached_in_prompt(chunk["usage"])}
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

    def _create_chat_result(self, response: Any, generation_info: Optional[dict] = None) -> ChatResult:
        data = response if isinstance(response, dict) else response.model_dump()
        if self.prompt_tokens_exclude_cached and isinstance(data.get("usage"), dict):
            data = {**data, "usage": include_cached_in_prompt(data["usage"])}
        result = super()._create_chat_result(data, generation_info)
        for gen, choice in zip(result.generations, data.get("choices") or []):
            msg = (choice or {}).get("message") or {}
            text = next((msg[k] for k in REASONING_KEYS if isinstance(msg.get(k), str) and msg[k]), "")
            if text and isinstance(gen.message, AIMessage):
                gen.message.additional_kwargs["reasoning_content"] = text
        return result
