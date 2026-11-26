"""Real LangChain tool invocations against a mocked gateway/factory, no DB/network."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from agent.core import settings
from agent.tools.plugins import tool_device_bridge as bridge
from agent.tools.telegram import tool_send_file as sources

DEVICE = '11111111-1111-4111-8111-111111111111'
TICKET = '22222222-2222-4222-8222-222222222222'
IMAGE = '33333333-3333-4333-8333-333333333333'
SECRET = 'service-secret-not-for-model'
CREDENTIAL = 't' * 64
DATA = b'example file\x00\xff'


def metadata(data=DATA):
    return {'path': '../../untrusted/name.txt', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'mime': 'untrusted/remote'}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.read_count = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv('DEVICE_BRIDGE_URL', 'http://gateway.test')
    monkeypatch.setenv('DEVICE_BRIDGE_SERVICE_TOKEN', SECRET)
    monkeypatch.setenv('CHATS_SERVICE_URL', 'http://chats.test')
    monkeypatch.setattr(bridge, 'DOWNLOAD_DIR', tmp_path / 'downloads')
    monkeypatch.setattr(sources, 'ALLOWED_SEND_ROOTS', [str(tmp_path / 'generated')])
    monkeypatch.setattr(sources, 'SANDBOX_URL', 'http://factory.test')
    (tmp_path / 'generated').mkdir()
    return Path(bridge._save_download(DATA, 'source.txt', 73)['path'])


@pytest.fixture
def gateway(monkeypatch, env):
    requests = []
    state = SimpleNamespace(command_result=None, download=DATA, uploaded=None,
                            overrides={}, stream=None, limit=bridge.MAX_FILE_BYTES)

    def handler(request):
        requests.append(request)
        method, path = request.method, request.url.path
        if request.url.host == 'chats.test' or request.url.host == 'chats_service':
            assert method == 'GET' and path == '/editor/owns'
            assert 'authorization' not in request.headers
            override = state.overrides.get('owns', httpx.Response(200, json={'owns': True}))
            if isinstance(override, Exception):
                raise override
            return override
        if request.url.host == 'factory.test':
            return state.overrides.get('factory', httpx.Response(200, content=DATA))
        assert request.url.host == 'gateway.test'
        assert request.headers['authorization'] == 'Bearer ' + SECRET
        if path.endswith('/commands'):
            body = json.loads(request.content)
            assert body['user_id'] == 73
            assert 'cwd' not in body
        else:
            assert dict(request.url.params) == {'user_id': '73'}
        override = state.overrides.get((method, path))
        if override is not None:
            if isinstance(override, Exception):
                raise override
            return override
        if path.endswith('/transfers'):
            return httpx.Response(200, json={'transfer_id': TICKET, 'transfer_token': CREDENTIAL,
                                            'max_bytes': state.limit})
        if path.endswith('/commands'):
            result = state.command_result
            if result is None:
                result = {'status': 'ok', 'exit_code': 0, 'output': json.dumps(metadata())}
            return httpx.Response(200, json=result)
        if method == 'PUT':
            state.uploaded = request.content
            return httpx.Response(200, json=metadata(request.content))
        if method == 'GET':
            if state.stream is not None:
                return httpx.Response(200, stream=state.stream)
            return httpx.Response(200, content=state.download,
                                  headers={'X-Content-SHA256': hashlib.sha256(state.download).hexdigest(),
                                           'Content-Type': 'text/evil'})
        if method == 'DELETE':
            return httpx.Response(200, json={'deleted': True})
        raise AssertionError((method, path))

    real_client = httpx.AsyncClient
    def factory(**kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['trust_env'] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(bridge.httpx, 'AsyncClient', factory)
    state.requests = requests
    return state


def tools(**kwargs):
    return {t.name: t for t in bridge.make_device_tools(73, **kwargs)}


async def invoke(name, **kwargs):
    return await tools()[name].ainvoke({'device_id': DEVICE, **kwargs})


def deleted(gateway):
    return [r for r in gateway.requests if r.method == 'DELETE']


def test_registry_and_schema():
    registered = tools()
    assert set(registered) == {'list_devices', 'run_on_device', 'send_file_to_device',
                               'get_file_from_device', 'capture_screenshot'}
    assert set(registered) <= set(settings.TOOLS_SETTINGS['plugins'])
    assert not set(registered) & set(settings.TOOLS_SETTINGS['content'])
    for name, tool in registered.items():
        assert name in settings.TOOL_DESCRIPTIONS
        assert 'user_id' not in tool.args
        assert 'transfer_token' not in tool.args
        assert 'url' not in tool.args
    assert '10 MiB' in registered['send_file_to_device'].description
    assert 'FULL mode' in registered['capture_screenshot'].description
    assert 'PRIVACY' in registered['capture_screenshot'].description


@pytest.mark.asyncio
async def test_send_local_protocol(gateway, env):
    result = await invoke('send_file_to_device', destination_path='output/report.txt', local_path=str(env))
    assert result['ok'], result
    assert gateway.uploaded == DATA
    assert result['sha256'] == metadata()['sha256']
    assert [(r.method, r.url.path) for r in gateway.requests] == [
        ('POST', f'/internal/devices/{DEVICE}/transfers'),
        ('PUT', f'/internal/device-transfers/{TICKET}'),
        ('POST', f'/internal/devices/{DEVICE}/commands'),
        ('DELETE', f'/internal/device-transfers/{TICKET}')]
    create = json.loads(gateway.requests[0].content)
    assert create == {'direction': 'to_device', 'size': len(DATA), 'sha256': metadata()['sha256']}
    command = json.loads(gateway.requests[2].content)
    assert command == {'user_id': 73, 'operation': 'file_download', 'path': 'output/report.txt',
                       'transfer_id': TICKET, 'transfer_token': CREDENTIAL,
                       'expected_size': len(DATA), 'sha256': metadata()['sha256'], 'timeout': 60}
    assert CREDENTIAL not in str(result) and SECRET not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('name,args,operation,filename,mime', [
    ('get_file_from_device', {'path': r'C:\private\..\report.txt'}, 'file_upload', 'report.txt', 'text/plain'),
    ('capture_screenshot', {}, 'screenshot', 'screenshot.png', 'image/png'),
])
async def test_download_protocol_and_hint(gateway, name, args, operation, filename, mime):
    result = await invoke(name, **args)
    assert result['ok'], result
    target = Path(result['path'])
    assert target.parent == bridge.DOWNLOAD_DIR / '73'
    assert target.name.endswith('_' + filename)
    assert target.read_bytes() == DATA
    assert result['filename'] == filename
    assert result['mime'] == mime
    assert 'send_file_to_user' in result['hint'] and 'analyze_image' in result['hint']
    assert 'saved_files' not in result  # no implicit delivery of private desktop data
    assert CREDENTIAL not in str(result) and SECRET not in str(result)
    create = json.loads(gateway.requests[0].content)
    assert create == {'direction': 'from_device'}
    command = json.loads(gateway.requests[1].content)
    assert command['operation'] == operation
    assert ('path' in command) == (operation != 'screenshot')
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('identifier', ['../other', 'x?user_id=9', 'not-a-uuid', DEVICE.upper() + '/commands'])
@pytest.mark.parametrize('name', ['send_file_to_device', 'get_file_from_device', 'capture_screenshot', 'run_on_device'])
async def test_invalid_device_before_http(gateway, env, identifier, name):
    args = {'device_id': identifier}
    if name == 'send_file_to_device':
        args.update(destination_path='file', local_path=str(env))
    elif name == 'get_file_from_device':
        args['path'] = 'file'
    result = await tools()[name].ainvoke(args)
    assert 'UUID' in str(result)
    assert not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('owner,enabled', [(None, True), (0, True), ('not-an-owner', True), (73, False)])
async def test_unavailable_owner_no_source_or_http(gateway, owner, enabled):
    for tool in bridge.make_device_tools(owner, enabled=enabled):
        args = {} if tool.name == 'list_devices' else {'device_id': DEVICE}
        if tool.name == 'send_file_to_device':
            args.update(destination_path='file', image_key=IMAGE)
        if tool.name == 'get_file_from_device':
            args['path'] = 'file'
        result = await tool.ainvoke(args)
        assert 'веб-чате' in str(result)
    assert not gateway.requests


@pytest.mark.asyncio
async def test_missing_service_secret(gateway, monkeypatch, env):
    monkeypatch.delenv('DEVICE_BRIDGE_SERVICE_TOKEN')
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok']
    assert 'SERVICE_TOKEN' in result['error']
    assert not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [301, 307, 401, 403, 404, 409, 413, 422, 429, 500, 503, 504])
async def test_command_errors_sanitized_and_cleaned(gateway, status, caplog):
    gateway.overrides['POST', f'/internal/devices/{DEVICE}/commands'] = httpx.Response(
        status, headers={'Location': 'http://evil.test'}, json={'detail': SECRET + CREDENTIAL})
    result = await invoke('capture_screenshot')
    assert not result['ok'] and str(status) in result['error']
    assert len(deleted(gateway)) == 1
    assert SECRET not in str(result) + caplog.text
    assert CREDENTIAL not in str(result) + caplog.text
    assert len(gateway.requests) == 3  # no redirect/GET after failed command


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [httpx.ReadTimeout('secret ' + SECRET), httpx.ConnectError(CREDENTIAL)])
async def test_network_errors_cleanup_no_leak(gateway, error):
    gateway.overrides['POST', f'/internal/devices/{DEVICE}/commands'] = error
    result = await invoke('capture_screenshot')
    assert not result['ok'] and SECRET not in str(result) and CREDENTIAL not in str(result)
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
async def test_create_error_no_ticket_no_cleanup(gateway):
    gateway.overrides['POST', f'/internal/devices/{DEVICE}/transfers'] = httpx.Response(404)
    result = await invoke('capture_screenshot')
    assert not result['ok'] and not deleted(gateway)


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_discard_success(gateway):
    gateway.overrides['DELETE', f'/internal/device-transfers/{TICKET}'] = httpx.Response(503)
    result = await invoke('capture_screenshot')
    assert result['ok']
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [
    {'status': 'error', 'exit_code': 1, 'output': SECRET + CREDENTIAL},
    {'status': 'timeout', 'exit_code': None},
    {'status': 'ok', 'exit_code': 0, 'output': 'not json ' + SECRET},
    {'status': 'ok', 'exit_code': 0, 'output': json.dumps({'size': True, 'sha256': '0' * 64})},
    {'status': 'ok', 'exit_code': 0, 'output': json.dumps({'size': -1, 'sha256': '0' * 64})},
    {'status': 'ok', 'exit_code': 0, 'output': json.dumps({'size': 10 * 1024 * 1024 + 1, 'sha256': '0' * 64})},
    {'status': 'ok', 'exit_code': 0, 'output': json.dumps({'size': 1, 'sha256': 'bad hash'})},
])
async def test_bad_device_result(gateway, bad):
    gateway.command_result = bad
    result = await invoke('capture_screenshot')
    assert not result['ok'] and SECRET not in str(result) and CREDENTIAL not in str(result)
    assert len(deleted(gateway)) == 1
    assert len(list(bridge.DOWNLOAD_DIR.glob('73/*'))) == 1  # only fixture source


@pytest.mark.asyncio
@pytest.mark.parametrize(('device_output', 'expected'), [
    ('Screenshot requires full mode (private desktop capture)', 'нужен Full mode'),
    ('Screenshot unavailable: install Pillow and enable a graphical desktop/capture permission', 'нет доступного GUI/Pillow'),
    ('Screenshot capture failed: no GUI or OS screen recording permission', 'отклонён Windows'),
    ('Path is inside a protected folder', 'защищённая папка'),
    ('Path is outside the workspace folder', 'вне рабочей папки'),
])
async def test_known_device_policy_errors_are_explained_without_raw_output(gateway, device_output, expected):
    gateway.command_result = {'status': 'error', 'exit_code': 1, 'output': device_output}
    result = await invoke('capture_screenshot')
    assert not result['ok'] and expected in result['error']
    assert device_output not in result['error']
    assert SECRET not in str(result) and CREDENTIAL not in str(result)
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
async def test_unknown_device_error_stays_generic(gateway):
    gateway.command_result = {'status': 'error', 'exit_code': 1, 'output': 'secret=' + SECRET}
    result = await invoke('capture_screenshot')
    assert not result['ok']
    assert 'Устройство отклонило файловую операцию' in result['error']
    assert SECRET not in str(result) and CREDENTIAL not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('size', len(DATA) + 1), ('sha256', '0' * 64)])
@pytest.mark.parametrize('direction', ['send', 'get'])
async def test_integrity_metadata(gateway, env, field, value, direction):
    meta = metadata()
    meta[field] = value
    gateway.command_result = {'status': 'ok', 'exit_code': 0, 'output': json.dumps(meta)}
    if direction == 'send':
        result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    else:
        result = await invoke('get_file_from_device', path='file')
    assert not result['ok'] and 'integrity' in result['error']
    assert len(deleted(gateway)) == 1
    assert len(list(bridge.DOWNLOAD_DIR.glob('73/*'))) == 1  # only fixture source


@pytest.mark.asyncio
async def test_header_integrity_mismatch(gateway):
    gateway.overrides['GET', f'/internal/device-transfers/{TICKET}'] = httpx.Response(
        200, content=DATA, headers={'X-Content-SHA256': '0' * 64})
    result = await invoke('capture_screenshot')
    assert not result['ok'] and 'integrity' in result['error']
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['declared', 'stream', 'short', 'encoding'])
async def test_bounded_downloads(gateway, mode):
    if mode == 'stream':
        stream = Chunks([b'x' * (64 * 1024)] * 180)
        response = httpx.Response(200, stream=stream)
    else:
        stream = Chunks([DATA])
        headers = {'declared': {'Content-Length': str(bridge.MAX_FILE_BYTES + 1)},
                   'short': {'Content-Length': '123'},
                   'encoding': {'Content-Encoding': 'gzip'}}[mode]
        response = httpx.Response(200, stream=stream, headers=headers)
    gateway.overrides['GET', f'/internal/device-transfers/{TICKET}'] = response
    result = await invoke('capture_screenshot')
    assert not result['ok']
    assert stream.closed
    if mode == 'stream':
        assert stream.read_count < 180
    elif mode in ('declared', 'encoding'):
        assert stream.read_count == 0
    assert len(deleted(gateway)) == 1
    assert len(list(bridge.DOWNLOAD_DIR.glob('73/*'))) == 1  # only fixture source


@pytest.mark.asyncio
async def test_bounded_json(gateway):
    stream = Chunks([b'x' * (64 * 1024)] * 10)
    gateway.overrides['POST', f'/internal/devices/{DEVICE}/commands'] = httpx.Response(200, stream=stream)
    result = await invoke('capture_screenshot')
    assert not result['ok'] and 'limit' in result['error']
    assert stream.closed and stream.read_count < 10
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('ticket', [
    {'transfer_id': TICKET, 'transfer_token': 'bad', 'max_bytes': bridge.MAX_FILE_BYTES},
    {'transfer_id': TICKET, 'transfer_token': CREDENTIAL, 'max_bytes': bridge.MAX_FILE_BYTES + 1},
    {'transfer_id': TICKET, 'transfer_token': CREDENTIAL, 'max_bytes': True},
])
async def test_malformed_ticket_cleanup(gateway, ticket):
    gateway.overrides['POST', f'/internal/devices/{DEVICE}/transfers'] = httpx.Response(200, json=ticket)
    result = await invoke('capture_screenshot')
    assert not result['ok']
    assert len(deleted(gateway)) == 1


@pytest.mark.asyncio
async def test_malicious_ticket_id_never_interpolated(gateway):
    gateway.overrides['POST', f'/internal/devices/{DEVICE}/transfers'] = httpx.Response(
        200, json={'transfer_id': '../devices', 'transfer_token': CREDENTIAL, 'max_bytes': 10})
    result = await invoke('capture_screenshot')
    assert not result['ok'] and len(gateway.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('source', [{}, {'local_path': 'https://evil.test/secret'},
    {'local_path': '/etc/passwd'}, {'local_path': '/sandbox/secret'},
    {'workspace_id': 'abc'}, {'workspace_path': 'file'},
    {'workspace_id': 'abc', 'workspace_path': '../secret'},
    {'workspace_id': 'abc?user_id=1', 'workspace_path': 'file'},
    {'workspace_id': 'abc', 'workspace_path': '/etc/passwd'},
    {'workspace_id': 'abc', 'workspace_path': r'..\file'},
    {'image_key': '../file'}, {'local_path': '/tmp/file', 'image_key': IMAGE}])
async def test_bad_sources_no_gateway(gateway, source):
    result = await invoke('send_file_to_device', destination_path='file', **source)
    assert not result['ok']
    assert not gateway.requests


@pytest.mark.asyncio
async def test_local_symlink_escape_directory_and_size(gateway, env, tmp_path):
    outside = tmp_path / 'secret'
    outside.write_bytes(b'secret')
    link = env.parent / 'link'
    link.symlink_to(outside)
    large = env.parent / 'large'
    with large.open('wb') as f:
        f.truncate(bridge.MAX_FILE_BYTES + 1)
    fifo = env.parent / 'fifo'
    import os
    os.mkfifo(fifo)
    for path in (link, env.parent, large, fifo):
        result = await invoke('send_file_to_device', destination_path='file', local_path=str(path))
        assert not result['ok'], path
    assert not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('source', [
    {'workspace_id': 'abc123', 'workspace_path': 'output/a b.txt'},
    {'local_path': '/tmp/exec/workspaces/abc123/output/a b.txt'},
])
async def test_workspace_bridge(gateway, source):
    result = await invoke('send_file_to_device', destination_path='file', **source)
    assert result['ok'], result
    assert gateway.uploaded == DATA
    owns, request = gateway.requests[:2]
    assert owns.url.host == 'chats.test'
    assert dict(owns.url.params) == {'user_id': '73', 'workspace_id': 'abc123'}
    assert request.url.host == 'factory.test'
    assert request.url.path == '/workspace/abc123/file/output/a b.txt'
    assert 'authorization' not in request.headers  # service token never sent to factory
    assert '%20' in str(request.url)


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [httpx.Response(302, headers={'Location': 'http://evil.test'}),
    httpx.Response(200, headers={'Content-Length': str(bridge.MAX_FILE_BYTES + 1)}, stream=Chunks([]))])
async def test_workspace_redirect_size(gateway, response):
    gateway.overrides['factory'] = response
    result = await invoke('send_file_to_device', destination_path='file',
                          workspace_id='abc123', workspace_path='file')
    assert not result['ok']
    assert [r.url.host for r in gateway.requests] == ['chats.test', 'factory.test']


@pytest.mark.asyncio
async def test_generated_image_owner_scoped(gateway):
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={'image_data': DATA}))
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    agent = SimpleNamespace(db=SimpleNamespace(pool=pool))
    result = await tools(agent=agent)['send_file_to_device'].ainvoke(
        {'device_id': DEVICE, 'destination_path': 'image.png', 'image_key': IMAGE})
    assert result['ok'], result
    sql, key, owner, limit = conn.fetchrow.call_args.args
    assert 'user_id = $2' in sql and 'octet_length(image_data) <= $3' in sql
    assert key == IMAGE and owner == 73 and limit == bridge.MAX_FILE_BYTES
    conn.fetchrow.return_value = None
    result = await tools(agent=agent)['send_file_to_device'].ainvoke(
        {'device_id': DEVICE, 'destination_path': 'image.png', 'image_key': IMAGE})
    assert not result['ok'] and 'this user' in result['error']


@pytest.mark.asyncio
async def test_upload_integrity_failure_stops_command(gateway, env):
    gateway.overrides['PUT', f'/internal/device-transfers/{TICKET}'] = httpx.Response(
        200, json={'size': 0, 'sha256': '0' * 64})
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok'] and len(deleted(gateway)) == 1
    assert not any(r.url.path.endswith('/commands') for r in gateway.requests)


@pytest.mark.asyncio
async def test_gateway_lower_limit(gateway, env):
    gateway.limit = 1
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok'] and len(deleted(gateway)) == 1
    assert gateway.uploaded is None


@pytest.mark.asyncio
async def test_empty_file_allowed(gateway, env):
    env = Path(bridge._save_download(b'', 'empty.txt', 73)['path'])
    gateway.command_result = {'status': 'ok', 'exit_code': 0, 'output': json.dumps(metadata(b''))}
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert result['ok'] and result['size'] == 0


@pytest.mark.parametrize('name', ['../../etc/passwd', r'C:\Windows\x.png', '../' + 'x' * 1000 + '.png',
                                  '\x00\n<>"secret', '.', '..', '/'])
def test_safe_download_filename(name, env):
    result = bridge._save_download(DATA, name, 73)
    target = Path(result['path'])
    assert target.parent == bridge.DOWNLOAD_DIR / '73'
    assert len(target.name) < 170
    assert '/' not in result['filename'] and '\\' not in result['filename']
    assert target.read_bytes() == DATA


@pytest.mark.asyncio
@pytest.mark.parametrize('file_owner,sender', [(1, 56), (56, 1), (73, 1)])
async def test_local_cross_owner_denied(gateway, file_owner, sender):
    saved = bridge._save_download(DATA, 'private.txt', file_owner)
    tool = {t.name: t for t in bridge.make_device_tools(sender)}['send_file_to_device']
    result = await tool.ainvoke({'device_id': DEVICE, 'destination_path': 'file',
                                'local_path': saved['path']})
    assert not result['ok'] and 'this user' in result['error']
    assert not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', [1, 56, 73])
async def test_arbitrary_shared_local_denied_even_admin(gateway, tmp_path, owner):
    shared = tmp_path / 'generated' / 'shared.txt'
    shared.write_bytes(DATA)
    # An owner-named directory alone is not trusted either.
    unregistered = bridge.DOWNLOAD_DIR / str(owner) / 'unregistered.txt'
    unregistered.parent.mkdir(exist_ok=True)
    unregistered.write_bytes(DATA)
    for path in (shared, unregistered):
        with pytest.raises(bridge._FileError, match='workspace_id.*workspace_path.*image_key'):
            await bridge._source_bytes(str(path), None, None, None, None, owner)
    assert not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'query', 'symlink', 'parent_symlink'])
async def test_registry_failure_denied(gateway, env, tmp_path, failure):
    registry = bridge.DOWNLOAD_DIR / '.owners.sqlite'
    if failure == 'missing':
        registry.unlink()
    elif failure == 'corrupt':
        registry.write_bytes(b'not sqlite')
    elif failure == 'query':
        with sqlite3.connect(registry) as conn:
            conn.execute('DROP TABLE downloads')
    elif failure == 'symlink':
        moved = tmp_path / 'other.sqlite'
        registry.rename(moved)
        registry.symlink_to(moved)
    else:
        moved = tmp_path / 'real-downloads'
        bridge.DOWNLOAD_DIR.rename(moved)
        bridge.DOWNLOAD_DIR.symlink_to(moved, target_is_directory=True)
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok']
    assert not gateway.requests


@pytest.mark.asyncio
async def test_tampered_local_hash_denied(gateway, env):
    env.write_bytes(b'x' * len(DATA))
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok'] and 'unchanged contents' in result['error']
    assert not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['file', 'directory'])
async def test_local_symlink_inside_trusted_tree_denied(gateway, env, kind):
    if kind == 'file':
        target = env.with_name('alias.txt')
        target.symlink_to(env)
    else:
        alias = bridge.DOWNLOAD_DIR / 'alias'
        alias.symlink_to(env.parent, target_is_directory=True)
        target = alias / env.name
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(target))
    assert not result['ok'] and not gateway.requests


@pytest.mark.asyncio
@pytest.mark.parametrize('source', [
    {'workspace_id': 'abc123', 'workspace_path': 'output/file'},
    {'local_path': '/tmp/exec/workspaces/abc123/output/file'},
])
@pytest.mark.parametrize('response', [
    httpx.Response(200, json={}), httpx.Response(200, json={'owns': False}),
    httpx.Response(200, json={'owns': 1}), httpx.Response(200, json={'owns': 'true'}),
    httpx.Response(200, json=[]), httpx.Response(200, content=b'bad JSON'),
    httpx.Response(500, json={'owns': True}),
    httpx.Response(302, headers={'Location': 'http://factory.test'}),
    httpx.ConnectError(SECRET), httpx.ReadTimeout(SECRET),
])
async def test_workspace_ownership_fail_closed(gateway, source, response):
    gateway.overrides['owns'] = response
    result = await invoke('send_file_to_device', destination_path='file', **source)
    assert not result['ok'] and 'ownership' in result['error']
    assert SECRET not in str(result)
    assert len(gateway.requests) == 1
    assert gateway.requests[0].url.host == 'chats.test'  # no sandbox or gateway fetch
    assert dict(gateway.requests[0].url.params) == {'user_id': '73', 'workspace_id': 'abc123'}


@pytest.mark.asyncio
@pytest.mark.parametrize('port', [None, '8765'])
async def test_workspace_default_chats_service_url(gateway, monkeypatch, port):
    monkeypatch.delenv('CHATS_SERVICE_URL')
    if port is None:
        monkeypatch.delenv('PORT_CHATS_SERVICE', raising=False)
    else:
        monkeypatch.setenv('PORT_CHATS_SERVICE', port)
    result = await invoke('send_file_to_device', destination_path='file',
                          workspace_id='abc123', workspace_path='file')
    assert result['ok'], result
    assert gateway.requests[0].url.host == 'chats_service'
    assert gateway.requests[0].url.port == int(port or '8001')


def test_download_registry_durable_private_and_owner_scoped(env):
    registry = bridge.DOWNLOAD_DIR / '.owners.sqlite'
    assert stat_mode(registry) == 0o600
    assert stat_mode(env) == 0o600
    assert stat_mode(env.parent) == 0o700
    with sqlite3.connect(registry) as conn:
        row = conn.execute('SELECT path, owner, sha256 FROM downloads').fetchone()
    assert row == (str(env.resolve()), 73, hashlib.sha256(DATA).hexdigest())
    # A fresh connection on every read; no process-local ownership cache.
    assert bridge._local_source(str(env), 73) == DATA


def stat_mode(path):
    return os.stat(path).st_mode & 0o777


@pytest.mark.parametrize('kind', ['registry', 'root', 'owner'])
def test_save_download_refuses_symlinks(env, tmp_path, kind):
    target = {'registry': bridge.DOWNLOAD_DIR / '.owners.sqlite',
              'root': bridge.DOWNLOAD_DIR, 'owner': env.parent}[kind]
    moved = tmp_path / 'moved'
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=kind != 'registry')
    with pytest.raises((bridge._FileError, OSError)):
        bridge._save_download(DATA, 'new.txt', 73)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['oversized', 'fifo', 'directory'])
async def test_registered_source_requires_bounded_regular_file(gateway, env, kind):
    if kind == 'oversized':
        with env.open('wb') as stream:
            stream.truncate(bridge.MAX_FILE_BYTES + 1)
    elif kind == 'fifo':
        env.unlink()
        os.mkfifo(env)
    else:
        env.unlink()
        env.mkdir()
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok']
    assert ('10 MiB' if kind == 'oversized' else 'regular file') in result['error']
    assert not gateway.requests


@pytest.mark.asyncio
async def test_trusted_download_round_trip(gateway):
    downloaded = await invoke('get_file_from_device', path='private.txt')
    assert downloaded['ok'], downloaded
    assert bridge._local_source(downloaded['path'], 73) == DATA
    result = await invoke('send_file_to_device', destination_path='copy.txt',
                          local_path=downloaded['path'])
    assert result['ok'], result
    assert gateway.uploaded == DATA


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', [1, 56])
async def test_workspace_check_uses_bound_owner(gateway, owner):
    assert await bridge._source_bytes(None, 'abc123', 'file', None, None, owner) == DATA
    assert dict(gateway.requests[0].url.params) == {'user_id': str(owner), 'workspace_id': 'abc123'}
    assert [r.url.host for r in gateway.requests] == ['chats.test', 'factory.test']


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
def test_registry_sidecar_symlink_denied(env, tmp_path, suffix):
    outside = tmp_path / 'outside'
    outside.write_bytes(b'private')
    sidecar = Path(str(bridge.DOWNLOAD_DIR / '.owners.sqlite') + suffix)
    sidecar.symlink_to(outside)
    with pytest.raises(bridge._FileError, match='registry is unavailable'):
        bridge._local_source(str(env), 73)
    assert outside.read_bytes() == b'private'


def test_failed_registration_removes_new_download(env, monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError('unavailable')
    monkeypatch.setattr(bridge.sqlite3, 'connect', broken)
    before = set(env.parent.iterdir())
    with pytest.raises(bridge._FileError, match='registry is unavailable'):
        bridge._save_download(DATA, 'new.txt', 73)
    assert set(env.parent.iterdir()) == before


@pytest.mark.asyncio
async def test_registry_owner_checked_not_only_directory(gateway, env):
    with sqlite3.connect(bridge.DOWNLOAD_DIR / '.owners.sqlite') as conn:
        conn.execute('UPDATE downloads SET owner = 56 WHERE path = ?', (str(env),))
    result = await invoke('send_file_to_device', destination_path='file', local_path=str(env))
    assert not result['ok'] and 'this user' in result['error']
    assert not gateway.requests
