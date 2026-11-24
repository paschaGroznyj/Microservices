"""Раздел «Плагины»: готовые JS-скрипты для консоли браузера (регистрация
устройства, токен, проверки). Скрипты хранятся в Redis, ключ
plugins:snippets (JSON-список {id,title,description,code}). Если ключа нет,
он заполняется встроенным набором DEFAULT_SNIPPETS (SET NX), дальше правится
прямо в Redis без релиза:

    redis-cli GET plugins:snippets
    redis-cli SET plugins:snippets "$(cat snippets.json)"

GET /api/plugins/snippets — только авторизованным пользователям.
"""
import json
import logging
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

KEY = 'plugins:snippets'
MAX_ITEMS = 50

_REQ = """async function deviceRequest(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...options,
    headers: { 'X-Device-Bridge': '1', ...(options.headers || {}) }
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${body}`);
  return body ? JSON.parse(body) : null;
}
"""

DEFAULT_SNIPPETS = [
    {
        'id': 'register',
        'title': 'Зарегистрировать устройство и скопировать токен',
        'description': 'Создаёт устройство и кладёт его токен в буфер обмена. '
                       'Вставь токен в поле Device token десктоп-клиента. Токен показывается один раз.',
        'code': _REQ + """
var registered = await deviceRequest('/api/devices', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name: 'My desktop', platform: 'windows' })  // windows | macos | linux
});
var deviceId = registered.device_id;
copy(registered.token);
console.log('Токен скопирован. device_id:', deviceId);
""",
    },
    {
        'id': 'list',
        'title': 'Список устройств',
        'description': 'Статус online/offline и capabilities. exec появляется, '
                       'когда в клиенте включён переключатель Allow agent commands.',
        'code': _REQ + """
var devices = (await deviceRequest('/api/devices')).devices;
console.table(devices.map(d => ({ ...d, capabilities: (d.capabilities || []).join(',') })));
var deviceId = (devices.find(d => d.status === 'online') || {}).device_id;
console.log('deviceId =', deviceId);
""",
    },
    {
        'id': 'ping',
        'title': 'Ping устройства',
        'description': 'Нужен deviceId из предыдущего скрипта. Ожидаем output: pong.',
        'code': _REQ + """
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ operation: 'ping', timeout: 10 })
});
""",
    },
    {
        'id': 'notify',
        'title': 'Отправить текст на устройство',
        'description': 'Текст появится в панели Messages клиента.',
        'code': _REQ + """
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ operation: 'notify', text: 'Привет из AuGent!', timeout: 10 })
});
""",
    },
    {
        'id': 'exec',
        'title': 'Выполнить команду (проверка exec)',
        'description': 'Работает, только если в клиенте включён Allow agent commands. '
                       'Windows — PowerShell, macOS — zsh, Linux — bash.',
        'code': _REQ + """
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ operation: 'exec', command: 'whoami; pwd', timeout: 30 })
});
""",
    },
    {
        'id': 'revoke',
        'title': 'Отозвать устройство',
        'description': 'Токен перестаёт действовать навсегда, клиент отключится. '
                       'Для повторного подключения нужна новая регистрация.',
        'code': _REQ + """
await deviceRequest(`/api/devices/${deviceId}`, { method: 'DELETE' });
""",
    },
]


def _valid(items: Any) -> bool:
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        return False
    for it in items:
        if not isinstance(it, dict):
            return False
        for field, limit in (('id', 64), ('title', 200), ('description', 2000), ('code', 20000)):
            if not isinstance(it.get(field), str) or len(it[field]) > limit:
                return False
    return True


def create_router(get_current_user: Callable, redis_client: Any) -> APIRouter:
    router = APIRouter()

    @router.get('/api/plugins/snippets')
    async def snippets(request: Request):
        user = await get_current_user(request)
        if not user:
            raise HTTPException(401, 'Not authenticated')
        items = DEFAULT_SNIPPETS
        try:
            raw = await redis_client.get(KEY)
            if raw is None:
                await redis_client.set(KEY, json.dumps(DEFAULT_SNIPPETS, ensure_ascii=False), nx=True)
            else:
                parsed = json.loads(raw)
                if _valid(parsed):
                    items = parsed
                else:
                    logging.warning('[plugins] %s has invalid format, using defaults', KEY)
        except Exception as exc:  # Redis down: built-in set still works
            logging.warning('[plugins] redis or snippets error: %s', type(exc).__name__)
        return {'snippets': [{k: it[k] for k in ('id', 'title', 'description', 'code')} for it in items]}

    return router
