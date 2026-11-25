"""Ключи пользователей: шифрование (совместимость с agent_service) и эндпоинты /profile/secrets."""
import importlib.util
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

import user_secrets as us

ROOT = Path(__file__).resolve().parents[2]
KEY = "sk-" + "a1B2" * 8


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("USER_SECRETS_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(us, "_fernet", None)


def _agent_module():
    """agent_service/app/agent/core/user_secrets.py без пакета agent (config подменяем)."""
    import sys, types
    pkg = types.ModuleType("agentcore_t"); pkg.__path__ = []
    cfg = types.ModuleType("agentcore_t.config"); cfg.ROUTER_API_KEY = "env-key"
    sys.modules["agentcore_t"] = pkg; sys.modules["agentcore_t.config"] = cfg
    spec = importlib.util.spec_from_file_location(
        "agentcore_t.user_secrets", ROOT / "agent_service/app/agent/core/user_secrets.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_roundtrip_and_row_binding():
    tok = us.encrypt(7, "routerai", "default", KEY)
    assert KEY not in tok
    assert us.decrypt(7, "routerai", "default", tok) == KEY
    # шифротекст, перенесённый в строку другого юзера/провайдера, не читается
    assert us.decrypt(8, "routerai", "default", tok) is None
    assert us.decrypt(7, "github", "default", tok) is None
    assert us.decrypt(7, "routerai", "work", tok) is None
    assert us.decrypt(7, "routerai", "default", "garbage") is None


def test_agent_decrypts_what_auth_encrypts():
    agent = _agent_module()
    tok = us.encrypt(7, "routerai", "default", KEY)
    assert agent.decrypt(7, "routerai", "default", tok) == KEY
    assert agent.decrypt(8, "routerai", "default", tok) is None


def test_rotation(monkeypatch):
    old = Fernet.generate_key().decode()
    monkeypatch.setenv("USER_SECRETS_KEYS", old); us._fernet = None
    tok = us.encrypt(1, "routerai", "default", KEY)
    monkeypatch.setenv("USER_SECRETS_KEYS", Fernet.generate_key().decode() + "," + old); us._fernet = None
    assert us.decrypt(1, "routerai", "default", tok) == KEY


@pytest.mark.parametrize("secret,ok", [
    (KEY, True), ("short", False), ("sk-with space-xxxxxxxxxx", False),
    ("x" * 257, False), ("sk-кириллица-xxxxxxxxxx", False),
])
def test_validate(secret, ok):
    if ok:
        assert us.validate("routerai", secret) == secret
    else:
        with pytest.raises(us.SecretError):
            us.validate("routerai", secret)


def test_validate_unknown_provider():
    with pytest.raises(us.SecretError):
        us.validate("github", KEY)


def test_last4():
    assert us.last4(KEY) == KEY[-4:]
    assert us.last4("abc") == ""


def test_endpoints_never_return_secret(client, fake_db):
    fake_db.set_user_secret.return_value = {"provider": "routerai", "name": "default",
                                           "last4": KEY[-4:], "updated_at": None, "last_used_at": None}
    r = client.post("/profile/secrets/set", json={"user_id": 5, "provider": "routerai", "secret": KEY})
    assert r.status_code == 200 and KEY not in r.text
    fake_db.set_user_secret.assert_awaited_once_with(5, "routerai", KEY, "default")

    fake_db.set_user_secret.side_effect = us.SecretError("Неизвестный провайдер")
    r = client.post("/profile/secrets/set", json={"user_id": 5, "provider": "zzz", "secret": KEY})
    assert r.status_code == 400

    fake_db.list_user_secrets.return_value = []
    assert client.post("/profile/secrets/list", json={"user_id": 5}).json()["items"] == []
    fake_db.delete_user_secret.return_value = True
    assert client.post("/profile/secrets/delete", json={"user_id": 5, "provider": "routerai"}).json() == {"deleted": True}


def test_endpoints_validate_user_id(client):
    assert client.post("/profile/secrets/list", json={"user_id": "x"}).status_code == 400
    assert client.post("/profile/secrets/list", json={}).status_code == 400
