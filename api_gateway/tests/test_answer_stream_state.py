import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import run_status


@pytest.fixture
def state_store(monkeypatch):
    values = {}
    redis = AsyncMock()

    async def get(key):
        return values.get(key)

    async def set_value(key, value, **kwargs):
        values[key] = value

    redis.get.side_effect = get
    redis.set.side_effect = set_value
    redis.pttl.return_value = 3590000
    redis.lrange.return_value = []
    redis.rpush.return_value = 1
    monkeypatch.setattr(run_status, "_redis", redis)
    monkeypatch.setattr(run_status, "count_active_requests", AsyncMock(return_value=1))
    return values


def test_answer_frames_do_not_pollute_reasoning_or_complete_run(state_store):
    async def scenario():
        await run_status.mark_running("c1", "r1")
        await run_status.append_reasoning_chunk("c1", "thinking", "live", "r1")
        baseline = json.loads(state_store["agent:reasoning:c1"])
        for kind, chunk in [
            ("answer_delta", '{"component":"Chart",'),
            ("answer_reset", ""),
            ("answer_delta", "replacement"),
            ("answer_end", "authoritative answer"),
            ("answer_end", ""),
        ]:
            assert await run_status.append_reasoning_chunk("c1", chunk, kind, "r1") == 1
            assert json.loads(state_store["agent:reasoning:c1"]) == baseline
            assert "agent:running:c1" in state_store
        await run_status.append_reasoning_chunk("c1", "", "reasoning_end", "r1")
        reasoning = await run_status.get_reasoning_state("c1")
        assert reasoning["steps"] == ["thinking"]
        assert reasoning["live_text"] == ""
        await run_status.append_reasoning_chunk("c1", "", "final_commit", "r1")
        reasoning = await run_status.get_reasoning_state("c1")
        assert reasoning["is_final"] is True
        assert reasoning["completed"] is False
        assert reasoning["steps"] == ["thinking"]
        await run_status.clear_running("c1", request_id="r1")
        assert (await run_status.get_reasoning_state("c1"))["completed"] is True

    asyncio.run(scenario())


def test_reconnect_running_state_has_reasoning_but_no_answer_candidate(state_store):
    async def scenario():
        await run_status.mark_running("c1", "r1")
        await run_status.append_reasoning_chunk("c1", "thought", "live", "r1")
        await run_status.append_reasoning_chunk("c1", "", "reasoning_end", "r1")
        await run_status.append_reasoning_chunk("c1", "candidate-only", "answer_delta", "r1")
        running = await run_status.get_running("c1")
        assert running["request_id"] == "r1"
        assert running["reasoning"]["steps"] == ["thought"]
        assert running["reasoning"]["live_text"] == ""
        assert "candidate-only" not in json.dumps(running)
        await run_status.append_reasoning_chunk("c1", "", "answer_reset", "r1")
        assert await run_status.get_running("c1") == running
        await run_status.mark_running("c1", "r2")
        running = await run_status.get_running("c1")
        assert running["reasoning"]["steps"] == []
        assert running["reasoning"]["request_id"] == "r2"

    asyncio.run(scenario())
