"""analyze_image: local_path с путём workspace читается из sandbox по HTTP."""
import asyncio
import base64

import httpx
import pytest

from agent.tools.vision import tool_analyze_image as ai

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.mark.parametrize("path,expected", [
    ("/tmp/exec/workspaces/f7b9a821e1c2/output/how/cmp_pc.png", ("f7b9a821e1c2", "output/how/cmp_pc.png")),
    ("/tmp/exec/workspaces/f7b9a821e1c2/output/../../x.png", (None, None)),
    ("/tmp/exec/workspaces/f7b9a821e1c2/output//x.png", (None, None)),
    ("/tmp/exec/workspaces/ZZZ/output/x.png", (None, None)),
    ("/app/generated_files/x.png", (None, None)),
])
def test_parse_workspace_path(path, expected):
    assert ai._parse_workspace_path(path) == expected


class FakeClient:
    status, content, urls = 200, PNG, []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        FakeClient.urls.append(url)
        return httpx.Response(FakeClient.status, content=FakeClient.content)


@pytest.fixture
def client(monkeypatch):
    FakeClient.status, FakeClient.content, FakeClient.urls = 200, PNG, []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return FakeClient


def test_workspace_png_to_data_uri(client):
    uri, err = asyncio.run(ai._workspace_image_as_data_uri(
        "/tmp/exec/workspaces/f7b9a821e1c2/output/a.jpg"))
    assert err is None
    # media type по сигнатуре байтов, а не по расширению .jpg
    assert uri == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert client.urls == [f"{ai._SANDBOX_URL}/workspace/f7b9a821e1c2/file/output/a.jpg"]


def test_workspace_missing_file(client):
    client.status = 404
    uri, err = asyncio.run(ai._workspace_image_as_data_uri(
        "/tmp/exec/workspaces/f7b9a821e1c2/output/none.png"))
    assert uri is None and "HTTP 404" in err


def test_workspace_bad_extension_no_request(client):
    uri, err = asyncio.run(ai._workspace_image_as_data_uri(
        "/tmp/exec/workspaces/f7b9a821e1c2/output/a.pdf"))
    assert uri is None and ".pdf" in err and client.urls == []


def test_tool_routes_workspace_path(client, monkeypatch):
    seen = []

    class Model:
        async def ainvoke(self, msgs):
            seen.append(msgs[0].content[1]["image_url"]["url"][:22])
            return "описание"

    class Agent:
        AGENT_MODELS = {"designer": Model()}

    monkeypatch.setattr("agent.core.agent_provider.get_vision_target", lambda m: "designer")
    monkeypatch.setattr("agent.core.agent_provider.get_vision_fallback_order", lambda k: ["designer"])
    tool = ai.make_analyze_image_tool(Agent(), images=[], chat_id="c")
    out = asyncio.run(tool.coroutine(
        prompt="что тут", local_path="/tmp/exec/workspaces/f7b9a821e1c2/output/a.png"))
    assert out == "описание" and seen == ["data:image/png;base64,"]
