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


# Порог, после которого картинка программно сжимается перед отправкой в
# vision-модель. Крупные картинки (скрины 4K, фото с телефона) раздувают
# base64 data_uri и заставляют модель долго жевать запрос — вплоть до таймаута.
_COMPRESS_THRESHOLD = 5 * 1024 * 1024  # 5 МБ
# Целевые параметры даунскейла. 2048px по большей стороне сохраняет читаемость
# текста на скринах UI, quality=85 даёт хороший баланс размер/качество.
_COMPRESS_MAX_SIDE = 2048
_COMPRESS_JPEG_QUALITY = 85


def _maybe_compress(raw: bytes, mime: str) -> tuple[bytes, str]:
    """Сжимает картинку, если её размер превышает _COMPRESS_THRESHOLD.

    Возвращает (bytes, mime). Если сжатие не требуется, Pillow недоступен
    или картинка не декодируется — возвращает исходные данные без изменений
    (fail-open: лучше отправить как есть, чем упасть).

    Логика: даунскейл до _COMPRESS_MAX_SIDE по большей стороне (thumbnail,
    aspect ratio сохраняется), затем пересохранение в JPEG quality=85.
    Прозрачность (PNG/GIF/WEBP с альфой) кладётся на белый фон, т.к. JPEG
    альфу не поддерживает. GIF-анимация схлопывается до первого кадра —
    для vision-анализа этого достаточно.
    """
    if len(raw) <= _COMPRESS_THRESHOLD:
        return raw, mime
    try:
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        # Приводим к RGB на белом фоне (снимаем альфу/палитру).
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")

        img.thumbnail((_COMPRESS_MAX_SIDE, _COMPRESS_MAX_SIDE), Image.LANCZOS)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=_COMPRESS_JPEG_QUALITY, optimize=True)
        compressed = out.getvalue()
        # Подстраховка: если после пересжатия почему-то стало не меньше —
        # берём исходник (маловероятно, но не хотим ухудшать).
        if len(compressed) >= len(raw):
            log.info("analyze_image: compress skipped (no gain: %d -> %d bytes)",
                     len(raw), len(compressed))
            return raw, mime
        log.info("analyze_image: compressed %d -> %d bytes (%s -> image/jpeg)",
                 len(raw), len(compressed), mime)
        return compressed, "image/jpeg"
    except Exception as e:
        log.warning("analyze_image: compress failed, sending original (%d bytes): %s",
                    len(raw), e)
        return raw, mime


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
            f"(/app/generated_files) или путь недопустим. "
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
        raw, mime = _maybe_compress(raw, mime)
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
        raw, mime = _maybe_compress(raw, mime)
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}", None
    except Exception as e:
        log.error("analyze_image: не смог прочитать image_key=%s из БД: %s", key, e)
        return None, f"Ошибка чтения image_key={key} из хранилища: {e}"


def make_analyze_image_tool(agent, images: list[dict] | None = None,
                            agent_model: str | None = None,
                            chat_id=None):
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

    # Выбор vision-модели и цепочки фолбэков из реестра models_config.yaml.
    from ...core.agent_provider import get_vision_fallback_order, get_vision_target
    vision_key = get_vision_target(agent_model)
    vision_llm = (
        agent.AGENT_MODELS.get(vision_key)
        or agent.AGENT_MODELS.get("designer")
        or next(iter(agent.AGENT_MODELS.values()), None)
    )
    log.info("analyze_image: agent_model=%s → vision-модель '%s'",
             agent_model, vision_key)

    # Цепочка vision-кандидатов: начинаем с выбранной модели, затем
    # остальные — чтобы пережить случай, когда конкретный бэкенд cloud.ru
    # временно/постоянно не принимает image_url (ModelArts.81001).
    _vision_order = get_vision_fallback_order(vision_key)
    _seen: set = set()
    vision_candidates: list = []
    for _k in _vision_order:
        if _k in _seen:
            continue
        _seen.add(_k)
        _m = agent.AGENT_MODELS.get(_k)
        if _m is not None:
            vision_candidates.append((_k, _m))

    # Краткая опись картинок для подсказки модели в docstring не вставить (он статичен),
    # поэтому кладём в имена — модель видит их из текста сообщения ([IMAGE #N: ...]).
    n_images = len(images)

    @tool
    async def analyze_image(
        prompt: str,
        image_index: int = 0,
        local_path: str | None = None,
        source_image_keys: list[str] | None = None,
        source_media_ids: list[str] | None = None,
    ) -> str:
        """
        Analyzes an image (JPEG/PNG/WEBP/GIF) and answers a question about it:
        describe, recognize text/diagram, extract data from a graph/table,
        assess the contents of a diagram, etc.

        The image source is selected based on ONE of the parameters (in priority order):

          1. source_image_keys — a list of image_key (UUID) of previously GENERATED
             images from the generated_images storage. It's convenient to look at
             a picture that you drew yourself using generate_image (its image_key
             was in the report / find_generated_image). You can send several —
             they will be analyzed one at a time in a single response.

          2. local_path is the path OR name of the image file on the server disk. It reads
             STRICTLY from the allowed folder /app/generated_files
             (you can specify the full path inside them or just the file name — then
             searched by name recursively). It will be useful for the pictures that I made.
             another tool (screenshot, rendered graph, saved asset).
             IMPORTANT: files collected in the factory workspace (output/... inside
             execute_python_code), are NOT directly visible to this tool — it only reads
             /app/generated_files. First, download the image using
             download_workspace_file (it will place it in /app/generated_files and return
             path), and only then pass this path to local_path. The format
             (JPEG/PNG/...) is automatically determined by the byte signature.
             You do NOT need to manually resave png-as-jpg.

          3. image_index — the image that the USER has attached to the current one.
             the message. In the text of the message, they are marked with [IMAGE #0: file] markers.
             The base64 of such images is already on the server, so you DON'T need to transfer it.
             The number from the marker (0 is the first one, 0 by default).

          4. source_media_ids — list of media_ids from the MEDIA STORAGE of this chat
             (Redis, TTL 24h). ALL images/files sent to
             in this chat in previous messages. Their registry is shown to you in the block
             "CHAT MEDIA STORAGE" at the beginning of the request: there's a media_id for each one
             the name is indicated and (if you have already watched it) a short description. Use THIS ONE
             source, when there is no picture in the current message, but you need to go back to
             previously sent ("that picture above", "bike photo from the past
             messages"). Pass the necessary media_ids and they will be analyzed by
             queues, and their descriptions will be added to the registry.

        The prompt parameter — what exactly to do with the image — is always required.
        If the PDF doesn't execute, render the first page to PNG using pdf2image/pdftoppm and pass the PNG.

        If you need to analyze several images from different sources —
        Call the tool separately for each (except for source_image_keys, where the list  is processed in a single call).
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
            msg = HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ])
            last_err = None
            for _k, _model in vision_candidates:
                try:
                    resp = await _model.ainvoke([msg])
                    _track = getattr(agent, "track_tokens", None)
                    if callable(_track):
                        _track(resp, tool="analyze_image",
                               model=getattr(_model, "model_name", None) or getattr(_model, "model", None) or _k)
                    content = resp.content if hasattr(resp, "content") else str(resp)
                    log.info("analyze_image: обработана картинка %s (vision-модель '%s')", label, _k)
                    return content
                except Exception as e:
                    last_err = e
                    _es = str(e).lower()
                    _vision_unsupported = (
                        "image_url" in _es
                        or "unsupported content type" in _es
                        or "only supported type" in _es
                        or "modelarts.81001" in _es
                    )
                    if _vision_unsupported:
                        log.warning("analyze_image: модель '%s' не поддержала vision (%s), пробую следующую", _k, label)
                        continue
                    # Не-vision ошибка (сеть/503/таймаут) — тоже пробуем следующую модель.
                    log.warning("analyze_image: модель '%s' упала (%s): %s, пробую следующую", _k, label, e)
                    continue
            log.error("analyze_image failed (%s): все vision-модели отказали: %s", label, last_err, exc_info=True)
            return f"Ошибка анализа изображения ({label}): ни одна vision-модель провайдера не приняла картинку. Последняя ошибка: {last_err}"

        # ── Источник 0: media_id из пер-чат media_store (Redis) ───────
        # Картинки, присланные в прошлых сообщениях этого чата. После анализа
        # дописываем описание в реестр — чтобы в следующий раз агент видел, что на картинке.
        _mids: list[str] = []
        if source_media_ids is not None:
            if isinstance(source_media_ids, list):
                _mids = [str(m).strip() for m in source_media_ids if str(m).strip()]
            else:
                _sm = str(source_media_ids).strip()
                if _sm:
                    try:
                        import json as _json
                        _parsed = _json.loads(_sm)
                        _mids = ([str(m).strip() for m in _parsed if str(m).strip()]
                                 if isinstance(_parsed, list) else [str(_parsed).strip()])
                    except Exception:
                        _mids = [x.strip() for x in _sm.replace(",", " ").split() if x.strip()]
        if _mids:
            try:
                from agent.media import media_store as _ms
            except Exception:
                _ms = None
            if _ms is None or chat_id is None:
                return ("Медиа-хранилище недоступно в этом контексте.")
            results: list[str] = []
            for _mid in _mids:
                raw, meta = await _ms.get_media(chat_id, _mid)
                if not raw:
                    results.append(f"[media_id={_mid}] не найдено в хранилище (истёк TTL 24ч или неверный id).")
                    continue
                _mime = (meta or {}).get("mime") or "image/png"
                if len(raw) > _COMPRESS_THRESHOLD:
                    raw, _mime = _maybe_compress(raw, _mime)
                _b64 = base64.b64encode(raw).decode("ascii")
                _data_uri = f"data:{_mime};base64,{_b64}"
                out = await _run_vision(_data_uri, f"media_id={_mid}")
                results.append(f"[media_id={_mid}]\n{out}")
                # Дописываем описание в реестр (коротко).
                try:
                    await _ms.attach_description(chat_id, _mid, out)
                except Exception:
                    pass
            return "\n\n".join(results)

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

        # ── Источник 2: локальный путь/имя в /app/generated_files ─
        if local_path and str(local_path).strip():
            data_uri, err = _local_image_as_data_uri(str(local_path))
            if err:
                return err
            return await _run_vision(data_uri, f"local:{os.path.basename(str(local_path))}")

        # ── Источник 3: картинка, приложенная пользователем к сообщению ──
        if not images:
            return ("К текущему сообщению не приложено ни одного изображения, "
                    "и не указан ни local_path, ни source_image_keys. "
                    "Передай local_path (файл в /app/generated_files), "
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
        # Сжимаем приложенную юзером картинку, если она крупная. b64 может
        # прийти с префиксом data: — тогда отделяем полезную часть, декодируем,
        # прогоняем через _maybe_compress и собираем data_uri заново.
        try:
            b64_payload = b64.split(",", 1)[1] if b64.startswith("data:") else b64
            raw = base64.b64decode(b64_payload)
            if len(raw) > _COMPRESS_THRESHOLD:
                raw, mime = _maybe_compress(raw, mime)
                b64 = base64.b64encode(raw).decode("ascii")
                data_uri = f"data:{mime};base64,{b64}"
            else:
                data_uri = b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"
        except Exception as e:
            log.warning("analyze_image: user-image compress skipped: %s", e)
            data_uri = b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"
        fname = img.get("filename", f"image_{image_index}")
        _out = await _run_vision(data_uri, f"user#{image_index}:{fname}")
        # Если эта картинка уже лежит в media_store (media_id проставлен при upload) —
        # дописываем описание в реестр, чтобы в следующих сообщениях агент знал, что на ней.
        _uid = img.get("media_id")
        if _uid and chat_id is not None:
            try:
                from agent.media import media_store as _ms
                await _ms.attach_description(chat_id, _uid, _out)
            except Exception:
                pass
        return _out

    return analyze_image
