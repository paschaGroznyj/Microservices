"""Вложения чата (attachments_db): разбор ссылок и курсора без БД, сбор по цепочке форков
на настоящем PostgreSQL в отдельной схеме (CHATS_TEST_POSTGRES_DSN, иначе тесты БД пропускаются)."""
import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.attachments_db import AttachmentsDB, extract_links, parse_cursor

DSN = os.getenv("CHATS_TEST_POSTGRES_DSN")
SQL = Path(__file__).resolve().parents[2] / "postgres/sql"
needs_pg = pytest.mark.skipif(not DSN, reason="CHATS_TEST_POSTGRES_DSN not set")


def test_extract_links_markdown_dedupe_and_trailing():
    text = ("См. [PDF.js](https://mozilla.github.io/pdf.js) и https://x.io/a, ещё раз https://x.io/a. "
            "(https://y.io/p?q=1) <https://z.io> `https://code.io/x` https://")
    got = extract_links(text)
    assert [l["url"] for l in got] == ["https://mozilla.github.io/pdf.js", "https://x.io/a",
                                       "https://y.io/p?q=1", "https://z.io", "https://code.io/x"]
    assert got[0]["title"] == "PDF.js" and got[1]["title"] is None
    assert extract_links("") == [] and extract_links(None) == []


def test_extract_links_cap():
    text = " ".join(f"https://h{i}.io" for i in range(50))
    assert len(extract_links(text)) == 20


def test_parse_cursor():
    assert parse_cursor(None, "media") is None
    assert parse_cursor("12.7", "files") == (12, 7)
    assert parse_cursor("44", "links") == (44, 0)
    for bad, tab in [("12", "media"), ("1.2.3", "files"), ("x.1", "media"), ("1.2", "links"), ("-1", "links")]:
        with pytest.raises(ValueError):
            parse_cursor(bad, tab)


async def _isolated(test):
    import asyncpg
    schema = "test_att_" + uuid4().hex
    admin = await asyncpg.connect(DSN)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4, server_settings={"search_path": schema})
        async with pool.acquire() as c:
            for f in ["01_auth/01_users_and_profile.sql", "02_chats/01_chats_core.sql", "03_agent/03_chats_forks.sql"]:
                await c.execute((SQL / f).read_text())
        await test(pool)
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def _seed(c):
    pav = await c.fetchval("INSERT INTO users(username, password_hash) VALUES ('pavel', 'x') RETURNING id")
    oth = await c.fetchval("INSERT INTO users(username, password_hash) VALUES ('other', 'x') RETURNING id")

    async def msg(chat, uid, bot, text, files=()):
        mid = "m-" + uuid4().hex[:10]
        dbid = await c.fetchval("INSERT INTO chats(user_id, chat_id, message_id, message_text, is_from_bot) "
                                "VALUES ($1, $2, $3, $4, $5) RETURNING id", -1 if bot else uid, chat, mid, text, bot)
        for name, kind in files:
            await c.execute("INSERT INTO chat_files(message_id, chat_id, user_id, file_name, file_ext, source, file_kind, file_size) "
                            "VALUES ($1, $2, $3, $4, $5, $6, $7, 100)", mid, chat, uid, name,
                            name.rsplit(".", 1)[-1], "agent" if bot else "user", kind)
        return dbid

    await c.execute("INSERT INTO chat_sidebar_meta(chat_id, user_id, auto_title) VALUES ('R', $1, 'Корень')", pav)
    await msg("R", pav, False, "картинка", [("a.png", "file")])
    point = await msg("R", pav, True, "Отчёт: [PDF.js](https://mozilla.github.io/pdf.js), https://x.io/a, https://x.io/a.",
                      [("report.pdf", "file"), ("chart.png", "image")])
    await msg("R", pav, False, "после точки форка https://hidden.io")
    await msg("R", pav, True, "после форка https://late.io", [("late.py", "file")])
    await c.execute("SELECT fork_chat('R', $1, 'F', $2)", point, pav)
    await msg("F", pav, False, "зеркало https://gitverse.ru/x", [("b.jpg", "file"), ("clip.mp4", "video")])
    await msg("F", pav, True, "готово", [("z.html", "file")])
    await msg("O", oth, False, "чужое https://secret.io", [("secret.png", "image")])
    return point


@needs_pg
def test_chain_fork_visibility_authors_and_pages():
    async def scenario(pool):
        async with pool.acquire() as c:
            point = await _seed(c)
        db = AttachmentsDB(pool)

        r = await db.list("F", "pavel", tab="media")
        assert [i["name"] for i in r["items"]] == ["clip.mp4", "b.jpg", "chart.png", "a.png"]
        assert [i["inherited"] for i in r["items"]] == [False, False, True, True]
        assert [i["kind"] for i in r["items"]] == ["video", "image", "image", "image"]
        assert r["counts"] == {"media": 4, "files": 2, "links": 3}
        assert [(x["chat_id"], x["depth"], x["point_id"]) for x in r["chain"]] == [("F", 0, None), ("R", 1, point)]
        assert r["chain"][0]["title"] == "Корень (1)" and r["chain"][1]["title"] == "Корень"
        assert r["next_cursor"] is None

        f = await db.list("F", "pavel", tab="files")
        assert [i["name"] for i in f["items"]] == ["z.html", "report.pdf"]  # late.py после точки форка не виден
        assert {i["author"] for i in f["items"]} == {"agent"}

        me = await db.list("F", "pavel", tab="media", author="me")
        assert [i["name"] for i in me["items"]] == ["clip.mp4", "b.jpg", "a.png"]
        assert me["counts"] == {"media": 3, "files": 0, "links": 1}
        ag = await db.list("F", "pavel", tab="media", author="agent")
        assert [i["name"] for i in ag["items"]] == ["chart.png"]

        # Страницы по одному: тот же порядок, без пропусков и повторов, counts только на первой.
        seen, cur = [], None
        while True:
            p = await db.list("F", "pavel", tab="media", cursor=cur, limit=1)
            assert ("counts" in p) == (cur is None)
            seen += [i["name"] for i in p["items"]]
            cur = p["next_cursor"]
            if not cur:
                break
        assert seen == ["clip.mp4", "b.jpg", "chart.png", "a.png"]

        ln = await db.list("F", "pavel", tab="links")
        assert [(i["url"], i["title"], i["author"]) for i in ln["items"]] == [
            ("https://gitverse.ru/x", None, "user"),
            ("https://mozilla.github.io/pdf.js", "PDF.js", "agent"),
            ("https://x.io/a", None, "agent")]
        p1 = await db.list("F", "pavel", tab="links", limit=1)
        p2 = await db.list("F", "pavel", tab="links", cursor=p1["next_cursor"], limit=1)
        assert [i["url"] for i in p1["items"]] == ["https://gitverse.ru/x"] and len(p2["items"]) == 2
        assert p2["next_cursor"] is None

        # Корень видит late.py, но не файлы ветки.
        rl = await db.list("R", "pavel", tab="links")
        assert [i["url"] for i in rl["items"]] == ["https://late.io", "https://hidden.io",
                                                   "https://mozilla.github.io/pdf.js", "https://x.io/a"]
        assert all(not i["inherited"] for i in rl["items"])
        rr = await db.list("R", "pavel", tab="files")
        assert [i["name"] for i in rr["items"]] == ["late.py", "report.pdf"]
        assert [x["chat_id"] for x in rr["chain"]] == ["R"]

        # Чужой и несуществующий пользователь: None (gateway отдаст 404).
        assert await db.list("F", "other", tab="media") is None
        assert await db.list("O", "pavel", tab="media") is None
        assert await db.list("F", "nobody", tab="media") is None
        with pytest.raises(ValueError):
            await db.list("F", "pavel", tab="media", cursor="bad")
    asyncio.run(_isolated(scenario))
