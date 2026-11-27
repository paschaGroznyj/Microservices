"""Раздел «Ключи API» в /start: кнопка tg:menu:keys открывает экран ключей."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _update(data: str, chat_type: str = "private"):
    chat = SimpleNamespace(id=100, type=chat_type)
    query = MagicMock()
    query.data = data
    query.message = SimpleNamespace(chat=chat)
    query.from_user = SimpleNamespace(id=100)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return SimpleNamespace(callback_query=query, effective_chat=chat), query


@pytest.fixture
def cb_bot(bot, monkeypatch):
    async def _allowed(_cid):
        return True
    monkeypatch.setattr(bot, "is_allowed", _allowed)
    status = AsyncMock(return_value=({"last4": "abcd", "updated_at": "2026-11-26T10:00:00+00:00"}, None))
    monkeypatch.setattr(bot.apikeys_ui, "fetch_status", status)
    return bot, status


@pytest.mark.parametrize("data", ["tg:menu:keys", "tg:keys:cancel"])
def test_keys_menu_opens_in_private(cb_bot, data):
    bot, status = cb_bot
    upd, query = _update(data)
    asyncio.run(bot.on_callback(upd, None))
    status.assert_awaited_once()
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "Ключи API" in text and "••••abcd" in text
    kb = query.edit_message_text.await_args.kwargs["reply_markup"]
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["tg:keys:set", "tg:keys:del", "tg:menu:root"]


def test_keys_menu_refused_in_group(cb_bot):
    bot, status = cb_bot
    upd, query = _update("tg:menu:keys", chat_type="group")
    asyncio.run(bot.on_callback(upd, None))
    status.assert_not_awaited()
    query.edit_message_text.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True
