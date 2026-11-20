import os; os.environ["OPENBLAS_NUM_THREADS"] = "1"
import json

from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import _convert_message_to_dict

from agent.core import agent_provider as ap
from agent.core.reasoning_openai import ChatOpenAIWithReasoning


def _ev(delta=None, usage=None, finish=None):
    ev = {"object": "chat.completion.chunk", "model": "openai/gpt-6.1-sol",
          "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        ev["usage"] = usage
    return ev


# Мини-SSE по форме реального стрима Cloud.ru (GPT, reasoning effort high)
GPT_HIGH_EVENTS = [
    _ev({"role": "assistant"}),
    _ev({"reasoning_content": "**Setting up gap enumeration**"}),
    _ev({"reasoning_content": "**Summing coefficient cases**"}),
    _ev({"content": "There are"}),
    _ev({"content": " twelve gaps"}),
    _ev({}, finish="stop"),
    _ev(usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160,
               "completion_tokens_details": {"reasoning_tokens": 30},
               "prompt_tokens_details": {"cached_tokens": 0}}),
]
# Без reasoning: короткий ответ GigaChat с накопительным usage в каждом чанке
PLAIN_EVENTS = [
    _ev({"role": "assistant", "content": ""}, usage={"prompt_tokens": 79, "completion_tokens": 0, "total_tokens": 79}),
    _ev({"content": "391"}, usage={"prompt_tokens": 79, "completion_tokens": 3, "total_tokens": 82}),
    _ev({}, finish="stop", usage={"prompt_tokens": 79, "completion_tokens": 4, "total_tokens": 83}),
]


def _replay(llm, events):
    agg = None
    for ev in events:
        gen = llm._convert_chunk_to_generation_chunk(ev, AIMessageChunk, None)
        if gen is not None:
            agg = gen.message if agg is None else agg + gen.message
    return agg


def _raw_reasoning(events):
    return "".join(
        (c.get("delta") or {}).get("reasoning_content", "")
        for ev in events for c in (ev.get("choices") or [])
    )


def test_gpt_sse_keeps_reasoning():
    expected = _raw_reasoning(GPT_HIGH_EVENTS)
    assert expected == "**Setting up gap enumeration****Summing coefficient cases**"
    llm = ChatOpenAIWithReasoning(model="openai/gpt-6.1-sol", api_key="replay")
    agg = _replay(llm, GPT_HIGH_EVENTS)
    assert agg.additional_kwargs["reasoning_content"] == expected
    assert agg.additional_kwargs["reasoning_content"] not in agg.content


def test_plain_chatopenai_loses_reasoning():
    # фиксирует саму причину: без подкласса поле пропадает
    agg = _replay(ChatOpenAI(model="x", api_key="replay"), GPT_HIGH_EVENTS)
    assert "reasoning_content" not in agg.additional_kwargs


def test_content_and_usage_unchanged():
    base = _replay(ChatOpenAI(model="x", api_key="replay"), GPT_HIGH_EVENTS)
    ours = _replay(ChatOpenAIWithReasoning(model="x", api_key="replay"), GPT_HIGH_EVENTS)
    assert ours.content == base.content
    assert ours.usage_metadata == base.usage_metadata


def test_stream_without_reasoning_untouched():
    agg = _replay(ChatOpenAIWithReasoning(model="x", api_key="replay"), PLAIN_EVENTS)
    assert "reasoning_content" not in agg.additional_kwargs
    assert agg.content == "391"


def test_reasoning_key_variant_and_no_double():
    llm = ChatOpenAIWithReasoning(model="x", api_key="replay")
    ev = lambda d: {"choices": [{"index": 0, "delta": d}]}
    agg = _replay(llm, [ev({"role": "assistant"}), ev({"reasoning": "a"}),
                        ev({"reasoning": "b", "reasoning_content": "b"}), ev({"content": "ok"})])
    assert agg.additional_kwargs["reasoning_content"] == "ab"
    assert agg.content == "ok"


def test_reasoning_not_sent_back_to_provider():
    agg = _replay(ChatOpenAIWithReasoning(model="x", api_key="replay"), GPT_HIGH_EVENTS)
    payload = _convert_message_to_dict(agg)
    assert "reasoning_content" not in json.dumps(payload, ensure_ascii=False)


def test_factory_builds_subclass(monkeypatch):
    monkeypatch.setattr(ap, "CLOUD_LLM_ACCESS_KEY", "test-key")
    spec = ap.get_models_registry().models["coder"]
    llm = ap.build_llm_from_spec(spec)
    assert isinstance(llm, ChatOpenAIWithReasoning)
    # langchain переносит extra_body из model_kwargs в собственное поле
    body = llm.extra_body or llm.model_kwargs.get("extra_body") or {}
    assert body["reasoning_effort"] == spec.reasoning["effort"] == "high"


def test_astream_collect_keeps_reasoning():
    import asyncio
    from agent.event_loop import _astream_collect

    conv = ChatOpenAIWithReasoning(model="x", api_key="replay")

    class Replay:
        model_name = "x"
        async def astream(self, messages, **kwargs):
            for ev in GPT_HIGH_EVENTS:
                gen = conv._convert_chunk_to_generation_chunk(ev, AIMessageChunk, None)
                if gen is not None:
                    yield gen.message
        async def ainvoke(self, *a, **k):
            raise AssertionError("unexpected fallback")

    seen = []
    async def on_chunk(text, kind="answer"):
        if kind == "reasoning":
            seen.append(text)

    result = asyncio.run(_astream_collect(Replay(), [], on_chunk=on_chunk))
    expected = _raw_reasoning(GPT_HIGH_EVENTS)
    assert result.additional_kwargs["reasoning_content"] == expected
    assert "".join(seen) == expected
