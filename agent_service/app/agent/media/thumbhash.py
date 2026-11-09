"""ThumbHash: крошечная (~25–35 байт) размытая заглушка картинки/видео.

Порт энкодера rgbaToThumbHash из https://github.com/evanw/thumbhash (MIT,
Evan Wallace). Хеш хранится в chat_files.thumbhash (base64), фронт
декодирует его в media-ring.js и показывает размытым фоном под кольцом,
пока грузится оригинал.

Чистый Python без numpy: картинка ужимается до ≤100 px, энкод ~0.1–0.3 с,
поэтому вызывать через asyncio.to_thread.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import os
import subprocess

logger = logging.getLogger(__name__)

MAX_SIDE = 100
VIDEO_EXTS = {"mp4", "webm", "mov", "m4v", "mkv", "avi", "ogv"}


def _round(x: float) -> int:
    # Math.round из JS (половины вверх), а не банковское округление Python
    return int(math.floor(x + 0.5))


def rgba_to_thumbhash(w: int, h: int, rgba: bytes) -> bytes:
    if w > MAX_SIDE or h > MAX_SIDE:
        raise ValueError(f"{w}x{h} doesn't fit in 100x100")
    n = w * h
    avg_r = avg_g = avg_b = avg_a = 0.0
    for j in range(0, n * 4, 4):
        alpha = rgba[j + 3] / 255
        avg_r += alpha / 255 * rgba[j]
        avg_g += alpha / 255 * rgba[j + 1]
        avg_b += alpha / 255 * rgba[j + 2]
        avg_a += alpha
    if avg_a:
        avg_r /= avg_a
        avg_g /= avg_a
        avg_b /= avg_a

    has_alpha = avg_a < n
    l_limit = 5 if has_alpha else 7
    lx = max(1, _round(l_limit * w / max(w, h)))
    ly = max(1, _round(l_limit * h / max(w, h)))
    l, p, q, a = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    for i in range(n):
        j = i * 4
        alpha = rgba[j + 3] / 255
        r = avg_r * (1 - alpha) + alpha / 255 * rgba[j]
        g = avg_g * (1 - alpha) + alpha / 255 * rgba[j + 1]
        b = avg_b * (1 - alpha) + alpha / 255 * rgba[j + 2]
        l[i] = (r + g + b) / 3
        p[i] = (r + g) / 2 - b
        q[i] = r - g
        a[i] = alpha

    def encode_channel(channel, nx, ny):
        dc, ac, scale = 0.0, [], 0.0
        for cy in range(ny):
            fy = [math.cos(math.pi / h * cy * (y + 0.5)) for y in range(h)]
            cx = 0
            while cx * ny < nx * (ny - cy):
                fx = [math.cos(math.pi / w * cx * (x + 0.5)) for x in range(w)]
                # порядок суммирования как в эталоне (ch * fx * fy поштучно):
                # иначе плавающая ошибка сдвигает округление на границе .5
                f = 0.0
                for y in range(h):
                    row = y * w
                    fyy = fy[y]
                    for x in range(w):
                        f += channel[row + x] * fx[x] * fyy
                f /= n
                if cx or cy:
                    ac.append(f)
                    scale = max(scale, abs(f))
                else:
                    dc = f
                cx += 1
        if scale:
            ac = [0.5 + 0.5 / scale * v for v in ac]
        return dc, ac, scale

    l_dc, l_ac, l_scale = encode_channel(l, max(3, lx), max(3, ly))
    p_dc, p_ac, p_scale = encode_channel(p, 3, 3)
    q_dc, q_ac, q_scale = encode_channel(q, 3, 3)
    if has_alpha:
        a_dc, a_ac, a_scale = encode_channel(a, 5, 5)

    is_landscape = w > h
    header24 = (_round(63 * l_dc) | (_round(31.5 + 31.5 * p_dc) << 6)
                | (_round(31.5 + 31.5 * q_dc) << 12) | (_round(31 * l_scale) << 18)
                | (int(has_alpha) << 23))
    header16 = ((ly if is_landscape else lx) | (_round(63 * p_scale) << 3)
                | (_round(63 * q_scale) << 9) | (int(is_landscape) << 15))
    out = [header24 & 255, (header24 >> 8) & 255, header24 >> 16,
           header16 & 255, header16 >> 8]
    ac_start = 6 if has_alpha else 5
    if has_alpha:
        out.append(_round(15 * a_dc) | (_round(15 * a_scale) << 4))
    idx = 0
    for ac in ([l_ac, p_ac, q_ac, a_ac] if has_alpha else [l_ac, p_ac, q_ac]):
        for f in ac:
            pos = ac_start + (idx >> 1)
            while len(out) <= pos:
                out.append(0)
            out[pos] |= _round(15 * f) << ((idx & 1) << 2)
            idx += 1
    return bytes(b & 255 for b in out)


def _image_to_hash(img) -> str | None:
    from PIL import Image
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.BILINEAR)
    img = img.convert("RGBA")
    w, h = img.size
    if not w or not h:
        return None
    return base64.b64encode(rgba_to_thumbhash(w, h, img.tobytes())).decode("ascii")


def _display_size(img) -> tuple[int, int]:
    """Размер картинки, как её покажет браузер: EXIF Orientation 5–8 меняет стороны."""
    w, h = img.size
    try:
        if img.getexif().get(0x0112) in (5, 6, 7, 8):
            w, h = h, w
    except Exception:
        pass
    return int(w), int(h)


def image_meta(src: str | bytes) -> dict:
    """Путь или байты картинки → {"thumbhash", "width", "height"}; что не вышло — не кладём."""
    out: dict = {}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
        w, h = _display_size(img)          # до draft(): он уменьшает img.size
        if w > 0 and h > 0:
            out["width"], out["height"] = w, h
        try:
            img.seek(0)          # GIF/APNG: первый кадр
        except Exception:
            pass
        img.draft("RGB", (MAX_SIDE * 2, MAX_SIDE * 2))   # JPEG: дешёвый даунскейл при декоде
        th = _image_to_hash(img)
        if th:
            out["thumbhash"] = th
    except Exception as e:
        logger.info(f"thumbhash: картинка не разобрана: {e}")
    return out


def thumbhash_from_image(src: str | bytes) -> str | None:
    """Путь к файлу или байты картинки → base64 ThumbHash. Ошибка → None."""
    return image_meta(src).get("thumbhash")


def video_size(path: str, timeout: float = 10) -> tuple[int, int] | None:
    """Размер кадра видео через ffprobe с учётом поворота (rotate / displaymatrix)."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
             "-of", "json", path],
            capture_output=True, timeout=timeout, check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return None
        import json
        st = (json.loads(res.stdout).get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        if w <= 0 or h <= 0:
            return None
        rot = (st.get("tags") or {}).get("rotate")
        for sd in st.get("side_data_list") or []:
            if "rotation" in sd:
                rot = sd["rotation"]
        try:
            if abs(int(float(rot or 0))) % 180 == 90:
                w, h = h, w
        except (TypeError, ValueError):
            pass
        return w, h
    except Exception as e:
        logger.info(f"thumbhash: размер видео не получен: {e}")
        return None


def thumbhash_from_video(path: str, timeout: float = 15) -> str | None:
    """Первый кадр видео через ffmpeg → base64 ThumbHash. Ошибка → None."""
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-frames:v", "1",
             "-vf", f"scale={MAX_SIDE}:{MAX_SIDE}:force_original_aspect_ratio=decrease",
             "-f", "image2pipe", "-vcodec", "png", "-"],
            capture_output=True, timeout=timeout, check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return None
        return thumbhash_from_image(res.stdout)
    except Exception as e:
        logger.info(f"thumbhash: кадр видео не получен: {e}")
        return None


def media_meta_for_file(path: str, kind: str | None = None) -> dict:
    """Картинка/видео на диске → {"thumbhash", "width", "height"} (что получилось); иначе {}."""
    if not path or not os.path.isfile(path):
        return {}
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if kind == "video" or ext in VIDEO_EXTS:
        out: dict = {}
        size = video_size(path)
        if size:
            out["width"], out["height"] = size
        th = thumbhash_from_video(path)
        if th:
            out["thumbhash"] = th
        return out
    if kind == "image" or ext in {"png", "jpg", "jpeg", "webp", "gif", "bmp"}:
        return image_meta(path)
    return {}


def thumbhash_for_file(path: str, kind: str | None = None) -> str | None:
    """Хеш для файла на диске: картинка или видео, остальное → None."""
    return media_meta_for_file(path, kind).get("thumbhash")
