"""Fixtures for agent_sandbox tests.

app.py imports aiokafka and defines FastAPI app at module import; no required
env. We redirect WORKSPACES_DIR to a tmp dir per test so workspace/TTL helpers
operate on an isolated tree.
"""
import importlib

import pytest


@pytest.fixture
def appmod():
    import app as a
    importlib.reload(a)  # fresh module-level _tasks/_kafka_producer per test
    return a


@pytest.fixture
def ws_root(tmp_path, appmod, monkeypatch):
    """Point WORKSPACES_DIR at an isolated tmp dir."""
    root = tmp_path / "workspaces"
    root.mkdir()
    monkeypatch.setattr(appmod, "WORKSPACES_DIR", str(root))
    return root
