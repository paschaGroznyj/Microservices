"""Live content routing and process_query contract, with no provider/network calls.

Legacy escaped/backtick tag envelopes are intentionally buffered until collection
finishes. They are parsed for the authoritative answer, not token-streamed.
"""
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessageChunk

import agent.event_loop as el
from agent.core.content_stream import ContentStreamRouter
from tests.conftest import MODEL_NAMES, ai
from tests.test_process_query import Harness


def events(h):
    return [(c.kwargs["kind"], c.args[0]) for c in h.send_chunk.await_args_list]


def route(parts):
    router = ContentStreamRouter()
    out = []
    for part in parts:
        out.extend(router.feed(part))
    out.extend(router.flush())
    return router, {k: "".join(t for c, t in out if c == k) for k in ("answer", "reasoning")}


CASES = [
    ("before<thinking>Plan.</thinking>after", "beforeafter", "Plan."),
    (" < ThInKiNg >Plan< / THINKING >Answer", " Answer", "Plan"),
    ("< REASONING >SECRET</ REASONING >Answer", "Answer", ""),
    ("A<thinking>Plan<reasoning>SECRET<thinking>SECRET</thinking></reasoning>done</thinking>B", "AB", "Plandone"),
    ("A<reasoning>SECRET<reasoning>SECRET</reasoning>SECRET</reasoning>B", "AB", ""),
    ("A<reasoning>SECRET", "A", ""),
    ("A<thinking>Plan<reasoning>SECRET", "A", "Plan"),
    ("A<thinking>Plan", "A", "Plan"),
    ("A<reason", "A", ""),
    ("A<thinking><reasoning>SECRET</thinking>SECRET", "A", ""),
    ("A < ordinary > B", "A < ordinary > B", ""),
]


@pytest.mark.parametrize("text,answer,thinking", CASES)
def test_router_chunk_invariance(text, answer, thinking):
    # Every two-way cut, every fixed width, and one-character deltas.
    partitions = [[text[:i], text[i:]] for i in range(len(text) + 1)]
    partitions += [[text[i:i + n] for i in range(0, len(text), n)] for n in range(1, len(text) + 1)]
    for parts in partitions:
        router, out = route(parts)
        assert out == {"answer": answer, "reasoning": thinking}, parts
        assert router.answer_text == answer
        assert router.thinking_text == thinking
        assert "SECRET" not in str(out)


@pytest.mark.parametrize("text", [
    r"\<thinking\>Plan\</thinking\>\<reasoning\>SECRET\</reasoning\>Answer",
    "```xml\n<thinking>Plan</thinking><reasoning>SECRET</reasoning>\n```Answer",
    "`<thinking>Plan</thinking>`Answer",
])
def test_legacy_envelopes_deliberately_buffered(text):
    for n in (1, 3, len(text)):
        _, out = route([text[i:i + n] for i in range(0, len(text), n)])
        assert out == {"answer": "", "reasoning": ""}


@pytest.mark.parametrize("model", MODEL_NAMES)
def test_each_model_streams_before_producer_completes(monkeypatch, model):
    h = Harness(monkeypatch)
    async def astream(messages, **kw):
        yield AIMessageChunk(content="First ")
        assert ("answer_delta", "First ") in events(h), "content buffered until producer completion"
        assert not any(k == "answer_end" for k, _ in events(h))
        yield AIMessageChunk(content="second")
    monkeypatch.setattr(h.models[model], "astream", astream)
    result = h.run(model=model)
    assert result["message"] == "First second"
    assert events(h) == [("answer_delta", "First "), ("answer_delta", "second"),
                         ("answer_end", "First second"), ("final_commit", "")]


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("text,answer,thinking", CASES[:-1])
def test_fragmented_tags_never_expose_private_live_or_final(monkeypatch, model, text, answer, thinking):
    h = Harness(monkeypatch)
    async def astream(messages, **kw):
        for char in text:
            yield AIMessageChunk(content=char)
    monkeypatch.setattr(h.models[model], "astream", astream)
    result = h.run(model=model)
    assert result["message"] == answer.strip()
    assert "SECRET" not in str(events(h))
    assert "SECRET" not in result["message"]
    assert "".join(t for k, t in events(h) if k == "answer_delta") == answer
    assert "".join(t for k, t in events(h) if k == "live") == thinking
    assert events(h)[-2:] == [("answer_end", answer.strip()), ("final_commit", "")]
    assert all(k != "reasoning_end" for k, _ in events(h)[events(h).index(("answer_end", answer.strip())):])


@pytest.mark.parametrize("model", MODEL_NAMES)
def test_native_reasoning_all_models_closes_once_and_traces_cleanly(monkeypatch, model):
    h = Harness(monkeypatch)
    states = []
    real_extension = el.make_extension_tool
    def capture(state):
        states.append(state)
        return real_extension(state)
    monkeypatch.setattr(el, "make_extension_tool", capture)
    async def astream(messages, **kw):
        yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "**Native"})
        yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": " summary**"})
        yield AIMessageChunk(content="Answer")
    monkeypatch.setattr(h.models[model], "astream", astream)
    assert h.run(model=model)["message"] == "Answer"
    ev = events(h)
    assert "".join(t for k, t in ev if k == "live") == "Native summary"
    assert sum(k == "reasoning_end" for k, _ in ev) == 1
    assert ev[-2:] == [("answer_end", "Answer"), ("final_commit", "")]
    assert states[0]["reasoning_trace"] == ["Native summary"]


@pytest.mark.parametrize("model", MODEL_NAMES)
def test_tool_candidate_reset_and_thinking_not_duplicated(monkeypatch, model):
    h = Harness(monkeypatch)
    h.add_tool("add")
    states = []
    real_extension = el.make_extension_tool
    def capture(state):
        states.append(state)
        return real_extension(state)
    monkeypatch.setattr(el, "make_extension_tool", capture)
    count = 0
    async def astream(messages, **kw):
        nonlocal count
        count += 1
        if count == 1:
            for part in ("<thi", "nking>**Plan", " tool**</thinking>", "candidate"):
                yield AIMessageChunk(content=part)
            yield AIMessageChunk(content="", tool_call_chunks=[
                {"name": "add", "args": '{"x":1}', "id": "call-live", "index": 0}])
        else:
            assert ("answer_reset", "") in events(h)
            yield AIMessageChunk(content="Actual answer")
    monkeypatch.setattr(h.models[model], "astream", astream)
    assert h.run(model=model)["message"] == "Actual answer"
    ev = events(h)
    assert "".join(t for k, t in ev if k == "live") == "Plan tool"
    assert ev.count(("reasoning_end", "")) == 1
    assert states[0]["reasoning_trace"] == ["Plan tool"]
    assert [t for k, t in ev if k == "answer_end"] == ["Actual answer"]
    assert ev.index(("answer_reset", "")) < ev.index(("answer_end", "Actual answer"))
    assert ev[-1] == ("final_commit", "")


@pytest.mark.parametrize("model", MODEL_NAMES)
def test_retry_resets_answer_and_parser_and_reasoning_trace(monkeypatch, fast_sleep, model):
    h = Harness(monkeypatch)
    count = 0
    async def astream(messages, **kw):
        nonlocal count
        count += 1
        if count == 1:
            yield AIMessageChunk(content="Abandoned ")
            yield AIMessageChunk(content="<thinking>Old thought</thinking><reasoning>SECRET")
            raise RuntimeError("transient")
        assert ("answer_reset", "") in events(h)
        yield AIMessageChunk(content="<thinking>New thought</thinking>Recovered")
    monkeypatch.setattr(h.models[model], "astream", astream)
    assert h.run(model=model)["message"] == "Recovered"
    ev = events(h)
    assert "SECRET" not in str(ev)
    assert ev.count(("answer_reset", "")) == 1
    assert [t for k, t in ev if k == "answer_end"] == ["Recovered"]
    assert ev.count(("reasoning_end", "")) == 2
    assert ev[-2:] == [("answer_end", "Recovered"), ("final_commit", "")]


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("key", ["reasoning", "reasoning_content"])
def test_fallback_reasoning_closes_before_answer_end(monkeypatch, fast_sleep, model, key):
    h = Harness(monkeypatch)
    async def broken(*a, **kw):
        raise RuntimeError("no stream")
        yield
    response = ai("Fallback")
    response.additional_kwargs[key] = "**Fallback summary**"
    monkeypatch.setattr(h.models[model], "astream", broken)
    monkeypatch.setattr(h.models[model], "ainvoke", AsyncMock(return_value=response))
    assert h.run(model=model)["message"] == "Fallback"
    assert events(h) == [("live", "Fallback summary"), ("reasoning_end", ""),
                         ("answer_end", "Fallback"), ("final_commit", "")]


def test_native_anthropic_thinking_no_duplicate(monkeypatch):
    h = Harness(monkeypatch)
    h.models["designer"].script = [ai([
        {"type": "thinking", "thinking": "**Plan**", "signature": "signed"},
        {"type": "text", "text": "Answer"}])]
    assert h.run(model="designer")["message"] == "Answer"
    assert "".join(t for k, t in events(h) if k == "live") == "Plan"
    assert events(h).count(("reasoning_end", "")) == 1


ORDINARY_PREFIXES = [
    "```python\nprint('hello')\n```",
    "```xml\n<ordinary>hello</ordinary>\n```",
    "```python\n  print('hello')\n```",
    "```\nhello\n```",
    "`inline code`",
    "``inline code``",
    "& normal ampersand",
    "&lt;ordinary&gt; text",
    r"\(x + y\)",
    r"\theta + \alpha",
    r"\<ordinary\> text",
]


@pytest.mark.parametrize("text", ORDINARY_PREFIXES)
def test_ordinary_legacy_like_prefixes_chunk_invariant(text):
    for prefix in ("", " \n\t"):
        source = prefix + text
        partitions = [[source[:i], source[i:]] for i in range(len(source) + 1)]
        partitions += [[source[i:i + n] for i in range(0, len(source), n)]
                       for n in range(1, len(source) + 1)]
        for parts in partitions:
            router = ContentStreamRouter()
            out = []
            for part in parts:
                out.extend(router.feed(part))
            assert "".join(t for c, t in out if c == "answer") == source, parts
            assert not router.envelope
            assert router.flush() == []
            assert router.answer_text == source


@pytest.mark.parametrize("prefix,decisive", [
    ("```python\n  ", "p"),
    ("```xml\n", "<o"),
    ("`", "i"),
    ("&", " "),
    ("\\", "("),
    ("&lt;", "o"),
])
def test_ambiguous_prefix_releases_as_soon_as_distinguishable(prefix, decisive):
    router = ContentStreamRouter()
    for char in " \n" + prefix:
        assert router.feed(char) == []
    assert router.feed(decisive) == [("answer", " \n" + prefix + decisive)]
    assert router.feed("more") == [("answer", "more")]


@pytest.mark.parametrize("text", [
    "&lt;thinking&gt;Plan&lt;/thinking&gt;&lt;reasoning&gt;SECRET&lt;/reasoning&gt;Answer",
    "&#60;reasoning&#62;SECRET&#60;/reasoning&#62;Answer",
    r"\<reasoning\>SECRET\</reasoning\>Answer",
    "` < ReAsOnInG >SECRET</reasoning>`Answer",
    "```python\n  <reasoning>SECRET</reasoning>\n```Answer",
    "```xml\n  <thinking>Plan</thinking><reasoning>SECRET</reasoning>\n```Answer",
    "```xml<thinking>Plan</thinking><reasoning>SECRET</reasoning>```Answer",
    "```\n<thinking>Plan</thinking><reasoning>SECRET</reasoning>\n```Answer",
])
def test_legacy_private_envelopes_all_prefix_splits_stay_buffered(text):
    text = " \n\t" + text
    partitions = [[text[:i], text[i:]] for i in range(len(text) + 1)]
    partitions += [[text[i:i + n] for i in range(0, len(text), n)]
                   for n in range(1, len(text) + 1)]
    for parts in partitions:
        router, out = route(parts)
        assert out == {"answer": "", "reasoning": ""}, parts
        assert router.envelope
        assert router.answer_text == router.thinking_text == ""


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("text", [ORDINARY_PREFIXES[i] for i in (0, 4, 6, 8)])
def test_ordinary_prefixes_stream_before_completion(monkeypatch, model, text):
    h = Harness(monkeypatch)
    async def astream(messages, **kw):
        for char in text:
            yield AIMessageChunk(content=char)
        assert "".join(t for k, t in events(h) if k == "answer_delta") == text
        assert not any(k == "answer_end" for k, _ in events(h))
    monkeypatch.setattr(h.models[model], "astream", astream)
    assert h.run(model=model)["message"] == text


@pytest.mark.parametrize("text", [
    "`", "```xml\n", "`<think", "&", "&lt;reason", "\\", "\\<thinking\\",
])
def test_unresolved_legacy_prefix_stays_buffered_at_completion(text):
    for parts in ([text], list(text), [" \n", *text]):
        router, out = route(parts)
        assert out == {"answer": "", "reasoning": ""}
        assert router.envelope


@pytest.mark.parametrize("model", MODEL_NAMES)
@pytest.mark.parametrize("text", [
    r"\<thinking\>Plan\</thinking\>\<reasoning\>SECRET\</reasoning\>Answer",
    "```xml\n<thinking>Plan</thinking><reasoning>SECRET</reasoning>\n```Answer",
    "`<thinking>Plan</thinking>`<reasoning>SECRET</reasoning>Answer",
])
def test_legacy_envelopes_use_authoritative_extraction(monkeypatch, model, text):
    h = Harness(monkeypatch)
    async def astream(messages, **kw):
        for char in text:
            yield AIMessageChunk(content=char)
        assert not any(k == "answer_delta" for k, _ in events(h))
        assert "SECRET" not in str(events(h))
    monkeypatch.setattr(h.models[model], "astream", astream)
    assert h.run(model=model)["message"] == "Answer"
    assert "SECRET" not in str(events(h))
    assert events(h)[-2:] == [("answer_end", "Answer"), ("final_commit", "")]
