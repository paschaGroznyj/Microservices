"""Workspace lifecycle + TTL cleanup helpers."""
import os
import time


def test_create_workspace_dir_makes_output_and_marker(appmod, ws_root):
    wid = appmod._create_workspace_dir()
    ws = ws_root / wid
    assert ws.is_dir()
    assert (ws / "output").is_dir()
    assert (ws / ".created_at").exists()


def test_get_workspace_path_found_and_missing(appmod, ws_root):
    wid = appmod._create_workspace_dir()
    assert appmod._get_workspace_path(wid) == str(ws_root / wid)
    assert appmod._get_workspace_path("doesnotexist") is None


def test_touch_workspace_writes_last_active(appmod, ws_root):
    wid = appmod._create_workspace_dir()
    appmod._touch_workspace(wid)
    assert (ws_root / wid / ".last_active").exists()


def test_list_workspace_files_walks_tree(appmod, ws_root):
    wid = appmod._create_workspace_dir()
    (ws_root / wid / "output" / "r.txt").write_text("data")
    names = {f["filename"] for f in appmod._list_workspace_files(wid)}
    assert any(n.endswith("r.txt") for n in names)


def test_has_keep_hold(appmod, ws_root):
    wid = appmod._create_workspace_dir()
    ws = ws_root / wid
    assert appmod._has_keep_hold(str(ws)) is False
    (ws / appmod.WORKSPACE_KEEP_MARKER).write_text("")
    assert appmod._has_keep_hold(str(ws)) is True


def test_cleanup_removes_expired(appmod, ws_root, monkeypatch):
    monkeypatch.setattr(appmod, "WORKSPACE_TTL_SECONDS", 1)
    wid = appmod._create_workspace_dir()
    old = time.time() - 9999
    (ws_root / wid / ".last_active").write_text(str(old))
    removed = appmod._cleanup_expired_workspaces()
    assert removed == 1
    assert not (ws_root / wid).exists()


def test_cleanup_keeps_fresh(appmod, ws_root, monkeypatch):
    monkeypatch.setattr(appmod, "WORKSPACE_TTL_SECONDS", 99999)
    wid = appmod._create_workspace_dir()
    removed = appmod._cleanup_expired_workspaces()
    assert removed == 0
    assert (ws_root / wid).exists()


def test_cleanup_keeps_only_hold(appmod, ws_root, monkeypatch):
    monkeypatch.setattr(appmod, "WORKSPACE_TTL_SECONDS", 1)
    # .git больше не держит область: просроченная — удаляется
    wg = appmod._create_workspace_dir()
    (ws_root / wg / "repo" / ".git").mkdir(parents=True)
    (ws_root / wg / ".last_active").write_text(str(time.time() - 9999))
    # keep-hold workspace, expired -> skipped
    wk = appmod._create_workspace_dir()
    (ws_root / wk / appmod.WORKSPACE_KEEP_MARKER).write_text("")
    (ws_root / wk / ".last_active").write_text(str(time.time() - 9999))
    removed = appmod._cleanup_expired_workspaces()
    assert removed == 1
    assert not (ws_root / wg).exists()
    assert (ws_root / wk).exists()
