"""Кеш промпта и учёт токенов: normalize_usage, cache_control, track_tokens, tokens_usage."""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from agent.core.token_usage import (
    append_to_system_tail, apply_anthropic_cache_control, normalize_usage,
)
from tests.conftest import ai
from tests.test_process_query import AnyArgs, Harness, system_text

CC = {"type": "ephemeral"}


@pytest.fixture
def h(monkeypatch, fast_sleep):
    return Harness(monkeypatch)


# ───────────────────────── normalize_usage ─────────────────────────

def test_normalize_langchain_metadata_with_cache():
    msg = AIMessage(content="x", usage_metadata={
        "input_tokens": 1000, "output_tokens": 20, "total_tokens": 1020,
        "input_token_details": {"cache_read": 800, "cache_creation": 150},
    })
    assert normalize_usage(msg) == {"input_tokens": 1000, "output_tokens": 20,
                                    "cache_read_tokens": 800, "cache_creation_tokens": 150}


def test_normalize_raw_openai_response():
    data = {"choices": [], "usage": {"prompt_tokens": 500, "completion_tokens": 40,
                                      "prompt_tokens_details": {"cached_tokens": 384}}}
    assert normalize_usage(data) == {"input_tokens": 500, "output_tokens": 40,
                                     "cache_read_tokens": 384, "cache_creation_tokens": 0}


def test_normalize_raw_anthropic_adds_cache_into_input():
    u = {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 900,
         "cache_creation_input_tokens": 90}
    assert normalize_usage(u) == {"input_tokens": 1000, "output_tokens": 5,
                                  "cache_read_tokens": 900, "cache_creation_tokens": 90}


@pytest.mark.parametrize("src", [None, {}, "str", AIMessage(content="x"), {"usage": None}])
def test_normalize_empty_sources(src):
    assert normalize_usage(src) == {"input_tokens": 0, "output_tokens": 0,
                                    "cache_read_tokens": 0, "cache_creation_tokens": 0}


# ───────────────────────── cache_control ─────────────────────────

def test_cache_control_marks_static_and_last_tool_result_without_mutation():
    sys0 = SystemMessage(content="STATIC")
    tm = ToolMessage(content="res", tool_call_id="c1")
    msgs = [sys0, SystemMessage(content="DYN"), HumanMessage("hi"),
            AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]), tm]
    out = apply_anthropic_cache_control(msgs)
    assert out[0].content == [{"type": "text", "text": "STATIC", "cache_control": CC}]
    assert out[1].content == "DYN"
    assert out[-1].content[0]["type"] == "tool_result"
    assert out[-1].content[0]["cache_control"] == CC
    assert sys0.content == "STATIC" and tm.content == "res"   # исходники не тронуты


def test_cache_control_survives_langchain_anthropic_formatting():
    from langchain_anthropic.chat_models import _format_messages
    msgs = apply_anthropic_cache_control([
        SystemMessage(content="STATIC"), SystemMessage(content="DYN"),
        HumanMessage(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]),
    ])
    system, formatted = _format_messages(msgs)
    assert system[0] == {"type": "text", "text": "STATIC", "cache_control": CC}
    assert "cache_control" not in system[1]
    assert formatted[-1]["content"][-1]["cache_control"] == CC
    assert "cache_control" not in formatted[-1]["content"][0]


def test_append_to_system_tail_keeps_static_first():
    msgs = [SystemMessage(content="S"), HumanMessage("q")]
    append_to_system_tail(msgs, "HINT")
    assert [m.content for m in msgs[:2]] == ["S", "HINT"]
    append_to_system_tail(msgs, "MORE")
    assert msgs[0].content == "S" and msgs[1].content == "HINT\n\nMORE"
    assert isinstance(msgs[2], HumanMessage)


# ───────────────────────── process_query ─────────────────────────

def test_static_system_prompt_first_and_date_in_dynamic_block(h):
    h.models["coder"].script = ["ok"]
    h.run()
    sent = h.models["coder"].calls[0]
    assert "05.11.2026 09:45" not in str(sent[0].content)
    assert "БОРТОВОЙ ЖУРНАЛ" not in str(sent[0].content)
    assert "ТЕКУЩИЙ КОНТЕКСТ" in system_text(sent) and "05.11.2026 09:45" in system_text(sent)


def test_static_prompt_identical_between_requests(h, monkeypatch):
    import agent.core.timezone_utils as tz
    from unittest.mock import AsyncMock
    h.models["coder"].script = ["a", "b"]
    h.run()
    monkeypatch.setattr(tz, "resolve_chat_context_and_timezone",
                        AsyncMock(return_value=("Europe/Samara", "05.11.2026 09:46", None, None)))
    h.db.get_agent_journal.return_value = "запись 2"
    h.run()
    c1, c2 = h.models["coder"].calls
    assert c1[0].content == c2[0].content


def test_designer_gets_cache_control_coder_does_not(h):
    h.add_tool("noop")
    h.models["designer"].script = [ai("", [("noop", {})]), "ok"]
    h.run(model="designer")
    first_call, second_call = h.models["designer"].calls
    assert first_call[0].content[-1]["cache_control"] == CC
    assert first_call[-1].content[-1]["cache_control"] == CC          # HumanMessage
    assert second_call[-1].content[0]["type"] == "tool_result"        # ToolMessage
    assert second_call[-1].content[0]["cache_control"] == CC
    marks = sum("cache_control" in str(m.content) for m in second_call)
    assert marks == 2                                                 # лимит Anthropic — 4

    h.models["coder"].script = ["ok"]
    h.run(model="coder")
    assert "cache_control" not in str(h.models["coder"].calls[0])


def test_cached_tokens_summed_in_tokens_usage(h):
    def u(i, o, cr, cw):
        return AIMessage(content="", usage_metadata={
            "input_tokens": i, "output_tokens": o, "total_tokens": i + o,
            "input_token_details": {"cache_read": cr, "cache_creation": cw}})
    h.add_tool("noop")
    first = u(1000, 10, 0, 900)
    first.tool_calls = [{"name": "noop", "args": {}, "id": "c9"}]
    second = u(1100, 5, 900, 100)
    second.content = "Готово"
    h.models["coder"].script = [first, second]
    out = h.run()
    assert out["tokens_usage"] == {"input_tokens": 2100, "output_tokens": 15, "total_tokens": 2115,
                                   "cache_read_tokens": 900, "cache_creation_tokens": 1000}


def _tool_with_usage(h, name, usage, model, extra=None):
    async def _impl(**kw):
        h.agent.track_tokens(usage, tool=name, model=model, extra=extra)
        return "done"
    h.tools.append(StructuredTool.from_function(coroutine=_impl, name=name,
                                                description=name, args_schema=AnyArgs))


def test_tools_usage_reported_separately(h):
    _tool_with_usage(h, "analyze_image",
                     AIMessage(content="кот", usage_metadata={"input_tokens": 1500, "output_tokens": 80,
                                                              "total_tokens": 1580}),
                     "anthropic/claude-test")
    _tool_with_usage(h, "generate_image",
                     {"usage": {"prompt_tokens": 30, "completion_tokens": 1290}},
                     "google/image-test", extra={"images": 1})
    h.models["coder"].script = [
        ai("", [("analyze_image", {}), ("analyze_image", {}), ("generate_image", {})], usage=(100, 5)),
        ai("Готово", usage=(200, 7)),
    ]
    out = h.run()
    tu = out["tokens_usage"]
    # основная модель не смешивается с тулами
    assert (tu["input_tokens"], tu["output_tokens"], tu["total_tokens"]) == (300, 12, 312)
    assert tu["tools"]["analyze_image"] == {
        "calls": 2, "input_tokens": 3000, "output_tokens": 160, "total_tokens": 3160,
        "cache_read_tokens": 0, "cache_creation_tokens": 0, "models": ["anthropic/claude-test"]}
    g = tu["tools"]["generate_image"]
    assert (g["calls"], g["input_tokens"], g["output_tokens"], g["images"]) == (1, 30, 1290, 1)
    assert g["models"] == ["google/image-test"]


def test_no_tools_key_without_tool_usage(h):
    h.models["coder"].script = ["ok"]
    assert "tools" not in h.run()["tokens_usage"]


def test_tool_usage_isolated_between_requests(h):
    _tool_with_usage(h, "analyze_image", {"input_tokens": 10, "output_tokens": 1}, "m")
    h.models["coder"].script = [ai("", [("analyze_image", {})]), "ok", "ok2"]
    assert h.run()["tokens_usage"]["tools"]["analyze_image"]["calls"] == 1
    assert "tools" not in h.run()["tokens_usage"]


def test_track_tokens_outside_request_does_not_fail(agent_obj):
    u = agent_obj.track_tokens({"prompt_tokens": 3, "completion_tokens": 2}, tool="analyze_image")
    assert u["input_tokens"] == 3 and u["output_tokens"] == 2


# ───────────────────────── реальные тулы ─────────────────────────

def test_analyze_image_tool_tracks_vision_tokens(h, tmp_path, monkeypatch):
    import base64
    from agent.tools.vision import tool_analyze_image as tai

    class VisionLLM:
        model_name = "anthropic/claude-vision-test"
        async def ainvoke(self, msgs, **kw):
            return AIMessage(content="на картинке кот", usage_metadata={
                "input_tokens": 1200, "output_tokens": 30, "total_tokens": 1230})

    # vision-кандидаты фабрика снимает при сборке тула; основной цикл остаётся на ScriptedLLM
    real_models = h.agent.AGENT_MODELS
    h.agent.AGENT_MODELS = {k: VisionLLM() for k in real_models}
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    tool = tai.make_analyze_image_tool(h.agent, images=[{"filename": "a.png", "b64": png,
                                                         "mime_type": "image/png"}])
    h.agent.AGENT_MODELS = real_models
    h.tools.append(tool)
    h.models["coder"].script = [ai("", [("analyze_image", {"prompt": "что там", "image_index": 0})]), "ok"]
    out = h.run()
    a = out["tokens_usage"]["tools"]["analyze_image"]
    assert a["calls"] == 1 and a["input_tokens"] == 1200 and a["output_tokens"] == 30
    assert a["models"] == ["anthropic/claude-vision-test"]
