"""Outbound Device Bridge client: ping, plain-text notify and opt-in shell exec.

exec runs only when the local config enables it (desktop toggle); the policy
lives in executor.py, never on the server."""
from __future__ import annotations

import json
import math
import random
import socket
import ssl
import threading
import time
import uuid
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import websocket

MAX_FRAME = 512 * 1024
MAX_NOTIFICATION_TEXT = 2000


def valid_notification_text(value) -> bool:
    """Validate raw text, without interpreting or normalizing its contents."""
    return (isinstance(value, str) and 0 < len(value) <= MAX_NOTIFICATION_TEXT
            and bool(value.strip())
            and all(char in '\n\t\r' or unicodedata.category(char) not in ('Cc', 'Cf', 'Cs')
                    for char in value))


USER_AGENT = 'AuGent-Device-Bridge/0.1'


def connection_diagnostic(exc: Exception) -> str:
    """Fixed safe messages only: never echo exceptions, headers or credentials."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return 'TLS certificate verification failed. Check Windows clock, certificate chain and HTTPS inspection; do not disable TLS verification.'
    if isinstance(exc, ssl.SSLError):
        return 'TLS handshake interrupted or failed. Check server TLS and network/HTTPS inspection.'
    if isinstance(exc, websocket.WebSocketProxyException):
        return 'Proxy connection failed. Check HTTP_PROXY/HTTPS_PROXY/ALL_PROXY settings and proxy availability.'
    if isinstance(exc, socket.gaierror):
        return 'DNS lookup failed. Check server hostname and DNS/network settings.'
    if isinstance(exc, ConnectionRefusedError):
        return 'TCP connection refused. Check server port, firewall and proxy.'
    if isinstance(exc, (TimeoutError, websocket.WebSocketTimeoutException)):
        return 'Connection or heartbeat timed out. Check network, proxy and gateway availability.'
    if isinstance(exc, websocket.WebSocketConnectionClosedException):
        return 'Remote peer closed the connection. Check nginx access/error logs, correct domain/location and network proxy.'
    if isinstance(exc, OSError):
        return 'Network socket error. Check network, firewall and proxy settings.'
    return 'Connection failed (unclassified). Check client installation and gateway logs.'


def normalize_url(value: str) -> str:
    try:
        u = urlsplit(value.strip())
        port = u.port
    except (ValueError, AttributeError):
        raise ValueError('Invalid server URL') from None
    if u.username or u.password or u.query or u.fragment or not u.hostname:
        raise ValueError('URL must not include credentials, query or fragment')
    scheme = {'https': 'wss', 'http': 'ws', 'wss': 'wss', 'ws': 'ws'}.get(u.scheme)
    if not scheme or (scheme == 'ws' and u.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Use HTTPS/WSS (plain WS allowed only on loopback for tests)')
    if u.path not in ('', '/', '/api/devices/connect'):
        raise ValueError('Use domain root or /api/devices/connect')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid server port')
    return urlunsplit((scheme, u.netloc, '/api/devices/connect', '', ''))


def valid_id(value) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class ProtocolError(Exception):
    pass


class BridgeClient:
    def __init__(self, server_url: str, token: str, on_event=None, executor=None):
        self.url = normalize_url(server_url)
        if not isinstance(token, str) or not 16 <= len(token.strip()) <= 4096:
            raise ValueError('Enter a device token')
        self._token = token
        if token != token.strip():
            raise ValueError('Token must not contain surrounding whitespace')
        if any(ord(c) < 33 or ord(c) > 126 for c in self._token):
            raise ValueError('Invalid device token')
        self._callback = on_event or (lambda *_: None)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._socket = None
        self._executor = executor
        self._send_lock = threading.Lock()
        self._live = None  # (ws, session) while online

    def capabilities(self):
        caps = ['ping', 'notify']
        if self._executor is not None and self._executor.config.exec_enabled:
            caps.extend(['exec', 'files'])
            if __package__:
                from .transfers import screenshot_available
            else:
                from transfers import screenshot_available
            if self._executor.config.data['mode'] == 'full' and screenshot_available():
                caps.append('screenshot')
        return caps

    def set_exec_enabled(self, value: bool):
        """Persist the toggle and tell the gateway mid-session."""
        if self._executor is None:
            return
        self._executor.config.set_exec_enabled(value)
        live = self._live
        if live:
            try:
                self._send(live[0], {'type': 'capabilities', 'capabilities': self.capabilities()})
            except Exception:
                pass

    def _emit(self, kind, message):
        # Only notification events contain validated raw server text, never log events.
        # Never forward raw exceptions, headers or frames to UI.
        self._callback(kind, message)

    def stop(self):
        self._stop.set()
        with self._lock:
            ws = self._socket
        if ws:
            # shutdown interrupts recv without a potentially blocking close handshake.
            try:
                ws.shutdown()
            except Exception:
                pass

    def _send(self, ws, value):
        data = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
        with self._send_lock:
            ws.send(data)

    def _session(self, ws):
        session = None
        last_frame = time.monotonic()
        seen = set()
        while not self._stop.is_set():
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                if time.monotonic() - last_frame > (20 if session else 10):
                    raise TimeoutError()
                continue
            if raw == '':
                raise websocket.WebSocketConnectionClosedException()
            if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_FRAME:
                raise ProtocolError()
            try:
                msg = json.loads(raw)
            except (ValueError, RecursionError):
                raise ProtocolError() from None
            if not isinstance(msg, dict):
                raise ProtocolError()
            kind = msg.get('type')
            last_frame = time.monotonic()
            if session is None:
                if (kind != 'hello' or not valid_id(msg.get('session_id'))
                        or not valid_id(msg.get('device_id')) or msg.get('heartbeat_interval') != 5
                        or ('protocol' in msg and (type(msg['protocol']) is not int
                                                  or msg['protocol'] != 1))):
                    raise ProtocolError()
                session = msg['session_id']
                self._send(ws, {'type': 'hello', 'protocol': 1,
                                'capabilities': self.capabilities()})
                self._live = (ws, session)
                self._emit('status', 'online')
            elif kind == 'ping':
                nonce = msg.get('nonce')
                if not isinstance(nonce, str) or not 1 <= len(nonce) <= 256:
                    raise ProtocolError()
                self._send(ws, {'type': 'pong', 'nonce': nonce})
            elif kind == 'command':
                job = msg.get('job_id')
                if not valid_id(job) or msg.get('session_id') != session:
                    raise ProtocolError()
                deadline, timeout = msg.get('deadline'), msg.get('timeout')
                if (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                        or not math.isfinite(deadline)):
                    raise ProtocolError()
                if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                        or not 0 < timeout <= (300 if msg.get('operation') in ('exec', 'file_download', 'file_upload', 'screenshot') else 30)):
                    raise ProtocolError()
                if job in seen:
                    continue
                # Bounded duplicate protection; don't evict and re-execute old job IDs.
                if len(seen) >= 10000:
                    raise ProtocolError()
                seen.add(job)
                expired = deadline < time.time()
                operation = msg.get('operation')
                if operation == 'exec' and not expired and self._executor is not None:
                    self._start_exec(ws, session, job, msg)
                    continue
                if operation in ('file_download', 'file_upload', 'screenshot') and not expired and self._executor is not None:
                    self._start_file(ws, session, job, msg)
                    continue
                status, output = 'error', 'Unsupported operation'
                if expired:
                    output = 'Job expired'
                elif operation == 'ping':
                    status, output = 'ok', 'pong'
                elif operation == 'notify':
                    if not valid_notification_text(msg.get('text')):
                        output = 'Invalid notification text'
                    else:
                        try:
                            # Callback must enqueue synchronously; no success ACK
                            # if the UI queue rejects the event or callback fails.
                            self._emit('notification', msg['text'])
                        except Exception:
                            output = 'Notification unavailable'
                        else:
                            status, output = 'ok', 'displayed'
                result = {'type': 'result', 'job_id': job, 'session_id': session,
                          'status': status, 'output': output,
                          'exit_code': 0 if status == 'ok' else 1}
                self._send(ws, result)
                self._emit('log', ('Ping answered' if operation == 'ping' else 'Notification queued')
                           if status == 'ok' else 'Job rejected')
            elif kind == 'error' and msg.get('error') == 'result_not_pending':
                self._emit('log', 'Gateway no longer waits for this result')
            else:
                raise ProtocolError()

    def _start_file(self, ws, session, job, msg):
        def work():
            if __package__:
                from .transfers import FileTransport
            else:
                from transfers import FileTransport
            try:
                result = FileTransport(self.url, self._executor).run(msg)
            except Exception as exc:
                # Always answer: a silent thread death leaves the gateway waiting (HTTP 504).
                self._emit('log', 'File operation crashed: ' + type(exc).__name__)
                result = {'status': 'error', 'output': 'File operation failed on the device', 'exit_code': 1}
            try:
                self._send(ws, {'type': 'result', 'job_id': job, 'session_id': session, **result})
            except Exception:
                pass
            self._emit('log', 'File operation finished')
        threading.Thread(target=work, name='bridge-files', daemon=True).start()

    def _start_exec(self, ws, session, job, msg):
        # Runs in a worker thread so heartbeat pongs keep flowing.
        def work():
            self._emit('log', 'Running command')
            try:
                result = self._executor.run(msg.get('command'), msg.get('cwd'), msg.get('timeout') or 30)
            except Exception:
                result = {'status': 'error', 'output': '', 'stderr': 'Local execution failed',
                          'exit_code': 1}
            frame = {'type': 'result', 'job_id': job, 'session_id': session}
            frame.update(result)
            try:
                self._send(ws, frame)
            except Exception:
                return
            self._emit('log', 'Command finished, exit ' + str(result.get('exit_code')))
        threading.Thread(target=work, name='bridge-exec', daemon=True).start()

    def run(self):
        delay = 1
        try:
            while not self._stop.is_set():
                ws = None
                started = time.monotonic()
                self._emit('status', 'connecting')
                try:
                    # No URL tokens, Origin, cookies, redirects, or TLS bypass.
                    ws = websocket.create_connection(
                        self.url, header={'Authorization': 'Bearer ' + self._token,
                                          'User-Agent': USER_AGENT},
                        timeout=5, suppress_origin=True, redirect_limit=0,
                        enable_multithread=True)
                    if ws.getstatus() != 101:
                        self._emit('error', 'WebSocket upgrade refused (HTTP ' + str(ws.getstatus()) + '). Check redirect, exact nginx location and Authentik bypass on this path only.')
                        break
                    with self._lock:
                        self._socket = ws
                    if self._stop.is_set():
                        break
                    ws.settimeout(1)
                    self._session(ws)
                except websocket.WebSocketBadStatusException as exc:
                    self._emit('log', 'WebSocket handshake HTTP ' + (str(exc.status_code) if type(exc.status_code) is int else 'unknown'))
                    if exc.status_code in (401, 403):
                        self._emit('status', 'unauthorized')
                        self._emit('error', 'Connection refused: token expired/revoked, another client active, or ingress denied access. Reconnect manually after checking.')
                        break
                    if exc.status_code in (301, 302, 303, 307, 308, 400, 404, 405, 426):
                        self._emit('error', 'Check server URL and nginx WebSocket route (unexpected HTTP response)')
                        break
                    self._emit('log', 'Gateway temporarily unavailable')
                except ProtocolError:
                    self._emit('error', 'Protocol mismatch: connection stopped')
                    break
                except Exception as exc:
                    if not self._stop.is_set():
                        self._emit('log', connection_diagnostic(exc))
                finally:
                    self._live = None
                    with self._lock:
                        self._socket = None
                    if ws:
                        try:
                            ws.close(timeout=0)
                        except Exception:
                            pass
                if self._stop.is_set():
                    break
                if time.monotonic() - started > 30:
                    delay = 1
                self._emit('status', 'reconnecting')
                if self._stop.wait(delay + random.uniform(0, delay * 0.2)):
                    break
                delay = min(30, delay * 2)
        finally:
            self._emit('status', 'stopped')
