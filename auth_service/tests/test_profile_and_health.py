"""Тесты health и профильных эндпоинтов (preferences с redis-кэшем)."""
import pytest


# ---------- /health ----------

def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "auth_service"
    assert body["status"] in {"ok", "error"}


# ---------- /profile/me ----------

def test_profile_me_returns_db_stats(client, fake_db):
    fake_db.get_user_stats.return_value = {"username": "pavel", "reports": 10}
    r = client.post("/profile/me", json={"username": "pavel"})
    assert r.status_code == 200
    assert r.json()["reports"] == 10
    fake_db.get_user_stats.assert_awaited_once_with(username="pavel")


# ---------- /profile/preferences/get ----------

def test_preferences_get_from_cache(client, fake_db, mocker, app_module):
    # кэш есть — в БД не ходим
    mocker.patch.object(app_module, "_prefs_cache_get",
                        new=mocker.AsyncMock(return_value={"palette": "dark"}))
    r = client.post("/profile/preferences/get", json={"user_id": 56})
    assert r.status_code == 200
    assert r.json() == {"palette": "dark"}
    fake_db.get_user_preferences.assert_not_awaited()


def test_preferences_get_falls_back_to_db(client, fake_db, mocker, app_module):
    # кэш пуст — идём в БД и кладём в кэш
    mocker.patch.object(app_module, "_prefs_cache_get",
                        new=mocker.AsyncMock(return_value=None))
    set_mock = mocker.patch.object(app_module, "_prefs_cache_set",
                                   new=mocker.AsyncMock())
    fake_db.get_user_preferences.return_value = {"palette": "light"}
    r = client.post("/profile/preferences/get", json={"user_id": 56})
    assert r.status_code == 200
    assert r.json() == {"palette": "light"}
    fake_db.get_user_preferences.assert_awaited_once_with(user_id=56)
    set_mock.assert_awaited_once()


# ---------- /profile/preferences/set ----------

def test_preferences_set_upserts_and_caches(client, fake_db, mocker, app_module):
    set_mock = mocker.patch.object(app_module, "_prefs_cache_set",
                                   new=mocker.AsyncMock())
    fake_db.upsert_user_preferences.return_value = {"palette": "ocean"}
    r = client.post("/profile/preferences/set",
                    json={"user_id": 56, "palette": "ocean"})
    assert r.status_code == 200
    assert r.json() == {"palette": "ocean"}
    fake_db.upsert_user_preferences.assert_awaited_once()
    set_mock.assert_awaited_once()


# ---------- whatsnew ----------

def test_whatsnew_pending_requires_user_id(client):
    r = client.post("/whatsnew/pending", json={})
    assert r.status_code == 400


def test_whatsnew_pending_returns_db_result(client, fake_db):
    fake_db.get_pending_whatsnew.return_value = {"demo": False, "updates": []}
    r = client.post("/whatsnew/pending", json={"user_id": 56})
    assert r.status_code == 200
    assert r.json()["updates"] == []


def test_whatsnew_update_seen_requires_both_ids(client):
    r = client.post("/whatsnew/updates/seen", json={"user_id": 56})
    assert r.status_code == 400
