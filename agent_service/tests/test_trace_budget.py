"""Бюджет tool_trace в токенах: сжатие на записи и вклейка в историю."""
import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from agent.common import trace_budget as tb

CPT = 4.0


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def item(n, ch="x"):
    return f"call{n}:" + ch * 400          # ~101 токен


async def fake_sum(text):
    fake_sum.calls.append(text)
    return "| tool | calls | result |"


def test_short_trace_raw():
    fake_sum.calls = []
    items = [item(i) for i in range(3)]
    assert run(tb.compress_trace(items, CPT, fake_sum)) == "; ".join(items)
    assert fake_sum.calls == []


def test_empty_trace_none():
    assert run(tb.compress_trace([], CPT, fake_sum)) is None


def test_long_trace_tail_raw_head_summarized():
    fake_sum.calls = []
    items = [item(i) for i in range(60)]          # ~6K токенов
    out = run(tb.compress_trace(items, CPT, fake_sum))
    head, tail = out.split(tb.TAIL_MARK)
    assert head == "| tool | calls | result |"
    tail_items = tail.split("; ")
    assert tail_items == items[-len(tail_items):]  # целые вызовы, по порядку
    assert tb.tokens(tail, CPT) <= tb.TRACE_RAW_TAIL_TOKENS
    assert len(tail_items) == 14                   # 14 * ~102 <= 1500
    assert fake_sum.calls == ["; ".join(items[:-14])]
    assert tb.tokens(out, CPT) <= tb.TRACE_MSG_TOKENS


def test_summary_failure_falls_back_to_clipped_head():
    async def boom(text):
        raise RuntimeError("llm down")
    items = [item(i) for i in range(60)]
    out = run(tb.compress_trace(items, CPT, boom))
    head, tail = out.split(tb.TAIL_MARK)
    assert head.startswith("call0:")               # начало сохранено
    assert tb.tokens(head, CPT) <= tb.TRACE_HEAD_TOKENS
    assert tail.endswith(items[-1])


def test_huge_summary_clipped():
    async def fat(text):
        return "y" * 100_000
    out = run(tb.compress_trace([item(i) for i in range(60)], CPT, fat))
    head = tb.head_of(out)
    assert tb.tokens(head, CPT) <= tb.TRACE_HEAD_TOKENS


def test_single_giant_call_clipped_into_tail():
    items = ["only:" + "z" * 40_000]
    out = run(tb.compress_trace(items, CPT, fake_sum))
    assert tb.TAIL_MARK not in out
    assert out.startswith("only:")
    assert tb.tokens(out, CPT) <= tb.TRACE_RAW_TAIL_TOKENS


def test_budget_depends_on_model_cpt():
    items = [item(i) for i in range(28)]           # ~11.4K симв
    # GPT (4.0 симв/ток): ~2.9K токенов, влезает сырым
    assert tb.TAIL_MARK not in run(tb.compress_trace(items, 4.0, fake_sum))
    # Opus (2.4 симв/ток): тот же трейс ~4.8K токенов, сжатие
    assert tb.TAIL_MARK in run(tb.compress_trace(items, 2.4, fake_sum))


def _hist(n_bot, trace):
    msgs = []
    for i in range(n_bot):
        msgs.append(HumanMessage(content=f"q{i}"))
        msgs.append(AIMessage(content=f"a{i}", additional_kwargs={"db_id": i, "tool_trace": trace}))
    return msgs


def test_attach_full_for_last_two_head_for_older():
    stored = "SUMMARY" + tb.TAIL_MARK + "raw-tail"
    out = tb.attach_traces(_hist(4, stored), CPT)
    bots = [m for m in out if isinstance(m, AIMessage)]
    assert [b.content.split(tb.CONTENT_MARK)[1] for b in bots] == ["SUMMARY", "SUMMARY", stored, stored]
    assert all(b.content.split(tb.CONTENT_MARK)[0] == f"a{i}" for i, b in enumerate(bots))
    assert all(b.additional_kwargs["db_id"] == i for i, b in enumerate(bots))


def test_attach_budgets_in_tokens():
    raw = "; ".join(item(i) for i in range(100))   # ~10K токенов, старый формат без маркера
    out = tb.attach_traces(_hist(3, raw), CPT)
    parts = [m.content.split(tb.CONTENT_MARK)[1] for m in out if isinstance(m, AIMessage)]
    assert tb.tokens(parts[0], CPT) <= tb.TRACE_OLD_TOKENS
    assert tb.tokens(parts[2], CPT) <= tb.TRACE_MSG_TOKENS


def test_attach_idempotent_and_skips_no_trace():
    msgs = [HumanMessage(content="q"), AIMessage(content="a"), AIMessage(content="b", additional_kwargs={"tool_trace": "t"})]
    once = tb.attach_traces(msgs, CPT)
    twice = tb.attach_traces(once, CPT)
    assert once[1].content == "a"
    assert twice[2].content == once[2].content and once[2].content.count(tb.CONTENT_MARK) == 1


def test_trim_counts_attached_trace():
    from langchain_core.messages.utils import count_tokens_approximately
    plain = _hist(2, None)
    withtr = tb.attach_traces(_hist(2, "; ".join(item(i) for i in range(20))), CPT)
    assert count_tokens_approximately(withtr) > count_tokens_approximately(plain) + 1000


def test_fast_short_trace_raw_no_llm():
    items = [item(i) for i in range(3)]
    assert tb.compress_trace_fast(items, CPT) == ("; ".join(items), False)
    assert tb.compress_trace_fast([], CPT) == (None, False)


def test_fast_long_trace_clips_head_and_asks_llm():
    items = [item(i) for i in range(60)]
    out, need = tb.compress_trace_fast(items, CPT)
    assert need is True
    head, tail = out.split(tb.TAIL_MARK)
    assert tb.tokens(head, CPT) <= tb.TRACE_HEAD_TOKENS + 1
    full = run(tb.compress_trace(items, CPT, fake_sum))
    assert tail == full.split(tb.TAIL_MARK)[1]
