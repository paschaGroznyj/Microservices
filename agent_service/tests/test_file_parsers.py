"""Binary file -> text parser."""
from agent.core.file_parsers import parse_file, _parse_text, TEXT_EXTS


def test_text_file_decoded_utf8():
    assert parse_file("a.txt", "привет".encode("utf-8")) == "привет"


def test_md_is_text():
    assert parse_file("readme.md", b"# title") == "# title"


def test_unknown_binary_format_message():
    out = parse_file("photo.heic", b"\x00\x01")
    assert "не поддерживается" in out


def test_text_decode_replaces_bad_bytes():
    out = _parse_text(b"\xff\xfe bad")
    assert isinstance(out, str)  # errors=replace -> no crash


def test_pdf_without_lib_or_bad_bytes_returns_message():
    out = parse_file("doc.pdf", b"not a real pdf")
    assert out.startswith("[PDF]")


def test_docx_bad_bytes_returns_message():
    out = parse_file("doc.docx", b"not a real docx")
    assert out.startswith("[DOCX]")


def test_common_text_exts_present():
    for e in (".txt", ".md", ".csv", ".json", ".py"):
        assert e in TEXT_EXTS
