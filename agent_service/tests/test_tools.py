"""Все тулы агента: реестр, фильтр по категориям/админам и мок-прогон каждого тула.

Сеть подменена httpx.MockTransport (три режима: сеть лежит / 500 / пустой 200),
БД — AsyncMock, sleep — мгновенный. Тул обязан вернуть ответ, а не уронить цикл.
"""
import asyncio

import httpx
import pytest

import agent.core.settings as settings
import agent.event_loop as el
from tests.conftest import ScriptedLLM

RUNTIME_TOOLS = {"request_more_steps", "call_subagent", "work_with_agent_journal", "no_tools_selected"}


@pytest.fixture
def all_tools(agent_obj):
    return agent_obj._build_tools(56, "chat-1", "привет", selected_tools=[], allow_all=True)


def names(tools):
    return [t.name for t in tools]


# ───────────────────────────── реестр ─────────────────────────────

def test_registry_names_unique_and_described(all_tools):
    n = names(all_tools)
    assert len(n) == len(set(n)) == 41
    for t in all_tools:
        assert (t.description or "").strip(), t.name


def test_settings_reference_only_existing_tools(all_tools):
    known = set(names(all_tools)) | RUNTIME_TOOLS
    referenced = set().union(*settings.TOOLS_SETTINGS.values()) | settings.UNIVERSAL_TOOLS
    assert referenced - known == set()


@pytest.mark.xfail(strict=True, reason="find_generated_image есть в UNIVERSAL/категориях, но нет в TOOL_DESCRIPTIONS")
def test_selectable_tools_have_ui_description():
    referenced = set().union(*settings.TOOLS_SETTINGS.values()) | settings.UNIVERSAL_TOOLS
    assert referenced - set(settings.TOOL_DESCRIPTIONS) == set()


# ─────────────────────────── фильтрация ───────────────────────────

def build(agent_obj, selected, user_id=56, **kw):
    return names(agent_obj._build_tools(user_id, "chat-1", "q", selected_tools=selected, **kw))


def test_nothing_selected_gives_universal_only(agent_obj):
    got = set(build(agent_obj, []))
    assert got == settings.UNIVERSAL_TOOLS


@pytest.mark.parametrize("category", [c for c in settings.TOOLS_SETTINGS if c not in settings.ADMIN_ONLY_CATEGORIES])
def test_category_adds_its_tools(agent_obj, category):
    got = set(build(agent_obj, [category]))
    built = set(names(agent_obj._build_tools(56, "c", "q", allow_all=True)))
    assert (set(settings.TOOLS_SETTINGS[category]) & built) <= got
    assert settings.UNIVERSAL_TOOLS <= got


def test_unknown_category_ignored(agent_obj):
    assert set(build(agent_obj, ["no_such"])) == settings.UNIVERSAL_TOOLS


@pytest.mark.parametrize("index_attr, uid, kw", [
    ("ADMINS_BY_USER_ID", 56, {}),
    ("ADMINS_BY_TG_ID", 111, {"is_telegram": True}),
    ("ADMINS_BY_MAX_ID", 222, {"is_max": True}),
])
def test_admin_category_requires_admin(agent_obj, monkeypatch, index_attr, uid, kw):
    for attr in ("ADMINS_BY_USER_ID", "ADMINS_BY_TG_ID", "ADMINS_BY_MAX_ID"):
        monkeypatch.setattr(el, attr, {})
    assert "execute_sql_query" not in build(agent_obj, ["admin"], user_id=uid, **kw)
    monkeypatch.setattr(el, index_attr, {uid: {"name": "admin"}})
    assert "execute_sql_query" in build(agent_obj, ["admin"], user_id=uid, **kw)


@pytest.mark.parametrize("uid", [None, "abc", "", 3.7j])
def test_bad_user_id_is_not_admin(agent_obj, monkeypatch, uid):
    monkeypatch.setattr(el, "ADMINS_BY_USER_ID", {56: {}})
    assert "execute_sql_query" not in build(agent_obj, ["admin"], user_id=uid)


# ─────────────────────── execute_sql_query ───────────────────────

@pytest.mark.parametrize("result, expected", [
    ({"status": "error", "message": "syntax"}, "Ошибка выполнения SQL: syntax"),
    ({"status": "ok", "rows": None, "affected_rows": 3}, "Затронуто строк: 3"),
    ({"status": "ok", "rows": [], "count": 0}, "результатов нет"),
    ({"status": "ok", "rows": [{"id": 1}], "count": 1}, "Найдено/возвращено строк: 1"),
])
def test_execute_sql_query_branches(all_tools, monkeypatch, result, expected):
    async def fake(db, sql):
        return result
    monkeypatch.setattr(el, "tool_execute_sql_query", fake)
    t = {x.name: x for x in all_tools}["execute_sql_query"]
    assert expected in asyncio.run(t.ainvoke({"sql": "select 1"}))


# ─────────────────────── мок-прогон каждого тула ───────────────────────

OVERRIDES = {
    "to": ["a@b.ru"], "sql": "select 1", "action": "list", "point_type": "start",
    "urls": ["https://example.com"], "url": "https://example.com", "query": "test",
    "code": "print(1)", "commands": "echo 1", "json_description": "{}",
    "prompt": "кот", "text": "привет", "path": "_smoke.txt", "content": "x",
    "old_str": "a", "new_str": "b", "pattern": "x", "name_pattern": "*.py",
    "workspace_id": "ws_smoke", "filepath": "output/a.txt", "library_id": "1",
}


def minimal_args(tool):
    schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
    props = schema.get("properties", {})
    args = {}
    for key in schema.get("required", []):
        p = props.get(key, {})
        types = {p.get("type")} | {a.get("type") for a in p.get("anyOf", [])}
        if "enum" in p:
            args[key] = p["enum"][0]
        elif key in OVERRIDES and (
            {"array": list, "string": str, "integer": int}.get(next(iter(types - {None}), "string"), str)
            is type(OVERRIDES[key])
        ):
            args[key] = OVERRIDES[key]
        elif "integer" in types or "number" in types:
            args[key] = 1
        elif "boolean" in types:
            args[key] = False
        elif "array" in types:
            args[key] = []
        elif "object" in types:
            args[key] = {}
        else:
            args[key] = "x"
    return args


def _down(request):
    raise httpx.ConnectError("network down", request=request)


HANDLERS = {
    "net_down": _down,
    "http_500": lambda r: httpx.Response(500, json={"detail": "boom"}),
    "empty_200": lambda r: httpx.Response(200, json={}),
}


@pytest.fixture
def mock_net(monkeypatch, request):
    handler = HANDLERS[request.param]
    real_async, real_sync = httpx.AsyncClient, httpx.Client

    class A(real_async):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            kw.pop("proxies", None)
            super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

    class S(real_sync):
        def __init__(self, *a, **kw):
            kw.pop("transport", None)
            kw.pop("proxies", None)
            super().__init__(*a, transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "AsyncClient", A)
    monkeypatch.setattr(httpx, "Client", S)
    return request.param


def _tool_names():
    import agent.core.config  # noqa: F401
    return [
        "analyze_image", "generate_image", "find_generated_image", "generate_video", "generate_audio",
        "generate_music", "get_db_schema", "execute_sql_query", "parse_web_page", "search_internet",
        "session_point", "manage_trigger", "send_file_to_user", "push_message_to_telegram",
        "work_with_email", "send_email", "connect_mcp_git", "manage_skills", "list_tree", "read_file",
        "grep_search", "find_files", "edit_sandbox_file", "write_sandbox_file", "execute_python_code",
        "execute_shell_commands", "create_workspace", "list_workspace_files", "download_workspace_file",
        "delete_workspace", "cleanup_workspaces", "edit_and_run", "presentation_design_skill",
        "html_design_skill", "upload_to_workspace", "save_to_code_library", "search_code_library",
        "submit_task", "check_task_status", "read_workspace_file", "edit_workspace_file",
    ]


# Тулы-инструкции: вся полезная нагрузка в docstring, тело пустое -> None.
DOC_ONLY = {"work_with_email", "html_design_skill"}

# Пустая строка — нормальный ответ: схема пустой (замоканной) БД пуста.
EMPTY_OK = {"get_db_schema"}

# Известные баги кода: тул падает исключением вместо текста ошибки.
KNOWN_BUGS = {
    "generate_audio": "audio_generator() возвращает str при пустом ключе/тексте, тул делает voice_meta['result']",
}


def test_smoke_list_matches_registry(all_tools):
    assert sorted(_tool_names()) == sorted(names(all_tools))


@pytest.mark.parametrize("mock_net", list(HANDLERS), indirect=True)
@pytest.mark.parametrize("tool_name", _tool_names())
def test_tool_survives_mocked_backends(agent_obj, monkeypatch, fast_sleep, mock_net, tool_name, tmp_path):
    if tool_name in KNOWN_BUGS:
        pytest.xfail(KNOWN_BUGS[tool_name])
    monkeypatch.chdir(tmp_path)
    import agent.tools.filesystem.tool_write_sandbox as ws
    import agent.tools.filesystem.tool_edit_sandbox as es
    import agent.tools.parsers.web_search as wsearch
    for mod in (ws, es):
        if hasattr(mod, "SANDBOX_ROOT"):
            monkeypatch.setattr(mod, "SANDBOX_ROOT", str(tmp_path))
    monkeypatch.setattr(wsearch, "SEARXNG_SEARCH_URL", "http://searxng:8080/search")
    for k in agent_obj.AGENT_MODELS:
        agent_obj.AGENT_MODELS[k] = ScriptedLLM(k, ["ok"] * 5)
    agent_obj.llm_lite = ScriptedLLM("lite", ["ok"] * 5)
    agent_obj._request_ctx.set({"request_id": "r", "chat_id": "chat-1", "user_id": 56,
                                "gateway_target": "main", "pending_generated_images": []})
    tools = {t.name: t for t in agent_obj._build_tools(56, "chat-1", "q", selected_tools=[], allow_all=True)}
    tool = tools[tool_name]

    async def go():
        return await asyncio.wait_for(tool.ainvoke(minimal_args(tool)), timeout=20)

    out = asyncio.run(go())
    if tool_name in DOC_ONLY:
        assert out is None
        assert len(tool.description) > 200
        return
    assert isinstance(out, (str, dict, list)), type(out)
    if tool_name not in EMPTY_OK:
        assert str(out).strip()


@pytest.mark.xfail(strict=True, reason=KNOWN_BUGS["generate_audio"])
def test_generate_audio_without_key_returns_text(all_tools, monkeypatch):
    import agent.tools.content.tool_generate_audio as ga
    monkeypatch.setattr(ga, "get_router_api_config", lambda: {"api_key": ""})
    t = {x.name: x for x in all_tools}["generate_audio"]
    assert "not configured" in asyncio.run(t.ainvoke({"text": "привет"}))
