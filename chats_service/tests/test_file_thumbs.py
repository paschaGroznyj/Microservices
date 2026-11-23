"""Превью 384 px панели вложений (core/file_thumbs.py, thumbs.py).

Уменьшение — без БД. Хранение, доступ и построение «один раз» — на настоящем PostgreSQL
в отдельной схеме (CHATS_TEST_POSTGRES_DSN, иначе тесты БД пропускаются)."""
import asyncio
import io
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core import file_thumbs

PIL = pytest.importorskip("PIL.Image")
DSN = os.getenv("CHATS_TEST_POSTGRES_DSN")
SQL = Path(__file__).resolve().parents[2] / "postgres/sql"
needs_pg = pytest.mark.skipif(not DSN, reason="CHATS_TEST_POSTGRES_DSN not set")


def img(w, h, fmt="JPEG", mode="RGB", exif_orient=None):
    b = io.BytesIO()
    im = PIL.new(mode, (w, h), (200, 30, 30, 120) if mode == "RGBA" else (200, 30, 30))
    kw = {}
    if exif_orient:
        ex = PIL.Exif()
        ex[0x0112] = exif_orient
        kw["exif"] = ex.tobytes()
    im.save(b, fmt, **kw)
    return b.getvalue()


def open_webp(data):
    im = PIL.open(io.BytesIO(data))
    assert im.format == "WEBP"
    return im


def test_make_thumb_sizes_and_formats():
    data, w, h = file_thumbs.make_thumb(img(4000, 2250))
    assert (w, h) == (384, 216) and open_webp(data).size == (384, 216)
    assert len(data) < 30_000
    # EXIF Orientation 6: стороны меняются местами, как покажет браузер
    _, w, h = file_thumbs.make_thumb(img(1600, 900, exif_orient=6))
    assert (w, h) == (216, 384)
    # прозрачность сохраняется, маленькие не растягиваются
    data, w, h = file_thumbs.make_thumb(img(100, 50, "PNG", "RGBA"))
    assert (w, h) == (100, 50) and open_webp(data).mode == "RGBA"
    _, w, h = file_thumbs.make_thumb(img(800, 800, "GIF", "P"))
    assert (w, h) == (384, 384)
    assert file_thumbs.make_thumb(b"%PDF-1.7 not an image") is None


def test_is_image():
    assert file_thumbs.is_image("a.JPG") and file_thumbs.is_image("x", "image/png")
    assert file_thumbs.is_image("gen", None, "image")
    assert not file_thumbs.is_image("a.svg", "image/svg+xml") and not file_thumbs.is_image("r.pdf")
    assert file_thumbs.has_images([{"name": "a.png"}]) and not file_thumbs.has_images([{"name": "a.pdf"}, "x"])


async def _isolated(test):
    import asyncpg
    schema = "test_thumb_" + uuid4().hex
    admin = await asyncpg.connect(DSN)
    pool = None
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4, server_settings={"search_path": schema})
        async with pool.acquire() as c:
            for f in ["01_auth/01_users_and_profile.sql", "02_chats/01_chats_core.sql", "02_chats/02_group_chats.sql",
                      "03_agent/03_chats_forks.sql", "02_chats/06_file_thumbs.sql"]:
                await c.execute((SQL / f).read_text())
        await file_thumbs.ensure_schema(pool)       # повторно — идемпотентно
        await test(pool)
    finally:
        if pool is not None:
            await pool.close()
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


def _client(pool):
    import httpx
    from fastapi import FastAPI
    from app import thumbs
    thumbs.bind(pool)
    app = FastAPI()
    app.include_router(thumbs.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@needs_pg
def test_chat_thumb_built_once_and_access(monkeypatch):
    async def scenario(pool):
        async with pool.acquire() as c:
            pav = await c.fetchval("INSERT INTO users(username, password_hash) VALUES ('pavel', 'x') RETURNING id")
            await c.fetchval("INSERT INTO users(username, password_hash) VALUES ('other', 'x') RETURNING id")
            key = await c.fetchval("INSERT INTO generated_images(chat_id, message_id, file_name, mime_type, prompt, image_data) "
                                   "VALUES ('R', 'm1', 'g.jpg', 'image/jpeg', 'p', $1) RETURNING image_key::text", img(2000, 1000))
            await c.execute("INSERT INTO chats(user_id, chat_id, message_id, message_text, is_from_bot) "
                            "VALUES ($1, 'R', 'm0', 'нарисуй', false)", pav)
            point = await c.fetchval("INSERT INTO chats(user_id, chat_id, message_id, message_text, is_from_bot) "
                                     "VALUES (-1, 'R', 'm1', 'картинка', true) RETURNING id")
            await c.execute("INSERT INTO chat_sidebar_meta(chat_id, user_id, auto_title) VALUES ('R', $1, 'Корень')", pav)
            fid = await c.fetchval("INSERT INTO chat_files(message_id, chat_id, user_id, file_name, file_ext, source, file_kind, image_key) "
                                   "VALUES ('m1', 'R', $1, 'g.jpg', 'jpg', 'agent', 'image', $2) RETURNING id", pav, key)
            pdf = await c.fetchval("INSERT INTO chat_files(message_id, chat_id, user_id, file_name, file_ext, source, file_kind) "
                                   "VALUES ('m1', 'R', $1, 'r.pdf', 'pdf', 'agent', 'file') RETURNING id", pav)
            await c.execute("SELECT fork_chat('R', $1, 'F', $2)", point, pav)
            late = await c.fetchval("INSERT INTO chats(user_id, chat_id, message_id, message_text, is_from_bot) "
                                    "VALUES (-1, 'R', 'm2', 'после форка', true) RETURNING id")
            assert late
            fid_late = await c.fetchval("INSERT INTO chat_files(message_id, chat_id, user_id, file_name, file_ext, source, file_kind, file_path) "
                                        "VALUES ('m2', 'R', $1, 'late.png', 'png', 'agent', 'image', '/p/late.png') RETURNING id", pav)

        builds = []
        real = file_thumbs.make_thumb
        monkeypatch.setattr(file_thumbs, "make_thumb", lambda raw, side=384: builds.append(1) or real(raw, side))

        async with _client(pool) as cl:
            h = {"X-User-Name": "pavel"}
            # два одновременных запроса — одно построение
            r1, r2 = await asyncio.gather(cl.get(f"/chat/R/thumb/{fid}", headers=h), cl.get(f"/chat/R/thumb/{fid}", headers=h))
            assert r1.status_code == r2.status_code == 200 and r1.headers["content-type"] == "image/webp"
            assert open_webp(r1.content).size == (384, 192) and r1.content == r2.content
            assert len(builds) == 1
            # ветка видит файл родителя до точки форка, дальше — из таблицы
            r = await cl.get(f"/chat/F/thumb/{fid}", headers=h)
            assert r.status_code == 200 and r.content == r1.content and len(builds) == 1
            # файл после точки форка в ветке не виден, чужой пользователь не видит ничего
            assert (await cl.get(f"/chat/F/thumb/{fid_late}", headers=h)).status_code == 404
            assert (await cl.get(f"/chat/R/thumb/{fid}", headers={"X-User-Name": "other"})).status_code == 404
            assert (await cl.get(f"/chat/R/thumb/{fid}", headers={"X-User-Name": "nobody"})).status_code == 404
            # не картинка: 404 и отметка «нельзя», без повторных попыток
            assert (await cl.get(f"/chat/R/thumb/{pdf}", headers=h)).status_code == 404
            row = await pool.fetchrow("SELECT data FROM file_thumbs WHERE chat_file_id = $1", pdf)
            assert row is not None and row["data"] is None

        # chats-consumer после записи сообщения: POST /internal/thumbs/chat_message строит в фоне
        key2 = await pool.fetchval("INSERT INTO generated_images(chat_id, message_id, file_name, mime_type, prompt, image_data) "
                                   "VALUES ('R', 'm3', 'g2.png', 'image/png', 'p', $1) RETURNING image_key::text",
                                   img(500, 1000, "PNG"))
        fid3 = await pool.fetchval("INSERT INTO chat_files(message_id, chat_id, user_id, file_name, file_ext, source, file_kind, image_key) "
                                   "VALUES ('m3', 'R', 1, 'g2.png', 'png', 'agent', 'image', $1) RETURNING id", key2)
        async with _client(pool) as cl:
            r = await cl.post("/internal/thumbs/chat_message", params={"chat_id": "R", "message_id": "m3"})
            assert r.status_code == 202
        await asyncio.gather(*list(file_thumbs._bg))
        assert await pool.fetchval("SELECT width FROM file_thumbs WHERE chat_file_id = $1", fid3) == 192

        # оригинал недоступен (agent_service не отвечает): в таблицу не пишем, следующий запрос попробует снова
        class Down:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k):
                import httpx
                raise httpx.ConnectError("down")
        monkeypatch.setattr(file_thumbs.httpx, "AsyncClient", Down)
        assert await file_thumbs.ensure(pool, "chat", fid_late) is None
        assert await pool.fetchval("SELECT count(*) FROM file_thumbs WHERE chat_file_id = $1", fid_late) == 0

        # удаление файла уносит превью (ON DELETE CASCADE)
        await pool.execute("DELETE FROM chat_files WHERE id = $1", fid)
        assert await pool.fetchval("SELECT count(*) FROM file_thumbs WHERE chat_file_id = $1", fid) == 0

    asyncio.run(_isolated(scenario))


@needs_pg
def test_group_thumb_upload_and_lazy(monkeypatch):
    async def scenario(pool):
        from app.core import obs_file_storage
        async with pool.acquire() as c:
            gid = await c.fetchval("SELECT id FROM group_rooms WHERE room_uid = 'global'")
            priv = await c.fetchval("INSERT INTO group_rooms(room_uid, room_type, title) VALUES ('team', 'group', 'Команда') RETURNING id")
            await c.execute("INSERT INTO group_room_members(room_id, user_id) VALUES ($1, 1)", priv)

            async def file(room, name, mime, image, deleted=False):
                mid = await c.fetchval("INSERT INTO group_messages(room_id, user_id, username, message_text, is_deleted) "
                                       "VALUES ($1, 1, 'Павел', 'x', $2) RETURNING id", room, deleted)
                return await c.fetchval("INSERT INTO group_message_files(message_id, room_id, obs_key, file_name, mime_type, size_bytes, is_image) "
                                        "VALUES ($1, $2, $3, $4, $5, 100, $6) RETURNING id", mid, room, "k/" + name, name, mime, image)
            up = await file(gid, "up.jpg", "image/jpeg", True)
            old = await file(gid, "old.png", "image/png", True)
            team = await file(priv, "team.png", "image/png", True)
            gone = await file(gid, "gone.png", "image/png", True, deleted=True)

        # загрузка: превью из уже прочитанных байтов, OBS не трогаем
        downloads = []

        async def fake_dl(key):
            downloads.append(key)
            return img(1200, 1200, "PNG")
        monkeypatch.setattr(obs_file_storage, "download_bytes", fake_dl)
        await file_thumbs.store_group_upload(pool, up, img(3000, 1500), "image/jpeg", "up.jpg")
        assert downloads == []

        async with _client(pool) as cl:
            r = await cl.get(f"/group/thumb/{up}", params={"viewer_id": 2})
            assert r.status_code == 200 and open_webp(r.content).size == (384, 192) and downloads == []
            # старый файл без превью: один раз из OBS, потом из таблицы
            assert (await cl.get(f"/group/thumb/{old}", params={"viewer_id": 2})).status_code == 200
            assert (await cl.get(f"/group/thumb/{old}", params={"viewer_id": 2})).status_code == 200
            assert downloads == ["k/old.png"]
            # закрытая комната: только участникам; удалённое сообщение — никому
            assert (await cl.get(f"/group/thumb/{team}", params={"viewer_id": 2})).status_code == 404
            assert (await cl.get(f"/group/thumb/{team}", params={"viewer_id": 1})).status_code == 200
            assert (await cl.get(f"/group/thumb/{gone}", params={"viewer_id": 1})).status_code == 404

    asyncio.run(_isolated(scenario))
