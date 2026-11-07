"""Тулы с путями от модели не принимают /sandbox и корень generated_files."""
import os

import pytest

from agent.tools.telegram import tool_send_file as sf
from agent.tools.email import tool_send_email_universal as em
from agent.tools.vision import tool_analyze_image as ai
from agent.tools.vision import tool_transcribe_media as tm
from agent.tools.content import tool_generate_image as gi
from agent.tools.content import tool_generate_video as gv
from agent.tools.sandbox import tool_execute_sandbox as sb


@pytest.mark.parametrize("roots", [
    sf.ALLOWED_SEND_ROOTS, em.ALLOWED_ATTACH_ROOTS, ai.ALLOWED_IMAGE_ROOTS,
    tm.ALLOWED_MEDIA_ROOTS, gi.ALLOWED_SOURCE_ROOTS, gv._AUDIO_ROOTS, sb.ALLOWED_UPLOAD_ROOTS,
])
def test_sandbox_not_in_roots(roots):
    assert all(not str(r).startswith("/sandbox") for r in roots)
    assert [os.path.realpath(r) for r in roots] == [os.path.realpath("/app/generated_files")]


@pytest.fixture
def tree(tmp_path, monkeypatch):
    gen = tmp_path / "gen"
    (gen / "run1").mkdir(parents=True)
    (tmp_path / "sandbox" / "executed_scripts").mkdir(parents=True)
    (tmp_path / "gen_evil").mkdir()
    ok = gen / "run1" / "pic.png"; ok.write_bytes(b"x")
    ok_audio = gen / "run1" / "voice.mp3"; ok_audio.write_bytes(b"x")
    secret = tmp_path / "sandbox" / "executed_scripts" / "secret.png"; secret.write_bytes(b"x")
    evil = tmp_path / "gen_evil" / "evil.mp3"; evil.write_bytes(b"x")
    link = gen / "run1" / "link.png"; link.symlink_to(secret)
    g = str(gen)
    monkeypatch.setattr(sf, "ALLOWED_SEND_ROOTS", [g])
    monkeypatch.setattr(em, "ALLOWED_ATTACH_ROOTS", [g])
    monkeypatch.setattr(ai, "ALLOWED_IMAGE_ROOTS", [os.path.realpath(g)])
    monkeypatch.setattr(tm, "ALLOWED_MEDIA_ROOTS", [os.path.realpath(g)])
    monkeypatch.setattr(gi, "ALLOWED_SOURCE_ROOTS", [os.path.realpath(g)])
    monkeypatch.setattr(gv, "_AUDIO_ROOTS", (g,))
    monkeypatch.setattr(sb, "ALLOWED_UPLOAD_ROOTS", (g,))
    return dict(gen=gen, ok=ok, ok_audio=ok_audio, secret=secret, evil=evil, link=link, tmp=tmp_path)


def test_send_and_email(tree):
    for check in (sf._valid_send_path, em._valid_attach_path):
        assert check(str(tree["ok"])) == os.path.realpath(tree["ok"])
        for bad in (tree["secret"], tree["evil"], tree["link"], tree["gen"],
                    f"{tree['gen']}/../sandbox/executed_scripts/secret.png"):
            assert check(str(bad)) is None, (check.__name__, bad)


@pytest.mark.parametrize("resolve", [ai._resolve_local_image, tm.resolve_local_media, gi._resolve_source_path])
def test_media_resolvers(tree, resolve):
    assert resolve(str(tree["ok"])) == os.path.realpath(tree["ok"])
    assert resolve(str(tree["secret"])) is None
    assert resolve(str(tree["link"])) is None
    assert resolve("secret.png") is None  # поиск по имени тоже только внутри gen


def test_video_audio(tree):
    assert gv._resolve_audio_to_data_uri(str(tree["ok_audio"])).startswith("data:audio/")
    assert gv._resolve_audio_to_data_uri(str(tree["evil"])) is None  # префикс gen_evil


def test_upload_roots(tree):
    assert sb._is_under_allowed_root(str(tree["ok"]))
    assert sb._is_under_allowed_root(str(tree["gen"] / "run1"))
    assert not sb._is_under_allowed_root(str(tree["gen"]))  # корень целиком
    assert not sb._is_under_allowed_root(str(tree["tmp"] / "sandbox"))
    assert not sb._is_under_allowed_root(str(tree["tmp"] / "gen_evil"))
    assert not sb._is_under_allowed_root(str(tree["link"]))


ADMIN_ONLY = ("list_tree", "read_file", "edit_sandbox_file", "write_sandbox_file")


def test_upload_error_texts(tree):
    root = sb._upload_path_error(str(tree["gen"]))
    assert "целиком" in root and "подпапку" in root
    sbx = sb._upload_path_error("/sandbox/executed_scripts")
    assert "служебная" in sbx and "/sandbox" in sbx
    other = sb._upload_path_error(str(tree["tmp"] / "gen_evil"))
    assert "недоступен" in other
    for msg in (root, sbx, other):
        assert "разрешённых корней" not in msg
        assert not any(a in msg for a in ADMIN_ONLY)


def test_send_file_error_texts(tree):
    import asyncio
    _, denied = asyncio.run(sf._resolve_single_path(str(tree["secret"])))
    assert "недоступен" in denied and "не найден" not in denied
    _, missing = asyncio.run(sf._resolve_single_path(str(tree["gen"] / "run1" / "nope.png")))
    assert "не найден" in missing
    for msg in (denied, missing):
        assert not any(a in msg for a in ADMIN_ONLY)


def test_email_attach_error_texts(tree):
    denied = em._attach_error(str(tree["secret"]))
    assert "вне разрешённой папки" in denied and "download_workspace_file" in denied
    missing = em._attach_error(str(tree["gen"] / "run1" / "nope.png"))
    assert "не найдено" in missing and "вне" not in missing
