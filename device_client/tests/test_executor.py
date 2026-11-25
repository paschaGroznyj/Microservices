import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from device_client import core
from device_client.executor import ExecConfig, Executor, is_inside

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX shell assertions')


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / 'conf' / 'config.json'
    c = ExecConfig(path)
    c.data['workspace_dir'] = str(tmp_path / 'ws')
    c.data['blocked_paths'] = c.data['blocked_paths'] + [str(tmp_path / 'secret')]
    c.data['shell'] = 'sh'
    c.save()
    (tmp_path / 'secret').mkdir()
    return ExecConfig(path)


def test_default_config_created_with_exec_off(tmp_path):
    path = tmp_path / 'c' / 'config.json'
    c = ExecConfig(path)
    assert path.exists()
    data = json.loads(path.read_text())
    assert data['exec_enabled'] is False and data['mode'] == 'workspace'
    assert str(path.parent) in data['blocked_paths']
    assert '/etc' in data['blocked_paths']


def test_broken_config_never_enables(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{"exec_enabled": tru')
    assert ExecConfig(path).exec_enabled is False
    path.write_text('{"exec_enabled": "yes"}')
    assert ExecConfig(path).exec_enabled is False


def test_disabled_refuses_without_running(cfg, tmp_path):
    marker = tmp_path / 'ws' / 'ran'
    r = Executor(cfg).run(f'touch {marker}')
    assert r['status'] == 'error' and r['exit_code'] == 126
    assert 'disabled' in r['stderr'] and not marker.exists()


def test_runs_in_workspace(cfg, tmp_path):
    cfg.set_exec_enabled(True)
    r = Executor(cfg).run('pwd; echo привет; echo err >&2; exit 3')
    assert r['exit_code'] == 3 and r['status'] == 'error'
    lines = r['output'].splitlines()
    assert Path(lines[0]).resolve() == (tmp_path / 'ws').resolve()
    assert lines[1] == 'привет' and r['stderr'].strip() == 'err'
    log = (cfg.path.parent / 'exec-audit.log').read_text()
    assert 'exit 3' in log and 'привет' in log


def test_workspace_mode_rejects_outside_cwd(cfg, tmp_path):
    cfg.set_exec_enabled(True)
    r = Executor(cfg).run('echo hi', cwd_text=str(tmp_path))
    assert r['exit_code'] == 126 and 'outside the workspace' in r['stderr']
    r = Executor(cfg).run('echo hi', cwd_text='..')
    assert r['exit_code'] == 126


def test_full_mode_allows_outside_but_not_blocked(cfg, tmp_path):
    cfg.set_exec_enabled(True)
    cfg.data['mode'] = 'full'
    ex = Executor(cfg)
    assert ex.run('echo hi', cwd_text=str(tmp_path))['output'].strip() == 'hi'
    r = ex.run('ls', cwd_text=str(tmp_path / 'secret'))
    assert r['exit_code'] == 126 and 'protected' in r['stderr']


def test_command_referencing_blocked_path_refused(cfg, tmp_path):
    cfg.set_exec_enabled(True)
    ex = Executor(cfg)
    for cmd in ['cat /etc/passwd', f'ls {tmp_path}/secret', 'cat ../secret/x',
                f'cat {cfg.path}', 'ls ~/.ssh']:
        r = ex.run(cmd)
        assert r['exit_code'] == 126, cmd
        assert 'protected' in r['stderr']


def test_symlink_into_blocked_resolved(cfg, tmp_path):
    cfg.set_exec_enabled(True)
    cfg.data['mode'] = 'full'
    link = tmp_path / 'ws' / 'lnk'
    (tmp_path / 'ws').mkdir(exist_ok=True)
    os.symlink(tmp_path / 'secret', link)
    r = Executor(cfg).run('ls', cwd_text=str(link))
    assert r['exit_code'] == 126


def test_timeout_kills_process_tree(cfg, tmp_path):
    cfg.set_exec_enabled(True)
    marker = tmp_path / 'ws' / 'late'
    started = time.monotonic()
    r = Executor(cfg).run(f'(sleep 3; touch {marker}) & sleep 30', timeout=1)
    assert time.monotonic() - started < 8
    assert r['exit_code'] == 124 and 'Timed out' in r['stderr']
    time.sleep(3.5)
    assert not marker.exists()


def test_output_truncated(cfg):
    cfg.set_exec_enabled(True)
    r = Executor(cfg).run("yes a | head -c 200000")
    assert r['truncated'] is True
    assert len(r['output'].encode()) <= 61 * 1024


def test_is_inside():
    assert is_inside(Path('/a/b/c'), Path('/a/b'))
    assert is_inside(Path('/a/b'), Path('/a/b'))
    assert not is_inside(Path('/a/bc'), Path('/a/b'))


# ---- protocol in BridgeClient -------------------------------------------------
SESSION = str(uuid.UUID(int=1)); DEVICE = str(uuid.UUID(int=2)); JOB = str(uuid.UUID(int=3))


class Frames:
    def __init__(self, frames, wait=0):
        self.frames = list(frames); self.sent = []; self.wait = wait

    def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline and not any(f.get('type') == 'result' for f in self.sent):
            time.sleep(0.02)
        raise KeyboardInterrupt

    def send(self, frame):
        self.sent.append(json.loads(frame))


def _hello():
    return dict(type='hello', session_id=SESSION, device_id=DEVICE, heartbeat_interval=5)


def test_hello_advertises_exec_only_when_enabled(cfg):
    ex = Executor(cfg)
    c = core.BridgeClient('http://127.0.0.1', 't' * 64, executor=ex)
    ws = Frames([_hello()])
    with pytest.raises(KeyboardInterrupt):
        c._session(ws)
    assert ws.sent[0]['capabilities'] == ['ping', 'notify']
    cfg.set_exec_enabled(True)
    ws = Frames([_hello()])
    with pytest.raises(KeyboardInterrupt):
        c._session(ws)
    assert ws.sent[0]['capabilities'] == ['ping', 'notify', 'exec', 'files']


def test_toggle_sends_capabilities_mid_session(cfg):
    c = core.BridgeClient('http://127.0.0.1', 't' * 64, executor=Executor(cfg))
    ws = Frames([])
    c._live = (ws, SESSION)
    c.set_exec_enabled(True)
    assert ws.sent == [{'type': 'capabilities', 'capabilities': ['ping', 'notify', 'exec', 'files']}]
    assert ExecConfig(cfg.path).exec_enabled is True


def test_exec_command_result_frame(cfg):
    cfg.set_exec_enabled(True)
    c = core.BridgeClient('http://127.0.0.1', 't' * 64, executor=Executor(cfg))
    ws = Frames([_hello(), dict(type='command', job_id=JOB, session_id=SESSION, deadline=time.time() + 60,
                                timeout=60, operation='exec', command='echo ok')], wait=10)
    with pytest.raises(KeyboardInterrupt):
        c._session(ws)
    res = [f for f in ws.sent if f.get('type') == 'result'][0]
    assert res['job_id'] == JOB and res['output'].strip() == 'ok' and res['exit_code'] == 0
    assert set(res) <= {'type', 'job_id', 'session_id', 'status', 'output', 'exit_code', 'stderr',
                        'truncated', 'duration_ms'}
