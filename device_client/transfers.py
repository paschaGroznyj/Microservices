"""Opt-in file transport, same trusted origin only, never shell commands."""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import mimetypes
import os
import re
import secrets
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

if __package__:
    from .executor import PolicyError
else:
    from executor import PolicyError

MAX_BYTES = 10 * 1024 * 1024


def screenshot_available():
    try:
        from PIL import ImageGrab, Image
        if sys.platform.startswith('linux'):
            return bool(os.environ.get('DISPLAY') and getattr(Image.core, 'HAVE_XCB', False))
        return sys.platform in ('win32', 'darwin') and callable(ImageGrab.grab)
    except ImportError:
        return False


@contextmanager
def _parent(path):
    """Pin existing POSIX parents, disallow replacement symlinks during I/O.

    On Windows open paths are revalidated by the caller; OS account permissions
    remain the boundary against a hostile process with the same local account.
    """
    fd = None
    try:
        if os.name == 'posix':
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
            for part in path.parent.parts[1:]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
        yield fd
    finally:
        if fd is not None:
            os.close(fd)


class FileTransport:
    def __init__(self, server_url, executor):
        # Reuse exactly the WS connection policy, not any command-provided URL.
        from .core import normalize_url
        self.origin = urlsplit(normalize_url(server_url))
        self.executor = executor

    def request(self, method, identifier, token, timeout, body=None):
        cls = http.client.HTTPSConnection if self.origin.scheme == 'wss' else http.client.HTTPConnection
        conn = cls(self.origin.hostname, self.origin.port, timeout=timeout)
        try:
            conn.request(method, '/api/device-transfers/' + identifier, body=body,
                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/octet-stream'})
            response = conn.getresponse()
            # No redirect handling, proxies, cookies, alternate host or TLS bypass.
            if response.status != 200:
                raise PolicyError('Gateway refused file transfer (HTTP %d)' % response.status)
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
                raise PolicyError('Transfer exceeds 10 MiB')
            chunks, size = [], 0
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() >= deadline:
                    raise PolicyError('Transfer timed out')
                chunk = response.read(min(64 * 1024, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise PolicyError('Transfer exceeds 10 MiB')
                chunks.append(chunk)
            if length is not None and size != int(length):
                raise PolicyError('Incomplete transfer body')
            return b''.join(chunks), response.getheader('X-Content-SHA256')
        finally:
            conn.close()

    @staticmethod
    def validate(data, expected_size, sha256):
        if len(data) > MAX_BYTES:
            raise PolicyError('Transfer exceeds 10 MiB')
        if expected_size is not None and len(data) != expected_size:
            raise PolicyError('Transfer size mismatch')
        digest = hashlib.sha256(data).hexdigest()
        if sha256 is not None and digest != sha256:
            raise PolicyError('Transfer sha256 mismatch')
        return digest

    def destination(self, msg, identifier):
        path = self.executor.safe_path(msg.get('path'), msg.get('cwd'))
        if path.is_dir():
            path = path / (identifier + '.bin')
        path = self.executor.safe_path(str(path), msg.get('cwd'))
        if path.exists():
            raise PolicyError('Destination already exists; overwrite is forbidden')
        if not path.parent.is_dir():
            raise PolicyError('Destination parent directory must exist')
        return path

    def save(self, path, data, msg):
        with _parent(path) as parent:
            # Revalidate after pinning parents; no remote mkdir or overwrite.
            if self.executor.safe_path(str(path), msg.get('cwd')) != path:
                raise PolicyError('Destination changed')
            name = '.bridge-' + secrets.token_hex(16) + '.tmp'
            temp = path.parent / name
            kw = {'dir_fd': parent} if parent is not None else {}
            tmp_arg = name if parent is not None else str(temp)
            fd = os.open(tmp_arg, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, **kw)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # link is atomic and fails if destination appeared meanwhile.
                if parent is not None:
                    os.link(name, path.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                else:
                    os.link(temp, path)
            finally:
                os.unlink(tmp_arg, **kw)

    def read(self, path, msg):
        with _parent(path) as parent:
            if self.executor.safe_path(str(path), msg.get('cwd')) != path:
                raise PolicyError('Source changed')
            fd = os.open(path.name if parent is not None else str(path),
                         os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0),
                         **({'dir_fd': parent} if parent is not None else {}))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise PolicyError('Source must be a regular file')
                if info.st_size > MAX_BYTES:
                    raise PolicyError('Transfer exceeds 10 MiB')
                data = stream.read(MAX_BYTES + 1)
                self.validate(data, None, None)
                return data

    def capture(self):
        if self.executor.config.data['mode'] != 'full':
            raise PolicyError('Screenshot requires full mode (private desktop capture)')
        if not screenshot_available():
            raise PolicyError('Screenshot unavailable: install Pillow and enable a graphical desktop/capture permission')
        from PIL import ImageGrab
        try:
            # Empty xdisplay disables Pillow's Linux external-command fallback.
            image = ImageGrab.grab(xdisplay='' if sys.platform.startswith('linux') else None)
            try:
                out = io.BytesIO()
                image.save(out, format='PNG')
                data = out.getvalue()
            finally:
                image.close()
        except Exception:
            raise PolicyError('Screenshot capture failed: no GUI or OS screen recording permission') from None
        self.validate(data, None, None)
        return data

    def run(self, msg):
        acquired = False
        try:
            if not self.executor.config.exec_enabled:
                raise PolicyError('Command execution is disabled on this device')
            identifier, token = msg.get('transfer_id'), msg.get('transfer_token')
            try:
                if not isinstance(identifier, str) or str(uuid.UUID(identifier)) != identifier:
                    raise ValueError
            except (ValueError, AttributeError):
                raise PolicyError('Invalid transfer ID')
            if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{64}', token):
                raise PolicyError('Invalid transfer credential')
            size, sha = msg.get('expected_size'), msg.get('sha256')
            if size is not None and (type(size) is not int or not 0 <= size <= MAX_BYTES):
                raise PolicyError('Invalid expected_size')
            if sha is not None and (not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha)):
                raise PolicyError('Invalid sha256')
            timeout = msg.get('timeout', 30)
            if type(timeout) not in (int, float) or not 0 < timeout <= 300:
                raise PolicyError('Invalid transfer timeout')
            timeout = min(timeout, self.executor.config.data['max_timeout'])
            if 'deadline' in msg:
                timeout = min(timeout, msg['deadline'] - time.time())
                if timeout <= 0:
                    raise PolicyError('Job expired')
            acquired = self.executor._slots.acquire(blocking=False)
            if not acquired:
                raise PolicyError('Too many commands running on this device')
            operation = msg.get('operation')
            if operation == 'file_download':
                path = self.destination(msg, identifier)
                data, header_sha = self.request('GET', identifier, token, timeout)
                digest = self.validate(data, size, sha)
                if header_sha is not None and header_sha != digest:
                    raise PolicyError('Transfer sha256 mismatch')
                self.save(path, data, msg)
                mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
            elif operation in ('file_upload', 'screenshot'):
                # Even screenshots validate cwd/workspace and the opt-in policy.
                self.executor.safe_path(None, msg.get('cwd'), directory=True)
                if operation == 'screenshot':
                    if msg.get('path') is not None:
                        raise PolicyError('Screenshot does not accept a path')
                    path, data, mime = None, self.capture(), 'image/png'
                else:
                    if not msg.get('path'):
                        raise PolicyError('Source path required')
                    path = self.executor.safe_path(msg['path'], msg.get('cwd'))
                    data = self.read(path, msg)
                    mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
                digest = self.validate(data, size, sha)
                self.request('PUT', identifier, token, timeout, data)
            else:
                raise PolicyError('Unsupported file operation')
            return {'status': 'ok', 'output': json.dumps({'path': str(path) if path else None,
                    'size': len(data), 'sha256': digest, 'mime': mime}), 'exit_code': 0}
        except PolicyError as exc:
            return {'status': 'error', 'output': str(exc), 'exit_code': 1}
        except Exception:
            # Never expose http errors (which could include Authorization).
            return {'status': 'error', 'output': 'File operation failed (network or filesystem error)', 'exit_code': 1}
        finally:
            if acquired:
                self.executor._slots.release()
