"""Личный ключ RouterAPI: чей ключ берём (media_key_owner) и чтение из vault (resolve_router_api_key)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet

from agent.core import user_secrets as us
from agent import event_loop as el

KEY = "sk-" + "Zz09" * 8


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("USER_SECRETS_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(us, "_fernet", None)


def _enc(uid, provider="routerai", name="default", secret=KEY):
    payload = json.dumps({"v": 1, "u": uid, "p": provider, "n": name, "s": secret})
    return us._f().encrypt(payload.encode()).decode()


def _agent(owner, row=None):
    conn = MagicMock()                     # asyncpg Pool: fetchrow прямо на пуле
    conn.fetchrow = AsyncMock(return_value=row)
    agent = SimpleNamespace(db=SimpleNamespace(pool=conn),
                            _ctx_get=lambda k, d=None: owner if k == "media_key_owner" else d)
    return agent, conn


def run(c):
    return asyncio.run(c)


def test_user_key_used(monkeypatch):
    monkeypatch.setattr(us, "ROUTER_API_KEY", "env-key")
    agent, conn = _agent({"user_id": 7, "is_admin": True}, {"secret_enc": _enc(7)})
    assert run(us.resolve_router_api_key(agent)) == (KEY, None)   # свой ключ даже у админа
    sql = conn.fetchrow.await_args.args[0]
    assert "vault.user_secrets" in sql and "last_used_at" in sql and conn.fetchrow.await_args.args[1:] == (7, "routerai", "default")


def test_foreign_ciphertext_rejected(monkeypatch):
    monkeypatch.setattr(us, "ROUTER_API_KEY", "env-key")
    agent, _ = _agent({"user_id": 7, "is_admin": False}, {"secret_enc": _enc(8)})
    key, err = run(us.resolve_router_api_key(agent))
    assert key is None and err == us.NO_KEY_MSG


def test_no_key_non_admin_gets_no_env_key(monkeypatch):
    monkeypatch.setattr(us, "ROUTER_API_KEY", "env-key")
    agent, _ = _agent({"user_id": 7, "is_admin": False}, None)
    assert run(us.resolve_router_api_key(agent)) == (None, us.NO_KEY_MSG)


def test_admin_without_key_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(us, "ROUTER_API_KEY", "env-key")
    agent, _ = _agent({"user_id": 56, "is_admin": True}, None)
    assert run(us.resolve_router_api_key(agent)) == ("env-key", None)


def test_no_owner(monkeypatch):
    monkeypatch.setattr(us, "ROUTER_API_KEY", "env-key")
    agent, conn = _agent(None)
    assert run(us.resolve_router_api_key(agent)) == (None, us.NO_OWNER_MSG)
    conn.fetchrow.assert_not_called()


def test_db_error_is_not_leaked(monkeypatch):
    monkeypatch.setattr(us, "ROUTER_API_KEY", "")
    agent, conn = _agent({"user_id": 7, "is_admin": False})
    conn.fetchrow.side_effect = RuntimeError("boom")
    assert run(us.resolve_router_api_key(agent)) == (None, us.NO_KEY_MSG)


def test_owner_web(monkeypatch):
    monkeypatch.setattr(el, "ADMINS_BY_USER_ID", {56})
    assert el.media_key_owner(7, is_telegram=False, is_max=False, initiated=False, trigger_owner=None) == {"user_id": 7, "is_admin": False}
    assert el.media_key_owner("56", is_telegram=False, is_max=False, initiated=False, trigger_owner=None) == {"user_id": 56, "is_admin": True}


def test_owner_messenger_has_no_personal_key(monkeypatch):
    monkeypatch.setattr(el, "ADMINS_BY_TG_ID", {111})
    assert el.media_key_owner(111, is_telegram=True, is_max=False, initiated=False, trigger_owner=None) == {"user_id": None, "is_admin": True}
    assert el.media_key_owner(222, is_telegram=True, is_max=False, initiated=False, trigger_owner=None) == {"user_id": None, "is_admin": False}


def test_owner_trigger_uses_author_not_request_user(monkeypatch):
    monkeypatch.setattr(el, "ADMINS_BY_USER_ID", {56})
    web = {"user_id": 7, "source": "web"}
    assert el.media_key_owner(0, is_telegram=True, is_max=False, initiated=True, trigger_owner=web) == {"user_id": 7, "is_admin": False}
    tg = {"user_id": 7, "source": "tg"}   # TG-id автора не совпадает с web user_id
    assert el.media_key_owner(0, is_telegram=True, is_max=False, initiated=True, trigger_owner=tg)["user_id"] is None
    assert el.media_key_owner(5, is_telegram=False, is_max=False, initiated=True, trigger_owner=None) == {"user_id": None, "is_admin": False}
