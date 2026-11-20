"""SQL contract tests: no read/modify/write or allow-list upsert on session writes."""
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.db.telegram_db import TelegramDB
from app.db.max_db import MaxDB


@pytest.fixture(params=[(TelegramDB, "telegram"), (MaxDB, "max")])
def database(request):
    cls, provider = request.param
    conn = AsyncMock()
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return cls(pool), conn, provider


@pytest.mark.parametrize("only_if_unset", [False, True])
def test_set_is_one_update_returning(database, only_if_unset):
    db, conn, provider = database
    candidate, winner = uuid4(), uuid4()
    conn.fetchrow.return_value = {"session_id": winner}
    result = asyncio.run(db.set_current_session(-100, candidate, only_if_unset))
    assert result == {"session_id": winner}
    conn.fetchrow.assert_awaited_once()
    sql, *args = conn.fetchrow.await_args.args
    sql = " ".join(sql.upper().split())
    assert sql.startswith(f"UPDATE {provider.upper()}_CHAT_SETTINGS AS S SET")
    assert "RETURNING S.CURRENT_SESSION_ID AS SESSION_ID" in sql
    assert ("COALESCE(S.CURRENT_SESSION_ID, $2::TEXT)" in sql) == only_if_unset
    assert "C.IS_ACTIVE = TRUE" in sql
    assert "INSERT" not in sql and "SELECT" not in sql
    assert args == ["-100", str(candidate)]
    conn.execute.assert_not_awaited()


@pytest.mark.parametrize("operation", ["get", "set"])
def test_unknown_differs_from_null_session(database, operation):
    db, conn, _ = database
    for row in [None, {"session_id": None}]:
        conn.fetchrow.return_value = row
        call = db.get_current_session("unknown") if operation == "get" else db.set_current_session("unknown", uuid4())
        assert asyncio.run(call) == row


def test_startup_adds_column_idempotently(database):
    db, conn, provider = database
    asyncio.run(db.init_db_pool())
    calls = [c.args[0] for c in conn.execute.await_args_list]
    assert calls[0] == f"ALTER TABLE {provider}_chat_settings ADD COLUMN IF NOT EXISTS current_session_id TEXT"
    # Second statement upgrades a UUID column created by the previous release.
    assert "ALTER COLUMN current_session_id TYPE TEXT" in calls[1]
    assert f"table_name = '{provider}_chat_settings'" in calls[1]
    assert len(calls) == 2


def test_settings_whitelist_does_not_allow_session_bypass(database):
    db, conn, _ = database
    conn.fetchrow.return_value = {"current_session_id": None}
    asyncio.run(db.update_settings("-100", current_session_id=uuid4(), session_id=uuid4()))
    conn.execute.assert_not_awaited()
