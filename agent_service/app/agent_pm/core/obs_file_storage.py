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

# Корневой префикс внутри бакета: obs://{OBS_BUCKET}/catchpm/
OBS_FILES_PREFIX = "catchpm"

# Корни локальной ФС, откуда разрешено брать файлы на заливку.
GENERATED_FILES_ROOT = "/app/generated_files"
SANDBOX_ROOT = "/sandbox"

# TTL эфемерных файлов на хосте (секунды).
HOST_FILE_TTL_SECONDS = 30 * 60  # 30 мин
CLEANER_INTERVAL_SECONDS = 5 * 60  # период запуска фонового клинера


def _obs_params() -> dict:
    return {
        "service_name": "s3",
        "aws_access_key_id": os.getenv("OBS_ACCESS_KEY"),
        "aws_secret_access_key": os.getenv("OBS_SECRET_KEY"),
        "region_name": os.getenv("OBS_REGION"),
        "endpoint_url": os.getenv("OBS_ENDPOINT"),
        "config": Config(s3={"addressing_style": "virtual"}),
    }


def _bucket() -> str:
    b = os.getenv("OBS_BUCKET")
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
            await client.put_object(Bucket=_bucket(), Key=key, Body=body)
        log.info("[obs] uploaded %s → obs://%s/%s", real, _bucket(), key)
        return key
    except Exception as e:
        log.error("[obs] ошибка заливки %s: %s", real, e)
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
