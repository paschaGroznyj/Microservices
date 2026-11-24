"""Автор автотриггера: поля при PATCH и чтение автора из БД."""
import asyncio

from app.trigger_owner import owner_fields_for_patch, resolve_trigger_owner


def test_prompt_without_author_resets_owner():
    f = owner_fields_for_patch({"prompt": "новый"})
    assert f["added_by_user_id"] is None and f["added_by_source"] is None


def test_preset_change_resets_owner():
    f = owner_fields_for_patch({"preset_key": "x"})
    assert f["added_by_user_id"] is None


def test_prompt_with_author_keeps_owner():
    f = owner_fields_for_patch({"prompt": "п", "added_by_user_id": 56, "added_by_source": "web"})
    assert (f["added_by_user_id"], f["added_by_source"]) == (56, "web")


def test_unknown_source_dropped():
    f = owner_fields_for_patch({"prompt": "п", "added_by_user_id": 56, "added_by_source": "evil"})
    assert f["added_by_source"] is None


def test_time_change_keeps_owner():
    f = owner_fields_for_patch({"time_msk": "09:00", "is_active": False, "added_by_source": "web"})
    assert "added_by_user_id" not in f and "added_by_source" not in f


class _DB:
    def __init__(self, row=None, exc=None):
        self.row, self.exc = row, exc

    async def get_trigger(self, tid):
        if self.exc:
            raise self.exc
        return self.row


ROW = {"id": 7, "chat_id": "-100", "added_by_user_id": 56, "added_by_source": "web", "prompt": "из БД"}


def test_resolve_owner_from_db():
    o = asyncio.run(resolve_trigger_owner(_DB(ROW), 7, "-100"))
    assert o == {"trigger_id": 7, "user_id": 56, "source": "web", "prompt": "из БД"}


def test_resolve_other_chat_rejected():
    assert asyncio.run(resolve_trigger_owner(_DB(ROW), 7, "-999")) is None


def test_resolve_missing_and_error():
    assert asyncio.run(resolve_trigger_owner(_DB(None), 7, "-100")) is None
    assert asyncio.run(resolve_trigger_owner(_DB(exc=RuntimeError("db")), 7, "-100")) is None
    assert asyncio.run(resolve_trigger_owner(_DB(ROW), "abc", "-100")) is None
