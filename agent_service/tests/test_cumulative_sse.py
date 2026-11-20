import os; os.environ['OPENBLAS_NUM_THREADS']='1'
import asyncio
import json
import pytest
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from agent.event_loop import _astream_collect
from agent.core.token_usage import normalize_usage, merge_usage_snapshot

def _ev(delta=None, usage=None, finish=None):
    ev = {"object": "chat.completion.chunk",
          "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        ev["usage"] = usage
    return ev


def _u(p, c, cached=None):
    u = {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}
    if cached is not None:
        u["prompt_tokens_details"] = {"cached_tokens": cached}
    return u


# Мини-SSE по форме реальных стримов Cloud.ru
STREAMS = {
    # GigaChat: накопительный usage в каждом чанке, cached приходит последним
    "giga_cumulative": [
        _ev({"role": "assistant", "content": ""}, _u(79, 0)),
        _ev({"content": "3"}, _u(79, 1)),
        _ev({"content": "9"}, _u(79, 2)),
        _ev({"content": "1"}, _u(79, 3)),
        _ev({}, _u(79, 4), finish="stop"),
        _ev(usage=_u(79, 4, cached=78)),
    ],
    # GPT/Gemini: usage один раз в конце
    "single_usage": [
        _ev({"role": "assistant"}),
        _ev({"content": "391"}),
        _ev({}, finish="stop"),
        _ev(usage=_u(17, 5, cached=0)),
    ],
}


# Модель определяет, считается ли usage накопительным (cumulative_stream_usage)
MODELS = {"giga_cumulative": "ai-sage/GigaChat3.5-432B-A28B", "single_usage": "openai/gpt-6.1-sol"}


@pytest.mark.parametrize("name", sorted(STREAMS))
def test_replay_sse(name):
    asyncio.run(_replay_sse(STREAMS[name], MODELS[name]))


async def _replay_sse(events, model):
    converter = ChatOpenAI(model="replay", api_key="replay")
    class Replay:
        model_name = model
        async def astream(self, messages, **kwargs):
            for event in events:
                generation = converter._convert_chunk_to_generation_chunk(event, AIMessageChunk, None)
                if generation is not None:
                    yield generation.message
        async def ainvoke(self, *args, **kwargs):
            raise AssertionError("unexpected fallback")
    expected = {}
    for event in events:
        merge_usage_snapshot(expected, event.get("usage") or {})
    result = await _astream_collect(Replay(), [])
    got = normalize_usage(result)
    assert got == normalize_usage(expected)
    # накопительные значения не суммируются
    assert got["input_tokens"] == events[-1]["usage"]["prompt_tokens"]


def test_snapshot_late_details():
    acc = {"input_tokens": 10, "output_tokens": 2}
    merge_usage_snapshot(acc, {"input_tokens": 10, "output_tokens": 3, "input_token_details": {"cache_read": 8}})
    merge_usage_snapshot(acc, {"input_tokens": 10, "output_tokens": 3})
    assert acc == {"input_tokens": 10, "output_tokens": 3, "input_token_details": {"cache_read": 8}}
