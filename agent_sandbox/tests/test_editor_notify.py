"""Метаданные изменений области (.ws_meta) и push в редактор.

_editor_parse_changed — разбор changed_files от инструментов;
_ws_meta_apply — сравнение с index.json и журнал events.jsonl;
_editor_notify_sync — доставка во все gateway из EDITOR_NOTIFY_URLS."""
import json
import os

import pytest


@pytest.fixture
def ws(ws_root):
    p = ws_root / "w1"
    p.mkdir()
    return p


def _meta(ws):
    d = ws / ".ws_meta"
    idx = json.loads((d / "index.json").read_text()) if (d / "index.json").exists() else None
    ev = (d / "events.jsonl").read_text().splitlines() if (d / "events.jsonl").exists() else []
    return idx, [json.loads(x) for x in ev]


# ---------- _editor_parse_changed ----------

@pytest.mark.parametrize("changed", [None, "", "  ", [], ",, ,"])
def test_parse_changed_empty_means_no_event(appmod, ws, changed):
    assert appmod._editor_parse_changed(str(ws), changed) is None


def test_parse_changed_star_is_full_resync(appmod, ws):
    assert appmod._editor_parse_changed(str(ws), "a.py,*") == ([], True)


def test_parse_changed_normalizes_and_dedups(appmod, ws):
    rels, full = appmod._editor_parse_changed(
        str(ws), f"repo/a.py, ./repo/a.py\n{ws}/out/b.txt,repo/a.py")
    assert rels == ["repo/a.py", "out/b.txt"] and full is False


def test_parse_changed_drops_outside_and_service_paths(appmod, ws):
    res = appmod._editor_parse_changed(
        str(ws), ["../../etc/passwd", "/etc/hosts", ".ws_meta/index.json",
                  ".last_active", "x/.tmp_editor_123"])
    assert res is None


def test_parse_changed_root_dir_means_full(appmod, ws):
    assert appmod._editor_parse_changed(str(ws), ".") == ([], True)


# ---------- _ws_meta_apply ----------

def test_apply_created_modified_deleted_and_noop(appmod, ws):
    f = ws / "a.txt"
    f.write_text("1")
    r = appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    assert r["created"] == ["a.txt"] and r["changed"] and r["tree_changed"] and r["seq"] == 1

    r = appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")  # касание без правки
    assert not r["changed"] and r["seq"] == 1

    f.write_text("22")
    r = appmod._ws_meta_apply(str(ws), ["a.txt"], False, "editor")
    assert r["modified"] == ["a.txt"] and r["seq"] == 2 and not r["tree_changed"]

    f.unlink()
    r = appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    assert r["deleted"] == ["a.txt"] and r["tree_changed"] and r["seq"] == 3

    idx, events = _meta(ws)
    assert idx["seq"] == 3 and "a.txt" not in idx["files"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[1]["source"] == "editor"


def test_apply_same_content_rewrite_is_not_a_change(appmod, ws):
    f = ws / "a.txt"
    f.write_text("same")
    appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    os.utime(f, (1, 1))  # mtime другой, sha1 тот же
    assert not appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")["changed"]


def test_apply_deleted_unknown_file_still_reported(appmod, ws):
    r = appmod._ws_meta_apply(str(ws), ["ghost.txt"], False, "execute")
    assert r["deleted"] == ["ghost.txt"]


def test_apply_directory_forces_resync(appmod, ws):
    (ws / "d").mkdir()
    r = appmod._ws_meta_apply(str(ws), ["d"], False, "execute")
    assert r["truncated"] and r["tree_changed"]


def test_apply_broken_index_forces_resync(appmod, ws):
    (ws / ".ws_meta").mkdir()
    (ws / ".ws_meta" / "index.json").write_text("{not json")
    (ws / "a.txt").write_text("1")
    r = appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    assert r["truncated"] and r["changed"]
    idx, _ = _meta(ws)
    assert "a.txt" in idx["files"]  # индекс пересобран


def test_apply_hides_meta_from_git(appmod, ws):
    (ws / ".git").mkdir()
    (ws / "a.txt").write_text("1")
    appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    assert "/.ws_meta/" in (ws / ".git" / "info" / "exclude").read_text().splitlines()
    appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    assert (ws / ".git" / "info" / "exclude").read_text().count("/.ws_meta/") == 1


def test_events_ring_is_trimmed(appmod, ws, monkeypatch):
    monkeypatch.setattr(appmod, "WS_META_EVENTS_MAX", 3)
    f = ws / "a.txt"
    for i in range(10):
        f.write_text(str(i) * (i + 1))
        appmod._ws_meta_apply(str(ws), ["a.txt"], False, "execute")
    _, events = _meta(ws)
    assert len(events) <= 6 and events[-1]["seq"] == 10


# ---------- _workspace_files_changed ----------

def test_files_changed_unknown_workspace_is_none(appmod, ws_root):
    assert appmod._workspace_files_changed("nope", "a.py", "execute") is None
    assert appmod._workspace_files_changed(None, "a.py", "execute") is None


def test_files_changed_touches_ttl_and_tags_source(appmod, ws):
    (ws / "a.txt").write_text("1")
    data = appmod._workspace_files_changed("w1", "a.txt", "submit")
    assert data["source"] == "submit" and data["ttl_reset"] is True and "changed" not in data
    assert (ws / ".last_active").exists()
    assert appmod._workspace_files_changed("w1", "a.txt", "submit") is None  # повтор без правки


def test_files_changed_survives_meta_crash(appmod, ws, monkeypatch):
    def boom(*a):
        raise RuntimeError("disk")
    monkeypatch.setattr(appmod, "_ws_meta_apply", boom)
    data = appmod._workspace_files_changed("w1", "a.txt", "execute")
    assert data["truncated"] and data["tree_changed"]


# ---------- _editor_notify_sync ----------

class FakeOpener:
    def __init__(self, fail=()):
        self.reqs, self.fail = [], set(fail)

    def open(self, req, timeout=None):
        self.reqs.append(req)
        if req.full_url in self.fail:
            raise OSError("down")

        class R:
            status = 200
            def __enter__(s): return s
            def __exit__(s, *a): return False
        return R()


URLS = ["http://api_gateway:7000/internal/editor/notify",
        "http://api_gateway_sigma:7010/internal/editor/notify"]


def test_notify_skipped_without_token(appmod, monkeypatch):
    op = FakeOpener()
    monkeypatch.setattr(appmod, "_editor_opener", op)
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_TOKEN", "")
    assert appmod._editor_notify_sync("w1", "ttl", {}) is False and op.reqs == []


def test_notify_goes_to_every_gateway(appmod, monkeypatch):
    op = FakeOpener()
    monkeypatch.setattr(appmod, "_editor_opener", op)
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_TOKEN", "t")
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_URLS", URLS)
    assert appmod._editor_notify_sync("w1", "change", {"modified": ["а.py"]}) is True
    assert [r.full_url for r in op.reqs] == URLS
    r = op.reqs[0]
    assert r.get_header("X-editor-token") == "t" and r.get_method() == "POST"
    assert json.loads(r.data) == {"workspace_id": "w1", "event": "change", "data": {"modified": ["а.py"]}}


def test_notify_one_gateway_down_still_delivers(appmod, monkeypatch):
    op = FakeOpener(fail={URLS[0]})
    monkeypatch.setattr(appmod, "_editor_opener", op)
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_TOKEN", "t")
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_URLS", URLS)
    assert appmod._editor_notify_sync("w1", "ttl", {}) is True and len(op.reqs) == 2


def test_notify_all_down_is_false(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "_editor_opener", FakeOpener(fail=set(URLS)))
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_TOKEN", "t")
    monkeypatch.setattr(appmod, "EDITOR_NOTIFY_URLS", URLS)
    assert appmod._editor_notify_sync("w1", "ttl", {}) is False


def test_notify_urls_env_split(monkeypatch):
    import importlib, app as a
    monkeypatch.setenv("EDITOR_NOTIFY_URL", " http://x/n , ,http://y/n ")
    importlib.reload(a)
    assert a.EDITOR_NOTIFY_URLS == ["http://x/n", "http://y/n"]
    monkeypatch.delenv("EDITOR_NOTIFY_URL")
    importlib.reload(a)
    assert len(a.EDITOR_NOTIFY_URLS) == 2  # по умолчанию оба gateway
