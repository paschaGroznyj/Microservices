"""ThumbHash: порт энкодера совпадает с эталоном evanw/thumbhash (npm 0.1.1)."""
import base64
import io
import shutil
import subprocess

import pytest

from agent.media.thumbhash import (
    image_meta, media_meta_for_file, rgba_to_thumbhash, thumbhash_for_file, thumbhash_from_image,
)


def _make(w, h, alpha):
    out = bytearray()
    for y in range(h):
        for x in range(w):
            r = (x * 255) // max(1, w - 1)
            g = (y * 255) // max(1, h - 1)
            b = ((x + y) * 7) % 256
            a = 255 if not alpha else (
                255 if (x - w / 2) ** 2 + (y - h / 2) ** 2 < (min(w, h) / 3) ** 2 else 0)
            out += bytes((r, g, b, a))
    return bytes(out)


# Эталон посчитан rgbaToThumbHash из npm thumbhash@0.1.1 на тех же пикселях
REFERENCE = [
    (100, 60, False, "4AcKNJqAh3dwd3d4eHZwgAf4dw=="),
    (40, 100, False, "3wcKOxpwcHd4d4eHgIEI+Hg="),
    (64, 64, True, "XwiCDQQ1co9iq7f6isDQDPh4B3iHeICLdw=="),
]


@pytest.mark.parametrize("w,h,alpha,expected", REFERENCE)
def test_matches_reference(w, h, alpha, expected):
    got = base64.b64encode(rgba_to_thumbhash(w, h, _make(w, h, alpha))).decode()
    assert got == expected


def test_too_big_rejected():
    with pytest.raises(ValueError):
        rgba_to_thumbhash(101, 10, bytes(101 * 10 * 4))


def test_from_image_downscales_big_png(tmp_path):
    from PIL import Image
    p = tmp_path / "slide-1.png"
    Image.new("RGB", (1920, 1080), (200, 80, 30)).save(p)
    h = thumbhash_from_image(str(p))
    assert h and 10 <= len(base64.b64decode(h)) <= 40
    # байты из БД (generated_images.image_data) дают тот же хеш
    assert thumbhash_from_image(p.read_bytes()) == h


def test_garbage_and_non_media(tmp_path):
    assert thumbhash_from_image(b"not an image") is None
    t = tmp_path / "report.txt"
    t.write_text("hi")
    assert thumbhash_for_file(str(t), "file") is None
    assert thumbhash_for_file(str(tmp_path / "missing.png"), "image") is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нет ffmpeg")
def test_video_first_frame(tmp_path):
    v = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x2060c0:s=320x180:d=1",
         "-pix_fmt", "yuv420p", str(v)], check=True, timeout=30)
    h = thumbhash_for_file(str(v), "video")
    assert h and 10 <= len(base64.b64decode(h)) <= 40


def _png(w, h, **save_kw):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 80, 40)).save(buf, **save_kw)
    return buf.getvalue()


def test_image_meta_size_and_hash():
    m = image_meta(_png(1600, 900, format="PNG"))
    assert (m["width"], m["height"]) == (1600, 900)
    assert m["thumbhash"] == thumbhash_from_image(_png(1600, 900, format="PNG"))


def test_image_meta_jpeg_draft_keeps_full_size():
    # draft() уменьшает JPEG при декоде — размер должен браться до него
    m = image_meta(_png(2000, 1000, format="JPEG"))
    assert (m["width"], m["height"]) == (2000, 1000)


def test_image_meta_exif_rotation_swaps_sides():
    from PIL import Image
    img = Image.new("RGB", (400, 200), (10, 120, 200))
    exif = img.getexif(); exif[0x0112] = 6
    buf = io.BytesIO(); img.save(buf, format="JPEG", exif=exif.tobytes())
    m = image_meta(buf.getvalue())
    assert (m["width"], m["height"]) == (200, 400)


def test_image_meta_garbage():
    assert image_meta(b"not an image") == {}


def test_media_meta_for_file_non_media(tmp_path):
    p = tmp_path / "a.txt"; p.write_text("x")
    assert media_meta_for_file(str(p)) == {}
    assert media_meta_for_file(str(tmp_path / "nope.png")) == {}


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="нет ffmpeg")
def test_media_meta_video(tmp_path):
    p = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                    "-t", "1", "-pix_fmt", "yuv420p", str(p)], check=True, timeout=30)
    m = media_meta_for_file(str(p), "video")
    assert (m["width"], m["height"]) == (320, 180) and m.get("thumbhash")
    # поворот 90° в метаданных — стороны меняются местами
    r = tmp_path / "r.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(p),
                    "-c", "copy", str(r)], check=True, timeout=30)
    m2 = media_meta_for_file(str(r), "video")
    assert (m2["width"], m2["height"]) == (180, 320)
