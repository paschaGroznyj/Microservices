import copy
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gw_shared.plugins_gw import (
    DEFAULT_SNIPPETS,
    KEY,
    VERSION_KEY,
    _LEGACY_DEFAULT_SNIPPETS,
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
    assert redis.values[VERSION_KEY] == '2'
    assert DEFAULT_SNIPPETS == original


def test_exact_legacy_builtin_set_is_migrated():
    redis = MemoryRedis({KEY: json.dumps(_LEGACY_DEFAULT_SNIPPETS, ensure_ascii=False)})
    response = client_for(redis).get('/api/plugins/snippets')

    assert response.status_code == 200
    assert json.loads(redis.values[KEY]) == DEFAULT_SNIPPETS
    assert redis.values[VERSION_KEY] == '2'
    register = next(item for item in response.json()['snippets'] if item['id'] == 'register')
    assert 'copy(registered.token)' not in register['code']
    assert 'console.log(registered.token)' not in register['code']
    assert 'window.__auGentDeviceBridge' in register['code']


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
  function setup({clipboard = false, storage = true, promptThrows = false, devices = [
    {device_id:'dev-one',name:'Desktop',status:'online',capabilities:['exec']}
  ]} = {}) {
    const requests = [], logs = [], prompts = [], values = {}, copies = [];
    const context = vm.createContext({
      console: Object.fromEntries(['log','info','error','table'].map(k => [k, (...a) => logs.push(a)])),
      navigator: clipboard ? {clipboard:{writeText: async t => copies.push(t)}} : {},
      sessionStorage: {
        getItem(k) { if (!storage) throw Error('denied'); return values[k] || null; },
        setItem(k,v) { if (!storage) throw Error('denied'); values[k] = v; },
        removeItem(k) { if (!storage) throw Error('denied'); delete values[k]; }
      },
      prompt: (label,value) => { prompts.push([label,value]); if (promptThrows) throw Error('blocked'); return '1'; },
      confirm: () => true,
      fetch: async (path, options = {}) => {
        requests.push([path, options]);
        const result = path === '/api/devices' ? (options.method === 'POST'
          ? {device_id:'registered',token:'SUPER_SECRET_TOKEN'} : {devices}) : {output:'pong'};
        return {ok:true, json: async () => result};
      }
    });
    context.window = context;
    return {context, requests, logs, prompts, values, copies,
      run: id => vm.runInContext(snippets[id], context)};
  }
  for (const options of [{}, {clipboard:true}, {storage:false}, {promptThrows:true}, {storage:false,promptThrows:true}]) {
    const s = setup(options);
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
    if (!options.clipboard) assert.equal(s.prompts[0][1], 'SUPER_SECRET_TOKEN');
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
    assert(!s.logs.some(row => row[0] === 'Не удалось выполнить скрипт. Проверьте вход, выбор устройства и доступ клиента.'));
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
  console.log('All snippets: repeated pastes, clipboard/prompt/storage failures, selection, revoke passed');
}).on('error', e => { console.error(e); process.exitCode = 1; });
'''
    result = subprocess.run(['node', '-e', script], input=json.dumps(DEFAULT_SNIPPETS), text=True,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
