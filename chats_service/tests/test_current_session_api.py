"""Both providers expose the same validated, dedicated session API."""
import importlib
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(params=[("tg", "tg_db"), ("max", "max_db")])
def api(request, monkeypatch):
    # app.py constructs a producer on import; no Kafka or startup resources here.
    monkeypatch.setattr("kafka.KafkaProducer", MagicMock())
    module = importlib.import_module("app.app")
    prefix, db_name = request.param
    database = MagicMock()
    database.get_current_session = AsyncMock(return_value={"session_id": None})
    database.set_current_session = AsyncMock()
    monkeypatch.setattr(module, db_name, database)
    # Not using a context manager deliberately avoids the external-service lifespan.
    return TestClient(module.app), database, f"/{prefix}/session/-100", module


@pytest.mark.parametrize("value", [None, uuid4()])
def test_get_null_or_uuid(api, value):
    client, database, path, _ = api
    database.get_current_session.return_value = {"session_id": str(value) if value else None}
    response = client.get(path)
    assert response.status_code == 200
    assert response.json() == {"session_id": str(value) if value else None}
    database.get_current_session.assert_awaited_once_with("-100")
    database.set_current_session.assert_not_awaited()


@pytest.mark.parametrize("method", ["get", "post"])
def test_unknown_chat_is_404_without_creating_group(api, method):
    client, database, path, _ = api
    database.get_current_session.return_value = None
    database.set_current_session.return_value = None
    response = client.get(path) if method == "get" else client.post(path, json={})
    assert response.status_code == 404
    database.upsert_chat.assert_not_called()
    database.update_settings.assert_not_called()


@pytest.mark.parametrize("body", [{}, {"session_id": None}, {"only_if_unset": True}])
def test_missing_candidate_defaults_to_group_root_history(api, body):
    """NULL during migration -> the group's own ID, never a fresh UUID."""
    client, database, path, module = api
    from unittest.mock import patch
    database.set_current_session.return_value = {"session_id": "-100"}
    with patch.object(module.uuid, "uuid4") as generate:
        response = client.post(path, json=body)
    assert response.status_code == 200
    assert response.json() == {"session_id": "-100"}
    generate.assert_not_called()
    database.set_current_session.assert_awaited_once_with(
        "-100", "-100", body.get("only_if_unset", False)
    )


@pytest.mark.parametrize("only_if_unset", [False, True])
def test_supplied_migration_or_menu_uuid_and_db_winner(api, only_if_unset):
    client, database, path, _ = api
    candidate, existing = uuid4(), uuid4()
    winner = existing if only_if_unset else candidate
    database.set_current_session.return_value = {"session_id": str(winner)}
    response = client.post(path, json={
        "session_id": str(candidate), "only_if_unset": only_if_unset,
    })
    assert response.status_code == 200
    assert response.json() == {"session_id": str(winner)}
    database.set_current_session.assert_awaited_once_with("-100", str(candidate), only_if_unset)


def test_supplied_uuid_defaults_to_overwrite(api):
    client, database, path, _ = api
    candidate = uuid4()
    database.set_current_session.return_value = {"session_id": str(candidate)}
    assert client.post(path, json={"session_id": str(candidate)}).status_code == 200
    database.set_current_session.assert_awaited_once_with("-100", str(candidate), False)


@pytest.mark.parametrize("body", [
    {"session_id": "not-a-uuid"}, {"session_id": ""}, {"session_id": 123},
    {"session_id": "-101"}, {"session_id": "100"}, {"session_id": -100},
    {"session_id": {}}, {"only_if_unset": "nonsense"}, [],
])
def test_invalid_input_never_reaches_db(api, body):
    client, database, path, _ = api
    assert client.post(path, json=body).status_code == 422
    database.set_current_session.assert_not_awaited()


def test_body_required(api):
    client, database, path, _ = api
    assert client.post(path).status_code == 422
    database.set_current_session.assert_not_awaited()


def test_generic_settings_cannot_write_session(api):
    client, database, path, _ = api
    database.update_settings = AsyncMock(return_value={})
    response = client.post(path.replace("/session/", "/settings/"), json={
        "session_id": str(uuid4()), "current_session_id": str(uuid4()),
    })
    assert response.status_code == 200
    database.update_settings.assert_awaited_once_with("-100")
    database.set_current_session.assert_not_awaited()


@pytest.mark.parametrize("only_if_unset", [False, True])
def test_group_own_physical_id_is_a_valid_root_session(api, only_if_unset):
    client, database, path, _ = api
    database.set_current_session.return_value = {"session_id": "-100"}
    response = client.post(path, json={"session_id": "-100", "only_if_unset": only_if_unset})
    assert response.status_code == 200
    assert response.json() == {"session_id": "-100"}
    database.set_current_session.assert_awaited_once_with("-100", "-100", only_if_unset)


def test_get_returns_root_session_text(api):
    client, database, path, _ = api
    database.get_current_session.return_value = {"session_id": "-100"}
    assert client.get(path).json() == {"session_id": "-100"}


def test_uuid_is_canonicalized_before_db(api):
    client, database, path, _ = api
    candidate = uuid4()
    database.set_current_session.return_value = {"session_id": str(candidate)}
    assert client.post(path, json={"session_id": str(candidate).upper()}).status_code == 200
    database.set_current_session.assert_awaited_once_with("-100", str(candidate), False)
