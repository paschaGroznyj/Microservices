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


# GigaChat 4: delta.reasoning, tool_state_id, один usage в конце, prompt_tokens БЕЗ cached
# (форма реального стрима GigaChat/GigaChat-4-708B-A41, 27.11.2026)
GIGA4_EVENTS = [
    _ev({"role": "assistant", "reasoning": "Нужно вызвать get_weather."}),
    _ev({"reasoning": " Параметр city = Самара."}),
    _ev({"tool_calls": [{"index": 0, "type": "function", "id": "057d6e30",
                         "function": {"name": "get_weather", "arguments": "{\"city\":\"Самара\"}"}}],
         "tool_state_id": "01a21dc7"}),
    _ev({}, finish="tool_calls"),
    _ev(None, usage={"prompt_tokens": 17, "prompt_tokens_details": {"cached_tokens": 3328},
                     "completion_tokens": 568, "total_tokens": 585}),
]


def test_giga4_stream_reasoning_tools_and_cached_usage():
    llm = ChatOpenAIWithReasoning(model="GigaChat/GigaChat-4-708B-A41", api_key="replay",
                                  prompt_tokens_exclude_cached=True)
    agg = _replay(llm, GIGA4_EVENTS)
    assert agg.additional_kwargs["reasoning_content"] == "Нужно вызвать get_weather. Параметр city = Самара."
    assert [(c["name"], c["args"]) for c in agg.tool_calls] == [("get_weather", {"city": "Самара"})]
    um = agg.usage_metadata
    assert um["input_tokens"] == 17 + 3328
    assert um["input_token_details"]["cache_read"] == 3328
    assert um["total_tokens"] == 585 + 3328


def test_cached_usage_untouched_without_flag():
    agg = _replay(ChatOpenAIWithReasoning(model="x", api_key="replay"), GIGA4_EVENTS)
    assert agg.usage_metadata["input_tokens"] == 17


def test_nonstream_result_keeps_reasoning_and_fixes_usage():
    llm = ChatOpenAIWithReasoning(model="GigaChat/GigaChat-4-708B-A41", api_key="replay",
                                  prompt_tokens_exclude_cached=True)
    resp = {"model": "GigaChat-4-708B-A41B:4.0.17.11", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "4", "reasoning": "2+2=4."}}],
            "usage": {"prompt_tokens": 13, "prompt_tokens_details": {"cached_tokens": 3328},
                      "completion_tokens": 9, "total_tokens": 22}}
    msg = llm._create_chat_result(resp).generations[0].message
    assert msg.content == "4"
    assert msg.additional_kwargs["reasoning_content"] == "2+2=4."
    assert msg.usage_metadata["input_tokens"] == 13 + 3328


def test_giga4_spec_flag_reaches_llm(monkeypatch):
    monkeypatch.setattr(ap, "CLOUD_LLM_ACCESS_KEY", "replay")
    spec = ap.get_model_spec("gigachat")
    assert spec.model == "GigaChat/GigaChat-4-708B-A41"
    assert spec.prompt_tokens_exclude_cached is True
    llm = ap.build_llm_from_spec(spec)
    inner = getattr(llm, "bound", llm)
    assert getattr(inner, "prompt_tokens_exclude_cached", None) is True
