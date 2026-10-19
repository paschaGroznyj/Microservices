"""Юнит-тесты работы с JWT: create_access_token / get_auth_data / get_token."""
import pytest
from datetime import datetime, timezone
from jose import jwt
from fastapi import HTTPException


@pytest.fixture()
def mod(app_module):
    return app_module


# ---------- get_auth_data ----------

def test_get_auth_data_returns_secret_and_algorithm(mod):
    data = mod.get_auth_data()
    assert data["secret_key"] == mod.SECRET_KEY
    assert data["algorithm"] == mod.ALGORITHM


# ---------- create_access_token ----------

def test_token_roundtrip_contains_payload(mod):
    token = mod.create_access_token({"sub": "pavel", "user_id": 56})
    decoded = jwt.decode(token, mod.SECRET_KEY, algorithms=[mod.ALGORITHM])
    assert decoded["sub"] == "pavel"
    assert decoded["user_id"] == 56


def test_token_has_future_expiration(mod):
    token = mod.create_access_token({"sub": "pavel"})
    decoded = jwt.decode(token, mod.SECRET_KEY, algorithms=[mod.ALGORITHM])
    assert "exp" in decoded
    exp = datetime.fromtimestamp(decoded["exp"], tz=timezone.utc)
    assert exp > datetime.now(timezone.utc)


def test_token_does_not_mutate_input_dict(mod):
    payload = {"sub": "pavel"}
    mod.create_access_token(payload)
    assert payload == {"sub": "pavel"}  # exp добавлен в копию


# ---------- get_token ----------

class _Req:
    def __init__(self, cookies):
        self.cookies = cookies


def test_get_token_reads_cookie(mod):
    req = _Req({"users_access_token": "abc.def.ghi"})
    assert mod.get_token(req) == "abc.def.ghi"


def test_get_token_missing_cookie_raises_401(mod):
    with pytest.raises(HTTPException) as exc:
        mod.get_token(_Req({}))
    assert exc.value.status_code == 401
