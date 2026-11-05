"""Политика вложений на gateway: inline-текст или файл в media_store."""
import file_parsers as fp


def test_short_text_goes_inline():
    assert fp.classify("notes.md", "привет".encode()) == (fp.MODE_INLINE, 6)


def test_text_over_limit_goes_file():
    body = ("я" * (fp.INLINE_MAX_CHARS + 1)).encode()
    assert fp.classify("big.txt", body) == (fp.MODE_FILE, fp.INLINE_MAX_CHARS + 1)


def test_limit_is_counted_in_chars_not_bytes():
    # 10 000 кириллических символов = 20 000 байт, но это ещё inline
    body = ("я" * fp.INLINE_MAX_CHARS).encode()
    assert fp.classify("edge.txt", body)[0] == fp.MODE_INLINE


def test_force_file_overrides_short_text():
    assert fp.classify("paste.txt", b"short", force_file=True)[0] == fp.MODE_FILE


def test_office_and_pdf_always_file():
    for name in ("a.pdf", "a.docx", "a.xlsx", "a.pptx", "a.png", "a.zip"):
        assert fp.classify(name, b"tiny") == (fp.MODE_FILE, None), name


def test_binary_with_text_ext_goes_file():
    assert fp.classify("fake.txt", b"\x00\x01\x02")[0] == fp.MODE_FILE
    assert fp.classify("cp1251.csv", "привет".encode("cp1251"))[0] == fp.MODE_FILE


def test_bom_stripped_and_extensionless_names():
    assert fp.classify("a.csv", "\ufeffa,b".encode("utf-8"))[1] == 3
    assert fp.classify("Dockerfile", b"FROM x")[0] == fp.MODE_INLINE


def test_upload_limit_is_50mb():
    assert fp.MAX_UPLOAD_BYTES == 50 * 1024 * 1024
