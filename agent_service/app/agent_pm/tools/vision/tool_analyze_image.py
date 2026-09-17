"""
tools/vision/tool_analyze_image.py
───────────────────────────────────
Тул анализа изображений (JPEG/PNG) через мультимодальный LLM (claude-opus-4.8).

Ключевая идея: base64 картинок НЕ таскается через контекст модели.
Эндпоинт /web/upload_files кладёт base64 в pending_files, handle_message
прокидывает список картинок в process_query(..., images=[...]), а тот —
в _build_tools(..., images=images). Тул захватывает images в замыкание.

Модель при вызове пишет ТОЛЬКО промпт (и опц. индекс картинки) —
сама base64-строка дёргается из замыкания, контекст не засоряется
и base64 не режется по числу символов.

Подключение в langchain_agent.py::_build_tools:
    from .tools.vision.tool_analyze_image import make_analyze_image_tool
    analyze_image = make_analyze_image_tool(agent, images)
    # добавить analyze_image в base_tools
"""

import os
import glob
import base64
import logging
import mimetypes

from langchain.tools import tool
from langchain.schema import HumanMessage

log = logging.getLogger(__name__)

# Whitelist-корни для чтения картинок с диска (те же, что у /web/file в
# agent_service). Читать разрешено ТОЛЬКО файлы внутри этих директорий —
# защита от path-traversal (../../etc/passwd и т.п.).
ALLOWED_IMAGE_ROOTS = [
    os.path.realpath("/app/generated_files"),
    os.path.realpath("/sandbox"),
]

# Поддерживаемые расширения картинок для чтения с диска.
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Соответствие magic-байтов -> media type. Vision-API cloud.ru валидирует
# реальную сигнатуру файла, поэтому media type определяем ПО БАЙТАМ, а не по
# расширению/значению из БД: сервис генерации отдаёт PNG под именем *.jpg,
# из-за чего mimetypes.guess_type давал image/jpeg и запрос падал с 400
# ("media type image/jpeg, but image appears to be image/png").
def _sniff_media_type(data: bytes, fallback: str = "image/jpeg") -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return fallback


def _is_within_allowed_roots(real_path: str) -> bool:
    """True, если real_path лежит внутри одного из whitelist-корней."""
    for root in ALLOWED_IMAGE_ROOTS:
        if real_path == root or real_path.startswith(root + os.sep):
            return True
    return False


def _resolve_local_image(path_or_name: str) -> str | None:
    """Резолвит картинку по пути/имени строго внутри whitelist-корней.

    Логика (как в agent_service._resolve_in_whitelist):
      1. Если передан абсолютный путь внутри разрешённого корня и файл
         существует — берём его.
      2. Иначе ищем по basename рекурсивно в whitelist-корнях (первое
         совпадение). basename берётся принудительно, чтобы в имени нельзя
         было протащить разделители пути / обход директорий.
    Возвращает абсолютный путь или None.
    """
    raw = (path_or_name or "").strip()
    if not raw:
        return None

    # 1) Пробуем как есть (абсолютный/относительный путь)
    real = os.path.realpath(raw)
    if _is_within_allowed_roots(real) and os.path.isfile(real):
        return real

    # 2) Ищем по basename в whitelist-корнях
    safe_name = os.path.basename(raw)
    if not safe_name:
        return None
    for root in ALLOWED_IMAGE_ROOTS:
        for cand in glob.glob(os.path.join(root, "**", safe_name), recursive=True):
            real = os.path.realpath(cand)
            if _is_within_allowed_roots(real) and os.path.isfile(real):
                return real
    return None


def _local_image_as_data_uri(path_or_name: str) -> tuple[str | None, str | None]:
    """Читает локальную картинку из whitelist и возвращает (data_uri, err).

    При успехе err=None; при ошибке data_uri=None, err — текст для модели.
    """
    real = _resolve_local_image(path_or_name)
    if not real:
        return None, (
            f"Файл «{path_or_name}» не найден в разрешённых папках "
            f"(/sandbox, /app/generated_files) или путь недопустим. "
            f"Если картинка собрана/сгенерирована в workspace (output/...) — она ещё не "
            f"в локальной ФС агента: сначала скачай её через download_workspace_file "
            f"(он положит файл в /app/generated_files и вернёт path), затем передай этот path в local_path."
        )
    ext = os.path.splitext(real)[1].lower()
    if ext not in _IMAGE_EXT:
        return None, f"«{path_or_name}»: неподдерживаемое расширение {ext} (только JPEG/PNG/WEBP/GIF)."
    try:
        with open(real, "rb") as f:
            raw = f.read()
        # media type — СТРОГО по сигнатуре байтов, а не по расширению:
        # файл может быть PNG под именем *.jpg — тогда guess_type солгал бы и запрос упал бы 400.
        fallback = mimetypes.guess_type(real)[0] or "image/jpeg"
        mime = _sniff_media_type(raw, fallback=fallback)
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}", None
    except Exception as e:
        log.error("analyze_image: не смог прочитать %s: %s", real, e)
        return None, f"Ошибка чтения «{path_or_name}»: {e}"


async def _db_image_as_data_uri(agent, image_key: str) -> tuple[str | None, str | None]:
    """Тянет картинку из generated_images по image_key. (data_uri, err)."""
    db = getattr(agent, "db", None)
    pool = getattr(db, "pool", None) if db else None
    if pool is None:
        return None, "Хранилище сгенерированных изображений недоступно (нет пула БД)."
    key = str(image_key).strip()
    if not key:
        return None, "Пустой image_key."
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT image_data, mime_type FROM generated_images "
                "WHERE image_key::text = $1 LIMIT 1",
                key,
            )
        if not row or not row["image_data"]:
            return None, f"Картинка с image_key={key} не найдена в хранилище."
        raw = bytes(row["image_data"])
        # media type — по реальной сигнатуре байтов; mime_type из БД — лишь fallback.
        mime = _sniff_media_type(raw, fallback=(row["mime_type"] or "image/jpeg"))
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}", None
    except Exception as e:
        log.error("analyze_image: не смог прочитать image_key=%s из БД: %s", key, e)
        return None, f"Ошибка чтения image_key={key} из хранилища: {e}"


def make_analyze_image_tool(agent, images: list[dict] | None = None,
                            agent_model: str | None = None):
    """
    Фабрика тула анализа изображений.

    :param agent: экземпляр KnowledgeAgent — источник AGENT_MODELS.
    :param images: список картинок текущего запроса, каждая:
        {"filename": str, "mime_type": "image/png"|"image/jpeg", "b64": "<base64>"}
        base64 БЕЗ префикса data: (или с ним — обработаем оба случая).
    :param agent_model: активная модель основного цикла ('coder' | 'designer' |
        'universal'). Определяет, какой vision-моделью распознавать картинку:
          - coder      → gpt (get_llm_model_gpt): если пользователь работает в
                         gpt-режиме, распознаём тоже через gpt;
          - universal  → gemini (умеет в картинки);
          - designer   → claude (мультимодальный opus).
        Если модель неизвестна/не задана — фолбэк на designer (claude).
    """
    images = images or []

    # Выбор vision-модели по активной модели основного цикла.
    # coder — свой gpt; universal — gemini; всё остальное — claude (designer).
    VISION_BY_MODEL = {
        "coder": "coder",
        "universal": "universal",
        "designer": "designer",
    }
    vision_key = VISION_BY_MODEL.get(agent_model or "", "designer")
    vision_llm = agent.AGENT_MODELS.get(vision_key) or agent.AGENT_MODELS["designer"]
    log.info("analyze_image: agent_model=%s → vision-модель '%s'",
             agent_model, vision_key)

    # Краткая опись картинок для подсказки модели в docstring не вставить (он статичен),
    # поэтому кладём в имена — модель видит их из текста сообщения ([IMAGE #N: ...]).
    n_images = len(images)

    @tool
    async def analyze_image(
        prompt: str,
        image_index: int = 0,
        local_path: str | None = None,
        source_image_keys: list[str] | None = None,
    ) -> str:
        """
        Анализирует изображение (JPEG/PNG/WEBP/GIF) и отвечает на вопрос о нём:
        описать, распознать текст/диаграмму, извлечь данные из графика/таблицы,
        оценить содержимое схемы и т.п.

        Источник картинки выбирается по ОДНОМУ из параметров (в порядке приоритета):

          1. source_image_keys — список image_key (UUID) ранее СГЕНЕРИРОВАННЫХ
             картинок из хранилища generated_images. Удобно, чтобы посмотреть на
             картинку, которую ты сам нарисовал через generate_image (её image_key
             был в отчёте / find_generated_image). Можно передать несколько —
             будут проанализированы по очереди в одном ответе.

          2. local_path — путь ИЛИ имя файла картинки на диске сервера. Читается
             СТРОГО из разрешённых папок: /sandbox и /app/generated_files
             (можно указать полный путь внутри них или просто имя файла — тогда
             ищется по имени рекурсивно). Пригодится для картинок, которые сделал
             другой тул (скриншот, отрисованный график, сохранённый ассет).
             ВАЖНО: файлы, собранные в workspace фабрики (output/... внутри
             execute_python_code), напрямую НЕ видны этому тулу — он читает только
             /app/generated_files и /sandbox. Сначала скачай картинку через
             download_workspace_file (он положит её в /app/generated_files и вернёт
             path), и только потом передавай этот path в local_path. Формат
             (JPEG/PNG/...) определяется по сигнатуре байтов автоматически,
             пересохранять png-как-jpg вручную НЕ нужно.

          3. image_index — картинка, которую ПОЛЬЗОВАТЕЛЬ приложил к текущему
             сообщению. В тексте сообщения они помечены маркерами [IMAGE #0: file].
             base64 таких картинок уже на сервере — передавать его НЕ нужно.
             Номер из маркера (0 — первая, по умолчанию 0).

        Параметр prompt — что именно сделать с изображением — обязателен всегда.

        Если нужно проанализировать несколько картинок из разных источников —
        вызывай тул отдельно для каждой (кроме source_image_keys, где список
        обрабатывается за один вызов).
        """
        # ── Нормализация source_image_keys (LLM может прислать строкой) ──
        keys: list[str] = []
        if source_image_keys is not None:
            if isinstance(source_image_keys, list):
                keys = [str(k).strip() for k in source_image_keys if str(k).strip()]
            else:
                s = str(source_image_keys).strip()
                if s:
                    try:
                        import json as _json
                        parsed = _json.loads(s)
                        if isinstance(parsed, list):
                            keys = [str(k).strip() for k in parsed if str(k).strip()]
                        else:
                            keys = [str(parsed).strip()]
                    except Exception:
                        keys = [p.strip() for p in s.strip("[]").split(",") if p.strip()]

        async def _run_vision(data_uri: str, label: str) -> str:
            """Один прогон vision-модели по data_uri, возврат текста/ошибки."""
            try:
                msg = HumanMessage(content=[
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ])
                resp = await vision_llm.ainvoke([msg])
                content = resp.content if hasattr(resp, "content") else str(resp)
                log.info("analyze_image: обработана картинка %s", label)
                return content
            except Exception as e:
                log.error("analyze_image failed (%s): %s", label, e, exc_info=True)
                return f"Ошибка анализа изображения ({label}): {e}"

        # ── Источник 1: image_key из хранилища generated_images ──────────
        if keys:
            results: list[str] = []
            for k in keys:
                data_uri, err = await _db_image_as_data_uri(agent, k)
                if err:
                    results.append(f"[image_key={k}] {err}")
                    continue
                out = await _run_vision(data_uri, f"image_key={k}")
                results.append(f"[image_key={k}]\n{out}")
            return "\n\n".join(results)

        # ── Источник 2: локальный путь/имя в /sandbox|/app/generated_files ─
        if local_path and str(local_path).strip():
            data_uri, err = _local_image_as_data_uri(str(local_path))
            if err:
                return err
            return await _run_vision(data_uri, f"local:{os.path.basename(str(local_path))}")

        # ── Источник 3: картинка, приложенная пользователем к сообщению ──
        if not images:
            return ("К текущему сообщению не приложено ни одного изображения, "
                    "и не указан ни local_path, ни source_image_keys. "
                    "Передай local_path (файл в /sandbox или /app/generated_files), "
                    "source_image_keys (сгенерированные картинки) или попроси "
                    "пользователя загрузить картинку.")

        if image_index < 0 or image_index >= n_images:
            avail = ", ".join(
                f"#{i} ({img.get('filename', '?')})" for i, img in enumerate(images)
            )
            return (f"Нет картинки с индексом {image_index}. "
                    f"Доступны: {avail}.")

        img = images[image_index]
        b64 = (img.get("b64") or "").strip()
        if not b64:
            return f"Картинка #{image_index} пустая (нет base64-данных)."

        mime = img.get("mime_type") or "image/png"
        data_uri = b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"
        fname = img.get("filename", f"image_{image_index}")
        return await _run_vision(data_uri, f"user#{image_index}:{fname}")

    return analyze_image
