import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import json
import threading
import uuid
from unittest.mock import Mock

import pytest
import websocket

from device_client import core

SESSION = str(uuid.UUID(int=1))
DEVICE = str(uuid.UUID(int=2))
JOB = str(uuid.UUID(int=3))


def client(events=None):
    return core.BridgeClient('http://127.0.0.1', 't' * 64,
                             None if events is None else lambda *event: events.append(event))


def hello(**updates):
    return dict(type='hello', session_id=SESSION, device_id=DEVICE,
                heartbeat_interval=5, **updates)


def command(**updates):
    result = dict(type='command', job_id=JOB, session_id=SESSION,
                  deadline=2000, timeout=3, operation='ping')
    result.update(updates)
    return result


class EndSession(Exception):
    pass


class Frames:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []
        self.closed = False

    def recv(self):
        try:
            frame = next(self.frames)
        except StopIteration:
            raise EndSession from None
        if isinstance(frame, Exception):
            raise frame
        return json.dumps(frame) if isinstance(frame, (dict, list)) else frame

    def send(self, frame):
        self.sent.append(json.loads(frame))

    def close(self, **kwargs):
        self.closed = True

    def getstatus(self):
        return 101

    def settimeout(self, timeout):
        self.timeout = timeout


@pytest.mark.parametrize(('url', 'expected'), [
    ('https://example.test', 'wss://example.test/api/devices/connect'),
    ('wss://example.test:443/', 'wss://example.test:443/api/devices/connect'),
    (' https://example.test/api/devices/connect ', 'wss://example.test/api/devices/connect'),
    ('http://localhost:8080', 'ws://localhost:8080/api/devices/connect'),
    ('ws://127.0.0.1', 'ws://127.0.0.1/api/devices/connect'),
    ('http://[::1]:8000', 'ws://[::1]:8000/api/devices/connect'),
])
def test_url_normalization(url, expected):
    assert core.normalize_url(url) == expected


@pytest.mark.parametrize('url', [
    '', 'example.test', 'ftp://example.test', 'http://example.test',
    'ws://192.168.1.2', 'ws://localhost.evil', 'https://u:p@example.test',
    'https://example.test?token=bad', 'https://example.test#fragment',
    'https://example.test/other', 'https://example.test:0',
    'https://example.test:65536', 'https://example.test:no',
    'https://[broken', None,
])
def test_url_rejected(url):
    with pytest.raises(ValueError):
        core.normalize_url(url)


@pytest.mark.parametrize('case', [
    'short', 'oversize', 'non_string', 'internal_lf', 'tab', 'unicode',
    'leading_lf', 'trailing_lf', 'crlf',
])
def test_invalid_token_rejected(case):
    tokens = {'short': 'x' * 15, 'oversize': 'x' * 4097, 'non_string': None,
              'internal_lf': 'x' * 32 + '\n' + 'x' * 32,
              'leading_lf': '\n' + 'x' * 64, 'trailing_lf': 'x' * 64 + '\n',
              'crlf': 'x' * 64 + '\r\n', 'tab': 'x' * 32 + '\t' + 'x' * 32,
              'unicode': 'я' * 64}
    with pytest.raises(ValueError):
        core.BridgeClient('https://example.test', tokens[case])


@pytest.mark.parametrize('value', [None, '', 1, 'not-uuid', str(uuid.UUID(int=10)).upper()])
def test_invalid_ids(value):
    assert not core.valid_id(value)


def test_hello_ping_command_duplicates_and_expiry(monkeypatch):
    monkeypatch.setattr(core.time, 'time', lambda: 1000)
    events = []
    ws = Frames([hello(), {'type': 'ping', 'nonce': 'abc'}, command(), command(),
                 command(job_id=str(uuid.UUID(int=4)), deadline=999),
                 {'type': 'error', 'error': 'result_not_pending'}])
    with pytest.raises(EndSession):
        client(events)._session(ws)
    assert ws.sent == [
        {'type': 'pong', 'nonce': 'abc'},
        {'type': 'result', 'job_id': JOB, 'session_id': SESSION,
         'status': 'ok', 'output': 'pong', 'exit_code': 0},
        {'type': 'result', 'job_id': str(uuid.UUID(int=4)), 'session_id': SESSION,
         'status': 'error', 'output': 'Job expired', 'exit_code': 1},
    ]
    assert events == [('status', 'online'), ('log', 'Ping answered'),
                      ('log', 'Job rejected'), ('log', 'Gateway no longer waits for this result')]


@pytest.mark.parametrize('operation', ['shell', 'exec', 'ls', 'ping; touch forbidden', '', None, {'cmd': 'sh'}])
def test_unsupported_operation_never_executes(monkeypatch, operation):
    import subprocess
    forbidden = Mock(side_effect=AssertionError('Execution is forbidden'))
    monkeypatch.setattr(os, 'system', forbidden)
    monkeypatch.setattr(os, 'popen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(core.time, 'time', lambda: 1000)
    ws = Frames([hello(), command(operation=operation, arguments=['sh', '-c', 'echo unsafe'])])
    with pytest.raises(EndSession):
        client()._session(ws)
    assert ws.sent[0]['status'] == 'error'
    assert ws.sent[0]['output'] == 'Unsupported operation'
    forbidden.assert_not_called()


@pytest.mark.parametrize('frame', [
    b'{}', 'not json', '[]', 'null', '1', '{', '[' * 2000,
    'x' * (core.MAX_FRAME + 1), 'я' * (core.MAX_FRAME // 2 + 1),
    {}, {'type': 'ping', 'nonce': 'before-hello'},
    {'type': 'hello', 'session_id': SESSION, 'device_id': DEVICE, 'heartbeat_interval': 6},
    {'type': 'hello', 'session_id': 'bad', 'device_id': DEVICE, 'heartbeat_interval': 5},
    {'type': 'hello', 'session_id': SESSION, 'device_id': 'bad', 'heartbeat_interval': 5},
], ids=lambda value: None if isinstance(value, dict) else type(value).__name__)
def test_malformed_initial_frame(frame):
    with pytest.raises(core.ProtocolError):
        client()._session(Frames([frame]))


@pytest.mark.parametrize('frame', [
    hello(), {'type': 'unknown'}, {'type': 'error', 'error': 'arbitrary'},
    {'type': 'ping'}, {'type': 'ping', 'nonce': ''},
    {'type': 'ping', 'nonce': 123}, {'type': 'ping', 'nonce': 'x' * 257},
    command(session_id=str(uuid.UUID(int=9))), command(job_id='bad'),
    command(deadline=True), command(deadline=None), command(deadline='2000'),
    command(deadline=float('nan')), command(deadline=float('inf')),
    command(timeout=True), command(timeout=0), command(timeout=-1),
    command(timeout=31), command(timeout=float('nan')), command(timeout=float('inf')),
])
def test_strict_session_frames(frame):
    with pytest.raises(core.ProtocolError):
        client()._session(Frames([hello(), frame]))


def test_duplicate_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(core.time, 'time', lambda: 1000)
    ws = Frames([hello()] + [command(job_id=str(uuid.UUID(int=n + 10))) for n in range(10001)])
    with pytest.raises(core.ProtocolError):
        client()._session(ws)
    assert len(ws.sent) == 10000


@pytest.mark.parametrize(('frames', 'clock'), [
    ([websocket.WebSocketTimeoutException()], [0, 11]),
    ([hello(), websocket.WebSocketTimeoutException()], [0, 1, 22]),
])
def test_inactivity_deadlines(monkeypatch, frames, clock):
    values = iter(clock)
    monkeypatch.setattr(core.time, 'monotonic', lambda: next(values))
    with pytest.raises(TimeoutError):
        client()._session(Frames(frames))


def test_receive_timeout_does_not_immediately_disconnect(monkeypatch):
    monkeypatch.setattr(core.time, 'monotonic', lambda: 0)
    ws = Frames([websocket.WebSocketTimeoutException(), hello(),
                 websocket.WebSocketTimeoutException(), {'type': 'ping', 'nonce': 'alive'}])
    with pytest.raises(EndSession):
        client()._session(ws)
    assert ws.sent == [{'type': 'pong', 'nonce': 'alive'}]


class BackoffStop:
    def __init__(self, limit):
        self.delays = []
        self.limit = limit

    def is_set(self):
        return False

    def wait(self, delay):
        self.delays.append(delay)
        return len(self.delays) >= self.limit


def test_reconnect_exponential_backoff_and_no_secret_logging(monkeypatch, capsys, caplog):
    events = []
    bridge = client(events)
    stop = BackoffStop(8)
    bridge._stop = stop
    monkeypatch.setattr(core.random, 'uniform', lambda *_: 0)
    connect = Mock(side_effect=OSError(bridge._token))
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    assert stop.delays == [1, 2, 4, 8, 16, 30, 30, 30]
    assert connect.call_count == 8
    assert events[-1] == ('status', 'stopped')
    observed = repr(events) + capsys.readouterr().out + capsys.readouterr().err + caplog.text
    assert bridge._token not in observed


@pytest.mark.parametrize('status', [401, 403, 301, 302, 303, 307, 308, 400, 404, 405, 426])
def test_auth_and_route_errors_are_terminal(monkeypatch, status):
    events = []
    bridge = client(events)
    connect = Mock(side_effect=websocket.WebSocketBadStatusException('redacted', status_code=status))
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    connect.assert_called_once()
    assert (('status', 'unauthorized') in events) == (status in (401, 403))
    assert events[-1] == ('status', 'stopped')


def test_connect_security_options_and_protocol_error_is_terminal(monkeypatch):
    events = []
    bridge = client(events)
    ws = Frames(['{"type":"unknown","message":"' + bridge._token + '"}'])
    connect = Mock(return_value=ws)
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    args, kwargs = connect.call_args
    assert args == (bridge.url,)
    assert kwargs == dict(header={'Authorization': 'Bearer ' + bridge._token,
                                  'User-Agent': core.USER_AGENT}, timeout=5,
                          suppress_origin=True, redirect_limit=0, enable_multithread=True)
    assert ws.closed
    assert ws.timeout == 1
    assert bridge._token not in repr(events)
    assert ('error', 'Protocol mismatch: connection stopped') in events
    assert connect.call_count == 1


def test_stop_interrupts_blocking_recv(monkeypatch):
    entered, released = threading.Event(), threading.Event()
    bridge = client()

    class BlockingSocket(Frames):
        def recv(self):
            entered.set()
            if not released.wait(3):
                raise AssertionError('Stop failed to interrupt recv')
            raise websocket.WebSocketConnectionClosedException()

        def shutdown(self):
            released.set()

    ws = BlockingSocket([])
    monkeypatch.setattr(core.websocket, 'create_connection', lambda *a, **kw: ws)
    thread = threading.Thread(target=bridge.run)
    thread.start()
    try:
        assert entered.wait(1)
        bridge.stop()
        thread.join(1)
        assert not thread.is_alive()
        assert released.is_set() and ws.closed
    finally:
        released.set()
        bridge.stop()
        thread.join(3)


def test_stop_interrupts_backoff(monkeypatch):
    reconnecting = threading.Event()
    bridge = core.BridgeClient('http://127.0.0.1', 't' * 64,
                              lambda kind, msg: reconnecting.set() if msg == 'reconnecting' else None)
    monkeypatch.setattr(core.websocket, 'create_connection', Mock(side_effect=OSError()))
    thread = threading.Thread(target=bridge.run)
    thread.start()
    try:
        assert reconnecting.wait(1)
        bridge.stop()
        thread.join(0.5)
        assert not thread.is_alive()
    finally:
        bridge.stop()
        thread.join(2)


def test_backoff_jitter_bounds_and_reset_after_stable_session(monkeypatch):
    bridge = client()
    stop = BackoffStop(4)
    bridge._stop = stop
    clock = iter([0, 0, 1, 1, 2, 33, 34, 34])
    monkeypatch.setattr(core.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(core.random, 'uniform', lambda low, high: high)
    monkeypatch.setattr(core.websocket, 'create_connection', Mock(side_effect=OSError()))
    bridge.run()
    assert stop.delays == pytest.approx([1.2, 2.4, 1.2, 2.4])


@pytest.mark.parametrize('status', [429, 500, 502, 503])
def test_transient_http_failures_retry_without_leaking_error(monkeypatch, status):
    events = []
    bridge = client(events)
    bridge._stop = BackoffStop(2)
    connect = Mock(side_effect=websocket.WebSocketBadStatusException(
        bridge._token, status_code=status))
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    assert connect.call_count == 2
    assert ('log', 'Gateway temporarily unavailable') in events
    assert bridge._token not in repr(events)


def test_closed_frame_is_transport_failure():
    with pytest.raises(websocket.WebSocketConnectionClosedException):
        client()._session(Frames(['']))


def test_stopped_client_never_connects(monkeypatch):
    bridge = client()
    connect = Mock()
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.stop()
    bridge.run()
    connect.assert_not_called()


def test_stop_during_connect_closes_transport(monkeypatch):
    bridge = client()
    ws = Frames([])

    def connect(*args, **kwargs):
        bridge.stop()
        return ws

    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    assert ws.closed
    assert bridge._socket is None


def test_stop_handles_shutdown_error():
    bridge = client()
    bridge._socket = Mock()
    bridge._socket.shutdown.side_effect = OSError('redacted')
    bridge.stop()
    assert bridge._stop.is_set()


def test_send_failure_reconnects_and_closes_old_socket(monkeypatch):
    events = []
    bridge = client(events)
    bridge._stop = BackoffStop(2)
    first = Frames([hello(), {'type': 'ping', 'nonce': 'alive'}])
    first.send = Mock(side_effect=OSError(bridge._token))
    second = Frames([hello(), {'type': 'unknown'}])
    connect = Mock(side_effect=[first, second])
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    assert first.closed and second.closed
    assert connect.call_count == 2
    assert events.count(('status', 'online')) == 2
    assert ('status', 'reconnecting') in events
    assert bridge._token not in repr(events)


def test_redirect_socket_is_terminal(monkeypatch):
    events = []
    bridge = client(events)
    ws = Frames([])
    ws.getstatus = lambda: 302
    connect = Mock(return_value=ws)
    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    bridge.run()
    assert connect.call_count == 1
    assert ws.closed
    assert ('status', 'online') not in events
    assert ('status', 'reconnecting') not in events
    assert any('redirect' in text for kind, text in events if kind == 'error')
    assert bridge._token not in repr(events)


def test_real_http_redirect_does_not_forward_token():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/must-not-follow')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    try:
        bridge = core.BridgeClient(f'http://127.0.0.1:{server.server_port}', 't' * 64,
                              lambda kind, msg: events.append((kind, msg)))
        bridge.run()
        assert seen == ['/api/devices/connect']
        assert ('status', 'online') not in events
        assert ('status', 'reconnecting') not in events
        assert 't' * 64 not in repr(events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize('exc,fragment', [
    (core.ssl.SSLCertVerificationError('sensitive-token'), 'TLS certificate'),
    (core.ssl.SSLError('sensitive-token'), 'TLS handshake'),
    (core.socket.gaierror('sensitive-token'), 'DNS'),
    (ConnectionRefusedError('sensitive-token'), 'TCP'),
    (websocket.WebSocketProxyException('sensitive-token'), 'Proxy'),
    (websocket.WebSocketTimeoutException('sensitive-token'), 'timed out'),
    (TimeoutError('sensitive-token'), 'timed out'),
    (websocket.WebSocketConnectionClosedException('sensitive-token'), 'Remote peer'),
    (OSError('sensitive-token'), 'Network socket'),
    (RuntimeError('sensitive-token'), 'unclassified'),
])
def test_safe_diagnostic_categories(exc, fragment):
    message = core.connection_diagnostic(exc)
    assert fragment in message
    assert 'sensitive-token' not in message


def test_diagnostic_emitted_by_real_run_loop(monkeypatch):
    events = []
    def emit(kind, msg):
        events.append((kind, msg))
        if kind == 'log':
            bridge.stop()
    bridge = core.BridgeClient('http://127.0.0.1', 't' * 64, emit)
    monkeypatch.setattr(core.websocket, 'create_connection', Mock(side_effect=core.socket.gaierror('secret')))
    bridge.run()
    assert any(kind == 'log' and 'DNS' in msg for kind, msg in events)
    assert 'secret' not in repr(events)


@pytest.mark.parametrize('status', [401, 403, 404, 429, 502])
def test_handshake_http_status_is_visible_without_response_secrets(monkeypatch, status):
    events = []
    bridge = client(events)
    bridge._stop = BackoffStop(1)
    monkeypatch.setattr(core.websocket, 'create_connection', Mock(side_effect=
        websocket.WebSocketBadStatusException('sensitive-token', status_code=status,
            resp_headers={'Authorization': 'sensitive-token'}, resp_body='sensitive-token')))
    bridge.run()
    assert ('log', 'WebSocket handshake HTTP ' + str(status)) in events
    assert 'sensitive-token' not in repr(events)


def test_handshake_identifies_device_client_without_origin_or_redirect(monkeypatch):
    c = client()
    captured = {}

    def connect(url, **kwargs):
        captured.update(url=url, **kwargs)
        c.stop()
        raise websocket.WebSocketConnectionClosedException()

    monkeypatch.setattr(core.websocket, 'create_connection', connect)
    c.run()
    assert captured['header']['User-Agent'] == 'AuGent-Device-Bridge/0.1'
    assert captured['header']['Authorization'] == 'Bearer ' + 't' * 64
    assert captured['suppress_origin'] is True
    assert captured['redirect_limit'] == 0
    assert 'Cookie' not in captured['header']
    assert 't' * 64 not in captured['url']
