"""Тесты обработки кадра (cover-resize) и HTTP-эндпоинтов.
Внешние вызовы (LLM API, скачивание style, ffmpeg) мокаются.
"""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.main import app, _normalize_frame, _fit_cover, _target_resolution


def _png_bytes(w, h, color=(10, 20, 30)):
    buf = BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    return TestClient(app)


# ── image ─────────────────────────────────────────

def test_fit_cover_exact_target_size():
    img = Image.new("RGB", (800, 400))
    out = _fit_cover(img, (1364, 768))
    assert out.size == (1364, 768)


def test_normalize_frame_outputs_valid_jpeg_at_target():
    target = _target_resolution("1:1")
    raw = _normalize_frame(_png_bytes(500, 900), target)
    img = Image.open(BytesIO(raw))
    assert img.format == "JPEG"
    assert img.size == target


def test_normalize_frame_upscales_small_input():
    target = _target_resolution("16:9")
    raw = _normalize_frame(_png_bytes(64, 64), target)
    assert Image.open(BytesIO(raw)).size == target


# ── /health ────────────────────────────────────

def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "base" in body and "has_key" in body


# ── /render валидация ────────────────────────────

def test_render_rejects_frames_le_1(client):
    r = client.post("/render", json={"frames": 1, "style_image_url": "http://x/i.jpg"})
    assert r.status_code == 400
    assert "frames" in r.json()["detail"]


def test_render_requires_style_image_url(client, mocker):
    # frames>1, но style_image_url не передан -> 400.
    # Мокаем ФС (в /render есть mkdir на /app/output, недоступный в тестах).
    mocker.patch("app.main.Path.mkdir")
    mocker.patch("app.main.Path.glob", return_value=[])
    r = client.post("/render", json={"frames": 3, "style_image_url": None})
    assert r.status_code == 400
    assert "style_image_url" in r.json()["detail"]
