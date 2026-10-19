"""Тесты _content_disposition — RFC 6266 заголовок с latin-1 fallback и UTF-8 именем.
Чистая функция: защита от UnicodeEncodeError на кириллице.
"""
import pytest

from app.group_chat import _content_disposition


def test_ascii_filename_inline_fallback():
    h = _content_disposition("report.pdf", "inline")
    assert h.startswith("inline; filename=\"report.pdf\"")
    assert "filename*=UTF-8''report.pdf" in h


def test_cyrillic_filename_has_safe_ascii_fallback():
    h = _content_disposition("отчёт.pdf")
    # fallback должен быть чистым ASCII (кириллица вырезана)
    assert h.encode("latin-1")  # не должно кидать UnicodeEncodeError
    assert "filename*=UTF-8''" in h
    assert "%" in h  # процент-кодированное имя


def test_empty_filename_defaults_to_file():
    h = _content_disposition("", "attachment")
    assert "filename=\"file\"" in h


def test_none_filename_does_not_crash():
    h = _content_disposition(None)
    assert "file" in h


@pytest.mark.parametrize("disposition", ["inline", "attachment"])
def test_disposition_prefix(disposition):
    assert _content_disposition("a.txt", disposition).startswith(disposition + ";")
