import copy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw_shared.plugins_gw import (
    DEFAULT_SNIPPETS,
    KEY,
    VERSION_KEY,
    _LEGACY_DEFAULT_SNIPPETS,
    _V2_DEFAULT_SNIPPETS,
    create_router,
)


class MemoryRedis:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def get(self, key):
        return self.values.get(key)

    async def eval(self, script, count, key, version_key, mode, raw, encoded, version):
        current = self.values.get(key)
        if (mode == 'missing' and current is None) or (mode == 'replace' and current == raw):
            self.values[key] = encoded
            self.values[version_key] = version
            return 1
        return 0

    async def set(self, key, value, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True


def client_for(redis, user=True):
    app = FastAPI()

    async def current_user(_request):
        return {'id': 'test'} if user else None

    app.include_router(create_router(current_user, redis))
    return TestClient(app)


def test_snippets_require_authentication():
    response = client_for(MemoryRedis(), user=False).get('/api/plugins/snippets')
    assert response.status_code == 401


def test_missing_redis_set_is_initialized_without_mutating_defaults():
    redis = MemoryRedis()
    original = copy.deepcopy(DEFAULT_SNIPPETS)
    response = client_for(redis).get('/api/plugins/snippets')

    assert response.status_code == 200
    assert [item['id'] for item in response.json()['snippets']] == [item['id'] for item in original]
    assert json.loads(redis.values[KEY]) == original
    assert redis.values[VERSION_KEY] == '3'
    assert DEFAULT_SNIPPETS == original


def test_exact_legacy_builtin_set_is_migrated():
    redis = MemoryRedis({KEY: json.dumps(_LEGACY_DEFAULT_SNIPPETS, ensure_ascii=False)})
    response = client_for(redis).get('/api/plugins/snippets')

    assert response.status_code == 200
    assert json.loads(redis.values[KEY]) == DEFAULT_SNIPPETS
    assert redis.values[VERSION_KEY] == '3'
    register = next(item for item in response.json()['snippets'] if item['id'] == 'register')
    assert 'copy(registered.token)' not in register['code']
    assert 'console.log(registered.token)' not in register['code']
    assert 'window.__auGentDeviceBridge' in register['code']


def test_v2_builtin_set_is_migrated_to_current():
    redis = MemoryRedis({KEY: json.dumps(_V2_DEFAULT_SNIPPETS, ensure_ascii=False)})
    ids = [item['id'] for item in client_for(redis).get('/api/plugins/snippets').json()['snippets']]
    assert ids == ['register', 'list', 'ping', 'notify', 'exec', 'revoke', 'revoke_offline', 'forget']
    assert json.loads(redis.values[KEY]) == DEFAULT_SNIPPETS
    assert redis.values[VERSION_KEY] == '3'


def test_edited_v2_list_is_preserved():
    edited = copy.deepcopy(_V2_DEFAULT_SNIPPETS)
    edited[0]['title'] = 'Мой заголовок'
    raw = json.dumps(edited, ensure_ascii=False)
    redis = MemoryRedis({KEY: raw})
    client_for(redis).get('/api/plugins/snippets')
    assert redis.values[KEY] == raw


def test_custom_set_is_preserved_during_version_migration():
    custom = [
        {'id': 'my-tool', 'title': 'My tool', 'description': 'Keep me', 'code': 'console.log("custom")'},
    ]
    redis = MemoryRedis({KEY: json.dumps(custom), VERSION_KEY: '1'})
    response = client_for(redis).get('/api/plugins/snippets')

    assert response.status_code == 200
    assert response.json()['snippets'] == custom
    assert json.loads(redis.values[KEY]) == custom
    assert redis.values[VERSION_KEY] == '1'  # existing user marker is not overwritten


def test_legacy_snapshot_matches_pre_v2_default():
    import hashlib
    canonical = json.dumps(_LEGACY_DEFAULT_SNIPPETS, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == 'dde9b1aaa9b4e83efac3cf1bf4063fe0de8542d7993ccfb91814b401149f6a49'


def test_edited_legacy_list_is_not_a_default():
    custom = copy.deepcopy(_LEGACY_DEFAULT_SNIPPETS)
    custom[0]['description'] += ' Custom instructions.'
    raw = json.dumps(custom)
    redis = MemoryRedis({KEY: raw})
    assert client_for(redis).get('/api/plugins/snippets').json()['snippets'] == custom
    assert redis.values == {KEY: raw}


def test_empty_custom_list_is_preserved():
    redis = MemoryRedis({KEY: '[]'})
    assert client_for(redis).get('/api/plugins/snippets').json()['snippets'] == []
    assert redis.values == {KEY: '[]'}


def test_concurrent_custom_edit_during_migration_is_preserved():
    custom = [{'id': 'custom', 'title': 'Custom', 'description': '', 'code': 'void 0'}]

    class EditingRedis(MemoryRedis):
        async def eval(self, *args):
            self.values[KEY] = json.dumps(custom)
            return await super().eval(*args)

    redis = EditingRedis({KEY: json.dumps(_LEGACY_DEFAULT_SNIPPETS)})
    assert client_for(redis).get('/api/plugins/snippets').json()['snippets'] == custom
    assert json.loads(redis.values[KEY]) == custom
    assert VERSION_KEY not in redis.values


def test_concurrent_custom_initialization_is_preserved():
    custom = [{'id': 'custom', 'title': 'Custom', 'description': '', 'code': 'void 0'}]

    class EditingRedis(MemoryRedis):
        async def eval(self, *args):
            self.values[KEY] = json.dumps(custom)
            return await super().eval(*args)

    redis = EditingRedis()
    assert client_for(redis).get('/api/plugins/snippets').json()['snippets'] == custom
    assert json.loads(redis.values[KEY]) == custom


def test_invalid_value_is_not_overwritten():
    for raw in ('not json', '{}', '[{"id":"incomplete"}]'):
        redis = MemoryRedis({KEY: raw})
        assert client_for(redis).get('/api/plugins/snippets').json()['snippets'] == DEFAULT_SNIPPETS
        assert redis.values == {KEY: raw}


def test_redis_failure_returns_defaults_without_leaking_exception():
    class BrokenRedis(MemoryRedis):
        async def get(self, key):
            raise RuntimeError('secret credential')

    response = client_for(BrokenRedis()).get('/api/plugins/snippets')
    assert response.status_code == 200
    assert response.json()['snippets'] == DEFAULT_SNIPPETS
    assert 'secret credential' not in response.text


def test_auth_failure_never_reads_or_migrates_redis():
    redis = MemoryRedis({KEY: json.dumps(_LEGACY_DEFAULT_SNIPPETS)})
    before = dict(redis.values)
    assert client_for(redis, user=False).get('/api/plugins/snippets').status_code == 401
    assert redis.values == before


def test_snippets_in_javascript_runtime():
    """Run the exact Python-generated JS in persistent contexts (repeat pastes)."""
    import shutil
    import subprocess
    import pytest

    if not shutil.which('node'):
        pytest.skip('node is not installed')
    script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
let input = '';
process.stdin.on('data', d => input += d);
process.stdin.on('end', async () => {
  const snippets = Object.fromEntries(JSON.parse(input).map(s => [s.id, s.code]));
  function setup({clipboard = false, storage = true, promptThrows = false, platform = 'Win32',
    status = 200, confirmAnswer = true, devices = [
    {device_id:'dev-one',name:'Desktop',status:'online',capabilities:['exec']}
  ]} = {}) {
    const requests = [], logs = [], prompts = [], values = {}, copies = [];
    const context = vm.createContext({
      console: Object.fromEntries(['log','info','error','table'].map(k => [k, (...a) => logs.push(a)])),
      navigator: clipboard ? {platform, clipboard:{writeText: async t => copies.push(t)}} : {platform},
      sessionStorage: {
        getItem(k) { if (!storage) throw Error('denied'); return values[k] || null; },
        setItem(k,v) { if (!storage) throw Error('denied'); values[k] = v; },
        removeItem(k) { if (!storage) throw Error('denied'); delete values[k]; }
      },
      prompt: (label,value) => { prompts.push([label,value]); if (promptThrows) throw Error('blocked');
        return label.startsWith('Имя') ? value : '1'; },
      confirm: () => confirmAnswer,
      fetch: async (path, options = {}) => {
        requests.push([path, options]);
        const result = path === '/api/devices' ? (options.method === 'POST'
          ? {device_id:'registered',token:'SUPER_SECRET_TOKEN'} : {devices}) : {output:'pong'};
        if (status !== 200 && !(path === '/api/devices' && !options.method)) {
          return {ok:false, status, json: async () => ({detail:'SERVER_BODY_SECRET'})};
        }
        return {ok:true, status:200, json: async () => result};
      }
    });
    context.window = context;
    return {context, requests, logs, prompts, values, copies,
      run: id => vm.runInContext(snippets[id], context)};
  }
  for (const options of [{}, {clipboard:true}, {storage:false}, {promptThrows:true}, {storage:false,promptThrows:true}]) {
    const s = setup({...options, devices:[{device_id:'registered',name:'Desktop',status:'online'}]});
    // Pre-existing global lexical names must not interfere with any paste.
    vm.runInContext('const deviceId = "external"; const state = {}; const registered = {};', s.context);
    await s.run('register'); await s.run('register');
    assert.equal(s.context.__auGentDeviceBridge.deviceId, 'registered');
    assert.equal(s.context.__auGentDeviceBridge.token, 'SUPER_SECRET_TOKEN');
    if (options.storage !== false) {
      assert.equal(s.values['augent.device_id'], 'registered');
      assert.equal(s.values['augent.device_token'], 'SUPER_SECRET_TOKEN');
    }
    assert(!JSON.stringify(s.logs).includes('SUPER_SECRET_TOKEN'));
    assert.equal(s.copies.length, options.clipboard ? 2 : 0);
    if (!options.clipboard) assert(s.prompts.some(p => p[1] === 'SUPER_SECRET_TOKEN'));
    const created = s.requests.filter(([u, o]) => u === '/api/devices' && o.method === 'POST');
    assert.equal(created.length, 2);
    assert.deepEqual(JSON.parse(created[0][1].body), {name: 'Windows desktop', platform: 'windows'});
    for (const id of ['ping','notify','exec']) { await s.run(id); await s.run(id); }
    assert(s.requests.every(([url]) => !url.includes('undefined')));
    await s.run('revoke');
    assert.equal(s.context.__auGentDeviceBridge.deviceId, null);
    assert.equal(s.context.__auGentDeviceBridge.token, null);
    if (options.storage !== false) assert.equal(s.values['augent.device_token'], undefined);
  }
  for (const id of ['list','ping','notify','exec','revoke']) {
    const s = setup();
    await s.run(id); await s.run(id);
    assert(!s.logs.some(row => row[0] === 'Не удалось выполнить скрипт:'));
    assert(s.requests.every(([url]) => !url.includes('undefined')));
    for (const [, opts] of s.requests) {
      assert.equal(opts.credentials, 'same-origin');
      assert.equal(opts.headers['X-Device-Bridge'], '1');
    }
  }
  const empty = setup({devices:[]}); await empty.run('ping');
  assert.equal(empty.requests.length, 1); // Never command /undefined.
  const multi = setup({devices:[{device_id:'one',status:'online'}, {device_id:'two',status:'online'}]});
  await multi.run('notify'); assert.equal(multi.prompts.length, 1);
  assert(multi.requests.at(-1)[0].includes('/one/'));
  const cancelled = setup({devices:[{device_id:'one'}, {device_id:'two'}]});
  cancelled.context.prompt = () => null;
  await cancelled.run('exec'); assert.equal(cancelled.requests.length,1);
  const restored = setup(); restored.values['augent.device_id'] = 'restored';
  await restored.run('ping'); assert.equal(restored.requests[0][0], '/api/devices/restored/commands');
  // Register on macOS/Linux; repeated register declined keeps the first device.
  const mac = setup({platform:'MacIntel'}); await mac.run('register');
  assert.equal(JSON.parse(mac.requests[0][1].body).platform, 'macos');
  const lin = setup({platform:'Linux x86_64'}); await lin.run('register');
  assert.equal(JSON.parse(lin.requests[0][1].body).platform, 'linux');
  const keep = setup(); await keep.run('register'); keep.context.confirm = () => false;
  keep.prompts.length = 0; await keep.run('register');
  assert.equal(keep.requests.filter(([, o]) => o.method === 'POST').length, 1);
  assert.equal(keep.prompts[0][1], 'SUPER_SECRET_TOKEN'); // Cancel re-shows the token.
  assert(!JSON.stringify(keep.logs).includes('SUPER_SECRET_TOKEN'));
  // Revoking a different (offline) device keeps this tab's device and token.
  const other = setup({devices:[{device_id:'registered',status:'online'},{device_id:'old',status:'offline'}]});
  await other.run('register');
  other.context.prompt = () => '2'; await other.run('revoke');
  assert.equal(other.requests.at(-1)[0], '/api/devices/old');
  assert.equal(other.context.__auGentDeviceBridge.deviceId, 'registered');
  assert.equal(other.context.__auGentDeviceBridge.token, 'SUPER_SECRET_TOKEN');
  // Revoke lists offline devices too while one is online.
  const mixed = setup({devices:[{device_id:'on',name:'A',status:'online'},{device_id:'off',name:'B',status:'offline'}]});
  mixed.context.prompt = (label) => { mixed.prompts.push([label]); return '2'; };
  await mixed.run('revoke');
  assert(mixed.prompts[0][0].includes('[offline]'));
  assert.equal(mixed.requests.at(-1)[0], '/api/devices/off');
  assert.equal(mixed.requests.at(-1)[1].method, 'DELETE');
  // Bulk offline cleanup never touches online devices.
  const bulk = setup({devices:[{device_id:'on',status:'online'},{device_id:'x1',status:'offline'},
    {device_id:'x2',status:'offline'},{device_id:'gone',status:'revoked'}]});
  await bulk.run('revoke_offline');
  assert.deepEqual(bulk.requests.filter(([, o]) => o.method === 'DELETE').map(([u]) => u),
    ['/api/devices/x1', '/api/devices/x2']);
  const declined = setup({confirmAnswer:false, devices:[{device_id:'x1',status:'offline'}]});
  await declined.run('revoke_offline');
  assert(!declined.requests.some(([, o]) => o.method === 'DELETE'));
  // Forget drops the token but keeps the selected device.
  const fg = setup(); await fg.run('register'); await fg.run('forget');
  assert.equal(fg.context.__auGentDeviceBridge.token, null);
  assert.equal(fg.context.__auGentDeviceBridge.deviceId, 'registered');
  assert.equal(fg.values['augent.device_token'], undefined);
  assert.equal(fg.values['augent.device_id'], 'registered');
  // Errors are specific, but server bodies never reach the console.
  const busy = setup({status:409}); await busy.run('exec');
  const err = busy.logs.find(r => r[0] === 'Не удалось выполнить скрипт:');
  assert(err && err[1].includes('409') && err[1].includes('выключена в клиенте'), JSON.stringify(busy.logs));
  assert(!JSON.stringify(busy.logs).includes('SERVER_BODY_SECRET'));
  const none = setup({devices:[]}); await none.run('ping');
  assert(none.logs.some(r => r[0] === 'Не удалось выполнить скрипт:' && r[1] === 'Нет устройств'));
  console.log('All snippets: repeated pastes, clipboard/prompt/storage failures, selection, revoke passed');
}).on('error', e => { console.error(e); process.exitCode = 1; });
'''
    result = subprocess.run(['node', '-e', script], input=json.dumps(DEFAULT_SNIPPETS), text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
