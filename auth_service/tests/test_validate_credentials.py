"""Юнит-тесты validate_credentials: границы длины логина/пароля."""
import pytest
from fastapi import HTTPException


@pytest.fixture()
def validate(app_module):
    return app_module.validate_credentials


# ---------- логин ----------

@pytest.mark.parametrize("username", ["abc", "user", "a" * 50])
def test_valid_username_passes(validate, username):
    validate(username, "secret6")  # не должно кидать


@pytest.mark.parametrize("username", ["", "ab", "  a "])
def test_too_short_username_rejected(validate, username):
    with pytest.raises(HTTPException) as exc:
        validate(username, "secret6")
    assert exc.value.status_code == 400


def test_too_long_username_rejected(validate):
    with pytest.raises(HTTPException) as exc:
        validate("a" * 51, "secret6")
    assert exc.value.status_code == 400


def test_username_is_stripped_before_length_check(validate):
    # 3 значащих символа с пробелами — ок
    validate("  abc  ", "secret6")


# ---------- пароль ----------

def test_too_short_password_rejected(validate):
    with pytest.raises(HTTPException) as exc:
        validate("user", "12345")
    assert exc.value.status_code == 400


def test_too_long_password_rejected(validate):
    with pytest.raises(HTTPException) as exc:
        validate("user", "x" * 73)
    assert exc.value.status_code == 400


def test_password_length_counted_in_bytes(validate):
    # 36 кириллических букв = 72 байта UTF-8 — ровно лимит, ок
    validate("user", "а" * 36)
    # 37 букв = 74 байта — перебор
    with pytest.raises(HTTPException):
        validate("user", "а" * 37)


def test_password_not_checked_when_flag_off(validate):
    # check_password=False — короткий пароль не должен ронять (логин)
    validate("user", "1", check_password=False)
