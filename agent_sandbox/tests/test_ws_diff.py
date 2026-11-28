"""Теневые git-снимки (ws_diff.py) и эндпоинты /diff."""
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

import ws_diff


@pytest.fixture
def client(appmod, ws_root):
    return TestClient(appmod.app)


def _create(client):
    r = client.post("/workspace/create")
    assert r.status_code == 200
    return r.json()["workspace_id"], r.json()["path"]


def test_create_inits_shadow_git(client):
    wid, path = _create(client)
    gd = os.path.join(path, ".ws_meta", "shadow", "root.git")
    assert os.path.isfile(os.path.join(gd, "HEAD"))
    assert not os.path.exists(os.path.join(path, ".git"))      # пользовательского .git нет
    ex = open(os.path.join(gd, "info", "exclude"), encoding="utf-8").read()
    assert "/.ws_meta/" in ex and "/output/.scripts/" in ex


def test_snap_base_then_changes(client):
    wid, path = _create(client)
    open(os.path.join(path, "a.py"), "w").write("x = 1\n")
    r = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r1", "chat_id": "c1"})
    assert r.status_code == 200 and r.json()["files_count"] == 0      # первый снимок = base

    open(os.path.join(path, "a.py"), "w").write("x = 2\ny = 3\n")
    open(os.path.join(path, "b.txt"), "w").write("new\n")
    open(os.path.join(path, ".env"), "w").write("TOKEN=secret\n")
    os.makedirs(os.path.join(path, "output", ".scripts"), exist_ok=True)
    open(os.path.join(path, "output", ".scripts", "s.py"), "w").write("print()\n")
    body = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r1"}).json()
    files = {f["path"]: f for f in body["files"]}
    assert set(files) == {"a.py", "b.txt", ".env"}
    assert files["a.py"]["status"] == "M" and (files["a.py"]["add"], files["a.py"]["del"]) == (2, 1)
    assert "+x = 2" in files["a.py"]["patch"] and files["a.py"]["patch"].startswith("@@")
    assert files["b.txt"]["status"] == "A"
    assert files[".env"]["secret"] is True and "patch" not in files[".env"]
    assert body["step"]["files_count"] == 3
    assert body["chat_id"] == "c1"


def test_step_vs_total_and_delete(client):
    wid, path = _create(client)
    open(os.path.join(path, "keep.txt"), "w").write("1\n")
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r2"})
    open(os.path.join(path, "n1.txt"), "w").write("other content\n")
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r2"})
    os.remove(os.path.join(path, "keep.txt"))
    body = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r2"}).json()
    assert body["step"]["paths"] == ["keep.txt"]
    st = {f["path"]: f["status"] for f in body["files"]}
    assert st == {"n1.txt": "A", "keep.txt": "D"}


def test_nested_repo_cloned_during_request(client):
    wid, path = _create(client)
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r3"})
    repo = os.path.join(path, "repo")
    subprocess.run(
        "git init -q repo && cd repo && printf 'one\\n' > f.txt && git add . "
        "&& git -c user.name=t -c user.email=t@t commit -qm init",
        shell=True, check=True, cwd=path)
    open(os.path.join(repo, "f.txt"), "w").write("two\n")
    body = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r3"}).json()
    assert [f["path"] for f in body["files"]] == ["repo/f.txt"]          # клон не считается «+N файлов»
    assert body["cloned"] == ["repo"]
    # пользовательский репозиторий не тронут: индекс и HEAD свои
    out = subprocess.run(["git", "status", "--short"], cwd=repo, capture_output=True, text=True).stdout
    assert out.strip() == "M f.txt"


def test_binary_file(client):
    wid, path = _create(client)
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r4"})
    open(os.path.join(path, "img.bin"), "wb").write(bytes(range(256)) * 4)
    f = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r4"}).json()["files"][0]
    assert f["binary"] is True and f["size_after"] == 1024 and f["size_before"] is None


def test_big_file_excluded(client, monkeypatch):
    monkeypatch.setattr(ws_diff, "BIG_FILE_BYTES", 100)
    wid, path = _create(client)
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r5"})
    open(os.path.join(path, "big.dat"), "w").write("x" * 500)
    open(os.path.join(path, "small.txt"), "w").write("ok\n")
    body = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r5"}).json()
    assert [f["path"] for f in body["files"]] == ["small.txt"]



def test_snapshot_budget_drops_largest(client, monkeypatch):
    monkeypatch.setattr(ws_diff, "SNAP_MAX_BYTES", 1000)
    wid, path = _create(client)
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rb"})
    for name, size in (("a.bin", 800), ("b.bin", 600), ("c.txt", 10)):
        open(os.path.join(path, name), "w").write("x" * size)
    body = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rb"}).json()
    assert sorted(f["path"] for f in body["files"]) == ["b.bin", "c.txt"]


def test_stale_lock_and_tmp_pack_cleaned(client):
    wid, path = _create(client)
    gd = os.path.join(path, ".ws_meta", "shadow", "root.git")
    lock = os.path.join(gd, "index.lock")
    tmp_pack = os.path.join(gd, "objects", "pack", "tmp_pack_abc123")
    os.makedirs(os.path.dirname(tmp_pack), exist_ok=True)
    open(lock, "w").close()
    open(tmp_pack, "wb").write(b"\0" * 4096)
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rl"})
    open(os.path.join(path, "n.txt"), "w").write("1\n")
    body = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rl"}).json()
    assert [f["path"] for f in body["files"]] == ["n.txt"]
    assert not os.path.exists(lock) and not os.path.exists(tmp_pack)


def test_timeout_cleans_garbage(client, monkeypatch):
    wid, path = _create(client)
    gd = os.path.join(path, ".ws_meta", "shadow", "root.git")
    real_git = ws_diff._git

    def fake_git(git_dir, work_tree, *args, **kw):
        if args[:1] == ("add",):
            open(os.path.join(git_dir, "index.lock"), "w").close()
            open(os.path.join(git_dir, "objects", "pack", "tmp_pack_x"), "wb").write(b"\0" * 100)
            raise subprocess.TimeoutExpired(["git", "add"], ws_diff.GIT_TIMEOUT)
        return real_git(git_dir, work_tree, *args, **kw)

    monkeypatch.setattr(ws_diff, "_git", fake_git)
    r = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rt"})
    assert r.status_code == 500 and "timeout" in r.json()["detail"]
    assert not os.path.exists(os.path.join(gd, "index.lock"))
    assert not os.path.exists(os.path.join(gd, "objects", "pack", "tmp_pack_x"))
    monkeypatch.setattr(ws_diff, "_git", real_git)
    assert client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rt"}).status_code == 200

def test_chat_restore_and_attach(client):
    wid, path = _create(client)
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r6", "chat_id": "chat-9"})
    open(os.path.join(path, "x.md"), "w").write("# hi\n")
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r6"})
    client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "r7", "chat_id": "chat-9"})  # пустой
    r = client.post("/diff/attach", json={"request_id": "r6", "message_id": "msg-1"})
    assert r.json()["workspaces"] == [wid]
    items = client.get("/diff/chat/chat-9").json()["items"]
    assert len(items) == 1
    assert items[0]["request_id"] == "r6" and items[0]["message_id"] == "msg-1"
    assert items[0]["workspace_id"] == wid and items[0]["files"][0]["path"] == "x.md"
    assert client.get("/diff/chat/other").json()["items"] == []
    assert client.get(f"/workspace/{wid}/diff", params={"request_id": "r6"}).json()["files_count"] == 1
    assert client.get(f"/workspace/{wid}/diff", params={"request_id": "nope"}).status_code == 404


def test_shadow_hidden_from_editor_and_listing(client, appmod):
    wid, path = _create(client)
    names = [f["filename"] for f in appmod._list_workspace_files(wid)]
    assert not any(n.startswith(".ws_meta") for n in names)
    tree = client.get(f"/workspace/{wid}/editor/tree").json()
    assert ".ws_meta" not in str(tree)


# ── шаги: diff одного вызова тула (phase pre/post + call_id) ─────────

def _sn(client, wid, rid, **kw):
    r = client.post(f"/workspace/{wid}/diff/snap", json={"request_id": rid, "chat_id": "c9", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_step_per_call_sequential(client):
    wid, path = _create(client)
    open(os.path.join(path, "a.py"), "w").write("x = 1\n")
    _sn(client, wid, "rs", call_id="t1", phase="pre")
    open(os.path.join(path, "a.py"), "w").write("x = 2\n")
    open(os.path.join(path, "b.txt"), "w").write("b\n")
    s1 = _sn(client, wid, "rs", call_id="t1", phase="post")["step"]
    assert s1["files_count"] == 2 and s1["approx"] is False and s1["call_id"] == "t1"
    assert {i["p"]: (i["s"], i["a"], i["d"]) for i in s1["list"]} == {"a.py": ("M", 1, 1), "b.txt": ("A", 1, 0)}

    _sn(client, wid, "rs", call_id="t2", phase="pre")
    os.remove(os.path.join(path, "b.txt"))
    s2 = _sn(client, wid, "rs", call_id="t2", phase="post")["step"]
    assert [i["p"] for i in s2["list"]] == ["b.txt"] and s2["list"][0]["s"] == "D"

    d1 = client.get(f"/workspace/{wid}/diff/step", params={"request_id": "rs", "call_id": "t1", "chat_id": "c9"}).json()
    assert {f["path"] for f in d1["files"]} == {"a.py", "b.txt"}
    assert "+x = 2" in {f["path"]: f for f in d1["files"]}["a.py"]["patch"]
    d2 = client.get(f"/workspace/{wid}/diff/step", params={"request_id": "rs", "call_id": "t2"}).json()
    assert [(f["path"], f["status"]) for f in d2["files"]] == [("b.txt", "D")]
    # итог запроса не изменился от шагов: a.py M, b.txt создан и удалён → нет
    tot = client.get(f"/workspace/{wid}/diff", params={"request_id": "rs"}).json()
    assert [f["path"] for f in tot["files"]] == ["a.py"]


def test_step_parallel_marked_approx(client):
    wid, path = _create(client)
    _sn(client, wid, "rp", call_id="p1", phase="pre")
    _sn(client, wid, "rp", call_id="p2", phase="pre")
    open(os.path.join(path, "one.txt"), "w").write("1\n")
    s1 = _sn(client, wid, "rp", call_id="p1", phase="post")["step"]
    open(os.path.join(path, "two.txt"), "w").write("2\n")
    s2 = _sn(client, wid, "rp", call_id="p2", phase="post")["step"]
    assert s1["approx"] is True and s2["approx"] is True
    assert [i["p"] for i in s2["list"]] == ["one.txt", "two.txt"]      # окно p2 видит и p1 — поэтому approx


def test_step_no_changes_not_stored_and_access(client):
    wid, path = _create(client)
    _sn(client, wid, "rq", call_id="n1", phase="pre")
    s = _sn(client, wid, "rq", call_id="n1", phase="post")["step"]
    assert s["files_count"] == 0
    q = {"request_id": "rq", "call_id": "n1"}
    assert client.get(f"/workspace/{wid}/diff/step", params=q).status_code == 404
    _sn(client, wid, "rq", call_id="n2", phase="pre")
    open(os.path.join(path, "z.txt"), "w").write("z\n")
    _sn(client, wid, "rq", call_id="n2", phase="post")
    q = {"request_id": "rq", "call_id": "n2"}
    assert client.get(f"/workspace/{wid}/diff/step", params={**q, "chat_id": "c9"}).status_code == 200
    assert client.get(f"/workspace/{wid}/diff/step", params={**q, "chat_id": "other"}).status_code == 404
    assert client.get(f"/workspace/{wid}/diff/step", params={**q, "request_id": "nope"}).status_code == 404
    assert client.get("/workspace/../diff/step", params=q).status_code == 404
    assert client.get("/workspace/..%2F..%2Fetc/diff/step", params=q).status_code == 404
    assert client.post(f"/workspace/{wid}/diff/snap", json={"request_id": "rq", "phase": "bad"}).status_code == 422


def test_step_post_without_pre_falls_back_to_prev(client):
    wid, path = _create(client)
    _sn(client, wid, "rf")
    open(os.path.join(path, "f.txt"), "w").write("f\n")
    s = _sn(client, wid, "rf", call_id="x", phase="post")["step"]
    assert [i["p"] for i in s["list"]] == ["f.txt"]


def test_step_survives_gc_and_limits(client, monkeypatch):
    wid, path = _create(client)
    monkeypatch.setattr(ws_diff, "MAX_STEPS", 3)
    for i in range(5):
        _sn(client, wid, "rl", call_id=f"c{i}", phase="pre")
        open(os.path.join(path, f"f{i}.txt"), "w").write(f"{i}\n")
        _sn(client, wid, "rl", call_id=f"c{i}", phase="post")
    st = ws_diff._load_state(path)["requests"]["rl"]
    assert sorted(st["steps"]) == ["c2", "c3", "c4"] and st["open"] == {}
    gd = os.path.join(path, ".ws_meta", "shadow", "root.git")
    assert subprocess.run(["git", "--git-dir", gd, "config", "gc.auto"], capture_output=True, text=True).stdout.strip() == "0"
    d = client.get(f"/workspace/{wid}/diff/step", params={"request_id": "rl", "call_id": "c2"}).json()
    assert [f["path"] for f in d["files"]] == ["f2.txt"]
