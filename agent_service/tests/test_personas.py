"""Voice persona registry + normalization."""
import pytest

from agent.mobile.personas import (
    DEFAULT_PERSONA, PERSONAS,
    normalize_persona, get_persona, get_voice_and_style, get_persona_prompt,
)


@pytest.mark.parametrize("raw,expected", [
    ("peyto", "peyto"),
    ("  Peyto  ", "peyto"),
    ("ATLANTIC", "atlantic"),
    ("unknown", DEFAULT_PERSONA),
    ("", DEFAULT_PERSONA),
    (None, DEFAULT_PERSONA),
    (123, DEFAULT_PERSONA),
])
def test_normalize_persona(raw, expected):
    assert normalize_persona(raw) == expected


def test_get_persona_returns_full_record():
    p = get_persona("peyto")
    assert set(p) >= {"display_name", "voice", "style", "persona_prompt"}


def test_get_voice_and_style_matches_registry():
    voice, style = get_voice_and_style("atlantic")
    assert voice == PERSONAS["atlantic"]["voice"]
    assert style == PERSONAS["atlantic"]["style"]


def test_get_persona_prompt_nonempty():
    assert get_persona_prompt("peyto").strip()


def test_garbage_persona_falls_back_to_default_record():
    assert get_persona("garbage") == PERSONAS[DEFAULT_PERSONA]
