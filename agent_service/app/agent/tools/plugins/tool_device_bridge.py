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

import json
import logging
import os
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


def make_device_tools(user_id, enabled: bool = True):
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

    return [list_devices, run_on_device]
