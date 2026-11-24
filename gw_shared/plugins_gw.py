"""The browser-console snippets exposed by the plugins panel.

The Redis value is intentionally kept as a JSON list for backwards
compatibility.  ``plugins:snippets:version`` is only migration metadata; old
installations and user-edited lists remain readable without a deployment.
"""
import copy
import json
import logging
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

KEY = 'plugins:snippets'
VERSION_KEY = 'plugins:snippets:version'
DEFAULT_VERSION = '2'
MAX_ITEMS = 50

# This is the default shipped before the snippets became self-contained.  It
# is deliberately retained as a migration marker, not exposed as a second
# set of snippets.  A list is migrated only when it is an exact old default;
# an edited list (including one with an extra item) is always user data.
_LEGACY_REQUEST = """async function deviceRequest(path, options = {}) {
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

# Each paste has its own lexical scope. Only data is shared through window;
# there are no top-level const/let declarations or DevTools-only helpers.
_REQ = """(async () => {
  const state = window.__auGentDeviceBridge || (window.__auGentDeviceBridge = {});
  // Session storage is tab-scoped. Credentials are never put in localStorage.
  try {
    state.deviceId ||= sessionStorage.getItem('augent.device_id');
    state.token ||= sessionStorage.getItem('augent.device_token');
  } catch (_) {} // Storage can be disabled; in-memory state still works.
  function remember(deviceId, token) {
    if (state.deviceId !== deviceId) state.token = null;
    state.deviceId = deviceId || null;
    if (token) state.token = token;
    try {
      for (const [key, value] of [['augent.device_id', state.deviceId], ['augent.device_token', state.token]]) {
        if (value) sessionStorage.setItem(key, value);
        else sessionStorage.removeItem(key);
      }
    } catch (_) {}
  }
  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin', cache: 'no-store', ...options,
      headers: { 'X-Device-Bridge': '1', ...(options.headers || {}) }
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}: запрос устройства не выполнен`);
    return response.json();
  }
  async function selectDevice(force = false) {
    if (!force && typeof state.deviceId === 'string' && state.deviceId.trim()) return state.deviceId;
    const devices = (await request('/api/devices')).devices || [];
    if (!devices.length) {
      remember(null);
      console.info('Нет устройств. Сначала выполните регистрацию.');
      throw new Error('Нет устройств');
    }
    const online = devices.filter(d => d.status === 'online');
    const choices = online.length ? online : devices;
    let selected = choices[0];
    if (choices.length > 1) {
      // Select by number, never interpolate an unchecked id into a URL.
      const answer = window.prompt('Выберите номер устройства:\\n' + choices.map((d, i) =>
        `${i + 1}. ${d.name} (${d.device_id})`).join('\\n'), '1');
      if (answer === null || !/^[1-9]\\d*$/.test(answer.trim())) throw new Error('Выбор устройства отменён.');
      selected = choices[Number(answer) - 1];
    }
    if (!selected || typeof selected.device_id !== 'string' || !selected.device_id.trim()) {
      throw new Error('Устройство не выбрано. Запустите «Список устройств».');
    }
    remember(selected.device_id);
    return selected.device_id;
  }
"""
_END = """})().catch(() => {
  // Do not print server bodies or rejected values: they could contain secrets.
  console.error('Не удалось выполнить скрипт. Проверьте вход, выбор устройства и доступ клиента.');
});
"""


def _snippet(code: str, **fields: str) -> dict[str, str]:
    return {**fields, 'code': _REQ + code + _END}


DEFAULT_SNIPPETS = [
    _snippet("""  const registered = await request('/api/devices', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'My desktop', platform: 'windows' }) // windows | macos | linux
  });
  if (!registered.device_id || !registered.token) throw new Error('Некорректный ответ регистрации');
  // Persist BEFORE attempting clipboard or prompt; either can be unavailable.
  remember(registered.device_id, registered.token);
  let copied = false;
  try {
    await navigator.clipboard.writeText(registered.token);
    copied = true;
  } catch (_) {}
  if (!copied) {
    try {
      window.prompt('Скопируйте Device token в клиент. Не отправляйте его в чат:', registered.token);
    } catch (_) {} // A blocked/cancelled dialog must not lose the credential.
  }
  console.info(copied ? 'Токен скопирован. device_id:' : 'Токен сохранён в этой вкладке. device_id:', state.deviceId);
  // To reopen the copy dialog later (without a new registration):
  // window.prompt('Device token', window.__auGentDeviceBridge.token)
""", id='register', title='Зарегистрировать устройство и сохранить токен',
             description='Сохраняет device_id и токен в памяти и sessionStorage этой вкладки до копирования. '
                         'Без Clipboard API открывает диалог. Не выводит токен в консоль. '
                         'Вставьте токен в Device token клиента; после настройки удалите его из сессии/закройте вкладку.'),
    _snippet("""  const devices = (await request('/api/devices')).devices || [];
  console.table(devices.map(d => ({
    device_id: d.device_id, name: d.name, status: d.status,
    capabilities: (d.capabilities || []).join(',')
  })));
  // Explicitly rerun selection, including the multiple-device chooser.
  const deviceId = await selectDevice(true);
  console.info('Выбрано устройство:', deviceId);
""", id='list', title='Список и выбор устройств',
             description='Показывает статус и capabilities. Выбирает online-устройство или спрашивает номер, '
                         'если устройств несколько. Для смены устройства запустите повторно.'),
    _snippet("""  const deviceId = await selectDevice();
  const result = await request(`/api/devices/${encodeURIComponent(deviceId)}/commands`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation: 'ping', timeout: 10 })
  });
  console.info('Ping:', result);
""", id='ping', title='Ping устройства',
             description='Самостоятельно выбирает устройство, если оно ещё не выбрано. Ожидаемый ответ: pong.'),
    _snippet("""  const deviceId = await selectDevice();
  await request(`/api/devices/${encodeURIComponent(deviceId)}/commands`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation: 'notify', text: 'Привет из AuGent!', timeout: 10 })
  });
  console.info('Сообщение отправлено на устройство:', deviceId);
""", id='notify', title='Отправить текст на устройство',
             description='Текст появится в панели Messages клиента. Использует общий выбор устройства или выбирает самостоятельно.'),
    _snippet("""  const deviceId = await selectDevice();
  const result = await request(`/api/devices/${encodeURIComponent(deviceId)}/commands`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operation: 'exec', command: 'whoami; pwd', timeout: 30 })
  });
  console.info('exec:', result);
""", id='exec', title='Выполнить команду (проверка exec)',
             description='Нужна capability exec: включите Allow agent commands в настройках/config клиента. '
                         'Windows — PowerShell, macOS — zsh, Linux — bash. Не запускайте недоверенные команды.'),
    _snippet("""  const deviceId = await selectDevice();
  if (!window.confirm('Навсегда отозвать устройство ' + deviceId + '?')) return;
  await request(`/api/devices/${encodeURIComponent(deviceId)}`, { method: 'DELETE' });
  remember(null);
  console.info('Устройство отозвано:', deviceId);
""", id='revoke', title='Отозвать устройство',
             description='После подтверждения навсегда отзывает токен и очищает выбор и токен из этой вкладки. '
                         'Для повторного подключения нужна новая регистрация.'),
]

# Exact old values are generated from the old source shape so migration is
# strict.  Keeping this separate also prevents a custom Redis list from being
# mistaken for a default merely because it has the same ids.
_LEGACY_DEFAULT_SNIPPETS = [
    {'id': 'register', 'title': 'Зарегистрировать устройство и скопировать токен',
     'description': 'Создаёт устройство и кладёт его токен в буфер обмена. Вставь токен в поле Device token десктоп-клиента. Токен показывается один раз.',
     'code': _LEGACY_REQUEST + """
var registered = await deviceRequest('/api/devices', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ name: 'My desktop', platform: 'windows' })  // windows | macos | linux
});
var deviceId = registered.device_id;
copy(registered.token);
console.log('Токен скопирован. device_id:', deviceId);
"""},
    {'id': 'list', 'title': 'Список устройств',
     'description': 'Статус online/offline и capabilities. exec появляется, когда в клиенте включён переключатель Allow agent commands.',
     'code': _LEGACY_REQUEST + """
var devices = (await deviceRequest('/api/devices')).devices;
console.table(devices.map(d => ({ ...d, capabilities: (d.capabilities || []).join(',') })));
var deviceId = (devices.find(d => d.status === 'online') || {}).device_id;
console.log('deviceId =', deviceId);
"""},
    {'id': 'ping', 'title': 'Ping устройства',
     'description': 'Нужен deviceId из предыдущего скрипта. Ожидаем output: pong.',
     'code': _LEGACY_REQUEST + """
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ operation: 'ping', timeout: 10 })
});
"""},
    {'id': 'notify', 'title': 'Отправить текст на устройство',
     'description': 'Текст появится в панели Messages клиента.',
     'code': _LEGACY_REQUEST + """
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ operation: 'notify', text: 'Привет из AuGent!', timeout: 10 })
});
"""},
    {'id': 'exec', 'title': 'Выполнить команду (проверка exec)',
     'description': 'Работает, только если в клиенте включён Allow agent commands. Windows — PowerShell, macOS — zsh, Linux — bash.',
     'code': _LEGACY_REQUEST + """
await deviceRequest(`/api/devices/${deviceId}/commands`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ operation: 'exec', command: 'whoami; pwd', timeout: 30 })
});
"""},
    {'id': 'revoke', 'title': 'Отозвать устройство',
     'description': 'Токен перестаёт действовать навсегда, клиент отключится. Для повторного подключения нужна новая регистрация.',
     'code': _LEGACY_REQUEST + """
await deviceRequest(`/api/devices/${deviceId}`, { method: 'DELETE' });
"""},
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


def _is_legacy_default(items: list[dict[str, str]]) -> bool:
    return items == _LEGACY_DEFAULT_SNIPPETS


# Compare-and-set both value and migration metadata in one Redis operation.
# A custom edit between GET and migration must never be overwritten.
_STORE_DEFAULT = """
local current = redis.call('GET', KEYS[1])
if (ARGV[1] == 'missing' and not current) or
   (ARGV[1] == 'replace' and current == ARGV[2]) then
  redis.call('SET', KEYS[1], ARGV[3])
  redis.call('SET', KEYS[2], ARGV[4])
  return 1
end
return 0
"""


async def _load_items(redis_client: Any) -> list[dict[str, str]]:
    encoded = json.dumps(DEFAULT_SNIPPETS, ensure_ascii=False)
    # Reread after a lost CAS so the response also reflects concurrent edits.
    for _ in range(3):
        raw = await redis_client.get(KEY)
        if raw is None:
            mode = 'missing'
        else:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                logging.warning('[plugins] %s contains invalid JSON, using defaults', KEY)
                return copy.deepcopy(DEFAULT_SNIPPETS)
            if not _valid(parsed):
                logging.warning('[plugins] %s has invalid format, using defaults', KEY)
                return copy.deepcopy(DEFAULT_SNIPPETS)
            if not _is_legacy_default(parsed):
                return parsed  # All custom values, including [], remain untouched.
            mode = 'replace'
        if await redis_client.eval(_STORE_DEFAULT, 2, KEY, VERSION_KEY,
                                   mode, raw or '', encoded, DEFAULT_VERSION):
            return copy.deepcopy(DEFAULT_SNIPPETS)
    return copy.deepcopy(DEFAULT_SNIPPETS)


def create_router(get_current_user: Callable, redis_client: Any) -> APIRouter:
    router = APIRouter()

    @router.get('/api/plugins/snippets')
    async def snippets(request: Request):
        user = await get_current_user(request)
        if not user:
            raise HTTPException(401, 'Not authenticated')
        try:
            items = await _load_items(redis_client)
        except Exception as exc:  # Redis down: built-in set still works
            logging.warning('[plugins] redis or snippets error: %s', type(exc).__name__)
            items = copy.deepcopy(DEFAULT_SNIPPETS)
        return {'snippets': [{k: it[k] for k in ('id', 'title', 'description', 'code')} for it in items]}

    return router
