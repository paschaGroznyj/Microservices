"""Тесты data-URI хелперов: кодирование/декодирование и извлечение картинки из ответа LLM."""
import base64
import pytest

from app.main import _decode_data_uri, _to_data_uri, _extract_data_uri


def test_roundtrip_to_and_from_data_uri():
    raw = b"\x89PNG\r\n\x1a\n fake image bytes"
    uri = _to_data_uri(raw, "image/png")
    assert uri.startswith("data:image/png;base64,")
    assert _decode_data_uri(uri) == raw


def test_to_data_uri_default_mime_jpeg():
    assert _to_data_uri(b"x").startswith("data:image/jpeg;base64,")


def test_decode_bad_uri_raises():
    with pytest.raises(ValueError):
        _decode_data_uri("not-a-data-uri")


def test_extract_from_images_field():
    data = "data:image/jpeg;base64," + base64.b64encode(b"img").decode()
    resp = {"choices": [{"message": {"images": [{"image_url": {"url": data}}]}}]}
    assert _extract_data_uri(resp) == data


def test_extract_from_string_content():
    data = "data:image/png;base64," + base64.b64encode(b"img").decode()
    resp = {"choices": [{"message": {"content": f"here: {data} end"}}]}
    assert _extract_data_uri(resp).startswith("data:image/png;base64,")


def test_extract_from_list_content_block():
    data = "data:image/webp;base64," + base64.b64encode(b"img").decode()
    resp = {"choices": [{"message": {"content": [{"text": f"x {data}"}]}}]}
    assert _extract_data_uri(resp).startswith("data:image/webp;base64,")


def test_extract_missing_image_raises():
    with pytest.raises(ValueError):
        _extract_data_uri({"choices": [{"message": {"content": "no image"}}]})
