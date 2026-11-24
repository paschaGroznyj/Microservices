"""Local shell execution for Device Bridge (operation "exec").

The gateway relays shell text verbatim. This module is the only place that
decides whether and where a command runs on this computer.

Config file (JSON, created with defaults on first start):
* Windows: %APPDATA%\\AuGent\\DeviceBridge\\config.json
* macOS:   ~/Library/Application Support/AuGent/DeviceBridge/config.json
* Linux:   $XDG_CONFIG_HOME/augent-device-bridge/config.json (~/.config/...)
Override with the DEVICE_BRIDGE_CONFIG environment variable.

Keys:
* exec_enabled  - false by default; the desktop toggle writes it.
* mode          - "workspace": cwd must be inside workspace_dir;
                  "full": any cwd except blocked paths.
* workspace_dir - default working folder (created if missing).
* blocked_paths - folders that cwd may not be inside and that the command text
                  may not reference. Defaults protect system folders, keys and
                  this config folder. Empty list = defaults; edit to change.
* shell         - "auto" | "powershell" | "cmd" | "bash" | "zsh" | "sh".
* max_timeout   - upper bound in seconds for one command (1..300).

Limit of the path policy: a shell command is arbitrary code. Checking cwd and
paths written in the command text stops ordinary mistakes and direct
references, not obfuscated ones (computed paths, scripts, encoded strings).
For a hard boundary run the client as a separate OS user without rights to
protected folders. Every command is appended to exec-audit.log next to the
config (command, cwd, exit code; never output).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_STREAM_BYTES = 60 * 1024   # gateway accepts 64 KiB per stream
MAX_COMMAND = 16000
HARD_MAX_TIMEOUT = 300
MAX_PARALLEL = 2
AUDIT_MAX_BYTES = 1024 * 1024
SHELLS = ('auto', 'powershell', 'cmd', 'bash', 'zsh', 'sh')
IS_WINDOWS = sys.platform == 'win32'


class PolicyError(Exception):
    """Refusal with a fixed, user-facing message."""


def config_path() -> Path:
    override = os.environ.get('DEVICE_BRIDGE_CONFIG')
    if override:
        return Path(override)
    if IS_WINDOWS:
        base = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
        return Path(base) / 'AuGent' / 'DeviceBridge' / 'config.json'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'AuGent' / 'DeviceBridge' / 'config.json'
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'augent-device-bridge' / 'config.json'


def default_workspace() -> Path:
    return Path.home() / 'AuGent-Workspace'


def default_blocked_paths(cfg_dir: Path) -> list[str]:
    home = Path.home()
    common = [str(cfg_dir), str(home / '.ssh'), str(home / '.gnupg'), str(home / '.aws'),
              str(home / '.kube'), str(home / '.docker')]
    if IS_WINDOWS:
        drive = os.environ.get('SystemDrive', 'C:')
        env = os.environ.get
        return [env('WINDIR', drive + '\\Windows'),
                env('ProgramFiles', drive + '\\Program Files'),
                env('ProgramFiles(x86)', drive + '\\Program Files (x86)'),
                env('ProgramData', drive + '\\ProgramData'),
                drive + '\\Recovery', drive + '\\System Volume Information',
                drive + '\\$Recycle.Bin',
                str(home / 'AppData' / 'Roaming' / 'Microsoft' / 'Credentials'),
                str(home / 'AppData' / 'Roaming' / 'Microsoft' / 'Protect'),
                str(home / 'AppData' / 'Local' / 'Microsoft' / 'Credentials')] + common
    if sys.platform == 'darwin':
        return ['/System', '/Library', '/bin', '/sbin', '/usr', '/etc', '/private/etc',
                '/private/var/db', '/Applications', str(home / 'Library' / 'Keychains')] + common
    return ['/bin', '/sbin', '/boot', '/dev', '/etc', '/lib', '/lib32', '/lib64', '/proc',
            '/root', '/sys', '/usr', '/var/lib', '/snap'] + common


def _default_config(cfg_dir: Path) -> dict:
    return {
        'exec_enabled': False,
        'mode': 'workspace',
        'workspace_dir': str(default_workspace()),
        'blocked_paths': default_blocked_paths(cfg_dir),
        'shell': 'auto',
        'max_timeout': 120,
    }


class ExecConfig:
    """Loaded/validated config; save() persists the toggle atomically."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_path()
        self.data = self._load()

    def _load(self) -> dict:
        defaults = _default_config(self.path.parent)
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict):
                raise ValueError
        except FileNotFoundError:
            raw = {}
            self.data = defaults
            self.save()
        except (OSError, ValueError):
            # A broken file never silently enables exec.
            raw = {'exec_enabled': False}
            self.load_error = True
        data = dict(defaults)
        for key in defaults:
            if key in raw:
                data[key] = raw[key]
        if type(data['exec_enabled']) is not bool:
            data['exec_enabled'] = False
        if data['mode'] not in ('workspace', 'full'):
            data['mode'] = 'workspace'
        if data['shell'] not in SHELLS:
            data['shell'] = 'auto'
        if (type(data['max_timeout']) is not int
                or not 1 <= data['max_timeout'] <= HARD_MAX_TIMEOUT):
            data['max_timeout'] = 120
        if not isinstance(data['workspace_dir'], str) or not data['workspace_dir'].strip():
            data['workspace_dir'] = str(default_workspace())
        blocked = data['blocked_paths']
        if (not isinstance(blocked, list) or not blocked
                or any(not isinstance(b, str) or not b.strip() for b in blocked)):
            data['blocked_paths'] = defaults['blocked_paths']
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, self.path)

    @property
    def exec_enabled(self) -> bool:
        return self.data['exec_enabled']

    def set_exec_enabled(self, value: bool) -> None:
        self.data['exec_enabled'] = bool(value)
        self.save()

    @property
    def workspace(self) -> Path:
        return _real(self.data['workspace_dir'])

    @property
    def blocked(self) -> list[Path]:
        return [_real(p) for p in self.data['blocked_paths']]


def _real(value: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(value).strip()))
    return Path(os.path.realpath(expanded))


def _norm(path: Path) -> str:
    text = os.path.normcase(str(path))
    return text.rstrip('\\/') or text


def is_inside(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    if c == p:
        return True
    sep = '\\' if IS_WINDOWS else '/'
    return c.startswith(p.rstrip(sep) + sep)


_PATH_TOKEN = re.compile(r'''(?:[A-Za-z]:[\\/]|\\\\|~[\\/]|/|%[A-Za-z_]+%|\$env:[A-Za-z_]+|\$[A-Za-z_]+|\.\.)[^\s'"`;|&<>(){}]*''')


def referenced_blocked_path(command: str, cwd: Path, blocked: list[Path]) -> Path | None:
    """Best-effort: absolute/relative paths written in the command text."""
    for match in _PATH_TOKEN.finditer(command):
        token = match.group(0).rstrip('.,:')
        if not token:
            continue
        expanded = token
        if IS_WINDOWS:
            expanded = re.sub(r'\$env:([A-Za-z_]+)', r'%\1%', expanded)
        candidate = Path(os.path.expandvars(os.path.expanduser(expanded)))
        if not candidate.is_absolute():
            candidate = cwd / candidate
        real = Path(os.path.realpath(str(candidate)))
        for b in blocked:
            if is_inside(real, b):
                return b
    return None


def resolve_shell(choice: str) -> tuple[str, list[str]]:
    """Return (shell name, argv prefix). The command text is appended."""
    if choice == 'auto':
        if IS_WINDOWS:
            choice = 'powershell'
        else:
            login = os.path.basename(os.environ.get('SHELL', ''))
            choice = login if login in ('bash', 'zsh', 'sh') else ('zsh' if sys.platform == 'darwin' else 'bash')
    if choice == 'powershell':
        exe = shutil.which('pwsh') or shutil.which('powershell') or 'powershell'
        return 'powershell', [exe, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                              '-EncodedCommand']
    if choice == 'cmd':
        return 'cmd', [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c']
    exe = shutil.which(choice) or shutil.which('sh') or '/bin/sh'
    return choice, [exe, '-c']


def _ps_encode(command: str) -> str:
    # UTF-8 console output, then the user command. EncodedCommand avoids quoting.
    prefix = ('$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);'
              '$ProgressPreference="SilentlyContinue";')
    return base64.b64encode((prefix + command).encode('utf-16-le')).decode('ascii')


def _decode(data: bytes) -> str:
    for enc in ('utf-8', 'cp866' if IS_WINDOWS else 'latin-1'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


def _clip(text: str) -> tuple[str, bool]:
    raw = text.encode('utf-8')
    if len(raw) <= MAX_STREAM_BYTES:
        return text, False
    return raw[:MAX_STREAM_BYTES].decode('utf-8', 'ignore') + '\n[output truncated]', True


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class Executor:
    """Runs one shell command under the local policy. Thread-safe."""

    def __init__(self, config: ExecConfig):
        self.config = config
        self._slots = threading.BoundedSemaphore(MAX_PARALLEL)
        self._audit_lock = threading.Lock()

    def shell_name(self) -> str:
        return resolve_shell(self.config.data['shell'])[0]

    def check(self, command, cwd_text) -> Path:
        cfg = self.config
        if not cfg.exec_enabled:
            raise PolicyError('Command execution is disabled on this device')
        if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND or '\x00' in command:
            raise PolicyError('Invalid command')
        workspace = cfg.workspace
        workspace.mkdir(parents=True, exist_ok=True)
        if cwd_text is None:
            cwd = workspace
        else:
            if not isinstance(cwd_text, str) or not cwd_text.strip() or '\x00' in cwd_text:
                raise PolicyError('Invalid cwd')
            raw = Path(os.path.expandvars(os.path.expanduser(cwd_text.strip())))
            cwd = Path(os.path.realpath(str(raw if raw.is_absolute() else workspace / raw)))
        if cfg.data['mode'] == 'workspace' and not is_inside(cwd, workspace):
            raise PolicyError('cwd is outside the workspace folder ' + str(workspace))
        blocked = cfg.blocked
        for b in blocked:
            if is_inside(cwd, b):
                raise PolicyError('cwd is inside a protected folder ' + str(b))
        hit = referenced_blocked_path(command, cwd, blocked)
        if hit is not None:
            raise PolicyError('Command references a protected folder ' + str(hit))
        if not cwd.is_dir():
            raise PolicyError('cwd does not exist: ' + str(cwd))
        return cwd

    def run(self, command, cwd_text=None, timeout=30) -> dict:
        """Return a result dict: status, output, stderr, exit_code, truncated, duration_ms."""
        started = time.monotonic()
        try:
            cwd = self.check(command, cwd_text)
        except PolicyError as exc:
            self._audit(command, cwd_text, 'refused', str(exc))
            return {'status': 'error', 'output': '', 'stderr': str(exc), 'exit_code': 126,
                    'truncated': False, 'duration_ms': 0}
        limit = max(1, min(int(timeout), self.config.data['max_timeout'], HARD_MAX_TIMEOUT))
        if not self._slots.acquire(blocking=False):
            return {'status': 'error', 'output': '', 'stderr': 'Too many commands running on this device',
                    'exit_code': 125, 'truncated': False, 'duration_ms': 0}
        try:
            name, prefix = resolve_shell(self.config.data['shell'])
            argv = prefix + [_ps_encode(command) if name == 'powershell' else command]
            kwargs = {}
            if IS_WINDOWS:
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs['start_new_session'] = True
            env = dict(os.environ)
            env.pop('DEVICE_BRIDGE_TOKEN', None)
            env['PYTHONIOENCODING'] = 'utf-8'
            try:
                proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, **kwargs)
            except OSError as exc:
                msg = 'Shell start failed: ' + type(exc).__name__
                self._audit(command, str(cwd), 'error', msg)
                return {'status': 'error', 'output': '', 'stderr': msg, 'exit_code': 127,
                        'truncated': False, 'duration_ms': int((time.monotonic() - started) * 1000)}
            timed_out = False
            try:
                out, err = proc.communicate(timeout=limit)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                try:
                    out, err = proc.communicate(timeout=5)
                except Exception:
                    out, err = b'', b''
            stdout, t1 = _clip(_decode(out or b''))
            stderr, t2 = _clip(_decode(err or b''))
            code = proc.returncode if proc.returncode is not None else -1
            if timed_out:
                stderr = (stderr + '\n' if stderr else '') + f'Timed out after {limit}s; process tree killed'
                code = 124
            status = 'ok' if code == 0 and not timed_out else 'error'
            self._audit(command, str(cwd), status, f'exit {code}')
            return {'status': status, 'output': stdout, 'stderr': stderr, 'exit_code': int(code),
                    'truncated': t1 or t2, 'duration_ms': int((time.monotonic() - started) * 1000)}
        finally:
            self._slots.release()

    def _audit(self, command, cwd, status, detail) -> None:
        line = json.dumps({'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'status': status,
                           'cwd': str(cwd) if cwd is not None else None,
                           'command': str(command)[:2000], 'detail': detail}, ensure_ascii=False)
        path = self.config.path.parent / 'exec-audit.log'
        with self._audit_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists() and path.stat().st_size > AUDIT_MAX_BYTES:
                    os.replace(path, path.with_suffix('.log.1'))
                with open(path, 'a', encoding='utf-8') as fh:
                    fh.write(line + '\n')
            except OSError:
                pass
