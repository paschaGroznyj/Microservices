"""Тесты run_status.py — Redis-флаги «агент в работе» / «самоцикл».
Redis-клиент подменяется AsyncMock. async-корутины гоняем через asyncio.run
(без pytest-asyncio — его нет в эталонном окружении репо).
"""
import asyncio
import json
import pytest

import run_status


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def redis_mock(mocker):
    rm = mocker.AsyncMock()
    async def _empty_scan(*args, **kwargs):
        if False:
            yield b""
    rm.scan_iter = mocker.MagicMock(side_effect=_empty_scan)
    mocker.patch.object(run_status, "_redis", rm)
    return rm


# ---------- ключи ----------

def test_key_builders():
    assert run_status._key("c1") == "agent:running:c1"
    assert run_status._loop_key("c1") == "agent:loop:c1"


# ---------- mark_running / get_running ----------

def test_mark_running_sets_key_with_ttl(redis_mock):
    run(run_status.mark_running("c1", "req-1", model="coder"))
    # два ключа: флаг генерации + начальное состояние рассуждений
    assert redis_mock.set.await_count == 2
    args, kwargs = redis_mock.set.await_args_list[0]
    assert args[0] == "agent:running:c1"
    payload = json.loads(args[1])
    assert payload["request_id"] == "req-1" and payload["model"] == "coder"
    assert kwargs["ex"] == run_status.RUN_TTL
    r_args, r_kwargs = redis_mock.set.await_args_list[1]
    assert r_args[0] == run_status._reasoning_key("c1")
    assert json.loads(r_args[1])["completed"] is False
    assert r_kwargs["ex"] == run_status.RUN_TTL


def test_get_running_parses_json(redis_mock):
    redis_mock.get.return_value = json.dumps({"request_id": "r", "model": "m"})
    res = run(run_status.get_running("c1"))
    assert res["request_id"] == "r"


def test_get_running_none_when_empty(redis_mock):
    redis_mock.get.return_value = None
    assert run(run_status.get_running("c1")) is None


def test_clear_running_deletes_key(redis_mock):
    redis_mock.get.return_value = None  # рассуждений нет
    run(run_status.clear_running("c1"))
    redis_mock.delete.assert_awaited_once_with("agent:running:c1")


# ---------- loop-флаг ----------

def test_mark_loop_sets_with_ttl(redis_mock):
    run(run_status.mark_loop("c1"))
    redis_mock.set.assert_awaited_once_with("agent:loop:c1", "1", ex=run_status.LOOP_TTL)


def test_is_loop_active_true_false(redis_mock):
    redis_mock.exists.return_value = 1
    assert run(run_status.is_loop_active("c1")) is True
    redis_mock.exists.return_value = 0
    assert run(run_status.is_loop_active("c1")) is False


# ---------- безопасные дефолты ----------

def test_none_chat_id_is_noop(redis_mock):
    run(run_status.mark_running(None, "req"))
    run(run_status.mark_loop(None))
    redis_mock.set.assert_not_awaited()
    assert run(run_status.get_running(None)) is None
    assert run(run_status.is_loop_active(None)) is False


def test_redis_none_returns_safe_defaults(mocker):
    mocker.patch.object(run_status, "_redis", None)
    assert run(run_status.get_running("c1")) is None
    assert run(run_status.is_loop_active("c1")) is False
    run(run_status.mark_running("c1", "r"))  # не должно падать


def test_get_running_swallows_redis_error(redis_mock):
    redis_mock.get.side_effect = RuntimeError("boom")
    assert run(run_status.get_running("c1")) is None


def test_register_setting_models_extends_normalization():
    assert run_status.normalize_setting_model("coder") == "gpt"
    assert run_status.normalize_setting_model("designer") == "claude"
    assert run_status.normalize_setting_model("universal") == "glm"
    assert run_status.normalize_setting_model("gemini") == "gemini"
    assert run_status.normalize_setting_model("gigachat") == "gigachat"
    assert run_status.normalize_setting_model("giga") == "gigachat"
    assert run_status.normalize_setting_model("deepseek") == "deepseek"
    assert run_status.normalize_setting_model("ds") == "deepseek"

    run_status.register_setting_models([
        {"id": "deepseek", "setting_key": "ds", "aliases": ["r1", "deepseek-r1"]},
    ])
    assert run_status.normalize_setting_model("deepseek") == "ds"
    assert run_status.normalize_setting_model("r1") == "ds"
    assert run_status.normalize_setting_model("ds") == "ds"


# ---------- count_active_requests ----------

def _scan_keys(mocker, rm, keys):
    async def _scan(*args, **kwargs):
        for k in keys:
            yield k
    rm.scan_iter = mocker.MagicMock(side_effect=_scan)


def test_count_active_running_and_loop(mocker, redis_mock):
    _scan_keys(mocker, redis_mock, [
        "agent:running:c1", "agent:loop:c1",   # один чат, два флага
        b"agent:loop:c2",                        # bytes-ключ
        "agent:reasoning:c3",                    # без флага — не считается
    ])
    assert run(run_status.count_active_requests()) == 2


def test_count_ignores_hanging_reasoning(mocker, redis_mock):
    _scan_keys(mocker, redis_mock, ["agent:reasoning:c9"])
    redis_mock.get.return_value = json.dumps({"completed": False, "is_final": False})
    assert run(run_status.count_active_requests()) == 0
    redis_mock.get.assert_not_awaited()


def test_count_swallows_scan_error(mocker, redis_mock):
    redis_mock.scan_iter = mocker.MagicMock(side_effect=RuntimeError("down"))
    assert run(run_status.count_active_requests()) == 0


# ---------- get_system_notice (restart.sh → sys:notice) ----------

def test_system_notice_none_when_missing(redis_mock):
    redis_mock.hgetall.return_value = {}
    assert run(run_status.get_system_notice()) is None
    redis_mock.hgetall.assert_awaited_once_with("sys:notice")


def test_system_notice_parsed(redis_mock):
    redis_mock.hgetall.return_value = {
        b"id": b"r-1", b"phase": b"maintenance",
        "text": "## Работы\n- **агент**".encode(),
        b"services": b"agent_service chats_service", b"ts": b"1700000000",
    }
    n = run(run_status.get_system_notice())
    assert n == {
        "id": "r-1", "phase": "maintenance", "text": "## Работы\n- **агент**",
        "services": ["agent_service", "chats_service"], "ts": 1700000000,
    }


def test_system_notice_unknown_phase_and_no_id(redis_mock):
    redis_mock.hgetall.return_value = {"id": "r-2", "phase": "weird", "ts": "x"}
    n = run(run_status.get_system_notice())
    assert n["phase"] == "maintenance" and n["ts"] == 0 and n["services"] == []
    redis_mock.hgetall.return_value = {"phase": "done", "text": "t"}
    assert run(run_status.get_system_notice()) is None


def test_system_notice_redis_error(redis_mock):
    redis_mock.hgetall.side_effect = Exception("WRONGTYPE")
    assert run(run_status.get_system_notice()) is None


# ---------- FlowMap: хронология flow:{chat_id} ----------

def test_flow_push_and_get(redis_mock):
    redis_mock.rpush.return_value = 1
    run(run_status.flow_push("c1", "send", "r1", ok=None))
    key, raw = redis_mock.rpush.await_args.args
    assert key == "flow:c1"
    rec = json.loads(raw)
    assert rec["ev"] == "send" and rec["rid"] == "r1" and "ok" not in rec
    redis_mock.expire.assert_awaited_with("flow:c1", run_status.FLOW_TTL)
    redis_mock.lrange.return_value = [raw, "битый json", json.dumps({"x": 1})]
    assert run(run_status.get_flow("c1")) == [rec]


def test_mark_running_resets_flow(redis_mock):
    redis_mock.rpush.return_value = 1
    run(run_status.mark_running("c1", "r1"))
    redis_mock.delete.assert_any_await("flow:c1")
    evs = [json.loads(c.args[1])["ev"] for c in redis_mock.rpush.await_args_list]
    assert evs == ["send", "gateway"]


def test_clear_running_pushes_done(redis_mock):
    redis_mock.rpush.return_value = 1
    async def get(k):
        return json.dumps({"request_id": "r1"}) if k == "agent:running:c1" else None
    redis_mock.get.side_effect = get
    run(run_status.clear_running("c1", ok=False))
    rec = json.loads(redis_mock.rpush.await_args.args[1])
    assert rec["ev"] == "done" and rec["rid"] == "r1" and rec["ok"] is False


def test_clear_running_with_explicit_request_id_and_dedupe(redis_mock):
    redis_mock.rpush.return_value = 1
    async def get(k):
        if k == "agent:reasoning:c1":
            return json.dumps({"request_id": "r2", "started_at": 100.0, "is_final": False, "completed": False})
        return None
    redis_mock.get.side_effect = get
    redis_mock.lrange.return_value = []
    run(run_status.clear_running("c1", request_id="r2"))
    rec = json.loads(redis_mock.rpush.await_args.args[1])
    assert rec["ev"] == "done" and rec["rid"] == "r2"
    saved_state = json.loads(redis_mock.set.await_args.args[1])
    assert saved_state["completed"] is True and saved_state["is_final"] is True

    # Повторный вызов, когда в хвосте flow:c1 уже лежит done для того же rid, не дублирует запись
    redis_mock.rpush.reset_mock()
    redis_mock.lrange.return_value = [json.dumps({"ev": "done", "rid": "r2"})]
    run(run_status.clear_running("c1", request_id="r2"))
    redis_mock.rpush.assert_not_awaited()


def test_flow_push_trims(redis_mock, mocker):
    mocker.patch.object(run_status, "FLOW_MAX", 2)
    redis_mock.rpush.return_value = 9
    run(run_status.flow_push("c1", "tool_end", "r1"))
    redis_mock.ltrim.assert_awaited_once_with("flow:c1", -2, -1)
