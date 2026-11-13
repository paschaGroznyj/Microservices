"""Режим «Модель» (type_query="model"): LLModel из agent/not_loop.py."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tests.conftest import ScriptedLLM, ai, build_agent


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
    assert kinds == ["live", "final_commit"]
    assert a._send_chunk.await_args_list[0].args[0] == "ответ"


def test_model_down(setup, fast_sleep):
    nl, a, _ = setup
    a.AGENT_MODELS["coder"] = ScriptedLLM("openai/gpt-test", [RuntimeError("503")] * 4)
    res = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert res["success"] is False and "недоступна" in res["message"]
    assert a._send_chunk.await_args_list[-1].kwargs["kind"] == "final_commit"


def test_empty_then_text_retries(setup):
    nl, a, _ = setup
    llm = ScriptedLLM("openai/gpt-test", ["", "второй раз"])
    a.AGENT_MODELS["coder"] = llm
    res = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert res["message"] == "второй раз"
    assert len(llm.calls) == 2


def test_empty_always_gives_fallback(setup):
    nl, a, _ = setup
    llm = ScriptedLLM("openai/gpt-test", ["", "", ""])
    a.AGENT_MODELS["coder"] = llm
    res = run(nl.LLModel(a).process_query("x", "r1", 56, "c1", "coder"))
    assert res["message"] == nl.MODEL_EMPTY_FALLBACK
    assert len(llm.calls) == nl.MODEL_EMPTY_RETRIES + 1


def test_text_of_thinking_only_is_empty():
    import agent.not_loop as nl
    msg = AIMessage(content=[{"type": "thinking", "thinking": "думаю"}])
    assert nl._text_of(msg) == ""
    msg2 = AIMessage(content=[{"type": "thinking", "thinking": "t"}, {"type": "text", "text": "ок"}])
    assert nl._text_of(msg2) == "ок"
