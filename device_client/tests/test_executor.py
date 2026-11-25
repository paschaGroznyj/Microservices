import json
import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'

import ntpath
import sys
import time
import uuid
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from device_client import core, executor
from device_client.executor import ExecConfig, Executor, is_inside

posix_only = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX shell assertions')


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    if sys.platform == 'win32':
        pytest.skip('POSIX shell assertions')
    path = tmp_path / 'conf' / 'config.json'
    c = ExecConfig(path)
    c.data['workspace_dir'] = str(tmp_path / 'ws')
    c.data['blocked_paths'] = c.data['blocked_paths'] + [str(tmp_path / 'secret')]
    c.data['shell'] = 'sh'
    c.save()
    (tmp_path / 'secret').mkdir()
    return ExecConfig(path)


@posix_only
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


class WindowsEnvironment(dict):
    def __getitem__(self, key):
        return super().__getitem__(key.upper())

    def get(self, key, default=None):
        return super().get(key.upper(), default)


@pytest.fixture
def windows_policy(monkeypatch):
    home = r'C:\Users\Alice'
    values = {
        'USERPROFILE': home,
        'SystemDrive': 'C:',
        'SystemRoot': r'C:\Windows',
        'WINDIR': r'C:\Windows',
        'ProgramFiles': r'C:\Program Files',
        'ProgramFiles(x86)': r'C:\Program Files (x86)',
        'ProgramData': r'C:\ProgramData',
        'APPDATA': home + r'\AppData\Roaming',
        'LOCALAPPDATA': home + r'\AppData\Local',
        'P': r'C:\Program Files',
        'BOUNDS': r'C:\Program Files',
        'BITMAP': r'C:\Windows',
        'SYSTEM': r'C:\Windows',
        'DEVICE_SECRET_2': home + r'\.ssh',
        'ProgramFilesX': home + r'\AuGent-Workspace',
    }
    environ = WindowsEnvironment({key.upper(): value for key, value in values.items()})
    monkeypatch.setattr(os, 'environ', environ)
    monkeypatch.setattr(executor, 'os', SimpleNamespace(path=ntpath, environ=environ))
    monkeypatch.setattr(executor, 'IS_WINDOWS', True)
    monkeypatch.setattr(executor, 'Path', PureWindowsPath)
    blocked = [PureWindowsPath(values[key]) for key in
               ('SystemRoot', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData')]
    blocked += [PureWindowsPath(home) / name for name in
                ('.ssh', '.gnupg', '.aws', '.kube', '.docker',
                 r'AppData\Roaming\Microsoft\Credentials',
                 r'AppData\Roaming\Microsoft\Protect',
                 r'AppData\Local\Microsoft\Credentials',
                 r'AppData\Roaming\AuGent\DeviceBridge')]
    return SimpleNamespace(cwd=PureWindowsPath(home) / 'AuGent-Workspace', blocked=blocked)


def test_windows_environment_simulation_is_case_insensitive(windows_policy):
    assert ntpath.expandvars('$p') == r'C:\Program Files'
    assert ntpath.expandvars('$bounds') == r'C:\Program Files'
    assert ntpath.expandvars('%sYsTeMrOoT%') == r'C:\Windows'
    assert ntpath.expandvars('$env:ProgramFilesX') == '$env:ProgramFilesX'


def test_windows_screenshot_variables_are_not_environment_paths(windows_policy):
    command = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width,$bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location,[System.Drawing.Point]::Empty,$bounds.Size)
$p = Join-Path (Get-Location) 'screenshot.png'
$bitmap.Save($p,[System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose(); $bitmap.Dispose()
"""
    assert executor.referenced_blocked_path(command, windows_policy.cwd, windows_policy.blocked) is None


@pytest.mark.parametrize('command', [
    '$p', '$P', '$bounds', '$bitmap', '$System', '${p}', '${System}',
    '$p = 1; Write-Output $p', r'Write-Output "$p\screenshot.png"',
    '$env:ProgramFilesX', r'$env:ProgramFilesX\screenshot.png',
    '${env:ProgramFilesX}', '%ProgramFilesX%',
    '$env:ProgramFilesMissing', '$env:SystemRootMissing', '$env:DEVICE_SECRET_20',
    '${env:DEVICE_SECRET_20}', '%DEVICE_SECRET_20%',
    r'Get-Item C:\WindowsBackup\safe.txt',
    r'Get-Item "C:\Program FilesBackup\safe.txt"',
    r'Get-Item C:\Users\Alice\.ssh-backup\safe.txt',
])
def test_windows_variables_and_prefix_siblings_are_allowed(windows_policy, command):
    assert executor.referenced_blocked_path(command, windows_policy.cwd, windows_policy.blocked) is None


@pytest.mark.parametrize(('command', 'protected'), [
    (r'Get-Item C:\Windows\System32\config\SAM', r'C:\Windows'),
    (r'Get-Item "c:/WINDOWS/System32/config/SAM"', r'C:\Windows'),
    (r'Get-Item "C:\Program Files\app\data"', r'C:\Program Files'),
    (r"Get-Item 'C:\Program Files (x86)\app\data'", r'C:\Program Files (x86)'),
    (r'Get-Item C:\ProgramData\app\data', r'C:\ProgramData'),
    (r'Get-Item ..\.ssh\id_rsa', r'C:\Users\Alice\.ssh'),
    (r'Get-Item ~\.aws\credentials', r'C:\Users\Alice\.aws'),
    (r'Get-Item C:\Users\Alice\.gnupg\secring.gpg', r'C:\Users\Alice\.gnupg'),
    (r'Get-Item C:\Users\Alice\.kube\config', r'C:\Users\Alice\.kube'),
    (r'Get-Item C:\Users\Alice\.docker\config.json', r'C:\Users\Alice\.docker'),
    (r'Get-Item $env:APPDATA\Microsoft\Credentials\secret', r'C:\Users\Alice\AppData\Roaming\Microsoft\Credentials'),
    (r'Get-Item $env:APPDATA\Microsoft\Protect\secret', r'C:\Users\Alice\AppData\Roaming\Microsoft\Protect'),
    (r'Get-Item $env:LOCALAPPDATA\Microsoft\Credentials\secret', r'C:\Users\Alice\AppData\Local\Microsoft\Credentials'),
    (r'Get-Content $env:APPDATA\AuGent\DeviceBridge\config.json', r'C:\Users\Alice\AppData\Roaming\AuGent\DeviceBridge'),
    ('$env:ProgramFiles', r'C:\Program Files'),
    (r'$env:ProgramFiles\app\data', r'C:\Program Files'),
    ('$ENV:pRoGrAmFiLeS', r'C:\Program Files'),
    ('${env:ProgramFiles}', r'C:\Program Files'),
    (r'${EnV:ProgramFiles(x86)}\app\data', r'C:\Program Files (x86)'),
    ('%ProgramFiles(x86)%', r'C:\Program Files (x86)'),
    ('%pRoGrAmFiLeS%', r'C:\Program Files'),
    (r'$env:SystemRoot\System32\config\SAM', r'C:\Windows'),
    ('${ENV:SystemRoot}', r'C:\Windows'),
    (r'$env:SystemDrive\Windows\System32', r'C:\Windows'),
    (r'%SystemDrive%\Windows\System32', r'C:\Windows'),
    ('%SystemRoot%', r'C:\Windows'),
    ('$env:WINDIR', r'C:\Windows'),
    ('$env:P', r'C:\Program Files'),
    ('%P%', r'C:\Program Files'),
    ('$env:DEVICE_SECRET_2', r'C:\Users\Alice\.ssh'),
    (r'$env:DEVICE_SECRET_2\id_rsa', r'C:\Users\Alice\.ssh'),
    ('${env:DEVICE_SECRET_2}', r'C:\Users\Alice\.ssh'),
    ('%DEVICE_SECRET_2%', r'C:\Users\Alice\.ssh'),
    (r'$p = "C:\Windows\System32"', r'C:\Windows'),
    (r'$bounds = 1; Get-Content C:\Users\Alice\.ssh\id_rsa', r'C:\Users\Alice\.ssh'),
])
def test_windows_explicit_protected_paths_still_blocked(windows_policy, command, protected):
    assert executor.referenced_blocked_path(command, windows_policy.cwd, windows_policy.blocked) == PureWindowsPath(protected)


def test_posix_environment_path_expansion_preserved(cfg, tmp_path, monkeypatch):
    cfg.set_exec_enabled(True)
    monkeypatch.setenv('DEVICE_SECRET_2', str(tmp_path / 'secret'))
    for command in ('cat $DEVICE_SECRET_2/key', 'cat ../secret/key', 'cat ~/.ssh/id_rsa'):
        result = Executor(cfg).run(command)
        assert result['exit_code'] == 126, command
        assert 'protected' in result['stderr']


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
