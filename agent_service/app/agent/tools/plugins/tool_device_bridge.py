"""Plugins: agent tools for the user's desktop devices (Device Bridge).

Calls the gateway's service-authenticated internal API:
* GET  {DEVICE_BRIDGE_URL}/internal/devices?user_id=<owner>
* POST {DEVICE_BRIDGE_URL}/internal/devices/{device_id}/commands

The owner (user_id) is bound from the authenticated server context when the
tool is built. The model never supplies it, so it can only reach devices of
the user who sent the request. The shell command is passed as-is; where and
whether it runs is decided by the desktop client (exec toggle, workspace
folder, protected folders in its local config).

Env: DEVICE_BRIDGE_URL (default http://gateway:7000),
     DEVICE_BRIDGE_SERVICE_TOKEN (same value as in the gateway).
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import logging
import os
import re
import stat
import sqlite3
from contextlib import contextmanager
import uuid
from pathlib import Path
from urllib.parse import quote
from typing import Optional

import httpx
from langchain.tools import tool

logger = logging.getLogger(__name__)

DEFAULT_URL = 'http://gateway:7000'
MAX_RETURN = 20000  # characters of stdout/stderr returned to the model


def _config():
    base = (os.getenv('DEVICE_BRIDGE_URL') or DEFAULT_URL).rstrip('/')
    token = os.getenv('DEVICE_BRIDGE_SERVICE_TOKEN') or ''
    return base, token


def _clip(text: str) -> str:
    if len(text) <= MAX_RETURN:
        return text
    return text[:MAX_RETURN] + f'\n…[обрезано, всего {len(text)} символов]'


_ERRORS = {
    401: 'сервисный ключ Device Bridge не принят gateway (проверь DEVICE_BRIDGE_SERVICE_TOKEN у agent_service и gateway)',
    404: 'устройство не найдено у этого пользователя',
    409: 'устройство offline или на нём выключен переключатель «Allow agent commands» (для exec)',
    422: 'неверные параметры команды',
    429: 'очередь команд устройства заполнена, повтори позже',
    503: 'Device Bridge недоступен (секрет не настроен или нет Redis)',
    504: 'устройство не ответило до таймаута',
}


def _format_devices(data: dict) -> str:
    devices = data.get('devices') or []
    if not devices:
        return ('Устройств нет. Пользователь регистрирует устройство в разделе «Плагины» '
                'и подключает десктоп-клиент.')
    lines = []
    for d in sorted(devices, key=lambda x: (x.get('status') != 'online', -int(x.get('created_at') or 0))):
        caps = ','.join(d.get('capabilities') or []) or '-'
        lines.append(f"- {d.get('device_id')} | {d.get('name')} | {d.get('platform')} | "
                     f"{d.get('status')} | capabilities: {caps}")
    return 'Устройства пользователя:\n' + '\n'.join(lines)


def _format_result(op: str, data: dict) -> str:
    if op != 'exec':
        return json.dumps(data, ensure_ascii=False)
    parts = [f"status: {data.get('status')} | exit_code: {data.get('exit_code')}"
             + (f" | {data.get('duration_ms')} ms" if 'duration_ms' in data else '')
             + (' | вывод обрезан устройством' if data.get('truncated') else '')]
    if data.get('output'):
        parts.append('stdout:\n' + _clip(data['output']))
    if data.get('stderr'):
        parts.append('stderr:\n' + _clip(data['stderr']))
    if len(parts) == 1:
        parts.append('(пустой вывод)')
    return '\n'.join(parts)



MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
DOWNLOAD_DIR = Path('/app/generated_files/device_bridge')


class _FileError(Exception):
    """Safe, static error text suitable for the model."""


def _identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise _FileError('Invalid device or transfer UUID.') from None
    return value


async def _bounded_body(response, limit):
    length = response.headers.get('content-length')
    if length is not None and (not length.isdigit() or int(length) > limit):
        raise _FileError('Response exceeds size limit (files: maximum 10 MiB).')
    if response.headers.get('content-encoding', 'identity') != 'identity':
        raise _FileError('Unexpected encoded transfer response.')
    data = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(data) + len(chunk) > limit:
            raise _FileError('Response exceeds size limit (files: maximum 10 MiB).')
        data.extend(chunk)
    if length is not None and len(data) != int(length):
        raise _FileError('Incomplete transfer response.')
    return bytes(data)


async def _file_http(client, method, url, *, binary=False, **kwargs):
    async with client.stream(method, url, **kwargs) as response:
        if response.status_code != 200:
            # Do not read/echo error bodies: validation errors can include tokens.
            reason = _ERRORS.get(response.status_code, 'transfer refused')
            raise _FileError(f'HTTP {response.status_code}: {reason}')
        raw = await _bounded_body(response, MAX_FILE_BYTES if binary else MAX_JSON_BYTES)
        if binary:
            digest = response.headers.get('x-content-sha256')
            if digest is not None and digest != hashlib.sha256(raw).hexdigest():
                raise _FileError('Transfer SHA-256 integrity mismatch.')
            return raw
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            raise _FileError('Invalid JSON response from gateway.') from None
        return data


def _metadata(data):
    if (not isinstance(data, dict) or type(data.get('size')) is not int
            or not 0 <= data['size'] <= MAX_FILE_BYTES
            or not isinstance(data.get('sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', data['sha256'])):
        raise _FileError('Invalid file size (maximum 10 MiB) or SHA-256 metadata.')


def _check_metadata(metadata, content):
    _metadata(metadata)
    if metadata['size'] != len(content) or metadata['sha256'] != hashlib.sha256(content).hexdigest():
        raise _FileError('Transfer size or SHA-256 integrity mismatch.')


def _safe_filename(value):
    # Both Windows and POSIX names; never treat a remote filename as a path.
    name = str(value).replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name).strip('._')
    if not name:
        return 'file.bin'
    stem, ext = os.path.splitext(name)
    return stem[:96] + ext[:16]


def _no_symlinks(path):
    """Check every component, not just the final filename (fail closed)."""
    # Do not normalize away a symlink followed by '..'. Trusted paths never need it.
    if '..' in Path(path).parts:
        raise OSError('Parent traversal is not allowed')
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        if stat.S_ISLNK(component.lstat().st_mode):
            raise OSError('Symlinks are not allowed')
    return path


def _private_directory(path):
    # Check existing ancestors before mkdir as well as the completed path.
    for component in (*reversed(path.parents), path):
        if component.exists() or component.is_symlink():
            _no_symlinks(component)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _no_symlinks(path)
    path.chmod(0o700)


@contextmanager
def _ownership_registry(*, create=False):
    """Durable server-written provenance. Reads never create/repair a registry."""
    conn = None
    try:
        root = _no_symlinks(DOWNLOAD_DIR)
        registry = root / '.owners.sqlite'
        # SQLite sidecars must not redirect writes either.
        for suffix in ('-journal', '-wal', '-shm'):
            sidecar = Path(str(registry) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _no_symlinks(sidecar)
        flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        if create:
            flags |= os.O_CREAT
        fd = os.open(registry, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('Invalid registry file')
            os.fchmod(fd, 0o600)
            _no_symlinks(registry)
            conn = sqlite3.connect(registry.as_uri() + '?mode=rw', uri=True, timeout=5)
        finally:
            os.close(fd)
        if create:
            conn.execute('CREATE TABLE IF NOT EXISTS downloads '
                         '(path TEXT PRIMARY KEY, owner INTEGER NOT NULL, sha256 TEXT NOT NULL)')
        yield conn
        conn.commit()
    except (OSError, sqlite3.Error, ValueError):
        raise _FileError('Local file ownership registry is unavailable; use workspace_id + '
                         'workspace_path or image_key instead.') from None
    finally:
        if conn is not None:
            conn.close()


_LOCAL_SOURCE_ERROR = ('local_path only accepts files previously downloaded through Device Bridge '
                       'for this user, with unchanged contents. Use workspace_id + workspace_path '
                       'or image_key instead.')


def _save_download(content, filename, owner):
    """Only trusted device-download results may acquire local-source provenance."""
    if type(owner) is not int or owner <= 0:
        raise _FileError('A bound file owner is required.')
    name = _safe_filename(filename)
    target = None
    digest = hashlib.sha256(content).hexdigest()
    try:
        _private_directory(DOWNLOAD_DIR)
        owner_dir = DOWNLOAD_DIR / str(owner)
        _private_directory(owner_dir)
        target = owner_dir / (str(uuid.uuid4()) + '_' + name)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        target = _no_symlinks(target)
        with _ownership_registry(create=True) as conn:
            conn.execute('INSERT INTO downloads (path, owner, sha256) VALUES (?, ?, ?)',
                         (str(target), owner, digest))
    except BaseException:
        if target is not None:
            target.unlink(missing_ok=True)
        raise
    # Infer from our sanitized filename, never from remote MIME/header metadata.
    mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    return {'ok': True, 'path': str(target), 'filename': name, 'size': len(content),
            'sha256': digest, 'mime': mime,
            'hint': 'Use send_file_to_user(path=...) to deliver this file, or analyze_image(local_path=...) '
                    'to inspect an image. No binary content is returned.'}


def _local_source(local_path, owner):
    try:
        target = _no_symlinks(local_path)
        root = _no_symlinks(DOWNLOAD_DIR)
        if target.parent != root / str(owner):
            raise _FileError(_LOCAL_SOURCE_ERROR)
        with _ownership_registry() as conn:
            row = conn.execute('SELECT sha256 FROM downloads WHERE path = ? AND owner = ?',
                               (str(target), owner)).fetchone()
        if row is None:
            raise _FileError(_LOCAL_SOURCE_ERROR)
        fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise _FileError('Source must be a regular file.')
            if info.st_size > MAX_FILE_BYTES:
                raise _FileError('File exceeds maximum 10 MiB.')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
        finally:
            os.close(fd)
        if hashlib.sha256(content).hexdigest() != row[0]:
            raise _FileError(_LOCAL_SOURCE_ERROR)
        return content
    except (OSError, ValueError):
        raise _FileError(_LOCAL_SOURCE_ERROR) from None


async def _source_bytes(local_path, workspace_id, workspace_path, image_key, agent, owner):
    # Normalize factory paths, but never inherit the generic local-file allowlist.
    # Lazy import keeps tool construction independent of sandbox/DB dependencies.
    from ..telegram.tool_send_file import _extract_workspace_info, SANDBOX_URL

    if sum(bool(x) for x in (local_path, workspace_id or workspace_path, image_key)) != 1:
        raise _FileError('Supply exactly one source: local_path, workspace_id/workspace_path, or image_key.')
    if local_path:
        ws, relative = _extract_workspace_info(local_path)
        if ws:
            workspace_id, workspace_path, local_path = ws, relative, None
    if workspace_id or workspace_path:
        if (not workspace_id or not re.fullmatch(r'[a-f0-9-]{1,64}', workspace_id)
                or not workspace_path or workspace_path.startswith('/') or '\\' in workspace_path
                or any(p in ('', '.', '..') for p in workspace_path.split('/'))
                or '\x00' in workspace_path):
            raise _FileError('Invalid workspace ID or relative workspace_path.')
        url = (SANDBOX_URL.rstrip('/') + '/workspace/' + quote(workspace_id, safe='')
               + '/file/' + quote(workspace_path, safe='/'))
        chats_url = (os.getenv('CHATS_SERVICE_URL') or
                     f"http://chats_service:{os.getenv('PORT_CHATS_SERVICE', '8001')}").rstrip('/')
        async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client:
            try:
                response = await client.get(chats_url + '/editor/owns',
                                            params={'user_id': str(owner), 'workspace_id': workspace_id})
                data = response.json() if response.status_code == 200 else None
                if not isinstance(data, dict) or data.get('owns') is not True:
                    raise _FileError('Workspace ownership could not be verified for this user.')
            except (httpx.HTTPError, ValueError):
                raise _FileError('Workspace ownership could not be verified for this user.') from None
            async with client.stream('GET', url, headers={'Accept-Encoding': 'identity'}) as response:
                if response.status_code != 200:
                    raise _FileError(f'Workspace download failed (HTTP {response.status_code}).')
                return await _bounded_body(response, MAX_FILE_BYTES)
    if image_key:
        _identifier(image_key)
        pool = getattr(getattr(agent, 'db', None), 'pool', None)
        if pool is None:
            raise _FileError('Generated image storage is unavailable.')
        # Same generated_images source as upload_to_workspace/send_file_to_user,
        # but owner-scoped and size-filtered before fetching BYTEA.
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT image_data FROM generated_images WHERE image_key::text = $1 '
                'AND user_id = $2 AND octet_length(image_data) <= $3 LIMIT 1',
                image_key, owner, MAX_FILE_BYTES)
        if not row or row['image_data'] is None:
            raise _FileError('Generated image not found for this user or exceeds 10 MiB.')
        content = bytes(row['image_data'])
    else:
        content = _local_source(local_path, owner)
    if len(content) > MAX_FILE_BYTES:
        raise _FileError('File exceeds maximum 10 MiB.')
    return content


def make_device_tools(user_id, enabled: bool = True, *, agent=None):
    """Build tools bound to the request owner. enabled=False for messengers/triggers,
    where user_id is not the web account that owns devices."""
    try:
        owner = int(user_id)
    except (TypeError, ValueError):
        owner = 0
    available = enabled and owner > 0

    async def _request(method: str, path: str, **kwargs):
        base, token = _config()
        if not token:
            return None, 'Ошибка: DEVICE_BRIDGE_SERVICE_TOKEN не задан в окружении agent_service.'
        try:
            async with httpx.AsyncClient(timeout=kwargs.pop('timeout', 20), trust_env=False) as client:
                resp = await client.request(method, base + path,
                                            headers={'Authorization': 'Bearer ' + token}, **kwargs)
        except httpx.TimeoutException:
            return None, 'Ошибка: gateway не ответил вовремя.'
        except httpx.HTTPError as exc:
            return None, f'Ошибка соединения с gateway: {type(exc).__name__}.'
        if resp.status_code != 200:
            detail = ''
            try:
                detail = str(resp.json().get('detail', ''))[:300]
            except Exception:
                pass
            reason = _ERRORS.get(resp.status_code, 'неожиданный ответ')
            return None, f'Ошибка HTTP {resp.status_code}: {reason}' + (f' ({detail})' if detail else '')
        return resp.json(), None

    @tool
    async def list_devices() -> str:
        """List the current user's desktop devices connected through the AuGent
        Device Bridge plugin: device_id, name, platform (windows/macos/linux),
        online/offline status and capabilities. "exec" in capabilities means
        the user allowed shell commands on that device. Call this before
        run_on_device to pick the device_id and the shell syntax for its OS."""
        if not available:
            return 'Устройства доступны только в веб-чате AuGent.'
        data, err = await _request('GET', '/internal/devices', params={'user_id': str(owner)})
        return err or _format_devices(data)

    @tool
    async def run_on_device(
        device_id: str,
        operation: str = 'exec',
        command: Optional[str] = None,
        cwd: Optional[str] = None,
        text: Optional[str] = None,
        timeout: int = 60,
    ) -> str:
        """Run something on the user's own desktop device via Device Bridge.

        operation:
          - "exec": run `command` in the device shell and return stdout,
            stderr and exit_code. Windows uses PowerShell, macOS zsh,
            Linux bash (check the platform with list_devices). One string,
            pipes/&&/; allowed. Non-interactive: no stdin, no GUI prompts.
            `cwd` is optional: relative paths resolve from the device's
            workspace folder, which is the default working directory.
            The device enforces its own policy (workspace-only or full
            access, protected system folders) and refuses with exit_code 126.
          - "notify": show plain `text` (<=2000 chars) in the client window.
          - "ping": connectivity check.
        timeout: seconds, exec 1..300, ping/notify 1..30.
        Commands affect the user's real computer: avoid destructive actions
        (deleting, overwriting, installing) unless the user explicitly asked."""
        if not available:
            return 'Устройства доступны только в веб-чате AuGent.'
        try:
            device_id = _identifier(device_id)
        except _FileError as exc:
            return 'Ошибка: ' + str(exc)
        op = (operation or '').strip().lower()
        if op not in ('exec', 'notify', 'ping'):
            return 'Ошибка: operation должен быть exec, notify или ping.'
        try:
            t = int(timeout)
        except (TypeError, ValueError):
            t = 60
        t = max(1, min(t, 300 if op == 'exec' else 30))
        body = {'user_id': owner, 'operation': op, 'timeout': t}
        if op == 'exec':
            if not command or not command.strip():
                return 'Ошибка: для exec нужен command.'
            body['command'] = command
            if cwd:
                body['cwd'] = cwd
        elif op == 'notify':
            if not text or not text.strip():
                return 'Ошибка: для notify нужен text.'
            body['text'] = text
        logger.info('device_bridge.%s user=%s device=%s', op, owner, device_id)
        data, err = await _request('POST', f'/internal/devices/{device_id}/commands',
                                   json=body, timeout=t + 15)
        return err or _format_result(op, data)

    async def _files(device_id, operation, path=None, local_path=None,
                     workspace_id=None, workspace_path=None, image_key=None, timeout=60):
        if not available:
            return {'ok': False, 'error': 'Устройства доступны только в веб-чате AuGent.'}
        ticket_path = None
        try:
            device_id = _identifier(device_id)
            if operation != 'screenshot' and (not path or not path.strip() or '\x00' in path):
                raise _FileError('A non-empty device path is required.')
            timeout = max(1, min(int(timeout), 300))
            base, token = _config()
            if not token:
                raise _FileError('DEVICE_BRIDGE_SERVICE_TOKEN is not configured.')
            headers = {'Authorization': 'Bearer ' + token, 'Accept-Encoding': 'identity'}
            params = {'user_id': str(owner)}
            async with httpx.AsyncClient(timeout=timeout + 15, trust_env=False,
                                         follow_redirects=False) as client:
                async def request(method, route, binary=False, **kwargs):
                    return await _file_http(client, method, base + route, headers=headers,
                                            params=params, binary=binary, **kwargs)

                body = {'direction': 'to_device' if operation == 'file_download' else 'from_device'}
                if operation == 'file_download':
                    content = await _source_bytes(local_path, workspace_id, workspace_path,
                                                  image_key, agent, owner)
                    body.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
                ticket = await request('POST', f'/internal/devices/{device_id}/transfers', json=body)
                # Assign cleanup as soon as a valid ticket ID is known, even if
                # the rest of the gateway response is malformed.
                ticket_id = _identifier(ticket.get('transfer_id'))
                ticket_path = '/internal/device-transfers/' + ticket_id
                try:
                    credential = ticket.get('transfer_token')
                    limit = ticket.get('max_bytes')
                    if (not isinstance(credential, str) or not re.fullmatch(r'[A-Za-z0-9_-]{64}', credential)
                            or type(limit) is not int or not 0 < limit <= MAX_FILE_BYTES):
                        raise _FileError('Invalid transfer ticket from gateway.')
                    command = {'user_id': owner, 'operation': operation, 'transfer_id': ticket_id,
                               'transfer_token': credential, 'timeout': timeout}
                    if path is not None:
                        command['path'] = path
                    if operation == 'file_download':
                        if len(content) > limit:
                            raise _FileError('File exceeds gateway transfer limit (maximum 10 MiB).')
                        uploaded = await request('PUT', ticket_path, content=content)
                        _check_metadata(uploaded, content)
                        command.update(expected_size=body['size'], sha256=body['sha256'])
                    result = await request('POST', f'/internal/devices/{device_id}/commands', json=command)
                    if result.get('status') != 'ok' or result.get('exit_code') != 0:
                        # Device output is untrusted and may contain credentials.
                        raise _FileError('Device refused or failed the operation. Check Allow agent commands, '
                                         'file policy, path and permissions; screenshots require full mode and a GUI.')
                    try:
                        metadata = json.loads(result['output'])
                    except (KeyError, TypeError, ValueError):
                        raise _FileError('Invalid file metadata from device.') from None
                    _metadata(metadata)
                    if operation == 'file_download':
                        _check_metadata(metadata, content)
                        return {'ok': True, 'destination_path': path, 'size': len(content),
                                'sha256': body['sha256']}
                    if metadata['size'] > limit:
                        raise _FileError('File exceeds gateway transfer limit (maximum 10 MiB).')
                    content = await request('GET', ticket_path, binary=True)
                    _check_metadata(metadata, content)
                    filename = 'screenshot.png' if operation == 'screenshot' else path
                    return _save_download(content, filename, owner)
                finally:
                    # Best effort; the gateway TTL is a fallback if cleanup is unavailable.
                    try:
                        await request('DELETE', ticket_path)
                    except Exception:
                        pass
        except _FileError as exc:
            return {'ok': False, 'error': str(exc)}
        except httpx.TimeoutException:
            return {'ok': False, 'error': 'Device Bridge timed out.'}
        except httpx.HTTPError:
            return {'ok': False, 'error': 'Device Bridge network error.'}
        except Exception:
            # Never echo exceptions, HTTP bodies, tokens or arbitrary device output.
            return {'ok': False, 'error': 'Unable to complete device file operation.'}

    @tool
    async def send_file_to_device(
        device_id: str, destination_path: str, local_path: Optional[str] = None,
        workspace_id: Optional[str] = None, workspace_path: Optional[str] = None,
        image_key: Optional[str] = None, timeout: int = 60,
    ) -> dict:
        """Send one file (maximum 10 MiB) to the user's desktop device.

        Pick device_id with list_devices. Supply exactly ONE source: local_path
        of a file previously downloaded through Device Bridge for this user
        (not generic local files), workspace_id AND relative workspace_path, OR
        image_key of a generated image. Factory /tmp/exec/workspaces/... paths
        are recognized as workspace sources and require verified ownership. URLs are not accepted. destination_path is on the
        device; relative paths use its configured workspace (no cwd override).
        Requires Allow agent commands and the device's file policy. Existing
        files are not overwritten; parent directory must exist. Ask the user
        before sending sensitive files or affecting their real computer.
        """
        return await _files(device_id, 'file_download', destination_path, local_path,
                            workspace_id, workspace_path, image_key, timeout)

    @tool
    async def get_file_from_device(device_id: str, path: str, timeout: int = 60) -> dict:
        """Get one file (maximum 10 MiB) from the user's desktop device.

        path is the device source path; relative paths use its configured
        workspace, subject to Allow agent commands and local file policy.
        Access only files the user requested. Returns a local generated_files
        path, not binary content. Use send_file_to_user(path=...) to deliver it,
        or analyze_image(local_path=...) to inspect an image.
        """
        return await _files(device_id, 'file_upload', path, timeout=timeout)

    @tool
    async def capture_screenshot(device_id: str, timeout: int = 60) -> dict:
        """Capture the user's desktop screenshot (maximum 10 MiB).

        PRIVACY: may expose private windows, credentials and personal data.
        Only capture when the user requests it. Requires client FULL mode,
        Allow agent commands, a graphical desktop and OS capture permissions;
        workspace-only mode refuses screenshots. Returns a local PNG path,
        never binary/base64. Use analyze_image(local_path=...) to inspect it or
        send_file_to_user(path=...) to deliver it to the user.
        """
        return await _files(device_id, 'screenshot', timeout=timeout)

    return [list_devices, run_on_device, send_file_to_device, get_file_from_device, capture_screenshot]
