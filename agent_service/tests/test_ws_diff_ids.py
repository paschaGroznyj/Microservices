"""ws_diff: какие workspace агент снимает вокруг тула (workspace_id и абсолютные пути в аргументах)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.event_loop import _ws_ids_from_args
from tests.conftest import build_agent

WS = "/tmp/exec/workspaces"


def run(coro):
    return asyncio.run(coro)


class FakeTool:
    def __init__(self, name, result="ok"):
        self.name = name
        self._result = result

    async def ainvoke(self, args):
        return self._result


@pytest.mark.parametrize("args, expected", [
    ({"workspace_id": "d976e0e002fd", "code": "print(1)"}, ["d976e0e002fd"]),
    ({"commands": f"cd {WS}/d976e0e002fd/Microservices && sed -i 's/a/b/' x.py"}, ["d976e0e002fd"]),
    ({"code": f'open("{WS}/abc123/notes.py", "w")'}, ["abc123"]),
    ({"paths": [f"{WS}/w1/output/a.png", f"{WS}/w2/output/b.png"]}, ["w1", "w2"]),
    ({"workspace_id": "w1", "commands": f"ls {WS}/w1 {WS}/w2/"}, ["w1", "w2"]),
    ({"nested": {"task": f"правь {WS}/w3/repo/app.py"}}, ["w3"]),
    ({"commands": f"ls {WS}/<wid>/repo && echo {WS}/"}, []),
    ({"commands": f"cat {WS}/../etc/passwd"}, []),
    ({"workspace_id": "bad id!", "code": "1"}, []),
    ({"query": "просто текст"}, []),
    ({}, []),
    (None, []),
])
def test_ws_ids_from_args(args, expected):
    assert _ws_ids_from_args(args) == expected


def test_ws_ids_cap():
    cmd = " ".join(f"{WS}/w{i}/f" for i in range(10))
    assert _ws_ids_from_args({"commands": cmd}) == ["w0", "w1", "w2", "w3"]


@pytest.fixture
def agent(monkeypatch):
    a = build_agent(monkeypatch, db=AsyncMock(name="db"))
    client = MagicMock(name="http")
    client.post = AsyncMock(name="post")
    monkeypatch.setattr(a, "_get_http_client", AsyncMock(return_value=client))
    monkeypatch.setattr(a, "_publish_sigma_stream", AsyncMock(name="publish"))
    monkeypatch.setattr(a, "_send_flow", AsyncMock(name="flow"))
    a._request_ctx.set({"request_id": "r1", "gateway_target": "main", "chat_id": "425826"})
    return a


def _changed(wid):
    return {"workspace_id": wid, "files_count": 1, "files": [{"path": "x.py"}], "step": {"files_count": 1}}


def test_invoke_tool_snaps_ws_from_absolute_path(agent, monkeypatch):
    """Без workspace_id: путь в commands → снимок до и после, итог уходит ws_diff."""
    snap = AsyncMock(side_effect=[{"files_count": 0}, _changed("d976e0e002fd")])
    send = AsyncMock()
    monkeypatch.setattr(agent, "_ws_snap", snap)
    monkeypatch.setattr(agent, "_send_ws_diff", send)
    call = {"name": "sh", "args": {"commands": f"cd {WS}/d976e0e002fd/Microservices && touch a"}, "id": "c1"}
    assert run(agent._invoke_tool({"sh": FakeTool("sh")}, call, "r1")) == "ok"
    assert [c.args for c in snap.await_args_list] == [("d976e0e002fd", "r1"), ("d976e0e002fd", "r1")]
    send.assert_awaited_once()
    rid, diff = send.await_args.args
    assert rid == "r1" and diff["workspace_id"] == "d976e0e002fd" and "step" not in diff


def test_invoke_tool_two_workspaces_send_each(agent, monkeypatch):
    calls = []

    async def snap(wid, rid):
        calls.append(wid)
        return _changed(wid) if calls.count(wid) == 2 else {"files_count": 0}

    send = AsyncMock()
    monkeypatch.setattr(agent, "_ws_snap", snap)
    monkeypatch.setattr(agent, "_send_ws_diff", send)
    call = {"name": "sh", "args": {"workspace_id": "w1", "commands": f"cp {WS}/w1/a {WS}/w2/a"}}
    run(agent._invoke_tool({"sh": FakeTool("sh")}, call, "r1"))
    assert sorted(calls) == ["w1", "w1", "w2", "w2"]
    assert sorted(c.args[1]["workspace_id"] for c in send.await_args_list) == ["w1", "w2"]


def test_invoke_tool_no_workspace_no_snap(agent, monkeypatch):
    snap = AsyncMock()
    monkeypatch.setattr(agent, "_ws_snap", snap)
    run(agent._invoke_tool({"t": FakeTool("t")}, {"name": "t", "args": {"query": "x"}}, "r1"))
    snap.assert_not_awaited()


def test_invoke_tool_unchanged_not_sent(agent, monkeypatch):
    monkeypatch.setattr(agent, "_ws_snap", AsyncMock(return_value={"files_count": 0, "step": {"files_count": 0}}))
    send = AsyncMock()
    monkeypatch.setattr(agent, "_send_ws_diff", send)
    run(agent._invoke_tool({"t": FakeTool("t")}, {"name": "t", "args": {"code": f"open('{WS}/w1/a')"}}, "r1"))
    send.assert_not_awaited()
