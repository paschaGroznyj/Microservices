"""Тесты editor_gw.py — роутер веб-редактора /editor.

Главное: readonly-режим (Sigma) запрещает сохранение на сервере, а не только
прячет кнопку; приём событий от песочницы закрыт токеном; хаб подписок
схлопывает переполненную очередь. chats_service и песочница — моки.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

import editor_gw as eg

USER = {"user_id": 56, "username": "pavel"}
TOKEN = "s3cret"


def _resp(data=None, status=200, content=b"", headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data if data is not None else {}
    r.content = content
    r.headers = headers or {}
    return r


class FakeTemplates:
    def __init__(self):
        self.calls = []

    def TemplateResponse(self, name, ctx):
        self.calls.append((name, ctx))
        return HTMLResponse("ok")


@pytest.fixture
def make(monkeypatch):
    """make(readonly=..., chat_transport=..., user=...) -> (client, http, templates)."""
    monkeypatch.setattr(eg, "EDITOR_NOTIFY_TOKEN", TOKEN)
    monkeypatch.setattr(eg, "_watchers", {})

    def _make(readonly=False, chat_transport="ws", user=USER, owns=True):
        http = AsyncMock()
        http.get.return_value = _resp({"owns": owns})
        templates = FakeTemplates()

        async def get_user(request):
            return user

        eg.bind(http, "http://chats", "http://sandbox", get_user, templates,
                readonly=readonly, chat_transport=chat_transport)
        app = FastAPI()
        app.include_router(eg.router)
        return TestClient(app), http, templates

    yield _make
    eg.bind(None, None, None, None, None)


def _subscribe(wid, maxsize=64):
    q = asyncio.Queue(maxsize=maxsize)
    eg._watchers.setdefault(wid, set()).add(q)
    return q


SAVE = {"workspace_id": "ws1", "path": "a.py", "content": "print(1)"}


# ---------- bind ----------

@pytest.mark.parametrize("given, expected", [("sse", "sse"), ("ws", "ws"), ("junk", "ws"), (None, "ws")])
def test_bind_normalizes_chat_transport(make, given, expected):
    make(chat_transport=given)
    assert eg._chat_transport == expected


# ---------- страница /editor ----------

def test_editor_page_passes_readonly_and_transport(make):
    client, _, templates = make(readonly=True, chat_transport="sse")
    r = client.get("/editor?chat_id=c1&workspace_id=ws1")
    assert r.status_code == 200
    name, ctx = templates.calls[-1]
    assert name == "editor.html"
    assert ctx["readonly"] is True and ctx["chat_transport"] == "sse"
    assert ctx["chat_id"] == "c1" and ctx["workspace_id"] == "ws1"


def test_editor_page_main_is_editable(make):
    client, _, templates = make()
    client.get("/editor")
    _, ctx = templates.calls[-1]
    assert ctx["readonly"] is False and ctx["chat_transport"] == "ws"


def test_editor_page_redirects_anonymous(make):
    client, _, _ = make(user=None)
    r = client.get("/editor", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login-page"


# ---------- PUT /api/editor/file ----------

def test_readonly_save_is_403_before_any_backend_call(make):
    client, http, _ = make(readonly=True)
    r = client.put("/api/editor/file", json=SAVE)
    assert r.status_code == 403
    http.get.assert_not_awaited()   # даже проверку владельца не делаем
    http.put.assert_not_awaited()   # песочница не тронута


def test_readonly_save_still_401_for_anonymous(make):
    client, _, _ = make(readonly=True, user=None)
    assert client.put("/api/editor/file", json=SAVE).status_code == 401


def test_save_writes_to_sandbox_and_publishes_change(make):
    client, http, _ = make()
    change = {"created": [], "modified": ["a.py"], "deleted": [], "tree_changed": False,
              "truncated": False, "source": "editor"}
    http.put.return_value = _resp({"ok": True, "change": change})
    q = _subscribe("ws1")
    r = client.put("/api/editor/file", json=SAVE)
    assert r.status_code == 200 and r.json() == {"ok": True}
    url = http.put.await_args.args[0]
    assert url == "http://sandbox/workspace/ws1/editor/write"
    assert http.put.await_args.kwargs["json"] == {"path": "a.py", "content": "print(1)"}
    assert q.get_nowait() == ("change", change)


def test_save_legacy_sandbox_publishes_modified_path(make):
    client, http, _ = make()
    http.put.return_value = _resp({"ok": True})  # старая песочница без поля change
    q = _subscribe("ws1")
    client.put("/api/editor/file", json=SAVE)
    event, data = q.get_nowait()
    assert event == "change" and data["modified"] == ["a.py"] and data["source"] == "editor"


def test_save_unchanged_content_publishes_nothing(make):
    client, http, _ = make()
    http.put.return_value = _resp({"ok": True, "change": None})
    q = _subscribe("ws1")
    client.put("/api/editor/file", json=SAVE)
    assert q.empty()


def test_save_foreign_workspace_403(make):
    client, http, _ = make(owns=False)
    assert client.put("/api/editor/file", json=SAVE).status_code == 403
    http.put.assert_not_awaited()


def test_save_registry_down_502(make):
    client, http, _ = make()
    http.get.side_effect = RuntimeError("chats down")
    assert client.put("/api/editor/file", json=SAVE).status_code == 502


def test_save_sandbox_error_detail_is_forwarded(make):
    client, http, _ = make()
    http.put.return_value = _resp({"detail": "Файл слишком большой"}, status=413)
    r = client.put("/api/editor/file", json=SAVE)
    assert r.status_code == 413 and r.json()["detail"] == "Файл слишком большой"


# ---------- чтение в readonly разрешено ----------

def test_readonly_can_read_file_and_tree(make):
    client, http, _ = make(readonly=True)
    owns = _resp({"owns": True})
    http.get.side_effect = [owns, _resp({"content": "x"}), owns, _resp({"tree": []})]
    assert client.get("/api/editor/file?workspace_id=ws1&path=a.py").json() == {"content": "x"}
    assert client.get("/api/editor/tree?workspace_id=ws1").json() == {"tree": []}


def test_download_sets_utf8_content_disposition(make):
    client, http, _ = make(readonly=True)
    http.get.side_effect = [_resp({"owns": True}),
                            _resp(content=b"hi", headers={"content-type": "text/plain"})]
    r = client.get("/api/editor/download?workspace_id=ws1&path=/output/отчёт.md")
    assert r.status_code == 200 and r.content == b"hi"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;") and "filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82.md" in cd
    assert http.get.await_args.args[0] == "http://sandbox/workspace/ws1/file/output/отчёт.md"


# ---------- POST /internal/editor/notify ----------

def _notify(client, body, token=TOKEN):
    headers = {"X-Editor-Token": token} if token is not None else {}
    return client.post("/internal/editor/notify", json=body, headers=headers)


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_notify_rejects_bad_token(make, token):
    client, _, _ = make()
    assert _notify(client, {"workspace_id": "ws1", "event": "ttl"}, token=token).status_code == 403


def test_notify_disabled_when_token_not_configured(make, monkeypatch):
    client, _, _ = make()
    monkeypatch.setattr(eg, "EDITOR_NOTIFY_TOKEN", "")
    assert _notify(client, {"workspace_id": "ws1", "event": "ttl"}, token="").status_code == 403


@pytest.mark.parametrize("body", [
    {"workspace_id": "ws1", "event": "rm -rf"},
    {"workspace_id": "../etc", "event": "change"},
    {"workspace_id": "", "event": "gone"},
])
def test_notify_rejects_bad_event_or_id(make, body):
    client, _, _ = make()
    assert _notify(client, body).status_code == 400


def test_notify_sanitizes_change_payload(make):
    client, _, _ = make()
    q = _subscribe("ws1")
    raw = {"created": ["ok.txt", 5, "", None], "modified": "not-a-list",
           "deleted": [f"f{i}" for i in range(300)], "tree_changed": 1,
           "source": "x" * 100, "evil": "drop me"}
    r = _notify(client, {"workspace_id": "ws1", "event": "change", "data": raw})
    assert r.json() == {"ok": True, "delivered": 1}
    _, data = q.get_nowait()
    assert data["created"] == ["ok.txt"]
    assert data["modified"] == []
    assert len(data["deleted"]) == eg._PATH_CAP
    assert data["tree_changed"] is True and len(data["source"]) == 32
    assert "evil" not in data


def test_notify_ttl_and_gone_payloads(make):
    client, _, _ = make()
    q = _subscribe("ws1")
    _notify(client, {"workspace_id": "ws1", "event": "ttl", "data": {"permanent": 1, "x": 2}})
    _notify(client, {"workspace_id": "ws1", "event": "gone", "data": {"x": 1}})
    assert q.get_nowait() == ("ttl", {"permanent": True})
    assert q.get_nowait() == ("gone", {})


def test_notify_without_subscribers_delivers_zero(make):
    client, _, _ = make()
    r = _notify(client, {"workspace_id": "nobody", "event": "ttl"})
    assert r.json()["delivered"] == 0


# ---------- publish: переполнение очереди ----------

def test_publish_overflow_collapses_to_full_resync(monkeypatch):
    monkeypatch.setattr(eg, "_watchers", {})
    q = _subscribe("ws1", maxsize=1)
    eg.publish("ws1", "change", {"modified": ["a"]})
    eg.publish("ws1", "change", {"modified": ["b"]})  # не влезло
    event, data = q.get_nowait()
    assert event == "change" and data["tree_changed"] is True and data["truncated"] is True
    assert q.empty()


def test_publish_overflow_keeps_gone(monkeypatch):
    monkeypatch.setattr(eg, "_watchers", {})
    q = _subscribe("ws1", maxsize=1)
    eg.publish("ws1", "change", {})
    eg.publish("ws1", "gone", {})
    assert q.get_nowait() == ("gone", {})


# ---------- GET /api/editor/watch: проверки до открытия стрима ----------

def test_watch_requires_auth(make):
    client, _, _ = make(user=None)
    assert client.get("/api/editor/watch?workspace_id=ws1").status_code == 401


def test_watch_rejects_bad_workspace_id(make):
    client, http, _ = make()
    assert client.get("/api/editor/watch?workspace_id=a/b").status_code == 400
    http.get.assert_not_awaited()


def test_watch_limits_subscribers_per_workspace(make):
    client, _, _ = make()
    for _ in range(eg._WATCH_MAX_PER_WS):
        _subscribe("ws1")
    assert client.get("/api/editor/watch?workspace_id=ws1").status_code == 429
