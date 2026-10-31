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

Хранение (по требованию Павла): ЕДИНСТВЕННЫЙ источник истины —
таблица generated_images (бинарник BYTEA + метаданные). Постоянного
накопления в /app/generated_files больше нет. На диск кладётся ТОЛЬКО
эфемерный файл текущего хода — он нужен транспорту, который отдаёт превью
фронту через files_to_send/web/file. При возврате к старой картинке
(find_generated_image) бинарник материализуется из БД во временный файл.
"""

import os
import re
import uuid
import base64
import logging
import mimetypes
import tempfile

import httpx
from langchain.tools import tool

from ...core.agent_provider import get_image_gen_config

log = logging.getLogger(__name__)

# Корень для ЭФЕМЕРНЫХ файлов транспорта: сюда кладётся картинка только
# ради текущей отдачи фронту (/web/file превью). Долговременное хранилище —
# таблица generated_images (BYTEA). Файлы в этой папке можно чистить в любой
# момент: при возврате к картинке она берётся из БД, а не с диска.
GENERATED_ROOT = "/app/generated_files"

# Whitelist-корни для чтения source-картинок с диска (image-to-image).
# Читаем ТОЛЬКО отсюда — защита от path-traversal. Совпадает с ALLOWED_FILES_ROOTS
# в agent_service/app.py и ALLOWED_IMAGE_ROOTS в tool_analyze_image.
ALLOWED_SOURCE_ROOTS = [
    os.path.realpath("/app/generated_files"),
    os.path.realpath("/sandbox"),
]


def _is_within_source_roots(real_path: str) -> bool:
    for root in ALLOWED_SOURCE_ROOTS:
        if real_path == root or real_path.startswith(root + os.sep):
            return True
    return False


def _resolve_source_path(path_or_name: str) -> str | None:
    """Резолвит source-картинку строго внутри whitelist-корней.

    Принимает как полный путь (внутри /sandbox|/app/generated_files), так и
    просто имя файла — тогда ищет по basename рекурсивно. basename берётся
    принудительно, чтобы нельзя было протащить обход директорий.
    """
    raw = (path_or_name or "").strip()
    if not raw:
        return None
    real = os.path.realpath(raw)
    if _is_within_source_roots(real) and os.path.isfile(real):
        return real
    safe_name = os.path.basename(raw)
    if not safe_name:
        return None
    import glob as _glob
    for root in ALLOWED_SOURCE_ROOTS:
        for cand in _glob.glob(os.path.join(root, "**", safe_name), recursive=True):
            real = os.path.realpath(cand)
            if _is_within_source_roots(real) and os.path.isfile(real):
                return real
    return None

# Жёсткий потолок генераций внутри одного вызова тула.
MAX_IMAGES_PER_CALL = 10

# Сколько минут держать эфемерный файл на диске, прежде чем он считается
# мусором и подлежит удалению. Источник истины — БД (generated_images),
# поэтому файл нужен лишь на время, пока фронт тянет превью через /web/file.
EPHEMERAL_TTL_MINUTES = 30


# Расширения, которые считаем сгенерированными картинками и вправе чистить
# по TTL. ВАЖНО: трогаем ТОЛЬКО эти файлы — остальные артефакты в
# generated_files (документы, csv, отчёты других тулов) НЕ удаляем.
_CLEANABLE_IMAGE_EXTS = (".png", ".gif", ".jpeg", ".jpg", ".webp")


def _cleanup_stale_generated_files() -> None:
    """DISABLED. Жёсткая TTL-очистка сгенерированных картинок отключена.

    Почему отключено: диск-фолбэк в /web/db_image (ветка
    feature/image-disk-fallback) отдаёт превью прямо с диска для картинок,
    которых нет в generated_images (артефакты в /app/generated_files,
    /sandbox). Если удалять файлы по TTL, такие превью снова начнут ловить
    404. Для картинок с записью в БД удаление файла было безопасным, но
    отличить их здесь без обращения к БД нельзя, поэтому уборку убираем
    целиком — пусть чисткой диска занимается внешний TTL песочницы/тома.

    Тело сохранено ниже закомментированным на случай возврата поведения.
    """
    return  # no-op: очистка намеренно отключена
    # import time as _t
    # try:
    #     if not os.path.isdir(GENERATED_ROOT):
    #         return
    #     cutoff = _t.time() - EPHEMERAL_TTL_MINUTES * 60
    #     removed = 0
    #     for root, _dirs, files in os.walk(GENERATED_ROOT):
    #         for name in files:
    #             if not name.lower().endswith(_CLEANABLE_IMAGE_EXTS):
    #                 continue  # чужой файл — не наш, не трогаем
    #             full = os.path.join(root, name)
    #             try:
    #                 if os.path.getmtime(full) < cutoff:
    #                     os.remove(full)
    #                     removed += 1
    #             except OSError:
    #                 continue
    #     if removed:
    #         log.info("generate_image: удалено устаревших картинок из %s: %d",
    #                  GENERATED_ROOT, removed)
    # except Exception as e:
    #     log.warning("generate_image: уборка GENERATED_ROOT не удалась: %s", e)

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


def _coerce_str_list(val) -> list[str]:
    """Приводит аргумент к list[str]. LLM часто шлёт JSON-строкой ("['a.png']")
    или CSV ("a.png, b.png") — не даём итерации разбежаться по символам."""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    s = str(val).strip()
    if not s:
        return []
    try:
        import json as _json
        parsed = _json.loads(s)
        if isinstance(parsed, list):
            return [str(x).strip() for x in parsed if str(x).strip()]
        return [str(parsed).strip()]
    except Exception:
        return [p.strip() for p in s.strip("[]").split(",") if p.strip()]


def _coerce_int_list(val) -> list[int]:
    """Приводит аргумент к list[int]. Терпит "[0]", "0", "0,1", [0], 0."""
    if val is None:
        return []
    if isinstance(val, int):
        return [val]
    if isinstance(val, list):
        out = []
        for x in val:
            try:
                out.append(int(str(x).strip()))
            except (TypeError, ValueError):
                continue
        return out
    s = str(val).strip()
    if not s:
        return []
    try:
        import json as _json
        parsed = _json.loads(s)
        if isinstance(parsed, list):
            return _coerce_int_list(parsed)
        return [int(parsed)]
    except Exception:
        out = []
        for p in s.strip("[]").split(","):
            p = p.strip()
            if not p:
                continue
            try:
                out.append(int(p))
            except ValueError:
                continue
        return out


def _materialize_from_db(image_data, file_name: str | None, rec_id) -> str | None:
    """Пишет бинарник из БД во временный файл (для передачи в source_images).
    Возвращает путь или None. Файл эфемерный — БД остаётся источником истины."""
    if not image_data:
        return None
    try:
        raw = bytes(image_data)
        ext = os.path.splitext(file_name or "")[1].lower() or ".jpg"
        fd, path = tempfile.mkstemp(prefix=f"img_{rec_id}_", suffix=ext)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return path
    except Exception as e:
        log.error("find_generated_image: не смог материализовать id=%s из БД: %s", rec_id, e)
        return None


def _read_image_as_data_uri(path: str) -> str | None:
    """Читает картинку с диска (СТРОГО из whitelist-корней) и возвращает
    data-URL или None при ошибке.

    Путь/имя резолвится через _resolve_source_path: разрешены только файлы
    внутри /sandbox и /app/generated_files (можно указать полный путь или
    просто имя файла — тогда ищется по basename рекурсивно). Так закрыт
    path-traversal — чужой путь вернёт None.
    """
    real = _resolve_source_path(path)
    if not real:
        log.warning("generate_image: файл не найден или вне whitelist: %s", path)
        return None
    try:
        ext = os.path.splitext(real)[1].lower()
        if ext not in IMAGE_EXT:
            log.warning("generate_image: неподдерживаемое расширение %s (%s)", ext, real)
            return None
        mime = mimetypes.guess_type(real)[0] or "image/png"
        with open(real, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        log.error("generate_image: не смог прочитать %s: %s", real, e)
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


async def _persist_generated_image(
    agent,
    *,
    raw: bytes,
    ext: str,
    fname: str,
    prompt: str,
    model: str,
    aspect_ratio: str,
    temperature: float,
) -> tuple[int | None, str | None]:
    """Пишет сгенерированную картинку (бинарник + метаданные) в generated_images.

    Возвращает кортеж (id, image_key) новой строки или (None, None) при ошибке.
    Ошибка записи в БД НЕ должна ронять генерацию.
    """
    db = getattr(agent, "db", None)
    pool = getattr(db, "pool", None) if db else None
    if pool is None:
        log.warning("generate_image: нет пула БД — картинка не записана в generated_images")
        return None, None

    mime = mimetypes.guess_type(fname)[0] or ("image/jpeg" if ext in (".jpg", ".jpeg") else "image/png")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO generated_images
                    (image_key, chat_id, user_id, message_id, file_name, mime_type,
                     prompt, model, aspect_ratio, temperature, image_data, byte_size)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id, image_key;
                """,
                agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None)),
                agent._ctx_get("user_id", getattr(agent, "current_user_id", None)),
                agent._ctx_get("message_id", getattr(agent, "current_message_id", None)),
                fname,
                mime,
                prompt,
                model,
                aspect_ratio,
                float(temperature),
                raw,
                len(raw),
            )
        new_id = row["id"] if row else None
        image_key = str(row["image_key"]) if row and row.get("image_key") else None
        log.info("generate_image: записано в generated_images id=%s key=%s (%d байт)", new_id, image_key, len(raw))
        return new_id, image_key
    except Exception as e:
        log.error("generate_image: не смог записать в generated_images: %s", e, exc_info=True)
        return None, None


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

    :param agent: экземпляр KnowledgeAgent. Используем request-local
        буфер сгенерированных картинок (через ContextVar) и, при
        необходимости, source-картинки пользователя.
    :param images: картинки, загруженные пользователем в текущем сообщении
        (тот же список, что у analyze_image): [{filename, mime_type, b64}].
        Доступны для image-to-image по индексу.
    """
    images = images or []
    cfg = get_image_gen_config()

    @tool
    async def generate_image(
        prompt: str,
        temperature: float = 0.4,
        aspect_ratio: str = "16:9",
        n: int = 1,
        source_images: list[str] | None = None,
        source_image_keys: list[str] | None = None,
        source_user_image_indexes: list[int] | None = None,
        source_media_ids: list[str] | None = None,
        extra_quality_prompt: str | None = None,
    ) -> str:
        """
        Generates image(s) based on a text prompt via gemini-flash-image.
        The result is high-quality pictures (JPEG) saved to the server and
        automatically sent to the user via the CatchPM interface.

        Parameters:
          prompt        — scene description (what to draw). Required.
          temperature   — 0.2–1.0, default is 0.4 (lower = stricter adherence to the prompt).
          aspect_ratio — "16:9" | "9:16" | "1:1" | "4:3" | "3:4" ( the default is 16:9).
          n — how many images to generate (1-10, 1 by default).
          source_images — a list of paths OR image file names for
                          image-to-image, read from disk STRICTLY from
                          Allowed folders: /sandbox and /app/generated_files.
                          You can specify the full path within them or just the file name
                          (searched recursively by name). Paths outside these
                          folders are ignored (directory traversal protection).
          source_image_keys — optional: a list of image_key from generated_images
                          for image-to-image without a disk (preferred path).
          source_user_image_indexes — optional: indexes of images that
                          the user attached to the message (0 is the first one),
                          if you want to edit them.
          source_media_ids — optional: list of media_ids from MEDIA STORAGE
                          chat (Redis, pictures of past messages, live ~24h).
                          Their registry is shown to you in the "CHAT MEDIA STORAGE" section.
                          Use it to make image-to-image based on
                          images sent earlier when in attachments
                          the current message is no longer there.
          extra_quality_prompt — optional: additional art direction in the system
                          (e.g. "Rembrandt light, 85mm portrait lens, f/1.8").

        Returns a text report with the paths of saved images.
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

        # ── Нормализация аргументов-списков ──────────────────────────
        # LLM часто передаёт списки СТРОКОЙ ("[0]", "0", "0,1", "['a.png']")
        # вместо настоящего list. Приводим к list, иначе итерация пойдёт по
        # символам строки и индексы «уедут вне диапазона» (баг из лога).
        source_images = _coerce_str_list(source_images)
        source_image_keys = _coerce_str_list(source_image_keys)
        source_user_image_indexes = _coerce_int_list(source_user_image_indexes)
        source_media_ids = _coerce_str_list(source_media_ids)

        log.info(
            "generate_image: source_images=%s, source_image_keys=%s, source_user_image_indexes=%s, "
            "доступно картинок из интерфейса=%d",
            source_images, source_image_keys, source_user_image_indexes, len(images),
        )

        # ── Собираем source-картинки для image-to-image ──────────────
        source_data_uris: list[str] = []
        if source_images:
            for p in source_images:
                uri = _read_image_as_data_uri(p)
                if uri:
                    source_data_uris.append(uri)
                else:
                    log.warning("generate_image: пропущен source-путь %s", p)
        if source_image_keys:
            db = getattr(agent, "db", None)
            pool = getattr(db, "pool", None) if db else None
            if pool is None:
                log.warning("generate_image: нет пула БД для source_image_keys")
            else:
                async with pool.acquire() as conn:
                    for k in source_image_keys:
                        try:
                            row = await conn.fetchrow(
                                "SELECT image_data, mime_type FROM generated_images WHERE image_key::text = $1 LIMIT 1",
                                str(k).strip(),
                            )
                            if not row or not row["image_data"]:
                                log.warning("generate_image: image_key=%s не найден", k)
                                continue
                            raw = bytes(row["image_data"])
                            mime = row["mime_type"] or "image/jpeg"
                            b64 = base64.b64encode(raw).decode("ascii")
                            source_data_uris.append(f"data:{mime};base64,{b64}")
                        except Exception as e:
                            log.warning("generate_image: не смог прочитать image_key=%s: %s", k, e)
        if source_user_image_indexes:
            for idx in source_user_image_indexes:
                if 0 <= idx < len(images):
                    img = images[idx]
                    b64 = (img.get("b64") or "").strip()
                    if not b64:
                        log.warning("generate_image: у картинки #%s пустой b64", idx)
                        continue
                    mime = img.get("mime_type") or "image/png"
                    uri = b64 if b64.startswith("data:") else f"data:{mime};base64,{b64}"
                    source_data_uris.append(uri)
                    log.info("generate_image: взята пользовательская картинка #%s (%s)",
                             idx, img.get("filename"))
                else:
                    log.warning(
                        "generate_image: индекс пользовательской картинки %s вне диапазона "
                        "(доступно %d). Картинка из интерфейса не подхватилась.",
                        idx, len(images),
                    )
        # Источник: media_id из пер-чат media_store (Redis, картинки прошлых сообщений).
        if source_media_ids:
            chat_id = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))
            if chat_id is None:
                log.warning("generate_image: chat_id неизвестен, source_media_ids пропущены")
            else:
                try:
                    from agent.media import media_store as _ms
                except Exception:
                    _ms = None
                if _ms is not None:
                    for _mid in source_media_ids:
                        try:
                            raw, meta = await _ms.get_media(chat_id, _mid)
                            if not raw:
                                log.warning("generate_image: media_id=%s не найден в хранилище", _mid)
                                continue
                            mime = (meta or {}).get("mime") or "image/png"
                            b64 = base64.b64encode(raw).decode("ascii")
                            source_data_uris.append(f"data:{mime};base64,{b64}")
                            log.info("generate_image: взят media_id=%s из media_store", _mid)
                        except Exception as e:
                            log.warning("generate_image: media_id=%s ошибка чтения: %s", _mid, e)

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

        # Источник истины — БД. На диск для generated_images ничего не пишем.
        db_records: list[dict] = []   # [{id, image_key, file_name}] — для отчёта/памяти
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
                    fname = f"{uuid.uuid4().hex[:20]}{ext}"

                    # Пишем бинарник + метаданные ТОЛЬКО в БД (без file_path).
                    # Для текущего ответа фронту передаём image_key + name, а загрузка
                    # превью идёт через /api/chats/{chat}/image/{message_id}/{file_name}
                    # → agent_service /web/db_image.
                    rec_id, image_key = await _persist_generated_image(
                        agent,
                        raw=raw, ext=ext, fname=fname,
                        prompt=prompt.strip(), model=cfg["model"],
                        aspect_ratio=aspect_ratio, temperature=temperature,
                    )
                    if rec_id is None or not image_key:
                        errors.append(f"#{i}: не удалось сохранить картинку в БД")
                        continue

                    db_records.append({"id": rec_id, "image_key": image_key, "file_name": fname})
                    agent._ctx_append_pending_image({
                        "image_key": image_key,
                        "name": fname,
                        "kind": "image",
                    })
                    log.info("generate_image: сохранено в БД id=%s key=%s (%d байт)", rec_id, image_key, len(raw))
                except Exception as e:
                    log.error("generate_image: ошибка генерации #%d: %s", i, e, exc_info=True)
                    errors.append(f"#{i}: {type(e).__name__}: {e}")

        if not db_records:
            return (
                "Не удалось сгенерировать ни одного изображения.\n"
                + "\n".join(errors)
            )

        lines = [f"Сгенерировано изображений: {len(db_records)} (модель {cfg['model']}, "
                 f"temperature={temperature}, aspect_ratio={aspect_ratio})."]
        # Печатаем id + image_key + file_name. Это оседает в tool_trace и даёт
        # стабильную ссылку на картинку без привязки к локальному пути.
        for rec in db_records:
            if rec["id"] is not None:
                lines.append(
                    f"  • name={rec['file_name']}  image_key={rec['image_key']}  "
                    f"(generated_images.id={rec['id']})"
                )
            else:
                lines.append(f"  • name={rec['file_name']}  (в БД не записано)")
        if errors:
            lines.append("Часть генераций не удалась:")
            lines.extend(f"  ! {e}" for e in errors)
        lines.append(
            "Картинки сохранены во внутреннем хранилище (prompt + model + aspect_ratio + "
            "created_at + сам бинарник). Чтобы вернуться к ранее сгенерированному "
            "изображению в следующих ходах — вызови тул find_generated_image: либо по "
            "ключевым словам из описания (query), либо по image_key из отчёта выше. "
            "Он вернёт image_key и при необходимости подготовит временный путь, который "
            "можно передать в source_images для правки (image-to-image). НЕ используй SQL "
            "для доступа к этому хранилищу. "
            "Нужно отправить изображение пользователю."
        )
        return "\n".join(lines)

    return generate_image


def make_find_generated_image_tool(agent):
    """
    Фабрика тула поиска РАНЕЕ сгенерированных изображений.

    Даёт агенту доступ к таблице generated_images БЕЗ SQL: все запросы —
    хардкод внутри тула через пул агента (agent.db.pool). Для обычного
    сценария пользователя это единственный способ вернуться к картинке —
    прямой execute_sql_query в generated_images делать не нужно (это
    админский путь).

    Агент может искать несколькими способами:
      • по ключевым словам из описания (query → ILIKE по prompt);
      • по уникальному ключу image_key;
      • по id записи generated_images.
    Поиск ограничен текущим чатом (request-local chat_id), если он известен.
    """

    @tool
    async def find_generated_image(
        query: str | None = None,
        image_key: str | None = None,
        image_id: int | None = None,
        limit: int = 5,
    ) -> str:
        """
        Ищет ранее сгенерированные (тобой же) изображения во внутреннем
        хранилище и возвращает image_key/метаданные, чтобы вернуться к картинке
        (например передать image_key в source_image_keys тула generate_image для
        правки — image-to-image).

        Параметры:
          query     — ключевые слова из описания картинки (напр. "поп-арт кот").
                      Ищет по тексту промпта, по которому картинка была создана.
          image_key — точный UUID-ключ картинки (из прошлого отчёта).
          image_id  — точный id строки generated_images.
          limit     — сколько последних совпадений вернуть (1–20, по умолчанию 5).

        Если оба аргумента пусты — вернёт последние сгенерированные картинки
        в текущем чате (по времени, свежие первыми).

        Возвращает список: путь, промпт, модель, соотношение сторон, время.
        НЕ требует и НЕ использует SQL — всё внутри тула.
        """
        db = getattr(agent, "db", None)
        pool = getattr(db, "pool", None) if db else None
        if pool is None:
            return "Хранилище сгенерированных изображений недоступно (нет пула БД)."

        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(limit, 20))

        chat_id = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))

        # ── Хардкод-запросы, параметризованные, без пользовательского SQL ──
        conds: list[str] = []
        params: list = []
        idx = 1

        if image_key and str(image_key).strip():
            conds.append(f"image_key::text = ${idx}")
            params.append(str(image_key).strip())
            idx += 1
        if image_id is not None:
            conds.append(f"id = ${idx}")
            params.append(int(image_id))
            idx += 1
        if query and query.strip():
            conds.append(f"prompt ILIKE ${idx}")
            params.append(f"%{query.strip()}%")
            idx += 1
        if chat_id is not None:
            conds.append(f"chat_id = ${idx}")
            params.append(chat_id)
            idx += 1

        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        sql = (
            "SELECT id, image_key, file_name, prompt, model, aspect_ratio, "
            "       temperature, byte_size, image_data, created_at "
            "FROM generated_images"
            + where
            + f" ORDER BY created_at DESC LIMIT {limit};"
        )

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(sql, *params)
        except Exception as e:
            log.error("find_generated_image: ошибка запроса: %s", e, exc_info=True)
            return f"Не удалось обратиться к хранилищу изображений: {type(e).__name__}: {e}"

        if not rows:
            hint = []
            if query:
                hint.append(f"по ключевым словам «{query}»")
            if image_key:
                hint.append(f"по image_key «{image_key}»")
            if image_id is not None:
                hint.append(f"по id={image_id}")
            scope = " ".join(hint) if hint else "в этом чате"
            return (
                f"Ранее сгенерированных изображений {scope} не нашёл. "
                "Возможно, картинка ещё не создавалась — используй generate_image."
            )

        out = [f"Найдено сгенерированных изображений: {len(rows)}"]
        for r in rows:
            # Хранилище — БД, а не диск. Если файла на диске нет (его почистили
            # или это другой инстанс) — материализуем бинарник из image_data во
            # временный файл, чтобы его можно было передать в source_images.
            usable_path = _materialize_from_db(r["image_data"], r["file_name"], r["id"])
            flag = "" if usable_path else "  [бинарник недоступен]"
            prompt_short = (r["prompt"] or "")[:160]
            out.append(
                f"\n• id={r['id']}  image_key={r['image_key']}  {usable_path or '(нет пути)'}{flag}\n"
                f"  промпт: {prompt_short}\n"
                f"  модель: {r['model']}  aspect: {r['aspect_ratio']}  "
                f"temp: {r['temperature']}  создано: {r['created_at']}"
            )
        out.append(
            "\nЧтобы отредактировать одну из них — передай image_key в аргумент "
            "source_image_keys тула generate_image."
        )
        return "\n".join(out)

    return find_generated_image
