"""Regression: start-windows.ps1 runs desktop.py as a script (no package)."""
import os
import subprocess
import sys
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1]


def test_file_transport_works_without_package():
    code = (
        "import transfers\n"
        "t = transfers.FileTransport('wss://example.com/api/devices/connect', None)\n"
        "assert t.origin.hostname == 'example.com'\n"
        "assert transfers._user_agent().startswith('AuGent-Device-Bridge/')\n"
    )
    env = dict(os.environ, PYTHONPATH='')
    r = subprocess.run([sys.executable, '-c', code], cwd=CLIENT, env=env,
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
