"""Fixtures for agent_service tests.

pythonpath=["app"] lets us import agent.* modules directly. A fake async Redis
is provided for mode_store (redis.asyncio client).

agent.core.db создаёт Fernet(FERNET_KEY) при импорте, поэтому ключ задаётся
до импорта event_loop. LLM-фабрики и OBSWorker подменяются: HarnessAgent()
собирается без сети и без ключей провайдеров.
"""
import asyncio
import base64
import itertools
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("FERNET_KEY", base64.urlsafe_b64encode(b"0" * 32).decode())

_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(delay=0, result=None):
    return await _REAL_SLEEP(0, result)


@pytest.fixture
def fake_redis():
    r = MagicMock(name="redis")
    for m in ("get", "set", "delete", "rpush", "expire"):
        setattr(r, m, AsyncMock(name=m))
    return r


@pytest.fixture
def fast_sleep(monkeypatch):
    """Бэкоффы ретраев (2/5/10 с) и поллинги не ждут по-настоящему."""
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)


# ---------------------------------------------------------------- fake LLM
_ids = itertools.count()


def ai(content="", calls=(), usage=None):
    """AIMessage с tool_calls: calls = [("name", {args}), ...]."""
    from langchain_core.messages import AIMessage
    tcs = [{"name": n, "args": a, "id": f"call_{next(_ids)}"} for n, a in calls]
    kw = {}
    if usage:
        i, o = usage
        kw["usage_metadata"] = {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}
    return AIMessage(content=content, tool_calls=tcs, **kw)


class ScriptedLLM:
    """Модель со сценарием: элемент — AIMessage, str (= ai(str)) или исключение.

    Поддерживает то, что зовёт event_loop: bind_tools, astream, ainvoke.
    calls хранит сообщения каждого вызова, methods — каким методом звали.
    """

    def __init__(self, name, script=()):
        self.model_name = name
        self.script = list(script)
        self.calls = []
        self.methods = []
        self.bound_tools = None

    def bind_tools(self, tools, **kw):
        self.bound_tools = list(tools)
        return self

    def _next(self, messages, method):
        self.calls.append(list(messages))
        self.methods.append(method)
        if not self.script:
            raise AssertionError(f"{self.model_name}: сценарий закончился")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return ai(item) if isinstance(item, str) else item

    async def astream(self, messages, **kw):
        yield self._next(messages, "astream")

    async def ainvoke(self, messages, **kw):
        return self._next(messages, "ainvoke")


MODEL_NAMES = {
    "coder": "openai/gpt-test",
    "designer": "anthropic/claude-test",
    "universal": "zai/glm-test",
    "gemini": "google/gemini-test",
    "gigachat": "ai-sage/gigachat-test",
    "deepseek": "deepseek-ai/deepseek-test",
}


def build_agent(monkeypatch, db=None):
    import agent.event_loop as el
    stub = ScriptedLLM("stub/lite")
    for fn in ("get_llm_model_gpt", "get_llm_model_claude", "get_llm_model_glm",
               "get_llm_model_gemini", "get_llm_model_gigachat", "get_llm_model_deepseek",
               "get_llm_lite_model", "get_llm_lite_model_gpt_mini",
               "get_llm_lite_model_gemini_flash"):
        monkeypatch.setattr(el, fn, lambda: stub, raising=False)
    agent = el.HarnessAgent(database=db)
    agent.AGENT_MODELS = {k: ScriptedLLM(v) for k, v in MODEL_NAMES.items()}
    return agent


@pytest.fixture
def agent_obj(monkeypatch):
    return build_agent(monkeypatch, db=AsyncMock(name="db"))
