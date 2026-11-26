"""Фоновая LLM-сводка tool_trace: финал её не ждёт, БД обновляется, когда строка появится."""
import asyncio

from agent.common import trace_budget as tb


class FakeDB:
    def __init__(self, miss=0):
        self.miss, self.calls = miss, []

    async def set_bot_tool_trace(self, message_id, trace):
        self.calls.append((message_id, trace))
        if self.miss:
            self.miss -= 1
            return False
        return True


async def _done(value):
    return value


def test_refine_waits_for_row_then_updates(monkeypatch):
    db = FakeDB(miss=2)
    monkeypatch.setattr(tb, "TRACE_REFINE_DELAY_SEC", 0)

    async def go():
        await tb.refine_tool_trace(asyncio.ensure_future(_done("SUM | tail")), "msg-1", db.set_bot_tool_trace)

    asyncio.run(go())
    assert db.calls == [("msg-1", "SUM | tail")] * 3


def test_refine_empty_trace_no_update(monkeypatch):
    db = FakeDB()

    async def go():
        await tb.refine_tool_trace(asyncio.ensure_future(_done(None)), "msg-1", db.set_bot_tool_trace)

    asyncio.run(go())
    assert db.calls == []
