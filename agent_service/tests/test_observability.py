import asyncio
import io
import json
import logging
import os

import httpx
import pytest
from agent.core import observability as obs
from agent.core.observability_http import instrument_async_client, instrument_llm


@pytest.fixture(autouse=True)
def isolated_context(monkeypatch, tmp_path):
    token = obs.LOG_CONTEXT.set({})
    monkeypatch.setenv('AGENT_DIAGNOSTICS_DIR', str(tmp_path / 'raw'))
    monkeypatch.delenv('AGENT_LOG_DIR', raising=False)
    monkeypatch.setenv('AGENT_LOG_JSONL', str(tmp_path / 'events.jsonl'))
    monkeypatch.delenv('AGENT_DIAGNOSTICS_CHATS', raising=False)
    yield
    obs.LOG_CONTEXT.reset(token)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def records(caplog):
    return [r for r in caplog.records if hasattr(r, 'event')]


def test_redact_nested_and_free_text(monkeypatch):
    monkeypatch.setenv('CLOUD_LLM_ACCESS_KEY', 'really-private-value')
    raw = {'authorization': 'Bearer abc', 'signature': 'signed-thinking', 'input_tokens': 23,
           'v': ['password=private Bearer foo', 'really-private-value', {'api_key': 'xxx'}]}
    text = json.dumps(obs.redact(raw))
    for secret in ('really-private-value', 'signed-thinking', 'private ', 'Bearer foo', 'xxx'):
        assert secret not in text
    assert obs.redact(raw)['input_tokens'] == 23


@pytest.mark.anyio
async def test_request_context_parallel_cancel_restore(caplog):
    caplog.set_level(logging.INFO)
    @obs.request_logged
    async def handle_message(payload, prod=None):
        obs.log_event('marker', seen=payload['chat_id'])
        await asyncio.sleep(0)
        assert obs.LOG_CONTEXT.get()['chat_id'] == payload['chat_id']
        if payload.get('cancel'):
            raise asyncio.CancelledError()
    await asyncio.gather(handle_message({'chat_id': 'a', 'request_id': 'ra', 'message': 'secret prompt'}),
                         handle_message({'chat_id': 'b', 'request_id': 'rb'}))
    with pytest.raises(asyncio.CancelledError):
        await handle_message({'chat_id': 'c', 'cancel': True})
    assert obs.LOG_CONTEXT.get() == {}
    assert [r.fields['seen'] for r in records(caplog) if r.event == 'marker'][:2] == ['a', 'b']
    for r in records(caplog):
        obs.ContextFilter().filter(r)
        assert 'secret prompt' not in obs.JSONFormatter().format(r)
        assert 'secret prompt' not in obs.ConsoleFormatter().format(r)


@pytest.mark.anyio
async def test_tool_context_parallel_restore(caplog):
    caplog.set_level(logging.INFO)
    class Tools:
        @obs.tool_logged
        async def invoke(self, tools, call, request_id):
            await asyncio.sleep(0)
            assert obs.LOG_CONTEXT.get()['tool_call_id'] == call['id']
            return 'sensitive output'
    await asyncio.gather(*(Tools().invoke({}, {'id': str(i), 'name': 'test', 'args': {'q': 'private'}}, 'r') for i in range(3)))
    assert obs.LOG_CONTEXT.get() == {}
    assert len([r for r in records(caplog) if r.event == 'tool.end']) == 3
    assert 'sensitive output' not in '\n'.join(str(r.fields) for r in records(caplog))


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, error=None):
        self.chunks, self.error, self.closed = chunks, error, False
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error
    async def aclose(self):
        self.closed = True


async def send(chunks, headers=None, error=None, payload=None):
    stream = Chunks(chunks, error)
    async def handler(request):
        return httpx.Response(200, headers=headers or {'content-type': 'text/event-stream', 'x-request-id': 'provider-123'}, stream=stream)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        instrument_async_client(client)
        instrument_async_client(client)
        assert len(client.event_hooks['request']) == 1
        async with client.stream('POST', 'https://llm.test/v1/messages',
                                 headers={'Authorization': 'Bearer NEVER_SAVE_ME'}, json=payload or {
                                     'model': 'test', 'messages': [{'role': 'user', 'content': 'do work'}],
                                     'tools': [{'name': 'shell'}], 'stream': True}) as response:
            result = [chunk async for chunk in response.aiter_bytes()]
    return result, stream


@pytest.mark.anyio
async def test_stream_fidelity_eof_opt_in_metadata(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv('AGENT_DIAGNOSTICS_CHATS', '425826')
    obs.LOG_CONTEXT.set({'chat_id': '425826', 'request_id': 'r', 'query_text': 'do work'})
    chunks = [b'event: message_start\ndata: {}\n\n', b'event: message_', b'stop\ndata: {"type":"message_stop"}\n\n']
    result, stream = await send(chunks)
    assert result == chunks and stream.closed
    events = records(caplog)
    req = next(r.fields for r in events if r.event == 'llm.http.request')
    assert req['current_query_present'] is True
    assert req['last_user']['sha256'] == obs.fingerprint('do work')['sha256']
    end = next(r.fields for r in events if r.event == 'llm.http.end')
    assert end['sse_terminal'] and end['eof'] and not end['incomplete_sse']
    files = list((tmp_path/'raw').glob('*.json'))
    assert len(files) == 2
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    assert (tmp_path/'raw').stat().st_mode & 0o777 == 0o700
    saved = ''.join(p.read_text() for p in files)
    assert 'NEVER_SAVE_ME' not in saved and 'query_text' not in saved
    assert 'provider-123' in saved
    obs.LOG_CONTEXT.set({'chat_id': 'other'})
    await send([b'data: [DONE]\n\n'])
    assert len(list((tmp_path/'raw').glob('*'))) == 2


@pytest.mark.anyio
async def test_incomplete_sse_and_error_propagation(caplog):
    caplog.set_level(logging.INFO)
    await send([b'event: message_start\n\n', b': ping\n\n'])
    end = [r.fields for r in records(caplog) if r.event == 'llm.http.end'][-1]
    assert end['incomplete_sse'] and end['eof']
    with pytest.raises(httpx.ReadError):
        await send([b'partial'], error=httpx.ReadError('broken'))
    end = [r.fields for r in records(caplog) if r.event == 'llm.http.end'][-1]
    assert end['error_type'] == 'ReadError' and not end['eof']


@pytest.mark.anyio
async def test_non_sse_json_not_incomplete(caplog):
    caplog.set_level(logging.INFO)
    await send([b'{"content":[]}'], headers={'content-type': 'application/json'})
    end = next(r.fields for r in records(caplog) if r.event == 'llm.http.end')
    assert not end['incomplete_sse'] and end['sse_terminal'] is None


@pytest.mark.anyio
async def test_bounds_ttl_and_body_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_DIAGNOSTICS_CHATS', 'c')
    monkeypatch.setenv('AGENT_DIAGNOSTICS_MAX_BYTES', '4096')
    monkeypatch.setenv('AGENT_DIAGNOSTICS_MAX_TOTAL_BYTES', '8192')
    monkeypatch.setenv('AGENT_DIAGNOSTICS_TTL_SECONDS', '1024')
    monkeypatch.setenv('CLOUD_LLM_ACCESS_KEY', 'known-secret-value')
    obs.LOG_CONTEXT.set({'chat_id': 'c'})
    d=tmp_path/'raw'; d.mkdir()
    expired = d/'old.json'; expired.write_text('{}'); os.utime(expired, (0, 0))
    for _ in range(8):
        await send([b'data: known-secret-value\n\n' * 400])
    files = list(d.glob('*.json'))
    assert not expired.exists()
    assert sum(p.stat().st_size for p in files) <= 8192
    assert all(p.stat().st_size <= 4096 for p in files)
    assert 'known-secret-value' not in ''.join(p.read_text() for p in files)
    assert any(json.loads(p.read_text()).get('capture_truncated') for p in files)


def test_jsonl_rotation_is_private(tmp_path, monkeypatch):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        obs.configure_logging(); obs.configure_logging()
        handlers = [h for h in root.handlers if getattr(h, '_agent_jsonl', False)]
        assert len(handlers) == 1
        h = handlers[0]; h.max_bytes = 1024
        obs.LOG_CONTEXT.set({'chat_id': 'c', 'query_text': 'hidden'})
        for i in range(40):
            obs.log_event('check', n=i, note='x'*50)
        files = list(tmp_path.glob('??_??_????.jsonl'))
        assert files
        assert sum(p.stat().st_size for p in files) <= 1024
        for p in files:
            assert p.stat().st_mode & 0o777 == 0o600
            for line in p.read_text().splitlines():
                value=json.loads(line); assert value['chat_id']=='c'
                assert 'query_text' not in value and 'hidden' not in line
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h); h.close()


@pytest.mark.anyio
async def test_hook_failure_never_breaks_request(monkeypatch):
    import agent.core.observability_http as wire
    monkeypatch.setattr(wire, '_capture', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    # Request hook and response close must be nonfatal even with unexpected capture errors.
    result, _ = await send([b'data: [DONE]\n\n'])
    assert result == [b'data: [DONE]\n\n']

@pytest.mark.anyio
async def test_early_close_preserves_underlying_stream(caplog):
    from agent.core.observability_http import ObservedStream
    caplog.set_level(logging.INFO)
    source = Chunks([b'one', b'two'])
    observed = ObservedStream(source, {}, False, 'text/event-stream')
    iterator=observed.__aiter__()
    assert await iterator.__anext__() == b'one'
    await observed.aclose()
    await iterator.aclose()
    assert source.closed
    assert next(r.fields for r in records(caplog) if r.event=='llm.http.end')['error_type']=='StreamClosedEarly'


@pytest.mark.anyio
async def test_actual_sdk_clients_are_instrumented(caplog):
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI
    caplog.set_level(logging.INFO)
    opus = ChatAnthropic(model='claude-test', api_key='test-key', base_url='https://llm.test', max_tokens=30)
    gpt = ChatOpenAI(model='gpt-test', api_key='test-key', base_url='https://llm.test')
    for model, sdk in [(opus, opus._async_client), (gpt, gpt.root_async_client)]:
        instrument_llm(model)
        client = sdk._client
        assert len(client.event_hooks['request']) >= 1
        instrument_llm(model.bind_tools([]))
        from agent.core.observability_http import _request
        assert client.event_hooks['request'].count(_request) == 1
        await client.aclose()


@pytest.mark.anyio
async def test_stream_retry_attempts_and_no_reasoning_in_result(monkeypatch, caplog):
    from agent.event_loop import _astream_collect
    from langchain_core.messages import AIMessageChunk, HumanMessage
    import agent.event_loop as el
    caplog.set_level(logging.INFO)
    attempts = []
    class Model:
        async def astream(self, messages, **kw):
            attempts.append(dict(obs.LOG_CONTEXT.get()))
            if len(attempts) == 1:
                raise httpx.ReadError('no connection')
            yield AIMessageChunk(content='Вижу пустой запрос.', response_metadata={'stop_reason':'end_turn'},
                                 additional_kwargs={'reasoning_content': 'sensitive reasoning', 'signature': 'huge signature'})
    async def no_sleep(*a): pass
    monkeypatch.setattr(el.asyncio, 'sleep', no_sleep)
    obs.LOG_CONTEXT.set({'chat_id':'c', 'request_id':'r'})
    response = await _astream_collect(Model(), [HumanMessage(content='work')], iteration=2)
    assert response.content == 'Вижу пустой запрос.'
    assert [a['attempt'] for a in attempts] == [1, 2]
    assert all(a['iteration']==2 and a['chat_id']=='c' for a in attempts)
    assert obs.LOG_CONTEXT.get() == {'chat_id':'c','request_id':'r'}
    result=next(r.fields for r in records(caplog) if r.event=='llm.result')
    assert result['suspicious_empty_claim'] is True
    assert 'sensitive reasoning' not in str(result) and 'huge signature' not in str(result)

@pytest.mark.anyio
async def test_native_anthropic_sdk_tool_call_end_to_end(tmp_path, monkeypatch, caplog):
    import anthropic
    from langchain_anthropic import ChatAnthropic
    from langchain_core.messages import HumanMessage
    from agent.event_loop import _astream_collect
    caplog.set_level(logging.INFO)
    monkeypatch.setenv('AGENT_DIAGNOSTICS_CHATS', 'sdk')
    obs.LOG_CONTEXT.set({'chat_id':'sdk','request_id':'sdk-real','query_text':'read schema'})
    def event(name, data):
        return f'event: {name}\ndata: {json.dumps(data)}\n\n'.encode()
    chunks=[event('message_start', {'type':'message_start','message':{'id':'msg-test','type':'message',
                'role':'assistant','content':[],'model':'claude-test', 'stop_reason':None,
                'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':0}}}),
            event('content_block_start', {'type':'content_block_start','index':0,
                'content_block':{'type':'tool_use','id':'toolu-test','name':'get_db_schema','input':{}}}),
            event('content_block_delta', {'type':'content_block_delta','index':0,
                'delta':{'type':'input_json_delta','partial_json':'{}'}}),
            event('content_block_stop', {'type':'content_block_stop','index':0}),
            event('message_delta', {'type':'message_delta','delta':{'stop_reason':'tool_use','stop_sequence':None},
                'usage':{'output_tokens':5}}),
            event('message_stop', {'type':'message_stop'})]
    async def handler(request):
        assert json.loads(request.content)['messages'][-1]['content']=='read schema'
        return httpx.Response(200,headers={'content-type':'text/event-stream','x-request-id':'native-sdk'},stream=Chunks(chunks))
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model=ChatAnthropic(model='claude-test', api_key='fake-test', base_url='https://test', max_tokens=100)
    model.__dict__['_async_client']=anthropic.AsyncAnthropic(api_key='fake-test', base_url='https://test',http_client=client)
    bound=model.bind_tools([{'name':'get_db_schema','description':'test schema','input_schema':{'type':'object','properties':{}}}])
    response=await _astream_collect(bound,[HumanMessage(content='read schema')],iteration=3)
    assert response.tool_calls[0]['name']=='get_db_schema'
    assert response.tool_calls[0]['args']=={}
    req=next(r.fields for r in records(caplog) if r.event=='llm.http.request')
    assert req['tools']==['get_db_schema'] and req['current_query_present'] is True
    assert next(r.fields for r in records(caplog) if r.event=='llm.http.end')['sse_terminal'] is True
    await client.aclose()


def test_console_single_line_and_redacts_credentials():
    record=logging.LogRecord('agent',logging.INFO,'',0,'one\nAuthorization: Bearer verysecret\ntwo',(),None)
    obs.LOG_CONTEXT.set({'chat_id':'c','request_id':'r','iteration':2,'query_text':'never print this'})
    obs.ContextFilter().filter(record)
    value=obs.ConsoleFormatter().format(record)
    assert '\n' not in value and 'verysecret' not in value and 'never print this' not in value
    assert 'chat_id=c' in value and 'iteration=2' in value

@pytest.mark.anyio
async def test_gzip_sse_is_observed_without_changing_delivery(tmp_path, monkeypatch, caplog):
    import gzip
    caplog.set_level(logging.INFO)
    monkeypatch.setenv('AGENT_DIAGNOSTICS_CHATS', 'gz')
    obs.LOG_CONTEXT.set({'chat_id':'gz'})
    raw=b'event: message_start\ndata: {}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'
    compressed=gzip.compress(raw)
    result, _ = await send([compressed[:13],compressed[13:]],
                          headers={'content-type':'text/event-stream','content-encoding':'gzip'})
    assert b''.join(result) == raw
    end=next(r.fields for r in records(caplog) if r.event=='llm.http.end')
    assert end['sse_terminal'] is True and end['response_bytes']==len(compressed)
    assert end['decoded_bytes']==len(raw) and not end['decode_error']
    response=next(p for p in (tmp_path/'raw').glob('*.response.json'))
    assert 'message_stop' in json.loads(response.read_text())['body']

@pytest.mark.anyio
async def test_openai_sdk_tool_call_end_to_end(caplog):
    import openai
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from agent.event_loop import _astream_collect
    caplog.set_level(logging.INFO)
    obs.LOG_CONTEXT.set({'chat_id':'openai','request_id':'r','query_text':'read schema'})
    def data(delta, finish=None):
        return ('data: '+json.dumps({'id':'chatcmpl-test','object':'chat.completion.chunk','created':1,'model':'gpt-test',
            'choices':[{'index':0,'delta':delta,'finish_reason':finish}]})+'\n\n').encode()
    chunks=[data({'role':'assistant','tool_calls':[{'index':0,'id':'call-1','type':'function',
                     'function':{'name':'get_db_schema','arguments':'{'}}]}),
            data({'tool_calls':[{'index':0,'function':{'arguments':'}'}}]}, 'tool_calls'), b'data: [DONE]\n\n']
    async def handler(request):
        assert request.url.path.endswith('/chat/completions')
        return httpx.Response(200,headers={'content-type':'text/event-stream','x-request-id':'openai-sdk'},stream=Chunks(chunks))
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sdk=openai.AsyncOpenAI(api_key='fake-test',base_url='https://test/v1',http_client=client)
    model=ChatOpenAI(model='gpt-test',api_key='fake-test',base_url='https://test/v1',root_async_client=sdk,async_client=sdk.chat.completions)
    response=await _astream_collect(model,[HumanMessage(content='read schema')],iteration=1)
    assert response.tool_calls[0]['name']=='get_db_schema' and response.tool_calls[0]['args']=={}
    end=next(r.fields for r in records(caplog) if r.event=='llm.http.end')
    assert end['sse_terminal'] and end['provider_request_id']=='openai-sdk'
    await client.aclose()


def test_model_mode_module_imports_with_request_decorator():
    from agent.not_loop import LLModel
    assert hasattr(LLModel.process_query, '__wrapped__')
