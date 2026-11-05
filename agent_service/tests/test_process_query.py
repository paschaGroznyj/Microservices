"""HarnessAgent.process_query: вход, выход, краевые случаи цикла и доступность моделей.

LLM — ScriptedLLM из conftest (сценарий ответов/исключений), тулы — фейковые
StructuredTool. Сеть, БД, Kafka/Redis, супервизор и автозаголовок замоканы.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

import agent.event_loop as el
from agent.prompts.system_prompt import INTERACTIVE_POLL_GUIDANCE
from tests.conftest import MODEL_NAMES, ai, build_agent

OUT_KEYS = {"input", "message", "used_tools", "action", "success", "files_to_send",
            "tool_trace", "agent_model", "tokens_usage", "auto_title"}


def fake_tool(name, result="ok", exc=None):
    calls = []

    async def _impl(**kwargs):
        calls.append(kwargs)
        if exc is not None:
            raise exc
        return result(**kwargs) if callable(result) else result

    t = StructuredTool.from_function(coroutine=_impl, name=name, description=f"fake {name}",
                                     args_schema=AnyArgs)
    return t, calls


class AnyArgs(BaseModel):
    """Схема фейкового тула: одно необязательное поле x, чтобы проверить проброс аргументов."""
    model_config = ConfigDict(extra="allow")
    x: int | None = None


class Harness:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        db = AsyncMock(name="db")
        db.get_agent_journal.return_value = "запись 1"
        db.get_last_session_summary.return_value = None
        db.get_rolling_summary.return_value = None
        db.get_chat_history.return_value = []
        self.db = db
        self.agent = build_agent(monkeypatch, db=db)
        self.models = self.agent.AGENT_MODELS
        self.tools = []
        self.build_kwargs = {}
        self.ctx_seen = []

        def _build_tools(user_id, chat_id, user_input, **kw):
            self.build_kwargs = kw
            self.ctx_seen.append(dict(self.agent._request_ctx.get() or {}))
            return list(self.tools)

        monkeypatch.setattr(self.agent, "_build_tools", _build_tools)
        self.send_chunk = AsyncMock()
        monkeypatch.setattr(self.agent, "_send_chunk", self.send_chunk)
        self.probe = AsyncMock(return_value="PROBE-STATUS")
        monkeypatch.setattr(self.agent, "_probe_providers_status", self.probe)
        self.summarize_trace = AsyncMock(return_value="SUMMARY")
        monkeypatch.setattr(self.agent, "_summarize_tool_trace", self.summarize_trace)

        import agent.core.timezone_utils as tz
        monkeypatch.setattr(tz, "resolve_chat_context_and_timezone",
                            AsyncMock(return_value=("Europe/Samara", "05.11.2026 09:45", None, None)))
        import agent.media.media_store as ms
        monkeypatch.setattr(ms, "build_media_prompt_block", AsyncMock(return_value=""))
        self.first_turn = AsyncMock(return_value=False)
        monkeypatch.setattr(el, "is_first_turn", self.first_turn)
        self.gen_title = AsyncMock(return_value=None)
        monkeypatch.setattr(el, "generate_chat_title", self.gen_title)
        self.supervisor = AsyncMock(return_value=None)
        monkeypatch.setattr(el, "run_supervisor", self.supervisor)

    def add_tool(self, name, result="ok", exc=None):
        t, calls = fake_tool(name, result, exc)
        self.tools.append(t)
        return calls

    def set_hard_limit(self, n):
        real = el.make_extension_tool

        def _wrap(loop_state):
            loop_state["hard_limit"] = n
            return real(loop_state)
        self.mp.setattr(el, "make_extension_tool", _wrap)

    def run(self, user_input="привет", model="coder", **kw):
        params = dict(user_input=user_input, request_id="req-1", user_id=56, chat_id="chat-1",
                      database=self.db, agent_model=model)
        params.update(kw)
        return asyncio.run(self.agent.process_query(**params))


@pytest.fixture
def h(monkeypatch, fast_sleep):
    return Harness(monkeypatch)


def tool_msgs(messages):
    return [m for m in messages if isinstance(m, ToolMessage)]


def system_text(messages):
    return "\n".join(str(m.content) for m in messages if isinstance(m, SystemMessage))


# ───────────────────────────── выход ─────────────────────────────

def test_plain_answer_contract(h):
    h.models["coder"].script = [ai("Ответ", usage=(10, 3))]
    out = h.run()
    assert set(out) == OUT_KEYS
    assert out["message"] == "Ответ"
    assert out["input"] == "привет"
    assert out["success"] is True
    assert out["used_tools"] == [] and out["action"] == ""
    assert out["files_to_send"] == []
    assert out["tool_trace"] is None
    assert out["agent_model"] == MODEL_NAMES["coder"]
    assert out["tokens_usage"] == {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13,
                                   "cache_read_tokens": 0, "cache_creation_tokens": 0}
    assert out["auto_title"] is None
    assert h.models["coder"].methods == ["astream"]


def test_final_answer_strips_thinking_and_streams_it(h):
    h.models["coder"].script = ["<thinking>думаю</thinking>Итог"]
    out = h.run()
    assert out["message"] == "Итог"
    kinds = [c.kwargs.get("kind") for c in h.send_chunk.await_args_list]
    assert "live" in kinds and "reasoning_end" in kinds
    assert h.send_chunk.await_args_list[0].args[0] == "думаю"


def test_answer_whitespace_is_trimmed(h):
    h.models["coder"].script = ["  \n Ответ \n "]
    assert h.run()["message"] == "Ответ"


def test_tool_step_then_answer(h):
    calls = h.add_tool("noop", result={"ok": True, "value": 42})
    h.models["coder"].script = [ai("", [("noop", {"x": 1})], usage=(100, 5)), ai("Готово", usage=(50, 7))]
    out = h.run()
    assert out["message"] == "Готово"
    assert out["used_tools"] == ["noop"] and out["action"] == "noop"
    assert calls[0].get("x") == 1
    assert out["tokens_usage"] == {"input_tokens": 150, "output_tokens": 12, "total_tokens": 162,
                                   "cache_read_tokens": 0, "cache_creation_tokens": 0}
    second = h.models["coder"].calls[1]
    [tm] = tool_msgs(second)
    assert "42" in tm.content
    assert tm.tool_call_id == second[-2].tool_calls[0]["id"]
    assert isinstance(out["tool_trace"], (str, type(None)))


def test_parallel_tools_error_and_unknown_do_not_break_loop(h):
    h.add_tool("good", result="fine")
    h.add_tool("boom", exc=RuntimeError("kaboom"))
    h.models["coder"].script = [ai("", [("good", {}), ("boom", {}), ("ghost", {})]), "Итог"]
    out = h.run()
    assert out["message"] == "Итог"
    assert out["used_tools"] == ["good", "boom", "ghost"]
    contents = [m.content for m in tool_msgs(h.models["coder"].calls[1])]
    assert "fine" in contents[0]
    assert "Ошибка при выполнении boom: kaboom" in contents[1]
    assert "Инструмент 'ghost' не найден" in contents[2]


def test_files_to_send_dedup_and_auto_send(h):
    f1 = {"filename": "a.png", "path": "/app/generated_files/x/a.png"}
    f2 = {"filename": "b.csv", "path": "/app/generated_files/x/b.csv"}
    f3 = {"filename": "c.txt", "path": "/app/generated_files/x/c.txt"}
    h.add_tool("send_file_to_user", result={"ok": True, "saved_files": [f1]})
    h.add_tool("make_file", result={"ok": True, "saved_files": [f2], "auto_send": True})
    h.add_tool("silent_file", result={"ok": True, "saved_files": [f3]})
    h.models["coder"].script = [
        ai("", [("send_file_to_user", {}), ("make_file", {}), ("silent_file", {})]),
        ai("", [("send_file_to_user", {})]),
        "Отправил",
    ]
    out = h.run()
    assert [f["path"] for f in out["files_to_send"]] == [f1["path"], f2["path"]]


def test_execute_python_code_stdout_truncated(h):
    h.add_tool("execute_python_code", result={"ok": True, "stdout": "Z" * 10_000, "stderr": "E" * 5_000})
    h.models["coder"].script = [ai("", [("execute_python_code", {})]), "ok"]
    h.run()
    [tm] = tool_msgs(h.models["coder"].calls[1])
    assert 2_900 <= tm.content.count("Z") <= 3_000
    assert tm.content.count("E") <= 1_000


def test_tool_message_capped(h):
    h.add_tool("huge", result="x" * 50_000)
    h.models["coder"].script = [ai("", [("huge", {})]), "ok"]
    h.run()
    [tm] = tool_msgs(h.models["coder"].calls[1])
    assert len(tm.content) < el.MAX_TOOL_MSG_CHARS + 200


def test_empty_answer_retried_then_success(h):
    h.models["coder"].script = ["", "", "Наконец"]
    out = h.run()
    assert out["message"] == "Наконец"
    assert len(h.models["coder"].calls) == 3
    nudge = h.models["coder"].calls[1][-1]
    assert isinstance(nudge, HumanMessage) and "пустым ответом" in nudge.content


def test_empty_answer_retries_exhausted(h):
    h.models["coder"].script = [""] * 6
    out = h.run()
    assert out["success"] is True and out["message"] == ""
    assert len(h.models["coder"].calls) == 6


def test_thinking_only_counts_as_empty(h):
    h.models["coder"].script = ["<thinking>только мысль</thinking>", "Ответ"]
    assert h.run()["message"] == "Ответ"
    assert len(h.models["coder"].calls) == 2


# ───────────────────── доступность моделей ─────────────────────

@pytest.mark.parametrize("err", [
    "All providers failed", "503 Service Unavailable", "connection reset",
    "Request timeout", "host unreachable",
])
def test_provider_down_message_with_probe(h, err):
    h.models["coder"].script = [RuntimeError(err)] * 4
    out = h.run()
    assert out["success"] is True
    assert "Провайдер моделей" in out["message"] and "PROBE-STATUS" in out["message"]
    assert h.models["coder"].methods == ["astream"] * 3 + ["ainvoke"]
    h.probe.assert_awaited_once()


def test_generic_llm_error_no_probe(h):
    h.models["coder"].script = [ValueError("bad schema")] * 4
    out = h.run()
    assert out["message"].startswith("Ошибка внутри цикла агента")
    h.probe.assert_not_awaited()


def test_transient_error_recovers_on_retry(h):
    h.models["coder"].script = [RuntimeError("503"), RuntimeError("503"), "Ответ"]
    out = h.run()
    assert out["message"] == "Ответ"
    assert h.models["coder"].methods == ["astream"] * 3
    h.probe.assert_not_awaited()


def test_astream_fails_ainvoke_fallback_succeeds(h):
    h.models["coder"].script = [RuntimeError("stream broke")] * 3 + ["Через ainvoke"]
    out = h.run()
    assert out["message"] == "Через ainvoke"
    assert h.models["coder"].methods[-1] == "ainvoke"


def test_provider_down_after_tool_step_keeps_tools(h):
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})])] + [RuntimeError("503")] * 4
    out = h.run()
    assert "Провайдер моделей" in out["message"]
    assert out["used_tools"] == ["noop"]


@pytest.mark.parametrize("requested, key", [
    ("coder", "coder"), ("gpt", "coder"), ("claude", "designer"), ("designer", "designer"),
    ("glm", "universal"), ("universal", "universal"), ("gemini", "gemini"),
    ("anthropic/claude-test", "designer"), ("google/gemini-test", "gemini"),
    ("unknown-model", "coder"), ("", "coder"), (None, "coder"),
])
def test_model_selection(h, requested, key):
    h.models[key].script = ["ok"]
    out = h.run(model=requested)
    assert out["agent_model"] == MODEL_NAMES[key]
    assert h.agent.current_agent_model == key
    for k, m in h.models.items():
        assert bool(m.calls) == (k == key), k
    assert h.ctx_seen[0]["agent_model"] == requested


# ───────────────────────────── вход ─────────────────────────────

@pytest.mark.parametrize("text", [
    "{datetime} {role_block} {0}",
    "😀 emoji и ünïcödé",
    "x" * 200_000,
    "<thinking>в запросе</thinking>",
    "строка\nс\nпереносами\t",
])
def test_user_input_passed_verbatim(h, text):
    h.models["coder"].script = ["ok"]
    out = h.run(user_input=text)
    assert out["input"] == text
    assert h.models["coder"].calls[0][-1] == HumanMessage(content=text)


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_user_input_dropped_by_sanitizer(h, text):
    h.models["coder"].script = ["ok"]
    out = h.run(user_input=text)
    assert out["success"] is True
    assert not any(isinstance(m, HumanMessage) for m in h.models["coder"].calls[0])


def test_none_user_input_raises_and_resets_ctx(h):
    with pytest.raises(TypeError):
        h.run(user_input=None)
    assert h.agent._request_ctx.get() is None
    assert all(not m.calls for m in h.models.values())


@pytest.mark.parametrize("size, max_tokens", [
    ("small", 7_500), ("medium", 15_000), ("large", 30_000),
    (None, 15_000), ("huge", 15_000), ("", 15_000),
])
def test_context_size_presets(h, size, max_tokens):
    seen = {}
    real = el.trim_messages

    def spy(msgs, **kw):
        seen.update(kw)
        return real(msgs, **kw)
    h.mp.setattr(el, "trim_messages", spy)
    h.models["coder"].script = ["ok"]
    h.run(context_size=size)
    assert seen["max_tokens"] == max_tokens
    h.db.get_chat_history.assert_awaited_once_with("chat-1", 30)


def test_history_rows_converted_and_blank_skipped(h):
    h.db.get_chat_history.return_value = [
        {"id": 1, "message_text": "вопрос", "is_from_bot": False},
        {"id": 2, "message_text": "   ", "is_from_bot": True},
        {"id": 3, "message_text": "ответ", "is_from_bot": True, "tool_trace": "t" * 50},
    ]
    h.models["coder"].script = ["ok"]
    h.run()
    sent = h.models["coder"].calls[0]
    texts = [m.content for m in sent if not isinstance(m, SystemMessage)]
    assert texts == ["вопрос", "ответ", "привет"]


def test_history_db_error_is_tolerated(h):
    h.db.get_chat_history.side_effect = RuntimeError("pg down")
    h.models["coder"].script = ["ok"]
    assert h.run()["message"] == "ok"


def test_default_main_has_poll_guidance_no_journal(h):
    h.models["coder"].script = ["ok"]
    h.run()
    sys_txt = system_text(h.models["coder"].calls[0])
    assert INTERACTIVE_POLL_GUIDANCE.strip()[:40] in sys_txt
    assert "БОРТОВОЙ ЖУРНАЛ" not in sys_txt
    h.db.get_agent_journal.assert_not_awaited()
    names = {t.name for t in h.models["coder"].bound_tools}
    assert {"request_more_steps", "call_subagent"} <= names
    assert "work_with_agent_journal" not in names
    assert h.ctx_seen[0]["gateway_target"] == "main"


def test_sessions_enable_journal(h):
    h.models["coder"].script = ["ok"]
    h.run(selected_tools=["sessions"])
    sys_txt = system_text(h.models["coder"].calls[0])
    assert "БОРТОВОЙ ЖУРНАЛ (Память):\nзапись 1" in sys_txt
    assert "work_with_agent_journal" in {t.name for t in h.models["coder"].bound_tools}


@pytest.mark.parametrize("source", ["telegram", "max"])
def test_messengers_journal_on_poll_guidance_off(h, source):
    h.models["coder"].script = ["ok"]
    h.run(source=source, tg_chat_id=-100)
    sys_txt = system_text(h.models["coder"].calls[0])
    assert "БОРТОВОЙ ЖУРНАЛ" in sys_txt
    assert INTERACTIVE_POLL_GUIDANCE.strip()[:40] not in sys_txt
    assert h.build_kwargs["is_telegram"] is (source == "telegram")
    assert h.build_kwargs["is_max"] is (source == "max")


@pytest.mark.parametrize("target, expected", [("sigma", "sigma"), (None, "main"), ("", "main")])
def test_gateway_target_in_ctx(h, target, expected):
    h.models["coder"].script = ["ok"]
    h.run(gateway_target=target)
    assert h.ctx_seen[0]["gateway_target"] == expected
    assert INTERACTIVE_POLL_GUIDANCE.strip()[:40] in system_text(h.models["coder"].calls[0])


@pytest.mark.xfail(strict=True, reason="BUG: get_agent_journal()=None -> len(None) до ветки «ПУСТОЙ»")
def test_journal_none_shows_empty_marker(h):
    h.db.get_agent_journal.return_value = None
    h.models["coder"].script = ["ok"]
    h.run(selected_tools=["sessions"])
    assert "ПУСТОЙ" in system_text(h.models["coder"].calls[0])


def test_selected_tools_and_images_forwarded(h):
    h.models["coder"].script = ["ok"]
    imgs = [{"filename": "a.png", "b64": "QUJD"}]
    h.run(selected_tools=["internet"], images=imgs)
    assert h.build_kwargs["selected_tools"] == ["internet"]
    assert h.build_kwargs["images"] is imgs
    assert h.build_kwargs["allow_all"] is False


def test_ctx_reset_after_success_and_failure(h):
    h.models["coder"].script = ["ok"]
    h.run()
    assert h.agent._request_ctx.get() is None
    h.db.get_last_session_summary.side_effect = RuntimeError("db")
    with pytest.raises(RuntimeError):
        h.run()
    assert h.agent._request_ctx.get() is None


def test_database_param_used_when_agent_has_none(h):
    h.agent.db = None
    h.models["coder"].script = ["ok"]
    h.run()
    assert h.agent.db is h.db


def test_parallel_requests_isolated_ctx(h):
    h.models["coder"].script = ["a"]
    h.models["designer"].script = ["b"]

    async def both():
        return await asyncio.gather(
            h.agent.process_query("q1", "r1", 56, "c1", h.db, "coder"),
            h.agent.process_query("q2", "r2", 57, "c2", h.db, "designer", gateway_target="sigma"),
        )
    o1, o2 = asyncio.run(both())
    assert (o1["message"], o2["message"]) == ("a", "b")
    by_req = {c["request_id"]: c for c in h.ctx_seen}
    assert by_req["r1"]["chat_id"] == "c1" and by_req["r1"]["gateway_target"] == "main"
    assert by_req["r2"]["chat_id"] == "c2" and by_req["r2"]["gateway_target"] == "sigma"


# ───────────────────────── лимиты цикла ─────────────────────────

def test_hard_limit_default_100(h):
    calls = h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})])] * 100 + ["Итог по лимиту"]
    out = h.run()
    assert out["message"] == "Итог по лимиту"
    assert len(calls) == 100
    assert h.models["coder"].methods[-1] == "ainvoke"
    assert "Лимит шагов исчерпан" in h.models["coder"].calls[-1][-1].content


@pytest.mark.parametrize("last, expected", [
    (ai("", [("noop", {})]), "Агент не уложился"),
    (RuntimeError("x"), "Ошибка агента: это не хеппи-энд"),
    (ai(""), "Достигнут лимит шагов"),
])
def test_hard_limit_final_branches(h, last, expected):
    h.set_hard_limit(2)
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})])] * 2 + [last]
    assert h.run()["message"].startswith(expected)


def test_check_task_status_throttled_after_3(h):
    calls = h.add_tool("check_task_status", result={"ok": True, "status": "running"})
    h.models["coder"].script = [ai("", [("check_task_status", {})])] * 5 + ["ждём"]
    h.run()
    assert len(calls) == 3
    tms = tool_msgs(h.models["coder"].calls[-1])
    assert ["throttled" in m.content for m in tms] == [False, False, False, True, True]


def test_check_task_status_streak_reset_by_other_tool(h):
    calls = h.add_tool("check_task_status")
    h.add_tool("noop")
    seq = ["check_task_status"] * 3 + ["noop"] + ["check_task_status"] * 2
    h.models["coder"].script = [ai("", [(n, {})]) for n in seq] + ["ok"]
    h.run()
    assert len(calls) == 5


def test_supervisor_hint_injected_into_system(h):
    h.supervisor.return_value = "держись цели"
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})])] * 7 + ["ok"]
    h.run()
    h.supervisor.assert_awaited_once()
    last_call = h.models["coder"].calls[-1]
    first = last_call[0]
    assert isinstance(first, SystemMessage)
    # статический блок под кешем не меняется, подсказка уходит в хвост системных
    assert "ФОНОВЫЙ КОНТРОЛЁР ЦЕЛИ" not in str(first.content)
    assert first.content == h.models["coder"].calls[0][0].content
    assert "держись цели" in system_text(last_call)


def test_supervisor_crash_tolerated(h):
    h.supervisor.side_effect = RuntimeError("sup down")
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})])] * 7 + ["ok"]
    assert h.run()["message"] == "ok"


@pytest.mark.parametrize("fail", [False, True])
def test_compaction_on_token_soft_limit(h, fail):
    comp = AsyncMock(side_effect=RuntimeError("compactor")) if fail else AsyncMock(
        side_effect=lambda agent, msgs, **kw: (msgs, {}))
    h.mp.setattr(el, "compact_messages", comp)
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})], usage=(el.TOKEN_SOFT_LIMIT, 1)), "ok"]
    assert h.run()["message"] == "ok"
    comp.assert_awaited_once()


def test_no_compaction_below_limit(h):
    comp = AsyncMock()
    h.mp.setattr(el, "compact_messages", comp)
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})], usage=(el.TOKEN_SOFT_LIMIT - 1, 1)), "ok"]
    h.run()
    comp.assert_not_awaited()


# ─────────────────── автозаголовок и tool_trace ───────────────────

def test_auto_title_on_first_turn(h):
    h.first_turn.return_value = True
    h.gen_title.return_value = "Заголовок"
    h.models["coder"].script = ["ok"]
    assert h.run()["auto_title"] == "Заголовок"


@pytest.mark.parametrize("where", ["first_turn", "gen_title"])
def test_auto_title_errors_fail_open(h, where):
    h.first_turn.return_value = True
    getattr(h, where).side_effect = RuntimeError("title")
    h.models["coder"].script = ["ok"]
    out = h.run()
    assert out["auto_title"] is None and out["message"] == "ok"


def test_long_tool_trace_summarized(h):
    h.mp.setattr(el, "TRACE_SUMMARY_THRESHOLD", 1)
    h.mp.setattr(el, "_summarize_tool_call", lambda tc, r: f"{tc['name']}:done")
    h.add_tool("noop")
    h.models["coder"].script = [ai("", [("noop", {})]), "ok"]
    assert h.run()["tool_trace"] == "SUMMARY"
    h.summarize_trace.assert_awaited_once_with("noop:done")


def test_short_tool_trace_kept_raw(h):
    h.mp.setattr(el, "_summarize_tool_call", lambda tc, r: f"{tc['name']}:done")
    h.add_tool("a")
    h.add_tool("b")
    h.models["coder"].script = [ai("", [("a", {}), ("b", {})]), "ok"]
    assert h.run()["tool_trace"] == "a:done; b:done"
    h.summarize_trace.assert_not_awaited()
