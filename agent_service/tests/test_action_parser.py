"""Mobile JSON action parser: extraction, validation, fallback-safety."""
import json

import pytest

from agent.mobile.action_parser import (
    parse_mobile_response,
    _extract_json_blob,
    _validate_actions,
)


def test_plain_json_object():
    raw = json.dumps({"v": 1, "speech": "привет", "actions": []})
    out = parse_mobile_response(raw)
    assert out["speech"] == "привет"
    assert out["actions"] == []


def test_fenced_json_block():
    raw = "вот ответ:\n```json\n{\"speech\": \"hi\", \"actions\": []}\n```"
    out = parse_mobile_response(raw)
    assert out["speech"] == "hi"


def test_json_with_surrounding_text():
    raw = 'бла бла {"speech": "yo", "actions": []} хвост'
    out = parse_mobile_response(raw)
    assert out["speech"] == "yo"


def test_broken_json_falls_back_to_speech():
    raw = "это не json вовсе"
    out = parse_mobile_response(raw)
    assert out["speech"] == "это не json вовсе"
    assert out["actions"] == []
    assert out["call_agent"] is False


def test_invalid_json_syntax_fallback():
    raw = '{"speech": "x", oops}'
    out = parse_mobile_response(raw)
    assert out["speech"] == raw.strip()
    assert out["actions"] == []


def test_empty_speech_is_valid_not_fallback():
    raw = json.dumps({"speech": "", "actions": [
        {"type": "flashlight", "params": {"state": "on"}}]})
    out = parse_mobile_response(raw)
    assert out["speech"] == ""
    assert out["actions"] == [{"type": "flashlight", "params": {"state": "on"}}]


def test_missing_speech_field_fallback():
    raw = json.dumps({"actions": []})
    out = parse_mobile_response(raw)
    assert out["actions"] == []
    assert out["speech"] == raw.strip()


def test_unknown_action_type_dropped():
    acts = _validate_actions([{"type": "nuke", "params": {}},
                              {"type": "flashlight", "params": {"state": "on"}}])
    assert acts == [{"type": "flashlight", "params": {"state": "on"}}]


def test_action_params_filtered_to_allowed():
    acts = _validate_actions([
        {"type": "volume", "params": {"cmd": "set", "value": 5, "evil": "x"}}])
    assert acts == [{"type": "volume", "params": {"cmd": "set", "value": 5}}]


def test_non_list_actions_become_empty():
    assert _validate_actions("not a list") == []


def test_call_agent_and_needs_agent_flags():
    raw = json.dumps({"speech": "ok", "actions": [],
                      "call_agent": True, "needs_agent": True})
    out = parse_mobile_response(raw)
    assert out["call_agent"] is True
    assert out["needs_agent"] is True


def test_style_hint_stripped():
    raw = json.dumps({"speech": "ok", "actions": [], "style": "  happy  "})
    assert parse_mobile_response(raw)["style"] == "happy"


def test_non_string_input_coerced():
    out = parse_mobile_response(None)
    assert out["speech"] == ""
    assert out["actions"] == []


@pytest.mark.parametrize("raw,expected_none", [("", True), ("no braces", True)])
def test_extract_json_blob_none_cases(raw, expected_none):
    assert (_extract_json_blob(raw) is None) == expected_none


def test_extract_balanced_nested_braces():
    raw = 'x {"a": {"b": 1}} y'
    assert _extract_json_blob(raw) == '{"a": {"b": 1}}'
