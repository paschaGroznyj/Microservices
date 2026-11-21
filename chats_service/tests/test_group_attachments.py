"""Вложения общего чата (group_attachments_db) на настоящем PostgreSQL в отдельной схеме
(CHATS_TEST_POSTGRES_DSN, иначе тест пропускается)."""
import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.group_attachments_db import GroupAttachmentsDB

DSN = os.getenv("CHATS_TEST_POSTGRES_DSN")
SQL = Path(__file__).resolve().parents[2] / "postgres/sql"
needs_pg = pytest.mark.skipif(not DSN, reason="CHATS_TEST_POSTGRES_DSN not set")


async def _isolated(test):
    import asyncpg
    schema = "test_gatt_" + uuid4().hex
    admin = await asyncpg.connect(DSN)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4, server_settings={"search_path": schema})
        async with pool.acquire() as c:
            await c.execute((SQL / "02_chats/02_group_chats.sql").read_text())
        await test(pool)
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _seed(c):
    gid = await c.fetchval("SELECT id FROM group_rooms WHERE room_uid = 'global'")
    priv = await c.fetchval("INSERT INTO group_rooms(room_uid, room_type, title) VALUES ('team', 'group', 'Команда') RETURNING id")
    await c.execute("INSERT INTO group_room_members(room_id, user_id) VALUES ($1, 1)", priv)

    async def msg(room, uid, name, text, files=(), deleted=False):
        mid = await c.fetchval("INSERT INTO group_messages(room_id, user_id, username, message_text, is_deleted) "
                               "VALUES ($1, $2, $3, $4, $5) RETURNING id", room, uid, name, text, deleted)
        for fn, mime, img in files:
            await c.execute("INSERT INTO group_message_files(message_id, room_id, obs_key, file_name, mime_type, size_bytes, is_image) "
                            "VALUES ($1, $2, $3, $4, $5, 100, $6)", mid, room, "k/" + fn, fn, mime, img)
        return mid

    await msg(gid, 1, "Павел", "смотри https://gitverse.ru/x.", [("a.png", "image/png", True)])
    await msg(gid, 2, "Олег Петров", "отчёт", [("report.pdf", "application/pdf", False), ("clip.mp4", "video/mp4", False)])
    await msg(gid, 2, "Олег Петров", "удалено https://hidden.io", [("gone.png", "image/png", True)], deleted=True)
    await msg(gid, 1, "Павел", "[доки](https://docs.io/a) и https://docs.io/a", [("b.jpg", "image/jpeg", True)])
    await msg(priv, 1, "Павел", "секрет", [("team.png", "image/png", True)])


@needs_pg
def test_group_attachments_on_postgres():
    async def scenario(pool):
        async with pool.acquire() as c:
            await _seed(c)
        db = GroupAttachmentsDB(pool)

        m = await db.list("global", 1, tab="media")
        assert [i["name"] for i in m["items"]] == ["b.jpg", "clip.mp4", "a.png"]
        assert [i["kind"] for i in m["items"]] == ["image", "video", "image"]
        assert m["counts"] == {"media": 3, "files": 1, "links": 2}
        assert [i["author"] for i in m["items"]] == ["user", "other", "user"]
        assert m["items"][1]["username"] == "Олег Петров"

        f = await db.list("global", 1, tab="files")
        assert [(i["name"], i["ext"]) for i in f["items"]] == [("report.pdf", "pdf")]

        me = await db.list("global", 1, tab="media", author="me")
        assert [i["name"] for i in me["items"]] == ["b.jpg", "a.png"] and me["counts"]["media"] == 2
        oth = await db.list("global", 1, tab="media", author="others")
        assert [i["name"] for i in oth["items"]] == ["clip.mp4"] and oth["counts"]["links"] == 0

        seen, cur = [], None
        while True:
            p = await db.list("global", 1, tab="media", cursor=cur, limit=1)
            assert ("counts" in p) == (cur is None)
            seen += [i["name"] for i in p["items"]]
            cur = p["next_cursor"]
            if not cur:
                break
        assert seen == ["b.jpg", "clip.mp4", "a.png"]

        ln = await db.list("global", 1, tab="links")
        assert [(i["url"], i["title"]) for i in ln["items"]] == [("https://docs.io/a", "доки"), ("https://gitverse.ru/x", None)]

        # Закрытая комната: участнику видна, остальным нет. Несуществующая — None.
        assert [i["name"] for i in (await db.list("team", 1, tab="media"))["items"]] == ["team.png"]
        assert await db.list("team", 2, tab="media") is None
        assert await db.list("nope", 1, tab="media") is None
        with pytest.raises(ValueError):
            await db.list("global", 1, tab="media", cursor="bad")
    asyncio.run(_isolated(scenario))
