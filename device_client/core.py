"""Outbound, ping-only Device Bridge client. No command execution capability."""
from __future__ import annotations

import json
import math
import random
import threading
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import websocket

MAX_FRAME = 512 * 1024


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
    def __init__(self, server_url: str, token: str, on_event=None):
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

    def _emit(self, kind, message):
        # Never forward raw exceptions, headers, frames or server strings to UI.
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
        ws.send(json.dumps(value, separators=(',', ':')))

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
                        or not valid_id(msg.get('device_id')) or msg.get('heartbeat_interval') != 5):
                    raise ProtocolError()
                session = msg['session_id']
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
                        or not math.isfinite(deadline) or isinstance(timeout, bool)
                        or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30):
                    raise ProtocolError()
                if job in seen:
                    continue
                # Bounded duplicate protection; don't evict and re-execute old job IDs.
                if len(seen) >= 10000:
                    raise ProtocolError()
                seen.add(job)
                expired = deadline < time.time()
                allowed = msg.get('operation') == 'ping' and not expired
                self._send(ws, {'type': 'result', 'job_id': job, 'session_id': session,
                                'status': 'ok' if allowed else 'error',
                                'output': 'pong' if allowed else ('Job expired' if expired else 'Unsupported operation'),
                                'exit_code': 0 if allowed else 1})
                self._emit('log', 'Ping answered' if allowed else 'Job rejected')
            elif kind == 'error' and msg.get('error') == 'result_not_pending':
                self._emit('log', 'Gateway no longer waits for this result')
            else:
                raise ProtocolError()

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
                        self.url, header={'Authorization': 'Bearer ' + self._token},
                        timeout=5, suppress_origin=True, redirect_limit=0,
                        enable_multithread=True)
                    if ws.getstatus() != 101:
                        self._emit('error', 'Check nginx WebSocket route: redirect or non-WebSocket response refused')
                        break
                    with self._lock:
                        self._socket = ws
                    if self._stop.is_set():
                        break
                    ws.settimeout(1)
                    self._session(ws)
                except websocket.WebSocketBadStatusException as exc:
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
                except Exception:
                    if not self._stop.is_set():
                        self._emit('log', 'Connection interrupted or unavailable')
                finally:
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
