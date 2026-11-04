"""
core/obs_file_storage.py
───────────────────────────────────────
OBS-хранилище для файлов, которые улетают пользователю через send_file_to_user
(и любые attached_files агента в CatchPM-вебе).

Модель хранения:
  obs://{OBS_BUCKET}/catchpm/{chat_id}/{run_id}__{basename}

Где run_id — имя папки внутри /app/generated_files/{run_id}/file (уже
содержит uuid → ключ детерминирован, коллизий нет). Для файлов
из /sandbox run_id = "sandbox".

Вариант E1: ключ полностью выводим из (chat_id + local_path), поэтому
/web/file может собрать тот же ключ без хранения маппинга в БД.

Файлы на хосте (generated_files) эфемерны: живут 30 мин, потом
чистятся фоновым клинером (start_generated_files_cleaner). Источник
истины — OBS.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

from aiobotocore.session import AioSession
from botocore.client import Config

log = logging.getLogger(__name__)

# botocore >= 1.36 по умолчанию (при TLS) шлёт PutObject как
# STREAMING-UNSIGNED-PAYLOAD-TRAILER: chunked-тело + trailer-заголовок
# x-amz-checksum-crc32. Это НЕ регулируется payload_signing_enabled —
# отдельный механизм ("default request checksums"). OBS не умеет его
# парсить и отвечает XAmzContentSHA256Mismatch. Откатываем на классику
# (Content-MD5 / обычный UNSIGNED-PAYLOAD одним телом).
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

# Корневой префикс внутри бакета: obs://{OBS_BUCKET}/catchpm/
OBS_FILES_PREFIX = "catchpm"

# Корни локальной ФС, откуда разрешено брать файлы на заливку.
GENERATED_FILES_ROOT = "/app/generated_files"
SANDBOX_ROOT = "/sandbox"

# TTL эфемерных файлов на хосте (секунды).
HOST_FILE_TTL_SECONDS = 30 * 60  # 30 мин
CLEANER_INTERVAL_SECONDS = 5 * 60  # период запуска фонового клинера


def _obs_params() -> dict:
    """
    Параметры S3-клиента для OBS.

    Для S3-совместимых хранилищ (в т.ч. OBS/прокси) режим virtual-host +
    payload signing иногда приводит к XAmzContentSHA256Mismatch на PutObject —
    это лечится отдельно, через checksum-переменные env выше, а НЕ через
    смену addressing_style. Сам OBS-эндпоинт при этом требует именно
    virtual-hosted style (bucket.endpoint/key) — path-style даёт
    VirtualHostDomainRequired. Дефолты:
      - virtual-host addressing
      - отключённый payload signing (UNSIGNED-PAYLOAD)
      - отключённые дефолтные request-checksums (см. env выше)
    При необходимости можно переопределить через env:
      OBS_S3_ADDRESSING_STYLE=virtual|path
      OBS_PAYLOAD_SIGNING_ENABLED=true|false
    """
    addressing_style = (os.getenv("OBS_S3_ADDRESSING_STYLE") or "virtual").strip().lower()
    if addressing_style not in {"path", "virtual"}:
        addressing_style = "virtual"

    payload_signing_enabled = (os.getenv("OBS_PAYLOAD_SIGNING_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes", "on"
    }

    config_kwargs = dict(
        signature_version="s3v4",
        s3={
            "addressing_style": addressing_style,
            "payload_signing_enabled": payload_signing_enabled,
        },
    )
    try:
        # Доступно в botocore >= 1.36. Дублирует env-переменные выше на
        # уровне Config — на случай если процесс уже успел проимпортить
        # botocore до простановки os.environ.setdefault (например, при
        # переиспользовании готовой сессии/интерпретатора).
        config_kwargs["request_checksum_calculation"] = "when_required"
        config_kwargs["response_checksum_validation"] = "when_required"
        config = Config(**config_kwargs)
    except TypeError:
        # Старый botocore без этих параметров — не страшно, тогда
        # достаточно env-переменных, которые он тоже читает.
        config_kwargs.pop("request_checksum_calculation", None)
        config_kwargs.pop("response_checksum_validation", None)
        config = Config(**config_kwargs)

    return {
        "service_name": "s3",
        "aws_access_key_id": os.getenv("OBS_ACCESS_KEY"),
        "aws_secret_access_key": os.getenv("OBS_SECRET_KEY"),
        "region_name": os.getenv("OBS_REGION"),
        "endpoint_url": os.getenv("OBS_ENDPOINT"),
        "config": config,
    }


def _bucket() -> str:
    b = os.getenv("OBS_OPEN_BUCKET")
    if not b:
        raise RuntimeError("OBS_BUCKET не задан в env")
    return b


def _run_id_and_name(local_path: str) -> tuple[str, str]:
    """
    Из локального пути вытаскиваем (run_id, basename).
    Для /app/generated_files/{run_id}/file  → (run_id, file)
    Для /sandbox/...                        → ("sandbox", basename)
    Ключ детерминирован, поэтому /web/file повторит его однозначно.
    """
    real = os.path.realpath(local_path)
    name = os.path.basename(real)
    gen_root = os.path.realpath(GENERATED_FILES_ROOT)
    if real.startswith(gen_root + os.sep):
        rel = os.path.relpath(real, gen_root)
        parts = rel.split(os.sep)
        run_id = parts[0] if len(parts) >= 2 else "root"
        return run_id, name
    return "sandbox", name


def build_obs_key(local_path: str, chat_id: int | str) -> str:
    """catchpm/{chat_id}/{run_id}__{basename} — детерминированный ключ (E1)."""
    run_id, name = _run_id_and_name(local_path)
    return f"{OBS_FILES_PREFIX}/{chat_id}/{run_id}__{name}"


def build_obs_key_from_name(chat_id: int | str, run_id: str, name: str) -> str:
    """Сборка ключа из готовых компонентов (для /web/file)."""
    return f"{OBS_FILES_PREFIX}/{chat_id}/{run_id}__{os.path.basename(name)}"


async def upload_user_file(local_path: str, chat_id: int | str) -> Optional[str]:
    """
    Заливает локальный файл в OBS под детерминированным ключом.
    Возвращает obs_key при успехе либо None (ошибка не фатальна —
    отдача файла пользователю не должна ломаться, если OBS недоступен).
    """
    real = os.path.realpath(local_path)
    if not os.path.isfile(real):
        log.warning("[obs] файл не найден для заливки: %s", real)
        return None
    key = build_obs_key(real, chat_id)
    try:
        with open(real, "rb") as f:
            body = f.read()
        async with AioSession().create_client(**_obs_params()) as client:
            await client.put_object(Bucket=_bucket(), Key=key, Body=body, ContentLength=len(body))
        log.info("[obs] uploaded %s → obs://%s/%s", real, _bucket(), key)
        return key
    except Exception as e:
        log.error("[obs] ошибка заливки %s: %s", real, e)
        return None


def build_group_obs_key(chat_id: int | str, run_id: str, name: str) -> str:
    """Ключ для файла группового чата: catchpm/group/{room}/{run_id}__{name}."""
    safe = os.path.basename(str(name)) or "file"
    return f"{OBS_FILES_PREFIX}/group/{chat_id}/{run_id}__{safe}"


async def upload_bytes(obs_key: str, body: bytes,
                       content_type: Optional[str] = None) -> Optional[str]:
    """
    Заливает произвольные байты в OBS под явным ключом (для group-chat files:
    файл приходит multipart-ом, промежуточного файла на диске нет).
    Возвращает obs_key при успехе, иначе None.
    """
    try:
        kwargs = dict(Bucket=_bucket(), Key=obs_key, Body=body,
                      ContentLength=len(body))
        if content_type:
            kwargs["ContentType"] = content_type
        async with AioSession().create_client(**_obs_params()) as client:
            await client.put_object(**kwargs)
        log.info("[obs] uploaded bytes(%d) → obs://%s/%s", len(body), _bucket(), obs_key)
        return obs_key
    except Exception as e:
        log.error("[obs] ошибка заливки bytes → %s: %s", obs_key, e)
        return None


async def download_range(obs_key: str, start: int, end: int) -> tuple[Optional[bytes], Optional[int]]:
    """Читает байтовый диапазон [start..end] из OBS для HTTP 206 Range-стриминга."""
    try:
        async with AioSession().create_client(**_obs_params()) as client:
            resp = await client.get_object(
                Bucket=_bucket(),
                Key=obs_key,
                Range=f"bytes={start}-{end}",
            )
            data = await resp["Body"].read()
            total = None
            cr = resp.get("ContentRange") or resp.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("content-range")
            if cr and "/" in str(cr):
                try:
                    total = int(str(cr).rsplit("/", 1)[-1])
                except ValueError:
                    total = None
            return data, total
    except Exception as e:
        log.warning("[obs] не удалось скачать range %s (%d-%d): %s", obs_key, start, end, e)
        return None, None


async def download_bytes(obs_key: str) -> Optional[bytes]:
    """Тянет объект из OBS в память. Возвращает bytes либо None."""
    try:
        async with AioSession().create_client(**_obs_params()) as client:
            resp = await client.get_object(Bucket=_bucket(), Key=obs_key)
            return await resp["Body"].read()
    except Exception as e:
        log.warning("[obs] не удалось скачать %s: %s", obs_key, e)
        return None


async def download_to_local(obs_key: str, dest_path: str) -> Optional[str]:
    """
    Тянет объект из OBS и кладёт в dest_path (generated_files/{chat_id}/...).
    mtime выставляется = now, чтобы TTL-30мин отсчитывался от выгрузки.
    Возвращает локальный путь либо None.
    """
    try:
        async with AioSession().create_client(**_obs_params()) as client:
            resp = await client.get_object(Bucket=_bucket(), Key=obs_key)
            data = await resp["Body"].read()
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(data)
        now = time.time()
        os.utime(dest_path, (now, now))
        log.info("[obs] downloaded obs://%s/%s → %s", _bucket(), obs_key, dest_path)
        return dest_path
    except Exception as e:
        log.warning("[obs] не удалось скачать %s: %s", obs_key, e)
        return None


def _sweep_dir(root: str, ttl: int) -> int:
    """Удаляет файлы старше ttl сек. Возвращает число удалённых."""
    removed = 0
    now = time.time()
    root_p = Path(root)
    if not root_p.is_dir():
        return 0
    for p in root_p.rglob("*"):
        if not p.is_file():
            continue
        try:
            if now - p.stat().st_mtime > ttl:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


async def start_generated_files_cleaner(
    root: str = GENERATED_FILES_ROOT,
    ttl: int = HOST_FILE_TTL_SECONDS,
    interval: int = CLEANER_INTERVAL_SECONDS,
):
    """
    Фоновый таск: раз в interval сек удаляет из generated_files файлы
    старше ttl сек. Применяется как к свежесозданным файлам,
    так и к скачанным из OBS. Запускается из lifespan.
    """
    log.info("[obs] generated_files cleaner запущен (ttl=%ss, interval=%ss)", ttl, interval)
    while True:
        try:
            n = _sweep_dir(root, ttl)
            if n:
                log.info("[obs] cleaner удалил %d эфемерных файлов старше %ss", n, ttl)
        except Exception as e:
            log.error("[obs] cleaner ошибка: %s", e)
        await asyncio.sleep(interval)