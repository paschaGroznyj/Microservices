"""
not_loop.py — режим «Модель» (type_query="model"): чистая языковая модель без тулов.

Никакого event-loop'а, супервизора, сжатия контекста, журнала и саммари:
  история (последние 10 запросов = до 20 сообщений) -> обрезка по токенам ->
  один стриминговый вызов модели из build_agent_models -> ответ.

Транспорт (стрим чанков, FlowMap, учёт токенов) переиспользуется у HarnessAgent,
поэтому фронт, Redis flow и сохранение в БД работают так же, как для агента.
"""
import logging
import time
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from langchain.schema import HumanMessage, SystemMessage
from langchain_core.messages import BaseMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from .core.agent_provider import get_real_model_name, resolve_model_tag
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


def _text_of(response) -> str:
    raw = getattr(response, "content", "") if response is not None else ""
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict) and b.get("type") == "text")
    return (raw or "").strip()


class LLModel:
    """Однопроходная модель без тулов поверх транспорта HarnessAgent."""

    def __init__(self, agent):
        self.agent = agent  # HarnessAgent: модели, история из БД, стрим, flow, токены

    async def _history(self, chat_id, max_tokens: int) -> List[BaseMessage]:
        history = await self.agent._load_history_from_db(chat_id, MODEL_HISTORY_REQUESTS * 2)
        return trim_messages(
            history,
            max_tokens=max_tokens,
            token_counter=count_tokens_approximately,
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
                             timezone_name: str | None) -> List[BaseMessage]:
        max_tokens = MODEL_CONTEXT_TOKENS.get(context_size or "medium", MODEL_CONTEXT_TOKENS["medium"])
        history = await self._history(chat_id, max_tokens)
        return [
            SystemMessage(content=MODEL_SYSTEM_PROMPT),
            SystemMessage(content=f"🕒 ТЕКУЩИЙ КОНТЕКСТ:\nCurrent date and time: {self._now(timezone_name)}"),
            *history,
            HumanMessage(content=user_input),
        ]

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
            messages = await self.build_messages(user_input, chat_id, context_size, timezone_name)
            logger.info(
                f"[model] type_query=model chat={chat_id} model={tag} ({real_model}) "
                f"messages={len(messages)}"
            )

            async def on_chunk(text: str, kind: str = "answer"):
                # Сырой reasoning провайдера не показываем — только текст ответа.
                if kind == "answer" and text:
                    await agent._send_chunk(text, request_id, msg_id=str(request_id), kind="live")

            try:
                response = await _astream_collect(llm, messages, on_chunk=on_chunk, error_info={})
            except Exception as e:
                logger.error(f"[model] вызов модели упал: {type(e).__name__}: {e}")
                response = None

            final = _text_of(response)
            if response is None:
                final = "⚠️ Модель сейчас недоступна. Попробуйте повторить запрос через пару минут."
            await agent._send_chunk("", request_id, msg_id=str(request_id), kind="final_commit")

            usage = agent.track_tokens(response) if response is not None else dict(EMPTY_USAGE)
            tokens_usage = {**usage, "total_tokens": usage["input_tokens"] + usage["output_tokens"]}
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

            return {
                "input": user_input,
                "message": final,
                "used_tools": [],
                "action": "",
                "success": response is not None,
                "files_to_send": [],
                "tool_trace": None,
                "agent_model": real_model,
                "tokens_usage": tokens_usage,
                "auto_title": auto_title,
                "type_query": "model",
            }
        finally:
            agent._request_ctx.reset(ctx_token)
            agent._flow_last.pop(request_id, None)
