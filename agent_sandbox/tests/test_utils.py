"""Pure helpers: truncation, path-traversal guard, output collection."""
import base64
import os

import pytest


def test_truncate_short_text_untouched(appmod):
    text = "hello"
    out, trunc = appmod._truncate(text)
    assert out == "hello"
    assert trunc is False


def test_truncate_long_text_is_cut(appmod, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_OUTPUT_SIZE", 10)
    out, trunc = appmod._truncate("x" * 50)
    assert trunc is True
    assert out.endswith("...(truncated)...")
    assert out.startswith("x" * 10)


def test_safe_join_ok(appmod, tmp_path):
    target = appmod._safe_join(str(tmp_path), "sub", "f.txt")
    assert target.startswith(os.path.realpath(str(tmp_path)))


@pytest.mark.parametrize("evil", ["../../etc/passwd", "../outside", "a/../../b"])
def test_safe_join_blocks_traversal(appmod, tmp_path, evil):
    with pytest.raises(ValueError):
        appmod._safe_join(str(tmp_path), evil)


def test_collect_output_files_empty_dir(appmod, tmp_path):
    files, warnings = appmod._collect_output_files(str(tmp_path / "nope"))
    assert files == []
    assert warnings == []


def test_collect_output_files_reads_and_encodes(appmod, tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "a.txt").write_bytes(b"hi")
    files, warnings = appmod._collect_output_files(str(out))
    assert len(files) == 1
    f = files[0]
    assert f.filename == "a.txt"
    assert f.size_bytes == 2
    assert base64.b64decode(f.content_base64) == b"hi"


def test_collect_output_files_skips_oversize(appmod, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_FILE_SIZE_FOR_RETURN", 1)
    out = tmp_path / "output"; out.mkdir()
    (out / "big.bin").write_bytes(b"xxxx")
    files, warnings = appmod._collect_output_files(str(out))
    assert files == []
    assert any("превышает лимит" in w for w in warnings)


def test_collect_output_files_caps_count(appmod, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "MAX_FILES_RETURNED", 2)
    out = tmp_path / "output"; out.mkdir()
    for i in range(5):
        (out / f"f{i}.txt").write_text("x")
    files, warnings = appmod._collect_output_files(str(out))
    assert len(files) == 2
    assert any("возвращаю первые" in w for w in warnings)
