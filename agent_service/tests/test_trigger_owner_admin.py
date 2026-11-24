"""Права автотриггера: админ-категории только если автор триггера — админ."""
import pytest

el = pytest.importorskip("agent.event_loop")


@pytest.fixture(autouse=True)
def admins(monkeypatch):
    monkeypatch.setattr(el, "ADMINS_BY_USER_ID", {56: {}})
    monkeypatch.setattr(el, "ADMINS_BY_TG_ID", {111: {}})
    monkeypatch.setattr(el, "ADMINS_BY_MAX_ID", {222: {}})


@pytest.mark.parametrize("owner,ok", [
    ({"user_id": 56, "source": "web"}, True),
    ({"user_id": 111, "source": "tg"}, True),
    ({"user_id": 222, "source": "max"}, True),
    # id есть, но не в своём пространстве
    ({"user_id": 56, "source": "tg"}, False),
    ({"user_id": 111, "source": "web"}, False),
    ({"user_id": 57, "source": "web"}, False),
    ({"user_id": 56, "source": None}, False),
    ({"user_id": None, "source": "web"}, False),
    ({"user_id": "x", "source": "web"}, False),
    (None, False),
    ("56", False),
])
def test_trigger_owner_is_admin(owner, ok):
    assert el.trigger_owner_is_admin(owner) is ok


def test_admin_role_has_no_personal_data():
    r = el.trigger_admin_role({"user_id": 56, "source": "web", "trigger_id": 7})
    assert "trigger_id=7" in r and "56" not in r.replace("trigger_id=7", "")
