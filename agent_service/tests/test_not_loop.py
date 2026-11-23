"""Режим «Модель» (type_query="model"): LLModel из agent/not_loop.py."""
import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from tests.conftest import MODEL_NAMES, ScriptedLLM, ai, build_agent


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def setup(monkeypatch):
    import agent.not_loop as nl
    a = build_agent(monkeypatch, db=AsyncMock(name="db"))
    a._send_chunk = AsyncMock(name="chunk")
    a._send_flow = AsyncMock(name="flow")
    monkeypatch.setattr(nl, "is_first_turn", AsyncMock(return_value=False))
    rows = []
    for i in range(30):
        rows.append({"id": 2 * i, "message_text": f"q{i}", "is_from_bot": False})
        rows.append({"id": 2 * i + 1, "message_text": f"a{i}", "is_from_bot": True})
    loaded = {}

    async def hist(chat_id, limit):
        loaded["limit"] = limit
        return rows[-limit:]

    a.db.get_chat_history = hist
    return nl, a, loaded


def test_no_tools_history_and_prompt(setup):
    nl, a, loaded = setup
    llm = ScriptedLLM("openai/gpt-test", [ai("Привет, я MauDel", usage=(120, 8))])
    a.AGENT_MODELS["coder"] = llm
    res = run(nl.LLModel(a).process_query("привет", "r1", 56, "c1", "coder"))

    assert res["context"]["tokens"] == res["tokens_usage"]["context_tokens"]
    assert res["message"] == "Привет, я MauDel"
    assert res["type_query"] == "model" and res["used_tools"] == [] and res["files_to_send"] == []
    assert res["tokens_usage"]["input_tokens"] == 120 and res["tokens_usage"]["total_tokens"] == 128
    assert llm.bound_tools is None                      # тулы не привязываются
    assert llm.methods == ["astream"]                   # один вызов, без цикла
    assert loaded["limit"] == 20                        # 10 последних запросов = 20 сообщений

    msgs = llm.calls[0]
    sys = msgs[0].content
    assert isinstance(msgs[0], SystemMessage)
    assert "MauDel" in sys and "AuGent, built by CDS" in sys and "InteractivePoll" in sys
    assert isinstance(msgs[-1], HumanMessage) and msgs[-1].content == "привет"
    hist = [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))][:-1]
    assert len(hist) == 20 and hist[0].content == "q20" and hist[-1].content == "a29"


def test_trim_by_tokens(setup, monkeypatch):
    nl, a, _ = setup
    monkeypatch.setitem(nl.MODEL_CONTEXT_TOKENS, "small", 40)
    llm = ScriptedLLM("openai/gpt-test", ["ok"])
    a.AGENT_MODELS["coder"] = llm
    run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder", context_size="small"))
    hist = [m for m in llm.calls[0] if isinstance(m, (HumanMessage, AIMessage))][:-1]
    assert 0 < len(hist) < 20 and isinstance(hist[0], HumanMessage)


def test_stream_and_flow(setup):
    nl, a, _ = setup
    a.AGENT_MODELS["coder"] = ScriptedLLM("openai/gpt-test", ["ответ"])
    run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    a._send_flow.assert_any_await("r1", "agent_start")
    kinds = [c.kwargs.get("kind") for c in a._send_chunk.await_args_list]
    assert kinds == ["answer_delta", "answer_end", "final_commit"]
    assert a._send_chunk.await_args_list[0].args[0] == "ответ"


def test_model_down(setup, fast_sleep):
    nl, a, _ = setup
    a.AGENT_MODELS["coder"] = ScriptedLLM("openai/gpt-test", [RuntimeError("503")] * 4)
    res = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert res["success"] is False and "недоступна" in res["message"]
    assert a._send_chunk.await_args_list[-1].kwargs["kind"] == "final_commit"


def test_empty_then_text_retries(setup, fast_sleep):
    nl, a, _ = setup
    llm = ScriptedLLM("openai/gpt-test", ["", "второй раз"])
    a.AGENT_MODELS["coder"] = llm
    res = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert res["message"] == "второй раз"
    assert len(llm.calls) == 2


def test_empty_always_gives_fallback(setup, monkeypatch):
    nl, a, _ = setup
    import agent.event_loop as el
    collect = AsyncMock(return_value=ai(""))
    monkeypatch.setattr(el, "_astream_collect", collect)
    llm = a.AGENT_MODELS["coder"]
    res = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert res["message"] == nl.MODEL_EMPTY_FALLBACK
    assert collect.await_count == nl.MODEL_EMPTY_RETRIES + 1


def test_text_of_thinking_only_is_empty():
    import agent.not_loop as nl
    msg = AIMessage(content=[{"type": "thinking", "thinking": "думаю"}])
    assert nl._text_of(msg) == ""
    msg2 = AIMessage(content=[{"type": "thinking", "thinking": "t"}, {"type": "text", "text": "ок"}])
    assert nl._text_of(msg2) == "ок"


def events(agent):
    return [(c.kwargs["kind"], c.args[0]) for c in agent._send_chunk.await_args_list]


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("key", ["reasoning", "reasoning_content", "thinking"])
def test_native_reasoning_is_live_before_answer(setup, monkeypatch, model, key):
    nl, a, _ = setup

    async def astream(messages, **kwargs):
        for text in ("**Native", " summary**", " ", "tail<"):
            if key == "thinking":
                yield AIMessageChunk(content=[{"type": "thinking", "thinking": text}])
            else:
                yield AIMessageChunk(content="", additional_kwargs={key: text})
        assert "".join(t for k, t in events(a) if k == "live") == "Native summary tail"
        yield AIMessageChunk(content="First ")
        assert events(a)[-1] == ("answer_delta", "First ")
        assert ("reasoning_end", "") in events(a)
        assert not any(k == "answer_end" for k, _ in events(a))
        yield AIMessageChunk(content="second")

    monkeypatch.setattr(a.AGENT_MODELS[model], "astream", astream)
    result = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", model))
    assert result["message"] == "First second"
    assert "".join(t for k, t in events(a) if k == "live") == "Native summary tail<"
    assert events(a).count(("reasoning_end", "")) == 1
    assert events(a)[-2:] == [("answer_end", "First second"), ("final_commit", "")]


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("text,answer,thinking", [
    ("A<thinking>**Plan**</thinking>B", "AB", "Plan"),
    ("A<reasoning>SECRET</reasoning>B", "AB", ""),
    ("<thinking>Plan<reasoning>SECRET<thinking>SECRET</thinking></reasoning> done</thinking>Answer", "Answer", "Plan done"),
    ("A<reasoning>SECRET", "A", ""),
    ("A<thinking>Plan<reasoning>SECRET</thinking>SECRET", "A", "Plan"),
    ("A<reason", "A", ""),
])
def test_fragmented_content_privacy(setup, monkeypatch, model, text, answer, thinking):
    nl, a, _ = setup

    async def astream(messages, **kwargs):
        for char in text:
            yield AIMessageChunk(content=char)

    monkeypatch.setattr(a.AGENT_MODELS[model], "astream", astream)
    result = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", model))
    assert result["message"] == answer
    assert "SECRET" not in str(events(a))
    assert "".join(t for k, t in events(a) if k == "answer_delta") == answer
    assert "".join(t for k, t in events(a) if k == "live") == thinking
    assert events(a)[-2:] == [("answer_end", answer), ("final_commit", "")]


@pytest.mark.parametrize("model", ["coder", "universal"])
def test_stream_retry_resets_candidate_cleaner_and_router(setup, monkeypatch, fast_sleep, model):
    nl, a, _ = setup
    attempts = 0

    async def astream(messages, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield AIMessageChunk(content="Abandoned ")
            yield AIMessageChunk(content="<thinking>**Old")
            yield AIMessageChunk(content="<reasoning>SECRET")
            raise RuntimeError("temporary failure")
        assert events(a)[-1] == ("answer_reset", "")
        assert events(a).count(("reasoning_end", "")) == 1
        yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "**New**"})
        yield AIMessageChunk(content="Recovered")

    monkeypatch.setattr(a.AGENT_MODELS[model], "astream", astream)
    result = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", model))
    assert result["message"] == "Recovered"
    assert attempts == 2
    assert "SECRET" not in str(events(a))
    assert events(a).count(("answer_reset", "")) == 1
    assert events(a).count(("reasoning_end", "")) == 2
    assert [t for k, t in events(a) if k == "live"] == ["Old", "New"]
    assert events(a)[-2:] == [("answer_end", "Recovered"), ("final_commit", "")]


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("key", ["reasoning", "reasoning_content", "thinking"])
def test_ainvoke_reasoning_shown_once(setup, monkeypatch, model, key):
    nl, a, _ = setup

    async def astream(messages, **kwargs):
        if False:
            yield

    response = ai("<thinking>**Content plan**</thinking><reasoning>SECRET</reasoning>Fallback")
    if key == "thinking":
        response.content = [{"type": "thinking", "thinking": "**Native summary**"},
                            {"type": "text", "text": response.content}]
    else:
        response.additional_kwargs[key] = "**Native summary**"
    monkeypatch.setattr(a.AGENT_MODELS[model], "astream", astream)
    monkeypatch.setattr(a.AGENT_MODELS[model], "ainvoke", AsyncMock(return_value=response))
    result = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", model))
    assert result["message"] == "Fallback"
    assert "SECRET" not in str(events(a))
    live = "".join(t for k, t in events(a) if k == "live")
    assert live.count("Native summary") == 1
    assert live.count("Content plan") == 1
    assert events(a).count(("reasoning_end", "")) == 1
    assert events(a)[-2:] == [("answer_end", "Fallback"), ("final_commit", "")]


@pytest.mark.parametrize("text", [
    r"\<thinking\>Plan\</thinking\>\<reasoning\>SECRET\</reasoning\>Answer",
    "```xml\n<thinking>Plan</thinking><reasoning>SECRET</reasoning>\n```Answer",
    "`<thinking>Plan</thinking>`<reasoning>SECRET</reasoning>Answer",
])
def test_buffered_envelope_authoritative_answer_is_private(setup, monkeypatch, text):
    nl, a, _ = setup

    async def astream(messages, **kwargs):
        for char in text:
            yield AIMessageChunk(content=char)

    monkeypatch.setattr(a.AGENT_MODELS["coder"], "astream", astream)
    result = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert result["message"] == "Answer"
    assert "SECRET" not in str(events(a))
    assert events(a)[-2:] == [("answer_end", "Answer"), ("final_commit", "")]
