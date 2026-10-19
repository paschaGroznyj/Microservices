"""Тесты разбора Google Drive URL и выбора целевого разрешения по aspect_ratio."""
import pytest

from app.main import _extract_gdrive_file_id, _target_resolution


@pytest.mark.parametrize("url,expected", [
    ("https://drive.google.com/file/d/ABC123_xyz/view", "ABC123_xyz"),
    ("https://drive.google.com/uc?export=download&id=ZZZ999", "ZZZ999"),
    ("https://drive.google.com/open?id=qwe-RTY_123", "qwe-RTY_123"),
])
def test_extract_gdrive_file_id(url, expected):
    assert _extract_gdrive_file_id(url) == expected


def test_extract_gdrive_none_for_plain_url():
    assert _extract_gdrive_file_id("https://example.com/pic.jpg") is None


@pytest.mark.parametrize("ar,expected", [
    ("16:9", (1364, 768)),
    ("9:16", (768, 1364)),
    ("1:1", (1024, 1024)),
    ("4:3", (1364, 1024)),
    ("3:4", (1024, 1364)),
])
def test_target_resolution_known(ar, expected):
    assert _target_resolution(ar) == expected


def test_target_resolution_unknown_defaults_16_9():
    assert _target_resolution("42:1") == (1364, 768)
