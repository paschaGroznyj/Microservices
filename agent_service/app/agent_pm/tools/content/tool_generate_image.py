"""
tools/content/tool_generate_image.py
──────────────────────────────────────
Тул генерации изображений через google/gemini-3.1-flash-image-preview
(OpenAI-совместимый эндпоинт foundation-models.api.cloud.ru).

Пресет качества и схема запроса подтверждены карточкой знаний #127:
  - POST /v1/chat/completions (НЕ imageGenerationConfig)
  - messages-пейлоад: system (мастер-художник) + user (сцена)
  - картинка приходит в choices[0].message.images[0].image_url.url
    как base64/data-URL, mime image/jpeg
  - temperature 0.4 по умолчанию, aspect_ratio через инструкцию в промпте

Возможности:
  - принимает промпт, temperature, размеры (aspect_ratio/width/height)
  - опционально image-to-image: путь(и) к картинкам в песочнице ИЛИ
    картинки, загруженные пользователем через интерфейс (из замыкания)
  - до 10 генераций за один вызов (n)
  - дефолтный quality-промпт добавляется к system, чтобы результат был
    качественным даже при коротком пользовательском промпте

Результат: бинарники сохраняются в /app/generated_files/<uuid>/ и
регистрируются в agent._pending_generated_images, откуда langchain_agent
кладёт их в files_to_send / отдаёт фронту через /web/file.
"""

import os
import re
import uuid
import base64
import logging
import mimetypes

import httpx
from langchain.tools import tool

from ...core.agent_provider import get_image_gen_config

log = logging.getLogger(__name__)

# Куда складываем сгенерированные бинарники (тот же корень, что и остальные
# генерируемые файлы — его отдаёт эндпоинт /web/file).
GENERATED_ROOT = "/app/generated_files"

# Жёсткий потолок генераций внутри одного вызова тула.
MAX_IMAGES_PER_CALL = 10

# Дефолтный quality-промпт (system). Универсальные требования качества из #127.
DEFAULT_QUALITY_SYSTEM = (
    "You are a master visual artist and art director. Produce a single, "
    "high-quality image strictly following the user's description. "
    "Quality requirements: controlled lighting and coherent palette; "
    "rich micro-details in materials and textures; correct anatomy "
    "(hands, faces, proportions); clean shadows and highlights; sharp focus. "
    "Explicit negatives: no artifacts, no distortions, no text, no watermark, "
    "no logo. Deliver a professional, publication-ready result."
)

_ASPECT_HINT = {
    "16:9": "Compose the image in a 16:9 widescreen landscape aspect ratio.",
    "9:16": "Compose the image in a 9:16 vertical portrait aspect ratio.",
    "1:1":  "Compose the image in a 1:1 square aspect ratio.",
    "4:3":  "Compose the image in a 4:3 landscape aspect ratio.",
    "3:4":  "Compose the image in a 3:4 portrait aspect ratio.",
}

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _read_image_as_data_uri(path: str) -> str | None:
    """Читает картинку с диска и возвращает data-URL (или None при ошибке)."""
    try:
        if not os.path.isfile(path):
            log.warning("generate_image: файл не найден: %s", path)
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_EXT:
            log.warning("generate_image: неподдерживаемое расширение %s (%s)", ext, path)
            return None
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        log.error("generate_image: не смог прочитать %s: %s", path, e)
        return None


def _extract_image_data_uri(message: dict) -> str | None:
    """Достаёт data-URL картинки из ответа модели.

    По #127 картинка лежит в choices[0].message.images[0].image_url.url.
    Подстраховываемся под пару возможных форматов.
    """
    imgs = message.get("images")
    if imgs and isinstance(imgs, list):
        first = imgs[0]
        if isinstance(first, dict):
            url = (first.get("image_url") or {}).get("url") or first.get("url")
            if url:
                return url
    # запасной вариант: иногда картинка в content как список частей
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image_url", "output_image"):
                url = (part.get("image_url") or {}).get("url")
                if url:
                    return url
    return None


def _data_uri_to_bytes(data_uri: str) -> tuple[bytes, str]:
    """data:image/jpeg;base64,XXXX → (bytes, ext). ext вида '.jpg'."""
    m = re.match(r"data:(?P<mime>[^;]+);base64,(?P<b64>.+)", data_uri, re.DOTALL)
    if m:
        mime = m.group("mime")
        raw = base64.b64decode(m.group("b64"))
        ext = mimetypes.guess_extension(mime) or ".jpg"
        if ext == ".jpe":
            ext = ".jpg"
        return raw, ext
    # уже голый base64
    return base64.b64decode(data_uri), ".jpg"


def make_generate_image_tool(agent, images: list[dict] | None = None):
    """
    Фабрика тула генерации изображений.

    :param agent: экземпляр KnowledgeAgent. Используем его как хранилище
        сгенерированных путей (agent._pending_generated_images) и, при
        необходимости, source-картинок пользователя.
    :param images: картинки, загруженные пользователем в текущем сообщении
        (тот же список, что у analyze_image): [{filename, mime_type, b64}].
        Доступны для image-to-image по индексу.
    """
    images = images or []
    cfg = get_image_gen_config()

    # Аккумулятор путей сгенерированных картинок для текущего инстанса агента.
    if not hasattr(agent, "_pending_generated_images"):
        agent._pending_generated_images = []

    @tool
    async def generate_image(
        prompt: str,
        temperature: float = 0.4,
        aspect_ratio: str = "16:9",
        n: int = 1,
        source_images: list[str] | None = None,
        source_user_image_indexes: list[int] | None = None,
        extra_quality_prompt: str | None = None,
    ) -> str:
        """
        Генерирует изображение(я) по текстовому промпту через gemini-flash-image.
        Результат — качественные картинки (JPEG), сохранённые на сервер и
        автоматически отданные пользователю в интерфейс CatchPM.

        Параметры:
          prompt        — описание сцены (что нарисовать). Обязателен.
          temperature   — 0.2–1.0, по умолчанию 0.4 (ниже = строже следует промпту).
          aspect_ratio  — "16:9" | "9:16" | "1:1" | "4:3" | "3:4" (по умолчанию 16:9).
          n             — сколько картинок сгенерировать (1–10, по умолчанию 1).
          source_images — опционально: список путей к картинкам внутри песочницы
                          (напр. слайды презентации для правки/переработки).
                          Поддержка .jpg/.jpeg/.png/.webp. Режим image-to-image.
          source_user_image_indexes — опционально: индексы картинок, которые
                          пользователь приложил к сообщению (0 — первая),
                          если хочешь редактировать их.
          extra_quality_prompt — опционально: доп. арт-дирекшен в system
                          (напр. "Rembrandt light, 85mm portrait lens, f/1.8").

        Возвращает текстовый отчёт с путями сохранённых картинок. Сами файлы
        уходят пользователю в чат автоматически (превью в интерфейсе).
        """
        if not prompt or not prompt.strip():
            return "Пустой промпт — нечего генерировать. Опиши, что нарисовать."

        # Клампим число генераций.
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 1
        if n < 1:
            n = 1
        if n > MAX_IMAGES_PER_CALL:
            return (
                f"Запрошено {n} изображений — превышен лимит "
                f"{MAX_IMAGES_PER_CALL} за один вызов. Уменьши n или вызови тул повторно."
            )

        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = 0.4
        temperature = max(0.0, min(temperature, 1.0))

        # ── Собираем source-картинки для image-to-image ──────────────
        source_data_uris: list[str] = []
        if source_images:
            for p in source_images:
                uri = _read_image_as_data_uri(p)
                if uri:
                    source_data_uris.append(uri)
                else:
                    log.warning("generate_image: пропущен source-путь %s", p)
        if source_user_image_indexes:
            for idx in source_user_image_indexes:
                if 0 <= idx < len(images):
                    img = images[idx]
                    b64 = (img.get("b64") or "").strip()
                    if not b64:
                        continue
                    mime = img.get("mime_type") or "image/png"
                    uri = b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"
                    source_data_uris.append(uri)
                else:
                    log.warning("generate_image: индекс пользовательской картинки %s вне диапазона", idx)

        # ── system-промпт: базовое качество + опц. арт-дирекшен + aspect ─
        system_parts = [DEFAULT_QUALITY_SYSTEM]
        if extra_quality_prompt:
            system_parts.append(extra_quality_prompt.strip())
        aspect_hint = _ASPECT_HINT.get(aspect_ratio)
        if aspect_hint:
            system_parts.append(aspect_hint)
        system_prompt = " ".join(system_parts)

        # ── user-контент: текст + опц. source-картинки ───────────────
        user_content: list[dict] = [{"type": "text", "text": prompt.strip()}]
        for uri in source_data_uris:
            user_content.append({"type": "image_url", "image_url": {"url": uri}})

        payload = {
            "model": cfg["model"],
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        headers = {
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        }
        url = cfg["base_url"].rstrip("/") + "/chat/completions"

        # Директория под текущий вызов.
        batch_dir = os.path.join(GENERATED_ROOT, uuid.uuid4().hex)
        os.makedirs(batch_dir, exist_ok=True)

        saved: list[str] = []
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=180.0) as client:
            for i in range(n):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code != 200:
                        errors.append(f"#{i}: HTTP {resp.status_code}: {resp.text[:200]}")
                        continue
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        errors.append(f"#{i}: пустой choices в ответе модели")
                        continue
                    msg = choices[0].get("message") or {}
                    data_uri = _extract_image_data_uri(msg)
                    if not data_uri:
                        errors.append(f"#{i}: в ответе нет изображения")
                        continue
                    raw, ext = _data_uri_to_bytes(data_uri)
                    fname = f"generated_{i}{ext}"
                    fpath = os.path.join(batch_dir, fname)
                    with open(fpath, "wb") as f:
                        f.write(raw)
                    saved.append(fpath)
                    agent._pending_generated_images.append(fpath)
                    log.info("generate_image: сохранено %s (%d байт)", fpath, len(raw))
                except Exception as e:
                    log.error("generate_image: ошибка генерации #%d: %s", i, e, exc_info=True)
                    errors.append(f"#{i}: {type(e).__name__}: {e}")

        if not saved:
            return (
                "Не удалось сгенерировать ни одного изображения.\n"
                + "\n".join(errors)
            )

        lines = [f"Сгенерировано изображений: {len(saved)} (модель {cfg['model']}, "
                 f"temperature={temperature}, aspect_ratio={aspect_ratio})."]
        for p in saved:
            lines.append(f"  • {p}")
        if errors:
            lines.append("Часть генераций не удалась:")
            lines.extend(f"  ! {e}" for e in errors)
        lines.append("Файлы отправлены пользователю в интерфейс (превью доступно).")
        return "\n".join(lines)

    return generate_image
