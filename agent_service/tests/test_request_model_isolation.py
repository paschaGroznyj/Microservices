"""Regression: one HarnessAgent serves parallel chats and background supervisors."""
import asyncio
import functools
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from agent.core.observability import LOG_CONTEXT, llm_logged


def async_test(fn):
    """Match the repository's asyncio.run style; no pytest-asyncio dependency."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


@async_test
async def test_background_model_is_request_local(agent_obj):
    agent = agent_obj
    ready = asyncio.Event()
    overwritten = asyncio.Event()
    seen = []

    async def invoke(messages):
        seen.append(dict(LOG_CONTEXT.get()))
        return 'claude-result'

    agent.AGENT_MODELS['designer'].ainvoke = AsyncMock(side_effect=invoke)
    agent.AGENT_MODELS['gemini'].ainvoke = AsyncMock(return_value='gemini-result')

    async def chat_a():
        ctx = agent._request_ctx.set({'agent_model': 'designer'})
        log = LOG_CONTEXT.set({'request_id': 'A', 'agent_model': 'designer'})
        agent.current_agent_model = 'designer'

        async def background():
            ready.set()
            await overwritten.wait()
            return await agent._main_ainvoke([HumanMessage(content='goal')], llm_role='supervisor')

        task = asyncio.create_task(background())
        # Simulate process_query cleanup while its background task still exists.
        agent._request_ctx.reset(ctx)
        LOG_CONTEXT.reset(log)
        return await task

    async def chat_b():
        await ready.wait()
        ctx = agent._request_ctx.set({'agent_model': 'gemini'})
        log = LOG_CONTEXT.set({'request_id': 'B', 'agent_model': 'gemini'})
        try:
            agent.current_agent_model = 'gemini'
            overwritten.set()
            return await agent._main_ainvoke([], llm_role='supervisor')
        finally:
            agent._request_ctx.reset(ctx)
            LOG_CONTEXT.reset(log)

    before = dict(LOG_CONTEXT.get())
    assert await asyncio.gather(chat_a(), chat_b()) == ['claude-result', 'gemini-result']
    assert seen == [{'request_id': 'A', 'agent_model': 'designer', 'llm_role': 'supervisor'}]
    agent.AGENT_MODELS['designer'].ainvoke.assert_awaited_once()
    agent.AGENT_MODELS['gemini'].ainvoke.assert_awaited_once()
    assert agent._request_ctx.get() is None
    assert LOG_CONTEXT.get() == before


@async_test
async def test_no_context_uses_universal_not_shared_model(agent_obj):
    agent_obj.current_agent_model = 'gemini'
    agent_obj.AGENT_MODELS['universal'].ainvoke = AsyncMock(return_value='universal')
    agent_obj.AGENT_MODELS['gemini'].ainvoke = AsyncMock()
    assert await agent_obj._main_ainvoke([]) == 'universal'
    agent_obj.AGENT_MODELS['gemini'].ainvoke.assert_not_awaited()


@async_test
async def test_missing_model_falls_back_and_restores_log_context(agent_obj):
    ctx = agent_obj._request_ctx.set({'agent_model': 'missing'})
    log = LOG_CONTEXT.set({'request_id': 'fallback', 'llm_role': 'parent'})
    seen = []

    async def lite(messages):
        seen.append(dict(LOG_CONTEXT.get()))
        return 'lite'

    agent_obj._lite_ainvoke = AsyncMock(side_effect=lite)
    try:
        assert await agent_obj._main_ainvoke([], llm_role='supervisor') == 'lite'
        assert seen[0]['llm_role'] == 'supervisor'
        assert LOG_CONTEXT.get() == {'request_id': 'fallback', 'llm_role': 'parent'}
    finally:
        agent_obj._request_ctx.reset(ctx)
        LOG_CONTEXT.reset(log)


@async_test
@pytest.mark.parametrize('error', [RuntimeError('provider failed'), asyncio.CancelledError()])
async def test_model_error_restores_log_context(agent_obj, error):
    ctx = agent_obj._request_ctx.set({'agent_model': 'designer'})
    log = LOG_CONTEXT.set({'request_id': 'error', 'llm_role': 'parent'})
    agent_obj.AGENT_MODELS['designer'].ainvoke = AsyncMock(side_effect=error)
    try:
        with pytest.raises(type(error)):
            await agent_obj._main_ainvoke([], llm_role='supervisor')
        assert LOG_CONTEXT.get() == {'request_id': 'error', 'llm_role': 'parent'}
    finally:
        agent_obj._request_ctx.reset(ctx)
        LOG_CONTEXT.reset(log)


@async_test
async def test_main_llm_role_is_scoped():
    seen = []

    @llm_logged
    async def call(llm, messages, **kwargs):
        from langchain_core.messages import AIMessage
        seen.append(dict(LOG_CONTEXT.get()))
        return AIMessage(content='ok')

    token = LOG_CONTEXT.set({'request_id': 'main'})
    try:
        await call(object(), [], iteration=7)
        assert seen == [{'request_id': 'main', 'iteration': 7, 'llm_role': 'main'}]
        assert LOG_CONTEXT.get() == {'request_id': 'main'}
    finally:
        LOG_CONTEXT.reset(token)


@async_test
async def test_real_supervisor_uses_request_model_and_role(agent_obj):
    from langchain_core.messages import AIMessage
    from agent.supervisor import run_supervisor

    seen = []

    async def invoke(messages):
        seen.append(dict(LOG_CONTEXT.get()))
        return AIMessage(content='{"status":"ok","reason":"on track"}')

    agent_obj.current_agent_model = 'gemini'
    agent_obj.AGENT_MODELS['designer'].ainvoke = AsyncMock(side_effect=invoke)
    agent_obj.AGENT_MODELS['gemini'].ainvoke = AsyncMock()
    ctx = agent_obj._request_ctx.set({'agent_model': 'designer'})
    log = LOG_CONTEXT.set({'request_id': 'real-supervisor'})
    state = {'iteration': 5, 'hard_limit': 100}
    try:
        assert await run_supervisor(agent_obj, 'fix bug', [HumanMessage(content='fix bug')], state) is None
        assert len(seen) == 1
        assert seen[0]['llm_role'] == 'supervisor'
        assert seen[0]['request_id'] == 'real-supervisor'
        assert state['supervisor_history'][0]['status'] == 'ok'
        agent_obj.AGENT_MODELS['gemini'].ainvoke.assert_not_awaited()
        assert LOG_CONTEXT.get() == {'request_id': 'real-supervisor'}
    finally:
        agent_obj._request_ctx.reset(ctx)
        LOG_CONTEXT.reset(log)


def test_process_query_saves_resolved_model_in_context(monkeypatch):
    from tests.test_process_query import Harness
    from tests.conftest import ai
    import agent.event_loop as el

    h = Harness(monkeypatch)
    original = el.resolve_model_tag
    monkeypatch.setattr(el, 'resolve_model_tag', lambda tag, models: original('designer', models))
    h.models['designer'].script = [ai('done')]
    assert h.run('fix', model='alias-from-ui')['message'] == 'done'
    assert h.ctx_seen[0]['agent_model'] == 'alias-from-ui'
    assert h.ctx_seen[0]['resolved_agent_model'] == 'designer'
    assert h.agent._request_ctx.get() is None


@async_test
async def test_resolved_alias_takes_precedence(agent_obj):
    ctx = agent_obj._request_ctx.set({'agent_model': 'ui-alias', 'resolved_agent_model': 'designer'})
    agent_obj.AGENT_MODELS['designer'].ainvoke = AsyncMock(return_value='claude')
    try:
        assert await agent_obj._main_ainvoke([]) == 'claude'
        agent_obj.AGENT_MODELS['designer'].ainvoke.assert_awaited_once()
    finally:
        agent_obj._request_ctx.reset(ctx)
