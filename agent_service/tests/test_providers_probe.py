"""HarnessAgent._probe_providers_status: текст о доступности моделей для сообщения «провайдер лёг»."""
import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest


class Resp:
    def __init__(self, data=None, status=200, bad_json=False):
        self._data, self.status_code, self._bad = data, status, bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError("err", request=req, response=httpx.Response(self.status_code, request=req))

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._data


def probe(agent_obj, monkeypatch, resp=None, exc=None):
    client = AsyncMock()
    if exc is not None:
        client.get.side_effect = exc
    else:
        client.get.return_value = resp
    monkeypatch.setattr(agent_obj, "_get_http_client", AsyncMock(return_value=client))
    return asyncio.run(agent_obj._probe_providers_status()), client


ALL = ("coder", "designer", "universal", "gemini")


def test_all_up(agent_obj, monkeypatch):
    out, _ = probe(agent_obj, monkeypatch, Resp({"providers": {k: {"up": True} for k in ALL}}))
    assert out.count("✅") == 4 and "❌" not in out
    assert "Можно переключиться на: GPT, Claude, GLM, Gemini." in out


def test_partial(agent_obj, monkeypatch):
    prov = {"coder": {"up": False}, "designer": {"up": True}, "universal": {"up": False}, "gemini": {"up": True}}
    out, _ = probe(agent_obj, monkeypatch, Resp({"providers": prov}))
    assert "❌ GPT" in out and "✅ Claude" in out and "❌ GLM" in out and "✅ Gemini" in out
    assert "Можно переключиться на: Claude, Gemini." in out


def test_missing_provider_counts_as_down(agent_obj, monkeypatch):
    out, _ = probe(agent_obj, monkeypatch, Resp({"providers": {"designer": {"up": True}}}))
    assert "❌ GPT" in out and "❌ Gemini" in out and "✅ Claude" in out


def test_all_down(agent_obj, monkeypatch):
    out, _ = probe(agent_obj, monkeypatch, Resp({"providers": {k: {"up": False} for k in ALL}}))
    assert out.count("❌") == 4
    assert "недоступна ни одна модель" in out


@pytest.mark.parametrize("data", [{}, {"providers": {}}, {"providers": None}, None])
def test_empty_payload(agent_obj, monkeypatch, data):
    out, _ = probe(agent_obj, monkeypatch, Resp(data))
    assert out == "Статус моделей получить не удалось."


@pytest.mark.parametrize("kw", [
    {"exc": httpx.ConnectError("refused")},
    {"exc": httpx.ReadTimeout("slow")},
    {"resp": Resp({}, status=500)},
    {"resp": Resp(bad_json=True)},
])
def test_healthcheck_unavailable(agent_obj, monkeypatch, kw):
    out, _ = probe(agent_obj, monkeypatch, **kw)
    assert out == "Не удалось опросить статус моделей (health-check недоступен)."


@pytest.mark.parametrize("port, expected", [(None, "9001"), ("9100", "9100")])
def test_probe_url_port(agent_obj, monkeypatch, port, expected):
    if port is None:
        monkeypatch.delenv("PORT_PM_AGENT", raising=False)
    else:
        monkeypatch.setenv("PORT_PM_AGENT", port)
    _, client = probe(agent_obj, monkeypatch, Resp({"providers": {}}))
    url = client.get.await_args.args[0]
    assert url == f"http://127.0.0.1:{expected}/internal/providers_status"
