import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately

from agent.core.token_calibration import REDIS_KEY, TokenCalibrator


def _msgs(n=40, size=400):
    out = [SystemMessage(content="s" * 200)]
    for i in range(n):
        out.append(HumanMessage(content="вопрос " * (size // 7)))
        out.append(AIMessage(content="ответ " * (size // 6)))
    return out


def test_counter_without_samples_uses_config():
    c = TokenCalibrator()
    m = _msgs()
    assert c.counter("designer", 2.4)(m) == count_tokens_approximately(m, chars_per_token=2.4)
    assert c.chars_per_token("designer", 2.4) == 2.4


def test_first_sample_then_ema():
    c = TokenCalibrator()
    assert c.observe("coder", 10_000, 12_000) == pytest.approx(1.2)
    assert c.observe("coder", 10_000, 10_000) == pytest.approx(0.8 * 1.2 + 0.2 * 1.0)
    assert c.samples("coder") == 2
    assert c.chars_per_token("coder", 3.8) == pytest.approx(3.8 / 1.16)


def test_models_are_independent():
    c = TokenCalibrator()
    c.observe("designer", 10_000, 15_000)
    assert c.ratio("coder") == 1.0
    assert c.ratio("designer") == pytest.approx(1.5)


def test_outliers_and_short_prompts_skipped():
    c = TokenCalibrator()
    assert c.observe("coder", 10_000, 40_000) is None     # x4
    assert c.observe("coder", 10_000, 2_000) is None      # x0.2
    assert c.observe("coder", 100, 150) is None           # короткий промпт
    assert c.observe("coder", 10_000, 0) is None
    assert c.samples("coder") == 0


def test_ratio_clamped():
    c = TokenCalibrator()
    assert c.observe("x", 10_000, 24_000) == pytest.approx(1.8)


def test_calibrated_counter_trims_more_for_dense_tokenizer():
    from langchain_core.messages import trim_messages
    c = TokenCalibrator()
    m = _msgs()
    base = trim_messages(m, max_tokens=5000, token_counter=c.counter("designer", 2.4),
                         strategy="last", start_on="human", include_system=True)
    c.observe("designer", 10_000, 15_000)
    tuned = trim_messages(m, max_tokens=5000, token_counter=c.counter("designer", 2.4),
                          strategy="last", start_on="human", include_system=True)
    assert len(tuned) < len(base)
    assert isinstance(tuned[1], HumanMessage)


def test_calibratable_rejects_images():
    c = TokenCalibrator()
    assert c.calibratable([HumanMessage(content="текст")])
    img = HumanMessage(content=[{"type": "text", "text": "x"},
                                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}])
    assert not c.calibratable([HumanMessage(content="a"), img])


def test_estimate_adds_tool_schema_chars():
    m = _msgs(2)
    assert TokenCalibrator.estimate(m, 4.0, extra_chars=4000) == count_tokens_approximately(m, chars_per_token=4.0) + 1000


class FakeRedis:
    def __init__(self):
        self.h = {}

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def hset(self, key, field, value):
        self.h.setdefault(key, {})[field] = value


class BrokenRedis:
    async def hgetall(self, key):
        raise ConnectionError("down")

    async def hset(self, key, field, value):
        raise ConnectionError("down")


def test_redis_roundtrip():
    r = FakeRedis()
    a = TokenCalibrator()
    a.observe("gemini", 10_000, 11_500)
    asyncio.run(a.save(r, "gemini"))
    assert REDIS_KEY in r.h
    b = TokenCalibrator()
    asyncio.run(b.load(r))
    assert b.ratio("gemini") == pytest.approx(1.15)
    assert b.samples("gemini") == 1


def test_redis_down_is_fail_open():
    c = TokenCalibrator()
    asyncio.run(c.load(BrokenRedis()))
    c.observe("coder", 10_000, 10_300)
    asyncio.run(c.save(BrokenRedis(), "coder"))
    asyncio.run(c.load(None))
    assert c.ratio("coder") == pytest.approx(1.03)
