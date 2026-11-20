"""Opt-in real PostgreSQL tests (isolated schema, never touching existing tables).

Set CHATS_TEST_POSTGRES_DSN to run. The SQL file is available in the repository;
container-only service tests skip without a DSN.
"""
import asyncio
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from app.db.telegram_db import TelegramDB
from app.db.max_db import MaxDB

DSN = os.getenv("CHATS_TEST_POSTGRES_DSN")
SQL_PATH = Path(__file__).resolve().parents[2] / "postgres/sql/02_chats/03_messengers_tg_max.sql"
pytestmark = pytest.mark.skipif(not DSN, reason="CHATS_TEST_POSTGRES_DSN not set")


async def isolated(test):
    schema = "test_current_session_" + uuid4().hex
    admin = await asyncpg.connect(DSN)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(DSN, min_size=2, max_size=12,
                                       server_settings={"search_path": schema})
        await test(pool)
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


@pytest.mark.parametrize("db_cls,provider", [(TelegramDB, "telegram"), (MaxDB, "max")])
def test_real_postgres_sessions(db_cls, provider):
    async def scenario(pool):
        sql = SQL_PATH.read_text()
        async with pool.acquire() as conn:
            await conn.execute(sql)
            await conn.execute(sql)  # Fresh schema SQL is rerunnable.
            await conn.execute(f"INSERT INTO {provider}_chats(chat_id) VALUES ('-100'), ('missing-settings')")
            await conn.execute(f"INSERT INTO {provider}_chat_settings(chat_id) VALUES ('-100')")
        db = db_cls(pool)
        await db.init_db_pool()
        assert await db.get_current_session('-100') == {"session_id": None}
        for chat_id in ['unknown', 'missing-settings']:
            assert await db.get_current_session(chat_id) is None
            assert await db.set_current_session(chat_id, str(uuid4()), True) is None
            assert await db.set_current_session(chat_id, str(uuid4())) is None
        # Concurrent initializers on independent connections all return one winner.
        candidates = [str(uuid4()) for _ in range(24)]
        results = await asyncio.gather(*[
            db.set_current_session('-100', candidate, True) for candidate in candidates
        ])
        winner = results[0]['session_id']
        assert winner in candidates
        assert all(r == {"session_id": winner} for r in results)
        assert await db.get_current_session('-100') == {"session_id": winner}
        # A fresh DB instance sees durable state, not an instance/Redis cache.
        assert await db_cls(pool).get_current_session('-100') == {"session_id": winner}
        selected = str(uuid4())
        assert await db.set_current_session('-100', selected) == {"session_id": selected}
        assert await db.set_current_session('-100', str(uuid4()), True) == {"session_id": selected}
        await db.update_settings('-100', current_session_id=str(uuid4()), agent_name='name')
        assert await db.get_current_session('-100') == {"session_id": selected}
        # A concurrent initializer blocked by a menu write must see its committed value.
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"UPDATE {provider}_chat_settings SET current_session_id=$1 WHERE chat_id='-100'", winner)
                pending = asyncio.create_task(db.set_current_session('-100', str(uuid4()), True))
                await asyncio.sleep(0.05)
                assert not pending.done()
            assert await pending == {"session_id": winner}
        async with pool.acquire() as conn:
            assert await conn.fetchval(f"SELECT count(*) FROM {provider}_chats") == 2
            await conn.execute(f"UPDATE {provider}_chats SET is_active=FALSE WHERE chat_id='-100'")
        assert await db.get_current_session('-100') is None
        assert await db.set_current_session('-100', str(uuid4())) is None
    asyncio.run(isolated(scenario))


def test_sql_and_startup_upgrade_old_tables_idempotently():
    async def scenario(pool):
        sql = SQL_PATH.read_text()
        async with pool.acquire() as conn:
            # Simulate the previous deployment's schema and data.
            await conn.execute(sql.replace('    current_session_id     TEXT,\n', ''))
            for provider in ['telegram', 'max']:
                await conn.execute(f"ALTER TABLE {provider}_chat_settings DROP COLUMN current_session_id")
                await conn.execute(f"INSERT INTO {provider}_chats(chat_id) VALUES ('old')")
                await conn.execute(f"INSERT INTO {provider}_chat_settings(chat_id, agent_name) VALUES ('old', 'preserved')")
        # Startup migration handles either provider before postgres is restarted.
        for cls in [TelegramDB, MaxDB]:
            await cls(pool).init_db_pool()
            await cls(pool).init_db_pool()
        async with pool.acquire() as conn:
            # SQL migration independently upgrades old deployments too.
            for provider in ['telegram', 'max']:
                await conn.execute(f"ALTER TABLE {provider}_chat_settings DROP COLUMN current_session_id")
            await conn.execute(sql)
            await conn.execute(sql)
            for provider in ['telegram', 'max']:
                row = await conn.fetchrow(f"SELECT agent_name, current_session_id FROM {provider}_chat_settings WHERE chat_id='old'")
                assert dict(row) == {"agent_name": "preserved", "current_session_id": None}
                assert await conn.fetchval("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=$1 AND column_name='current_session_id'", provider + '_chat_settings') == 'text'
    asyncio.run(isolated(scenario))


def test_real_http_routes_use_postgres_for_both_messengers(monkeypatch):
    # Use the actual FastAPI routes and actual DB methods, not a fake persistence layer.
    import importlib
    import sys
    from unittest.mock import Mock
    import kafka
    import httpx
    monkeypatch.setattr(kafka, "KafkaProducer", Mock(return_value=Mock()))
    sys.modules.pop("app.app", None)
    mod = importlib.import_module("app.app")

    async def scenario(pool):
        async with pool.acquire() as conn:
            await conn.execute(SQL_PATH.read_text())
            for provider in ["telegram", "max"]:
                await conn.execute(f"INSERT INTO {provider}_chats(chat_id) VALUES ('-100')")
                await conn.execute(f"INSERT INTO {provider}_chat_settings(chat_id) VALUES ('-100')")
        monkeypatch.setattr(mod, "tg_db", TelegramDB(pool))
        monkeypatch.setattr(mod, "max_db", MaxDB(pool))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://test") as client:
            for prefix in ["tg", "max"]:
                path = f"/{prefix}/session/-100"
                assert (await client.get(path)).json() == {"session_id": None}
                replies = await asyncio.gather(*[
                    client.post(path, json={"only_if_unset": True}) for _ in range(16)
                ])
                assert all(r.status_code == 200 for r in replies)
                winner = replies[0].json()["session_id"]
                assert {r.json()["session_id"] for r in replies} == {winner}
                chosen = str(uuid4())
                assert (await client.post(path, json={"session_id": chosen})).json() == {"session_id": chosen}
                assert (await client.post(path, json={"only_if_unset": True})).json() == {"session_id": chosen}
                assert (await client.get(path)).json() == {"session_id": chosen}
                assert (await client.get(f"/{prefix}/session/unknown")).status_code == 404
                async with pool.acquire() as conn:
                    table = "telegram" if prefix == "tg" else "max"
                    assert await conn.fetchval(f"SELECT current_session_id FROM {table}_chat_settings WHERE chat_id='-100'") == chosen
                # Root history under the group's own physical ID; other IDs rejected.
                assert (await client.post(path, json={"session_id": "-100"})).json() == {"session_id": "-100"}
                assert (await client.get(path)).json() == {"session_id": "-100"}
                assert (await client.post(path, json={"session_id": "-101"})).status_code == 422
                assert (await client.get(path)).json() == {"session_id": "-100"}
    asyncio.run(isolated(scenario))


def test_previous_uuid_column_upgrades_to_text_preserving_values():
    """0006bc4 deployed a UUID column: values survive, physical IDs then fit."""
    async def scenario(pool):
        sql = SQL_PATH.read_text()
        kept = str(uuid4())
        async with pool.acquire() as conn:
            await conn.execute(sql)
            for provider in ['telegram', 'max']:
                await conn.execute(f"ALTER TABLE {provider}_chat_settings ALTER COLUMN current_session_id TYPE UUID USING current_session_id::uuid")
                await conn.execute(f"INSERT INTO {provider}_chats(chat_id) VALUES ('-100')")
                await conn.execute(f"INSERT INTO {provider}_chat_settings(chat_id, current_session_id) VALUES ('-100', $1::uuid)", kept)
        for cls in [TelegramDB, MaxDB]:
            await cls(pool).init_db_pool()
        for cls, provider in [(TelegramDB, 'telegram'), (MaxDB, 'max')]:
            db = cls(pool)
            assert await db.get_current_session('-100') == {"session_id": kept}
            assert await db.set_current_session('-100', '-100') == {"session_id": "-100"}
        async with pool.acquire() as conn:
            # SQL bootstrap path performs the same upgrade idempotently.
            for provider in ['telegram', 'max']:
                await conn.execute(f"ALTER TABLE {provider}_chat_settings ALTER COLUMN current_session_id TYPE UUID USING NULL")
            await conn.execute(sql)
            await conn.execute(sql)
            for provider in ['telegram', 'max']:
                assert await conn.fetchval("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=$1 AND column_name='current_session_id'", provider + '_chat_settings') == 'text'
    asyncio.run(isolated(scenario))
