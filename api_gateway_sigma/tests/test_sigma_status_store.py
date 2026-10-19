"""Тесты SigmaStatusStore — in-memory состояние для polling/SSE.
Чистый класс без инфраструктуры — ничего мокать не нужно.
"""
import pytest

from sigma_status_store import SigmaStatusStore


@pytest.fixture()
def store():
    return SigmaStatusStore()


# ---------- состояние чата ----------

def test_unknown_chat_is_idle(store):
    assert store.get_chat_state("c1") == {"status": "idle"}


def test_mark_running_then_state_is_running(store):
    store.mark_running("c1", request_id="req-1")
    assert store.get_chat_state("c1") == {"status": "running"}
    assert store.get_chat_request_id("c1") == "req-1"


def test_mark_done_returns_message_once(store):
    store.mark_running("c1", request_id="req-1")
    store.mark_done("c1", {"message": "готово"})
    first = store.get_chat_state("c1")
    assert first == {"status": "done", "message": {"message": "готово"}}
    # повторный опрос не дублирует — сброс в idle
    assert store.get_chat_state("c1") == {"status": "idle"}


def test_mark_done_preserves_request_id(store):
    store.mark_running("c1", request_id="req-42")
    store.mark_done("c1", {"message": "x"})
    # request_id сохранён до считывания done
    assert store.get_chat_request_id("c1") == "req-42"


def test_chat_id_normalized_to_str(store):
    store.mark_running(123)
    assert store.get_chat_state(123) == {"status": "running"}
    assert store.get_chat_state("123") == {"status": "running"}


# ---------- пользовательские события ----------

def test_push_and_pop_user_events(store):
    store.push_user_event("pavel", {"type": "article"})
    store.push_user_event("pavel", {"type": "error"})
    events = store.pop_user_events("pavel")
    assert events == [{"type": "article"}, {"type": "error"}]
    # после pop — пусто
    assert store.pop_user_events("pavel") == []


def test_broadcast_user_event_reaches_known_users(store):
    store.pop_user_events("u1")  # регистрируем u1 как known
    store.pop_user_events("u2")
    store.broadcast_user_event({"type": "feedback"})
    assert store.pop_user_events("u1") == [{"type": "feedback"}]
    assert store.pop_user_events("u2") == [{"type": "feedback"}]


def test_user_events_capped(store):
    for i in range(250):
        store.push_user_event("pavel", {"i": i})
    events = store.pop_user_events("pavel")
    assert len(events) == 200  # _MAX_USER_EVENTS
    assert events[-1] == {"i": 249}  # остались последние


# ---------- group-события ----------

def test_group_events_cursor_flow(store):
    start = store.group_cursor("room1")
    store.push_group_event("room1", {"e": 1})
    store.push_group_event("room1", {"e": 2})
    events, cursor = store.pop_group_events("room1", start)
    assert events == [{"e": 1}, {"e": 2}]
    assert cursor > start
    # с нового курсора — пусто
    assert store.pop_group_events("room1", cursor) == ([], cursor)


# ---------- GC ----------

def test_gc_drops_stale_done(store, mocker):
    import sigma_status_store as mod
    store.mark_done("c1", {"message": "x"})
    # состарим запись за предел TTL и дёрнем get_chat_state (внутри _gc)
    store._chats["c1"]["ts"] -= mod._DONE_TTL_SEC + 10
    # прямой вызов _gc должен вычистить протухший done
    store._gc()
    assert "c1" not in store._chats
