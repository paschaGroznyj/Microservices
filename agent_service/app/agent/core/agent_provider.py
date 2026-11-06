from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml
try:
    from langchain_gigachat.embeddings import GigaChatEmbeddings
except ImportError:  # pragma: no cover
    GigaChatEmbeddings = None  # type: ignore[assignment]
from langchain_openai import ChatOpenAI

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:  # pragma: no cover - fallback if package is loaded lazily
    ChatAnthropic = None  # type: ignore[assignment]

from .config import CLOUD_LLM_ACCESS_KEY, ROUTER_API_KEY, ROUTER_API_BASE_URL

DEFAULT_MODELS_CONFIG_PATH = Path(__file__).with_name("models_config.yaml")


@dataclass(frozen=True)
class ModelSpec:
    """Описание одной LLM-модели из models_config.yaml."""
    tag: str
    model: str
    provider: str = "openai"
    label: str = ""
    aliases: tuple[str, ...] = ()
    max_tokens: int = 16384
    reasoning: dict[str, Any] = field(default_factory=dict)
    chars_per_token: float = 2.0
    imitate_tool_status: bool = False
    tool_name_integrity_guidance: bool = True
    vision_target: str = "designer"
    base_url: str = "https://foundation-models.api.cloud.ru/v1"
    prompt_cache: bool = True
    legacy_builder: str | None = None
    setting_key: str = ""
    hint: str = ""
    accent: str = "#10a37f"
    svg: str = ""
    svg_sm: str = ""

    def to_frontend_dict(self) -> dict[str, Any]:
        default_svg = (
            '<svg viewBox="0 0 24 24" fill="none" width="20" height="20" '
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="12" cy="12" r="8"/></svg>'
        )
        default_svg_sm = (
            '<svg viewBox="0 0 24 24" fill="none" width="15" height="15" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="12" cy="12" r="8"/></svg>'
        )
        setting = self.setting_key or (self.aliases[0] if self.aliases else self.tag)
        return {
            "id": self.tag,
            "model": self.model,
            "label": self.label or self.tag,
            "setting_key": setting,
            "aliases": list(self.aliases),
            "hint": self.hint,
            "accent": self.accent or "#10a37f",
            "svg": self.svg or default_svg,
            "svgSm": self.svg_sm or self.svg or default_svg_sm,
        }


@dataclass
class ModelsRegistry:
    """Собранный реестр моделей, алиасов и фолбэков из YAML-конфига."""
    default_model: str
    models: dict[str, ModelSpec]
    aliases: dict[str, str]
    lite_primary: ModelSpec
    lite_fallbacks: list[ModelSpec | str]


def _parse_model_spec(tag: str, raw: dict[str, Any], defaults: dict[str, Any]) -> ModelSpec:
    provider = str(raw.get("provider") or "openai").strip().lower()
    default_url = (
        defaults.get("base_url_anthropic", "https://foundation-models.api.cloud.ru")
        if provider == "anthropic"
        else defaults.get("base_url_openai", "https://foundation-models.api.cloud.ru/v1")
    )
    aliases_raw = raw.get("aliases") or []
    aliases = tuple(str(a).strip() for a in aliases_raw if str(a).strip())
    reasoning_raw = raw.get("reasoning")
    reasoning = dict(reasoning_raw) if isinstance(reasoning_raw, dict) else {}
    return ModelSpec(
        tag=tag,
        model=str(raw["model"]).strip(),
        provider=provider,
        label=str(raw.get("label") or tag).strip(),
        aliases=aliases,
        max_tokens=int(raw.get("max_tokens") or 16384),
        reasoning=reasoning,
        chars_per_token=float(raw.get("chars_per_token") or 2.0),
        imitate_tool_status=bool(raw.get("imitate_tool_status", False)),
        tool_name_integrity_guidance=bool(raw.get("tool_name_integrity_guidance", True)),
        vision_target=str(raw.get("vision_target") or tag).strip(),
        base_url=str(raw.get("base_url") or default_url).strip(),
        prompt_cache=bool(raw.get("prompt_cache", defaults.get("prompt_cache", True))),
        legacy_builder=str(raw["legacy_builder"]).strip() if raw.get("legacy_builder") else None,
        setting_key=str(raw.get("setting_key") or (aliases[0] if aliases else tag)).strip(),
        hint=str(raw.get("hint") or "").strip(),
        accent=str(raw.get("accent") or "#10a37f").strip(),
        svg=str(raw.get("svg") or "").strip(),
        svg_sm=str(raw.get("svg_sm") or raw.get("svg") or "").strip(),
    )


def load_models_registry(config_path: str | Path | None = None) -> ModelsRegistry:
    """Загружает и валидирует YAML-конфиг моделей."""
    path = Path(config_path or os.getenv("MODELS_CONFIG_PATH") or DEFAULT_MODELS_CONFIG_PATH)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    defaults = data.get("defaults") or {}
    models_raw = data.get("models") or {}
    if not models_raw:
        raise ValueError(f"В конфиге моделей {path} отсутствует секция 'models'")

    models: dict[str, ModelSpec] = {}
    aliases: dict[str, str] = {}
    for tag, raw in models_raw.items():
        tag_str = str(tag).strip()
        spec = _parse_model_spec(tag_str, raw or {}, defaults)
        models[tag_str] = spec
        for alias in spec.aliases:
            aliases[alias] = tag_str

    default_model = str(data.get("default_model") or next(iter(models))).strip()
    if default_model not in models:
        default_model = next(iter(models))

    lite_cfg = data.get("lite_models") or {}
    primary_raw = lite_cfg.get("primary") or {
        "tag": "lite",
        "model": "anthropic/claude-haiku-4.5",
        "provider": "openai",
        "max_tokens": 8192,
        "legacy_builder": "get_llm_lite_model",
    }
    lite_primary = _parse_model_spec(
        str(primary_raw.get("tag") or "lite"),
        primary_raw,
        defaults,
    )

    lite_fallbacks: list[ModelSpec | str] = []
    for idx, fb in enumerate(lite_cfg.get("fallbacks") or []):
        if isinstance(fb, dict) and fb.get("ref"):
            lite_fallbacks.append(str(fb["ref"]).strip())
        elif isinstance(fb, dict) and fb.get("model"):
            fb_tag = str(fb.get("tag") or f"lite_fallback_{idx}")
            lite_fallbacks.append(_parse_model_spec(fb_tag, fb, defaults))

    return ModelsRegistry(
        default_model=default_model,
        models=models,
        aliases=aliases,
        lite_primary=lite_primary,
        lite_fallbacks=lite_fallbacks,
    )


_REGISTRY: ModelsRegistry = load_models_registry()
_MODEL_BUILDERS: dict[str, Callable[[], Any]] = {}


def get_models_registry() -> ModelsRegistry:
    """Возвращает активный реестр моделей."""
    return _REGISTRY


def build_openai_cache_headers(chat_id: Any = None, model_tag: str = "") -> dict[str, str]:
    """Формирует заголовки кеширования промпта и sticky-сессии для OpenAI-совместимых моделей."""
    raw_chat = str(chat_id).strip() if chat_id is not None else ""
    session_id = f"chat-{raw_chat}" if raw_chat else f"augent-{model_tag or 'default'}"
    return {
        "X-Enable-Prompt-Cache": "true",
        "x-session-id": session_id,
    }


def build_llm_from_spec(spec: ModelSpec):
    """Создаёт экземпляр LLM (ChatOpenAI или ChatAnthropic) по декларативному ModelSpec."""
    if spec.provider == "anthropic":
        cls = ChatAnthropic
        if cls is None:
            from langchain_anthropic import ChatAnthropic as cls  # type: ignore[no-redef]
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "anthropic_api_key": CLOUD_LLM_ACCESS_KEY,
            "anthropic_api_url": spec.base_url,
            "max_tokens_to_sample": spec.max_tokens,
        }
        if spec.reasoning:
            kwargs["thinking"] = dict(spec.reasoning)
        return cls(**kwargs)

    model_kwargs: dict[str, Any] = {}
    if spec.prompt_cache:
        model_kwargs["extra_headers"] = build_openai_cache_headers(model_tag=spec.tag)

    if spec.reasoning:
        extra_body: dict[str, Any] = {}
        if "effort" in spec.reasoning:
            extra_body["reasoning_effort"] = spec.reasoning["effort"]
        for k, v in spec.reasoning.items():
            if k != "effort":
                extra_body[k] = v
        if extra_body:
            model_kwargs["extra_body"] = extra_body

    return ChatOpenAI(
        model=spec.model,
        base_url=spec.base_url,
        api_key=CLOUD_LLM_ACCESS_KEY,
        max_tokens=spec.max_tokens,
        # без stream_usage astream не присылает usage -> в логах [tokens] нули
        stream_usage=True,
        model_kwargs=model_kwargs,
    )


def _make_cached_builder(spec: ModelSpec) -> Callable[[], Any]:
    @lru_cache(maxsize=1)
    def _builder():
        return build_llm_from_spec(spec)

    _builder.__name__ = spec.legacy_builder or f"get_llm_model_{spec.tag}"
    _builder.__doc__ = f"Фабрика модели [{spec.tag}] ({spec.model}), сгенерированная из models_config.yaml."
    return _builder


def _register_builders(registry: ModelsRegistry) -> None:
    """В цикле создаёт фабрики для всех моделей из YAML и экспортирует legacy-имена."""
    _MODEL_BUILDERS.clear()
    all_specs: list[ModelSpec] = list(registry.models.values()) + [registry.lite_primary]
    for fb in registry.lite_fallbacks:
        if isinstance(fb, ModelSpec):
            all_specs.append(fb)

    for spec in all_specs:
        builder = _make_cached_builder(spec)
        _MODEL_BUILDERS[spec.tag] = builder
        if spec.legacy_builder:
            globals()[spec.legacy_builder] = builder


def reload_models_registry(config_path: str | Path | None = None) -> ModelsRegistry:
    """Перечитывает YAML-конфиг и пересоздаёт фабрики моделей (удобно для тестов и горячего обновления)."""
    global _REGISTRY
    _REGISTRY = load_models_registry(config_path=config_path)
    _register_builders(_REGISTRY)
    return _REGISTRY


_register_builders(_REGISTRY)


def get_model_by_tag(tag: str):
    """Возвращает инстанс модели по уникальному тегу из YAML (или алиасу)."""
    resolved = resolve_model_tag(tag)
    builder = _MODEL_BUILDERS.get(resolved)
    if builder is not None:
        return builder()
    spec = _REGISTRY.models[resolved]
    return build_llm_from_spec(spec)


def build_agent_models(builder_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """В цикле по реестру собирает словарь {tag: llm_instance} для HarnessAgent."""
    overrides = builder_overrides or {}
    result: dict[str, Any] = {}
    for tag, spec in _REGISTRY.models.items():
        fn = None
        if spec.legacy_builder and spec.legacy_builder in overrides:
            fn = overrides[spec.legacy_builder]
        elif tag in overrides and callable(overrides[tag]):
            fn = overrides[tag]
        else:
            fn = _MODEL_BUILDERS.get(tag)
        result[tag] = fn() if callable(fn) else build_llm_from_spec(spec)
    return result


def build_lite_models(
    agent_models: dict[str, Any] | None = None,
    builder_overrides: dict[str, Any] | None = None,
) -> tuple[Any, list[Any]]:
    """Собирает основную лайт-модель и список её фолбэков из YAML."""
    overrides = builder_overrides or {}
    prim = _REGISTRY.lite_primary
    prim_fn = (
        overrides.get(prim.legacy_builder)
        if prim.legacy_builder and prim.legacy_builder in overrides
        else _MODEL_BUILDERS.get(prim.tag)
    )
    llm_lite = prim_fn() if callable(prim_fn) else build_llm_from_spec(prim)

    fallbacks: list[Any] = []
    for fb in _REGISTRY.lite_fallbacks:
        if isinstance(fb, str):
            if agent_models and fb in agent_models:
                fallbacks.append(agent_models[fb])
            elif fb in _REGISTRY.models:
                spec = _REGISTRY.models[fb]
                fn = (
                    overrides.get(spec.legacy_builder)
                    if spec.legacy_builder and spec.legacy_builder in overrides
                    else _MODEL_BUILDERS.get(fb)
                )
                fallbacks.append(fn() if callable(fn) else build_llm_from_spec(spec))
        elif isinstance(fb, ModelSpec):
            fn = (
                overrides.get(fb.legacy_builder)
                if fb.legacy_builder and fb.legacy_builder in overrides
                else _MODEL_BUILDERS.get(fb.tag)
            )
            fallbacks.append(fn() if callable(fn) else build_llm_from_spec(fb))

    return llm_lite, fallbacks


def get_default_model_tag() -> str:
    return _REGISTRY.default_model


def get_model_aliases() -> dict[str, str]:
    return dict(_REGISTRY.aliases)


def get_model_labels() -> dict[str, str]:
    return {tag: spec.label for tag, spec in _REGISTRY.models.items()}


def get_imitate_tool_status_models() -> set[str]:
    return {tag for tag, spec in _REGISTRY.models.items() if spec.imitate_tool_status}


def get_chars_per_token_map() -> dict[str, float]:
    return {tag: spec.chars_per_token for tag, spec in _REGISTRY.models.items()}


def get_vision_target(tag_or_alias: str | None) -> str:
    resolved = resolve_model_tag(tag_or_alias)
    spec = _REGISTRY.models.get(resolved)
    target = spec.vision_target if spec else "designer"
    if target in _REGISTRY.models:
        return target
    return "designer" if "designer" in _REGISTRY.models else _REGISTRY.default_model


def get_vision_fallback_order(primary_tag: str | None = None) -> list[str]:
    order: list[str] = []
    if primary_tag:
        order.append(primary_tag)
    for preferred in ("universal", "designer", "coder"):
        if preferred in _REGISTRY.models and preferred not in order:
            order.append(preferred)
    for tag in _REGISTRY.models:
        if tag not in order:
            order.append(tag)
    return order


def get_provider_models_for_ping() -> dict[str, tuple[str, Callable[[], Any]]]:
    """Словарь {tag: (model_name, factory)} для /internal/providers_status."""
    return {
        tag: (spec.model, _MODEL_BUILDERS[tag])
        for tag, spec in _REGISTRY.models.items()
    }


def get_frontend_models_payload() -> dict[str, Any]:
    """Полный каталог моделей для фронтенда (ModelPicker) с описаниями и SVG."""
    return {
        "agent_available": True,
        "default_model": _REGISTRY.default_model,
        "models": [spec.to_frontend_dict() for spec in _REGISTRY.models.values()],
    }


def resolve_model_tag(
    requested: str | None,
    agent_models: dict[str, Any] | None = None,
) -> str:
    """Приводит входное имя модели (тег, алиас или полное имя провайдера) к тегу модели."""
    models_map = agent_models if agent_models is not None else _REGISTRY.models
    default_tag = (
        _REGISTRY.default_model
        if _REGISTRY.default_model in models_map
        else (next(iter(models_map)) if models_map else _REGISTRY.default_model)
    )
    if not requested or not str(requested).strip():
        return default_tag

    candidate = str(requested).strip()
    if candidate in _REGISTRY.aliases:
        mapped = _REGISTRY.aliases[candidate]
        if mapped in models_map:
            return mapped

    if candidate in models_map:
        return candidate

    if agent_models is not None:
        for tag, inst in agent_models.items():
            if get_real_model_name(inst) == candidate:
                return tag

    for tag, spec in _REGISTRY.models.items():
        if spec.model == candidate and tag in models_map:
            return tag

    return default_tag


def get_model_spec(tag_or_alias: str | None, agent_models: dict[str, Any] | None = None) -> ModelSpec:
    tag = resolve_model_tag(tag_or_alias, agent_models=agent_models)
    if tag in _REGISTRY.models:
        return _REGISTRY.models[tag]
    return _REGISTRY.models[_REGISTRY.default_model]


# ── Генерация изображений ──────────────────────────────────────────
IMAGE_GEN_MODEL = "google/gemini-3.1-flash-image-preview"
IMAGE_GEN_BASE_URL = "https://foundation-models.api.cloud.ru/v1"


def get_image_gen_config() -> dict:
    """Конфиг клиента генерации изображений (endpoint + ключ + модель)."""
    return {
        "model": IMAGE_GEN_MODEL,
        "base_url": IMAGE_GEN_BASE_URL,
        "api_key": CLOUD_LLM_ACCESS_KEY,
    }


# ── Расшифровка аудио/видео ────────────────────────────────────────
# pro, а не flash: flash не слышит звук внутри видео и выдумывает речь
# (проверено на шлюзе cloud.ru 05.11.2026). Переопределяется TRANSCRIBE_MODEL.
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "google/gemini-3.1-pro-preview")


def get_transcribe_config() -> dict:
    """Конфиг клиента расшифровки медиа (endpoint + ключ + модель)."""
    return {
        "model": TRANSCRIBE_MODEL,
        "base_url": IMAGE_GEN_BASE_URL,
        "api_key": CLOUD_LLM_ACCESS_KEY,
    }


# ══ RouterAPI (routerai.ru) — media generation (video/audio) ══
ROUTER_VIDEO_MODEL = "heygen/heygen-video-1"
ROUTER_AUDIO_MODEL = "google/gemini-3.8-flash-lite-tts"
ROUTER_MUSIC_MODEL = "google/lyria-3-pro-preview"
ROUTER_AVATAR_MODEL = "heygen/avatar-iv"


def get_router_api_config() -> dict:
    """Config for the RouterAPI media provider (base_url + key + models)."""
    return {
        "base_url": ROUTER_API_BASE_URL,
        "api_key": ROUTER_API_KEY,
        "video_model": ROUTER_VIDEO_MODEL,
        "audio_model": ROUTER_AUDIO_MODEL,
        "music_model": ROUTER_MUSIC_MODEL,
        "avatar_model": ROUTER_AVATAR_MODEL,
    }


def get_real_model_name(model_or_key) -> str:
    """Возвращает точное название модели из провайдера (например 'openai/gpt-5.4')."""
    if hasattr(model_or_key, "model_name") and getattr(model_or_key, "model_name"):
        return str(model_or_key.model_name)
    if hasattr(model_or_key, "model") and getattr(model_or_key, "model"):
        return str(model_or_key.model)
    key = str(model_or_key or "").strip()
    if key in _REGISTRY.aliases:
        key = _REGISTRY.aliases[key]
    if key in _REGISTRY.models:
        fn = _MODEL_BUILDERS.get(key)
        if fn is not None:
            try:
                llm = fn()
                return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or _REGISTRY.models[key].model)
            except Exception:
                return _REGISTRY.models[key].model
        return _REGISTRY.models[key].model
    if key == _REGISTRY.lite_primary.tag or key == "lite":
        fn = _MODEL_BUILDERS.get(_REGISTRY.lite_primary.tag)
        if fn is not None:
            try:
                llm = fn()
                return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or _REGISTRY.lite_primary.model)
            except Exception:
                return _REGISTRY.lite_primary.model
        return _REGISTRY.lite_primary.model
    fn = _MODEL_BUILDERS.get(key)
    if fn is not None:
        llm = fn()
        return str(getattr(llm, "model_name", None) or getattr(llm, "model", None) or key)
    return key
