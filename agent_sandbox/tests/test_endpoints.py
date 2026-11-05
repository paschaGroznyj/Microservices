"""HTTP endpoints via FastAPI TestClient."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(appmod, ws_root):
    return TestClient(appmod.app)


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_execute_python_runs(client):
    r = client.post("/execute", json={"code": "print('hi from exec')"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "hi from exec" in body["stdout"]


def test_execute_requires_code_or_commands(client):
    r = client.post("/execute", json={"is_shell": False})
    assert r.status_code == 400


def test_execute_shell_requires_commands(client):
    r = client.post("/execute", json={"is_shell": True})
    assert r.status_code == 400


def test_execute_code_too_large(client, appmod, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_CODE_SIZE", 10)
    r = client.post("/execute", json={"code": "x" * 100})
    assert r.status_code == 413


def test_execute_unknown_workspace_404(client):
    r = client.post("/execute", json={"code": "print(1)", "workspace_id": "nope"})
    assert r.status_code == 404


def test_workspace_create_and_list(client, appmod):
    r = client.post("/workspace/create")
    assert r.status_code == 200
    wid = r.json()["workspace_id"]
    # new workspace -> empty or only internal files
    r2 = client.get(f"/workspace/{wid}/files")
    assert r2.status_code == 200
