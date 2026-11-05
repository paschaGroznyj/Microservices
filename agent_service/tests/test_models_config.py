"""Тесты декларативного YAML-конфига моделей (models_config.yaml) и масштабирования."""
from unittest.mock import MagicMock

import pytest

import agent.core.agent_provider as ap
import agent.event_loop as el
from agent.prompts.system_prompt import TOOL_NAME_INTEGRITY_GUIDANCE
from tests.conftest import ScriptedLLM
from tests.test_process_query import Harness, system_text


@pytest.fixture
def h(monkeypatch, fast_sleep):
    return Harness(monkeypatch)


@pytest.fixture(autouse=True)
def _restore_default_registry():
    yield
    ap.reload_models_registry()


def test_default_yaml_registry_loaded():
    reg = ap.get_models_registry()
    assert reg.default_model == "coder"
    assert set(reg.models) == {"coder", "designer", "universal", "gemini", "gigachat", "deepseek"}
    assert reg.aliases["gpt"] == "coder"
    assert reg.aliases["claude"] == "designer"
    assert reg.aliases["opus"] == "designer"
    assert reg.aliases["glm"] == "universal"
    assert reg.aliases["gemini"] == "gemini"
    assert reg.aliases["gigachat"] == "gigachat"
    assert reg.aliases["giga"] == "gigachat"
    assert reg.aliases["deepseek"] == "deepseek"
    assert reg.aliases["ds"] == "deepseek"
    assert reg.models["gigachat"].model == "ai-sage/GigaChat3.5-432B-A28B-Reasoning"
    assert reg.models["gigachat"].provider == "openai"
    assert reg.models["deepseek"].model == "deepseek-ai/DeepSeek-V4.1-Flash"
    assert reg.models["deepseek"].provider == "openai"

    coder = reg.models["coder"]
    assert coder.model == "openai/gpt-5.4"
    assert coder.provider == "openai"
    assert coder.max_tokens == 16384
    assert coder.reasoning == {"effort": "low"}
    assert coder.chars_per_token == 4.0
    assert coder.imitate_tool_status is True
    assert coder.tool_name_integrity_guidance is True

    designer = reg.models["designer"]
    assert designer.model == "anthropic/claude-opus-5.5"
    assert designer.provider == "anthropic"
    assert designer.max_tokens == 64000
    assert designer.reasoning == {"type": "adaptive", "display": "summarized"}
    assert designer.chars_per_token == 1.3
    assert designer.imitate_tool_status is False
    assert designer.tool_name_integrity_guidance is False


def test_build_llm_from_spec_openai_and_anthropic(monkeypatch):
    openai_calls = []
    anthropic_calls = []

    class FakeOpenAI:
        def __init__(self, **kw):
            openai_calls.append(kw)
            self.model_name = kw.get("model")

    class FakeAnthropic:
        def __init__(self, **kw):
            anthropic_calls.append(kw)
            self.model = kw.get("model")

    monkeypatch.setattr(ap, "ChatOpenAI", FakeOpenAI)
    monkeypatch.setattr(ap, "ChatAnthropic", FakeAnthropic)

    reg = ap.get_models_registry()
    gpt_inst = ap.build_llm_from_spec(reg.models["coder"])
    claude_inst = ap.build_llm_from_spec(reg.models["designer"])

    assert gpt_inst.model_name == "openai/gpt-5.4"
    assert openai_calls[0]["max_tokens"] == 16384
    assert openai_calls[0]["model_kwargs"]["extra_body"] == {"reasoning_effort": "low"}
    assert openai_calls[0]["model_kwargs"]["extra_headers"] == {
        "X-Enable-Prompt-Cache": "true",
        "x-session-id": "augent-coder",
    }

    assert claude_inst.model == "anthropic/claude-opus-5.5"
    assert anthropic_calls[0]["max_tokens_to_sample"] == 64000
    assert anthropic_calls[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert anthropic_calls[0]["anthropic_api_url"] == "https://foundation-models.api.cloud.ru"


def test_dynamic_scaling_via_custom_yaml(tmp_path, monkeypatch, h):
    custom_yaml = tmp_path / "custom_models.yaml"
    custom_yaml.write_text(
        """
default_model: coder
defaults:
  base_url_openai: "https://foundation-models.api.cloud.ru/v1"
  base_url_anthropic: "https://foundation-models.api.cloud.ru"
  prompt_cache: true
models:
  coder:
    model: "openai/gpt-5.4"
    provider: "openai"
    label: "GPT"
    aliases: ["gpt"]
    max_tokens: 16384
    chars_per_token: 4.0
    imitate_tool_status: true
    tool_name_integrity_guidance: true
    vision_target: "coder"
  designer:
    model: "anthropic/claude-opus-5.5"
    provider: "anthropic"
    label: "Claude"
    aliases: ["claude"]
    max_tokens: 64000
    chars_per_token: 1.3
    imitate_tool_status: false
    tool_name_integrity_guidance: false
    vision_target: "designer"
  deepseek:
    model: "deepseek-ai/DeepSeek-R1"
    provider: "openai"
    label: "DeepSeek"
    aliases: ["ds", "r1"]
    max_tokens: 32768
    reasoning:
      effort: "high"
    chars_per_token: 3.2
    imitate_tool_status: true
    tool_name_integrity_guidance: false
    vision_target: "coder"
""",
        encoding="utf-8",
    )

    ap.reload_models_registry(custom_yaml)
    monkeypatch.setattr(ap, "build_llm_from_spec", lambda spec: ScriptedLLM(spec.model))

    models = ap.build_agent_models()
    assert set(models) == {"coder", "designer", "deepseek"}
    assert ap.resolve_model_tag("ds", models) == "deepseek"
    assert ap.resolve_model_tag("r1", models) == "deepseek"
    assert ap.resolve_model_tag("deepseek-ai/DeepSeek-R1", models) == "deepseek"
    assert ap.get_model_labels()["deepseek"] == "DeepSeek"
    assert "deepseek" in ap.get_imitate_tool_status_models()
    assert ap.get_chars_per_token_map()["deepseek"] == 3.2
    assert ap.get_vision_target("ds") == "coder"
    assert "deepseek" in ap.get_provider_models_for_ping()

    # Проверяем прохождение новой модели через event_loop.process_query по алиасу 'ds'
    h.agent.AGENT_MODELS = models
    h.models = models
    h.models["deepseek"].script = ["Ответ от DeepSeek"]
    out = h.run(model="ds")
    assert out["message"] == "Ответ от DeepSeek"
    assert out["agent_model"] == "deepseek-ai/DeepSeek-R1"
    assert h.agent.current_agent_model == "deepseek"
    # У deepseek в кастомном YAML tool_name_integrity_guidance: false
    sys_txt = system_text(h.models["deepseek"].calls[0])
    assert TOOL_NAME_INTEGRITY_GUIDANCE.strip()[:30] not in sys_txt


def test_tool_name_integrity_guidance_per_model(h):
    h.models["coder"].script = ["ok"]
    h.run(model="coder")
    coder_sys = system_text(h.models["coder"].calls[0])
    assert TOOL_NAME_INTEGRITY_GUIDANCE.strip()[:30] in coder_sys

    h.models["designer"].script = ["ok"]
    h.run(model="claude")
    designer_sys = system_text(h.models["designer"].calls[0])
    assert TOOL_NAME_INTEGRITY_GUIDANCE.strip()[:30] not in designer_sys


def test_frontend_models_payload_has_descriptions_and_svg():
    payload = ap.get_frontend_models_payload()
    assert payload["agent_available"] is True
    assert payload["default_model"] == "coder"
    models = {m["id"]: m for m in payload["models"]}
    assert set(models) == {"coder", "designer", "universal", "gemini", "gigachat", "deepseek"}
    assert models["gigachat"]["label"] == "GigaChat"
    assert models["gigachat"]["setting_key"] == "gigachat"
    assert models["gigachat"]["svg"].startswith("<svg")
    assert models["gigachat"]["svgSm"].startswith("<svg")
    assert models["deepseek"]["label"] == "DeepSeek"
    assert models["deepseek"]["setting_key"] == "deepseek"
    assert models["deepseek"]["svg"].startswith("<svg")
    assert models["deepseek"]["svgSm"].startswith("<svg")

    assert models["coder"]["label"] == "GPT"
    assert models["coder"]["setting_key"] == "gpt"
    assert "OpenAI" in models["coder"]["hint"]
    assert models["coder"]["svg"].startswith("<svg")
    assert models["coder"]["svgSm"].startswith("<svg")

    assert models["designer"]["label"] == "Claude"
    assert models["designer"]["setting_key"] == "claude"
    assert "Anthropic" in models["designer"]["hint"]
    assert models["designer"]["svg"].startswith("<svg")

    assert models["universal"]["label"] == "GLM"
    assert models["universal"]["setting_key"] == "glm"
    assert "Zhipu" in models["universal"]["hint"]
    assert models["universal"]["svg"].startswith("<svg")

    assert models["gemini"]["label"] == "Gemini"
    assert models["gemini"]["setting_key"] == "gemini"
    assert "Google" in models["gemini"]["hint"]
    assert models["gemini"]["svg"].startswith("<svg")
