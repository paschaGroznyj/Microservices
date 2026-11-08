"""/web/file и /web/db_image: OBS первым, без подмены файла с тем же именем."""
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import web_ui
from agent.core import obs_file_storage as obs


class _Pool:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def acquire(self):
        pool = self

        class _CM:
            async def __aenter__(self_):
                class _Conn:
                    async def fetchrow(self__, sql, *args):
                        pool.calls.append(sql)
                        for key, row in pool.rows.items():
                            if key in sql:
                                return row
                        return None
                return _Conn()

            async def __aexit__(self_, *a):
                return False
        return _CM()


@pytest.fixture
def env(monkeypatch, tmp_path):
    gen = tmp_path / "gen"
    sb = tmp_path / "sb"
    gen.mkdir(); sb.mkdir()
    monkeypatch.setattr(web_ui, "ALLOWED_FILES_ROOTS",
                        [os.path.realpath(gen), os.path.realpath(sb)])
    monkeypatch.setattr(obs, "GENERATED_FILES_ROOT", str(gen))
    store = {}

    async def fake_stat(key):
        return len(store[key]) if key in store else None

    async def fake_iter(key, start=0, end=None, chunk_size=0):
        data = store[key]
        yield data[start:(len(data) if end is None else end + 1)]

    monkeypatch.setattr(obs, "stat_object", fake_stat)
    monkeypatch.setattr(obs, "iter_object", fake_iter)
    pool = _Pool({})
    monkeypatch.setattr(web_ui.db, "pool", pool, raising=False)
    app = FastAPI()
    app.include_router(web_ui.web_router)
    return TestClient(app), gen, sb, store, pool


def _put_local(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return os.path.realpath(path)


def test_obs_wins_over_local_copy(env):
    c, gen, _, store, _ = env
    p = _put_local(gen / "run1" / "slide-1.png", b"LOCAL")
    store[obs.build_obs_key(p, "c1")] = b"FROM-OBS"
    r = c.get("/web/file", params={"path": p, "chat_id": "c1"})
    assert r.status_code == 200 and r.content == b"FROM-OBS"
    assert r.headers["x-file-source"] == "obs"


def test_disk_when_obs_missing(env):
    c, gen, _, _, _ = env
    p = _put_local(gen / "run1" / "a.png", b"LOCAL")
    r = c.get("/web/file", params={"path": p, "chat_id": "c1"})
    assert r.content == b"LOCAL" and r.headers["x-file-source"] == "disk"


def test_shared_sandbox_key_prefers_disk(env):
    c, _, sb, store, _ = env
    p = _put_local(sb / "x.png", b"LOCAL")
    store[obs.build_obs_key(p, "c1")] = b"OTHER-REQUEST"
    r = c.get("/web/file", params={"path": p, "chat_id": "c1"})
    assert r.content == b"LOCAL"


def test_range_from_obs(env):
    c, gen, _, store, _ = env
    p = os.path.realpath(gen / "run2" / "v.mp4")
    store[obs.build_obs_key(p, "c1")] = b"0123456789"
    r = c.get("/web/file", params={"path": p, "chat_id": "c1"},
              headers={"Range": "bytes=2-5"})
    assert r.status_code == 206 and r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"


def test_404_when_nowhere(env):
    c, gen, _, _, _ = env
    p = os.path.realpath(gen / "run3" / "none.png")
    assert c.get("/web/file", params={"path": p, "chat_id": "c1"}).status_code == 404


def test_db_image_same_name_other_run_not_substituted(env, monkeypatch):
    """Вчерашний slide-1.png вычищен с диска, сегодня есть другой slide-1.png."""
    c, gen, _, store, _ = env
    old = os.path.realpath(gen / "runA" / "slide-1.png")
    store[obs.build_obs_key(old, "c1")] = b"YESTERDAY"
    _put_local(gen / "runB" / "slide-1.png", b"TODAY")

    async def fake_resolve(chat_id, message_id, file_name):
        return old
    monkeypatch.setattr(web_ui, "_resolve_chat_file_path", fake_resolve)
    r = c.get("/web/db_image", params={"chat_id": "c1", "message_id": "m-old",
                                        "file_name": "slide-1.png"})
    assert r.status_code == 200 and r.content == b"YESTERDAY"
    assert r.headers["x-image-source"] == "obs"


def test_db_image_by_image_key(env):
    c, _, _, _, pool = env
    pool.rows["image_key::text"] = {"file_name": "g.png", "mime_type": "image/png",
                                    "image_data": b"DB", "image_key": "k1"}
    r = c.get("/web/db_image", params={"image_key": "k1", "message_id": "m"})
    assert r.content == b"DB" and r.headers["x-image-key"] == "k1"


def test_db_image_message_fallback_when_no_file(env, monkeypatch):
    c, _, _, _, pool = env
    pool.rows["message_id"] = {"file_name": "g.png", "mime_type": "image/png",
                               "image_data": b"BYMSG", "image_key": None}

    async def fake_resolve(*a):
        return None
    monkeypatch.setattr(web_ui, "_resolve_chat_file_path", fake_resolve)
    r = c.get("/web/db_image", params={"chat_id": "c1", "message_id": "m",
                                        "file_name": "generated_0.jpg"})
    assert r.content == b"BYMSG"
