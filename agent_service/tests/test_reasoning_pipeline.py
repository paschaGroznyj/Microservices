"""Regression: provider reasoning must leave process_query, not just the collector."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessageChunk

from tests.test_process_query import Harness
from tests.conftest import ai
import agent.event_loop as el


@pytest.mark.parametrize("key", ["reasoning_content", "reasoning"])
def test_native_reasoning_tool_and_final(monkeypatch, key):
    h = Harness(monkeypatch)
    calls = h.add_tool("add", result="42")
    first = ai("", calls=[("add", {"x": 42})])
    first.additional_kwargs[key] = "Tool-step summary."
    final = ai("Result: 42")
    final.additional_kwargs[key] = "Final-step summary."
    h.models["coder"].script = [first, final]
    result = h.run("Use add")
    assert calls and result["message"] == "Result: 42"
    events = [(c.kwargs["kind"], c.args[0]) for c in h.send_chunk.await_args_list]
    assert events == [("live", "Tool-step summary."), ("reasoning_end", ""),
                      ("live", "Final-step summary."), ("reasoning_end", "")]
    assert "summary" not in result["message"]
    assert all(m.content != "Tool-step summary." for m in h.models["coder"].calls[-1])


def test_reasoning_streamed_before_tool_and_before_response_complete(monkeypatch):
    h = Harness(monkeypatch)
    order = []
    async def local_tool(*args, **kw):
        order.append("tool")
        return "42"
    h.add_tool("add", result="42")
    monkeypatch.setattr(h.agent, "_invoke_tool", local_tool)
    original_send = h.send_chunk
    async def capture(text, *args, **kwargs):
        order.append((kwargs["kind"], text))
        await original_send(text, *args, **kwargs)
    monkeypatch.setattr(h.agent, "_send_chunk", capture)
    count = 0
    async def astream(messages, **kw):
        nonlocal count
        count += 1
        if count == 1:
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "First "})
            assert order == [("live", "First ")], "reasoning was buffered, not streamed"
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "step."})
            yield AIMessageChunk(content="", tool_call_chunks=[
                {"name": "add", "args": '{"x":42}', "id": "native-call", "index": 0}])
        else:
            yield AIMessageChunk(content="Result: 42")
    monkeypatch.setattr(h.models["coder"], "astream", astream)
    result = h.run()
    assert result["message"] == "Result: 42"
    assert order[:4] == [("live", "First "), ("live", "step."), ("reasoning_end", ""), "tool"]
    assert [x for x in order if isinstance(x, tuple) and x[0] == "live"] == [("live", "First "), ("live", "step.")]


def test_native_beats_imitation_and_content(monkeypatch):
    h = Harness(monkeypatch)
    h.add_tool("add")
    first = ai("tool preamble", calls=[("add", {})])
    first.additional_kwargs["reasoning_content"] = "Native summary"
    h.models["coder"].script = [first, ai("Done")]
    imitate = lambda *a: (_ for _ in ()).throw(AssertionError("must not imitate native reasoning"))
    monkeypatch.setattr(el, "get_tools_status", imitate)
    h.run()
    texts = [c.args[0] for c in h.send_chunk.await_args_list if c.kwargs["kind"] == "live"]
    assert texts == ["Native summary"]


def test_failed_stream_closes_reasoning_before_retry(monkeypatch, fast_sleep):
    h = Harness(monkeypatch)
    attempts = 0
    async def astream(messages, **kw):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "Abandoned attempt"})
            raise RuntimeError("transient stream error")
        yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "Recovered summary"})
        yield AIMessageChunk(content="Recovered answer")
    monkeypatch.setattr(h.models["coder"], "astream", astream)
    result = h.run()
    assert result["message"] == "Recovered answer"
    assert [(c.kwargs["kind"], c.args[0]) for c in h.send_chunk.await_args_list] == [
        ("live", "Abandoned attempt"), ("reasoning_end", ""),
        ("live", "Recovered summary"), ("reasoning_end", "")]


def test_ainvoke_fallback_publishes_native_reasoning_once(monkeypatch, fast_sleep):
    h = Harness(monkeypatch)
    async def broken_stream(*args, **kw):
        raise RuntimeError("stream unavailable")
        yield
    monkeypatch.setattr(h.models["coder"], "astream", broken_stream)
    response = ai("Fallback answer")
    response.additional_kwargs["reasoning_content"] = "Fallback summary"
    monkeypatch.setattr(h.models["coder"], "ainvoke", AsyncMock(return_value=response))
    result = h.run()
    assert result["message"] == "Fallback answer"
    assert [(c.kwargs["kind"], c.args[0]) for c in h.send_chunk.await_args_list] == [
        ("live", "Fallback summary"), ("reasoning_end", "")]


def test_markdown_stripped_without_gluing(monkeypatch):
    # GLM шлёт по 1–3 слова; раньше strip() на каждом чанке давал «Theuser isasking»
    h = Harness(monkeypatch)
    pieces = ["The", " user", " is", " asking", ":\n", "    *", "   **Plan", ":** one", ".\n**Next**"]
    async def astream(messages, **kw):
        for p in pieces:
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": p})
        yield AIMessageChunk(content="Answer")
    monkeypatch.setattr(h.models["coder"], "astream", astream)
    result = h.run()
    assert result["message"] == "Answer"
    live = "".join(c.args[0] for c in h.send_chunk.await_args_list if c.kwargs["kind"] == "live")
    assert live == "The user is asking:\n◦ Plan: one.\nNext"
    kinds = [c.kwargs["kind"] for c in h.send_chunk.await_args_list]
    assert kinds[-1] == "reasoning_end" and kinds.count("reasoning_end") == 1
