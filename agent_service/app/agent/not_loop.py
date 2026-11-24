"""
not_loop.py — режим «Модель» (type_query="model"): чистая языковая модель без тулов.

Никакого event-loop'а, супервизора, сжатия контекста, журнала и саммари:
  история (последние 10 запросов = до 20 сообщений) -> обрезка по токенам ->
  один стриминговый вызов модели из build_agent_models -> ответ.

Транспорт (стрим чанков, FlowMap, учёт токенов) переиспользуется у HarnessAgent,
поэтому фронт, Redis flow и сохранение в БД работают так же, как для агента.
"""
from .core.timezone_utils import HISTORY_TIME_NOTE, user_tzinfo
from .core.observability import request_logged

import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import logging
import time
from app.agent.common.context_limits import context_payload
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from langchain.schema import HumanMessage, SystemMessage
from langchain_core.messages import BaseMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from .core.agent_provider import get_model_spec, get_real_model_name, resolve_model_tag
from .core.content_stream import ContentStreamRouter
from .core.reasoning_clean import ReasoningCleaner, clean_reasoning
from .core.token_calibration import CALIBRATOR
from .common.trace_budget import attach_traces
from .prompts.system_prompt import DEFAULT_AGENT_IDENTITY, INTERACTIVE_POLL_GUIDANCE
from .title_generator import generate_chat_title, is_first_turn

logger = logging.getLogger(__name__)

# 10 последних запросов = 10 пар human/ai
MODEL_HISTORY_REQUESTS = 10
MODEL_CONTEXT_TOKENS = {"small": 7_500, "medium": 15_000, "large": 30_000}

MAUDEL_IDENTITY = (
    "# Режим «Модель»\n"
    "Сейчас ты работаешь не как агент, а как MauDel — языковая модель AuGent без инструментов. "
    "У тебя нет доступа к интернету, базе знаний, файлам, песочнице, почте и планировщику, "
    "ты не можешь выполнять код или сохранять данные. Отвечай из собственных знаний и истории диалога. "
    "Если тебя называют агентом — мягко поправь, что ты MauDel. "
    "Если для ответа нужны инструменты (поиск, файлы, БД, запуск кода, актуальные данные), "
    "честно скажи об этом и предложи переключиться на режим «Агент» в пилюле у кнопки отправки. "
    "Не выдумывай результаты действий, которые ты не можешь выполнить."
)

MODEL_SYSTEM_PROMPT = DEFAULT_AGENT_IDENTITY + "\n\n" + MAUDEL_IDENTITY + "\n" + INTERACTIVE_POLL_GUIDANCE

EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}


MODEL_EMPTY_RETRIES = 2
MODEL_EMPTY_FALLBACK = (
    "Модель вернула пустой ответ. Повторите запрос или переключитесь на режим «Агент»."
)


def _content_of(response) -> tuple[str, str, str]:
    from .event_loop import _unwrap_escaped_tags

    raw = getattr(response, "content", "")
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        raw = "".join(parts)
    router = ContentStreamRouter()
    # Complete content needs no envelope buffering, but still obeys private scopes.
    router.started = True
    router.feed(_unwrap_escaped_tags(str(raw or "")))
    router.flush()
    return router.thinking_text, router.private_text, router.answer_text


def _native_reasoning_of(response) -> str:
    kwargs = getattr(response, "additional_kwargs", {}) or {}
    native = kwargs.get("reasoning_content") or kwargs.get("reasoning")
    parts = [native] if isinstance(native, str) else []
    raw = getattr(response, "content", None)
    if isinstance(raw, list):
        parts.extend(
            str(b.get("thinking") or "") for b in raw
            if isinstance(b, dict) and b.get("type") == "thinking"
        )
    return "".join(parts)


def _text_of(response) -> str:
    return _content_of(response)[2].strip()


def _shape_of(response) -> str:
    """Короткая диагностика пустого ответа: типы блоков и stop_reason."""
    raw = getattr(response, "content", None)
    if isinstance(raw, list):
        kinds = [b.get("type", "?") if isinstance(b, dict) else type(b).__name__ for b in raw]
    else:
        kinds = [f"str[{len(raw or '')}]"]
    meta = getattr(response, "response_metadata", {}) or {}
    stop = meta.get("stop_reason") or meta.get("finish_reason")
    return f"blocks={kinds} stop={stop}"


class LLModel:
    """Однопроходная модель без тулов поверх транспорта HarnessAgent."""

    def __init__(self, agent):
        self.agent = agent  # HarnessAgent: модели, история из БД, стрим, flow, токены

    async def _history(self, chat_id, max_tokens: int, model_tag: str | None = None,
                       tz=None) -> List[BaseMessage]:
        history = await self.agent._load_history_from_db(chat_id, MODEL_HISTORY_REQUESTS * 2, tz=tz)
        counter = count_tokens_approximately
        cpt = 4.0  # дефолт count_tokens_approximately
        if model_tag:
            # тот же счётчик, что у агента: chars_per_token модели с калибровкой
            spec = get_model_spec(model_tag, self.agent.AGENT_MODELS)
            counter = CALIBRATOR.counter(model_tag, spec.chars_per_token)
            cpt = CALIBRATOR.chars_per_token(model_tag, spec.chars_per_token)
        history = attach_traces(history, cpt)
        return trim_messages(
            history,
            max_tokens=max_tokens,
            token_counter=counter,
            strategy="last",
            start_on="human",
            include_system=True,
        )

    @staticmethod
    def _now(timezone_name: str | None) -> str:
        try:
            tz = ZoneInfo(timezone_name or "Europe/Moscow")
        except Exception:
            tz = ZoneInfo("Europe/Moscow")
        return datetime.now(tz).strftime("%d.%m.%Y %H:%M, %A") + f" ({tz.key})"

    async def build_messages(self, user_input: str, chat_id, context_size: str | None,
                             timezone_name: str | None, model_tag: str | None = None) -> List[BaseMessage]:
        max_tokens = MODEL_CONTEXT_TOKENS.get(context_size or "medium", MODEL_CONTEXT_TOKENS["medium"])
        history = await self._history(chat_id, max_tokens, model_tag, tz=user_tzinfo(timezone_name))
        return [
            SystemMessage(content=MODEL_SYSTEM_PROMPT),
            SystemMessage(content=(
                f"🕒 ТЕКУЩИЙ КОНТЕКСТ:\nCurrent date and time: {self._now(timezone_name)}\n{HISTORY_TIME_NOTE}"
            )),
            *history,
            HumanMessage(content=user_input),
        ]

    @request_logged
    async def process_query(
        self,
        user_input: str,
        request_id: str,
        user_id: int,
        chat_id,
        agent_model: str,
        context_size: str | None = None,
        gateway_target: str = "main",
        timezone_name: str | None = None,
        **_ignored,
    ) -> Dict[str, Any]:
        from .event_loop import _astream_collect  # ленивый импорт: без цикла модулей

        agent = self.agent
        target = (gateway_target or "main").lower()
        ctx_token = agent._request_ctx.set({
            "request_id": request_id, "gateway_target": target, "chat_id": chat_id,
            "user_id": user_id, "agent_model": agent_model, "tool_usage": {},
            "tg_reasoning_buf": [],
        })
        agent._gateway_target = target
        if request_id:
            agent._flow_last[request_id] = time.monotonic()
        await agent._send_flow(request_id, "agent_start")
        try:
            tag = resolve_model_tag(agent_model, agent.AGENT_MODELS)
            llm = agent.AGENT_MODELS[tag]
            real_model = get_real_model_name(llm)
            messages = await self.build_messages(user_input, chat_id, context_size, timezone_name, model_tag=tag)
            logger.info(
                f"[model] type_query=model chat={chat_id} model={tag} ({real_model}) "
                f"messages={len(messages)}"
            )

            content_router = ContentStreamRouter()
            reasoning_cleaner = ReasoningCleaner()
            native_reasoning = []
            stream_state = {"reasoning": False, "answer": False, "thinking": False}
            reasoning_trace, internal_reasoning_trace = [], []

            async def send(text, kind):
                await agent._send_chunk(text, request_id, msg_id=str(request_id), kind=kind)

            async def emit_reasoning(text):
                piece = reasoning_cleaner.feed(text)
                if piece and (stream_state["reasoning"] or piece.strip()):
                    await send(piece, "live")
                    stream_state["reasoning"] = True

            async def close_reasoning():
                tail = reasoning_cleaner.flush()
                if tail and (stream_state["reasoning"] or tail.strip()):
                    await send(tail, "live")
                    stream_state["reasoning"] = True
                if stream_state["reasoning"]:
                    await send("", "reasoning_end")
                    stream_state["reasoning"] = False

            async def emit_content(events):
                for channel, piece in events:
                    if channel == "reasoning":
                        stream_state["thinking"] = True
                        await emit_reasoning(piece)
                    else:
                        await close_reasoning()
                        await send(piece, "answer_delta")
                        stream_state["answer"] = True

            async def reset_stream():
                nonlocal content_router, reasoning_cleaner
                await close_reasoning()
                if stream_state["answer"]:
                    await send("", "answer_reset")
                stream_state.update(reasoning=False, answer=False, thinking=False)
                native_reasoning.clear()
                reasoning_trace.clear()
                internal_reasoning_trace.clear()
                content_router = ContentStreamRouter()
                reasoning_cleaner = ReasoningCleaner()

            async def on_chunk(text: str, kind: str = "answer"):
                if kind == "retry":
                    await reset_stream()
                elif kind == "reasoning" and isinstance(text, str) and text:
                    native_reasoning.append(text)
                    await emit_reasoning(text)
                elif kind == "answer" and isinstance(text, str):
                    await emit_content(content_router.feed(text))

            from .common.context_usage import prompt_weights, scale_breakdown
            prompt_estimate = prompt_weights(messages)
            response, final = None, ""
            for attempt in range(MODEL_EMPTY_RETRIES + 1):
                if attempt:
                    await reset_stream()
                try:
                    response = await _astream_collect(llm, messages, on_chunk=on_chunk, error_info={})
                except Exception as e:
                    logger.error(f"[model] вызов модели упал: {type(e).__name__}: {e}")
                    response = None
                    break
                if response is None:
                    break
                await emit_content(content_router.flush())
                thinking, internal, answer = _content_of(response)
                native = "".join(native_reasoning) or _native_reasoning_of(response)
                if native:
                    reasoning_trace.append(clean_reasoning(native))
                    if not native_reasoning:
                        await emit_reasoning(native)
                if thinking:
                    reasoning_trace.append(clean_reasoning(thinking))
                    if not stream_state["thinking"]:
                        await emit_reasoning(thinking)
                if internal:
                    internal_reasoning_trace.append(internal)
                await close_reasoning()
                final = answer.strip()
                if final:
                    break
                logger.warning(
                    f"[model][empty_response] chat={chat_id} попытка {attempt + 1}/{MODEL_EMPTY_RETRIES + 1} "
                    f"{_shape_of(response)}"
                )

            if response is None:
                final = "⚠️ Модель сейчас недоступна. Попробуйте повторить запрос через пару минут."
            elif not final:
                final = MODEL_EMPTY_FALLBACK
            await close_reasoning()
            await send(final, "answer_end")
            await send("", "final_commit")

            from .core.token_usage import cache_read_known
            usage = agent.track_tokens(response) if response is not None else dict(EMPTY_USAGE)
            tokens_usage = {**usage, "total_tokens": usage["input_tokens"] + usage["output_tokens"]}
            tokens_usage.update(context_tokens=usage["input_tokens"],
                                context_cache_read_tokens=(usage["cache_read_tokens"] if cache_read_known(response) else None),
                                context_breakdown=scale_breakdown(prompt_estimate, usage["input_tokens"]))
            logger.info(
                f"[tokens] type_query=model in={usage['input_tokens']} out={usage['output_tokens']} "
                f"cached={usage['cache_read_tokens']}"
            )

            # Автозаголовок по первому обмену — как у агента (fail-open).
            auto_title = None
            try:
                if final and agent.db and chat_id is not None and await is_first_turn(agent.db.pool, chat_id):
                    auto_title = await generate_chat_title(agent._lite_ainvoke, user_input, final)
            except Exception as e:
                logger.error(f"[model][title] chat={chat_id}: {e}")

            _steps = 1 if response is not None else 0
            context = context_payload(tokens_usage)
            if context is not None:
                context["agent"] = {"steps": _steps, "tool_calls": 0}
            return {
                "input": user_input,
                "message": final,
                "used_tools": [],
                "action": "",
                "agent_steps": _steps,
                "tool_calls": [],
                "success": response is not None,
                "files_to_send": [],
                "tool_trace": None,
                "agent_model": real_model,
                "tokens_usage": tokens_usage,
                "context": context,
                "auto_title": auto_title,
                "type_query": "model",
            }
        finally:
            agent._request_ctx.reset(ctx_token)
            agent._flow_last.pop(request_id, None)
