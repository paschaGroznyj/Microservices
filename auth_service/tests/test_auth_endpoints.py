"""Тесты HTTP-эндпоинтов аутентификации через TestClient.
БД замокана (fake_db), сеть/Postgres не дёргаем.
"""
import pytest


# ---------- /register ----------

def test_register_success(client, fake_db):
    fake_db.add_user.return_value = 123
    r = client.post("/register", data={"username": "newuser", "password": "secret6"})
    assert r.status_code == 200
    assert r.json()["user_id"] == 123
    fake_db.add_user.assert_awaited_once()


def test_register_existing_user_returns_400(client, fake_db):
    fake_db.add_user.return_value = None
    r = client.post("/register", data={"username": "dup", "password": "secret6"})
    assert r.status_code == 400


def test_register_invalid_credentials_returns_400(client, fake_db):
    r = client.post("/register", data={"username": "ab", "password": "secret6"})
    assert r.status_code == 400
    fake_db.add_user.assert_not_awaited()


# ---------- /login ----------

def test_login_success_sets_cookie(client, fake_db):
    fake_db.check_user_password.return_value = {"id": 56}
    r = client.post("/login", data={"username": "pavel", "password": "secret6"})
    assert r.status_code == 200
    assert r.json()["token_type"] == "bearer"
    assert "users_access_token" in r.cookies


def test_login_wrong_password_returns_401(client, fake_db):
    fake_db.check_user_password.return_value = None
    r = client.post("/login", data={"username": "pavel", "password": "wrongpass"})
    assert r.status_code == 401


# ---------- /guest-login ----------

def test_guest_login_success(client, fake_db):
    fake_db.add_guest_user.return_value = (777, "guest_777")
    r = client.post("/guest-login")
    assert r.status_code == 200
    assert r.json()["user_id"] == 777
    assert "users_access_token" in r.cookies


def test_guest_login_failure_returns_500(client, fake_db):
    fake_db.add_guest_user.return_value = (None, None)
    r = client.post("/guest-login")
    assert r.status_code == 500


# ---------- /logout ----------

def test_logout_clears_cookie(client):
    r = client.post("/logout")
    assert r.status_code == 200
    assert "вышел" in r.json()["message"]


# ---------- /register-and-login ----------

def test_register_and_login_success(client, fake_db):
    fake_db.add_user.return_value = 42
    r = client.post("/register-and-login", data={"username": "combo", "password": "secret6"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 42 and body["token_type"] == "bearer"


def test_register_and_login_duplicate_returns_400(client, fake_db):
    fake_db.add_user.return_value = None
    r = client.post("/register-and-login", data={"username": "combo", "password": "secret6"})
    assert r.status_code == 400
