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
