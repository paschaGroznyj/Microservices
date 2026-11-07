"""Служебный вход агента после фоновой задачи (task_trigger.py)."""
import task_trigger as tt


def _payload(**kw):
    p = {"user_id": -2, "task_trigger": True, "chat_id": "c1", "is_from_bot": False,
         "owner_user_id": 56,
         "task": {"task_id": "t1", "label": "Парсинг", "kind": "script", "status": "failed",
                  "exit_code": 1, "elapsed_ms": 75_000, "workspace_id": "w1",
                  "error": "Скрипт завершился с кодом 1", "stderr_tail": "ValueError: boom",
                  "stdout_tail": "ok", "files": [{"filename": "r.csv"}],
                  "stages": [{"title": "Сбор", "status": "done"}, {"title": "Запись", "status": "failed"}]}}
    p.update(kw)
    return p


def test_is_task_trigger_requires_flag_and_service_id():
    assert tt.is_task_trigger(_payload())
    assert not tt.is_task_trigger(_payload(task_trigger=False))
    assert not tt.is_task_trigger(_payload(user_id=56))
    assert not tt.is_task_trigger(_payload(is_from_bot=True))
    assert not tt.is_task_trigger(_payload(user_id="x"))


def test_owner_user_id_from_payload_or_task():
    assert tt.owner_user_id(_payload()) == 56
    p = _payload(owner_user_id=None)
    p["task"]["owner"] = {"user_id": "57"}
    assert tt.owner_user_id(p) == 57
    assert tt.owner_user_id(_payload(owner_user_id=None)) is None


def test_build_message_contains_result():
    m = tt.build_message(_payload())
    assert m.startswith("[TASK TRIGGER")
    for part in ("task_id: t1", "завершилась с ошибкой", "1 мин 15 с", "ValueError: boom",
                 "r.csv", "Запись — failed", "w1"):
        assert part in m, part
