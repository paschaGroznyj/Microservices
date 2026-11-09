"""
core/sandbox_scripts_cleaner.py
───────────────────────────────────────
Чистка архива скриптов в /sandbox.

execute_python_code сохраняет каждый запуск:
  /sandbox/executed_scripts/<run_id>.py         успешные
  /sandbox/executed_scripts/failed/<run_id>.py  упавшие
  /sandbox/reusable/<run_id>.py                 reusable=True

Долго хранятся только скрипты, чей путь записан в code_library
(save_to_code_library). Остальные удаляются, когда с последнего изменения
прошло больше SCRIPT_TTL_SECONDS, как эфемерные файлы generated_files.
Остальной /sandbox (черновики админа) не трогается.
"""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Awaitable, Callable, Iterable

log = logging.getLogger(__name__)

SANDBOX_ROOT = Path("/sandbox")
EXECUTED_DIR = SANDBOX_ROOT / "executed_scripts"
REUSABLE_DIR = SANDBOX_ROOT / "reusable"

SCRIPT_TTL_SECONDS = 30 * 60
CLEANER_INTERVAL_SECONDS = 5 * 60


def sweep_scripts(keep: Iterable[str], ttl: int = SCRIPT_TTL_SECONDS,
                  dirs: Iterable[Path] | None = None, now: float | None = None) -> int:
    """Удаляет файлы старше ttl в executed_scripts/ и reusable/, кроме путей из keep.

    Возвращает число удалённых файлов. Симлинки пропускаются.
    """
    dirs = list(dirs) if dirs is not None else [EXECUTED_DIR, REUSABLE_DIR]
    keep_real = {os.path.realpath(p) for p in keep if p}
    now = time.time() if now is None else now
    removed = 0
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            try:
                if p.is_symlink() or not p.is_file():
                    continue
                if os.path.realpath(p) in keep_real:
                    continue
                if now - p.stat().st_mtime > ttl:
                    p.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


async def start_scripts_cleaner(
    get_keep_paths: Callable[[], Awaitable[Iterable[str]]],
    ttl: int = SCRIPT_TTL_SECONDS,
    interval: int = CLEANER_INTERVAL_SECONDS,
):
    """Фоновый таск из lifespan. Если пути code_library получить не удалось,
    проход пропускается целиком: без списка нельзя отличить скрипт библиотеки."""
    log.info("[scripts] cleaner запущен (ttl=%ss, interval=%ss)", ttl, interval)
    while True:
        try:
            keep = await get_keep_paths()
        except Exception as e:
            log.warning("[scripts] нет путей code_library, проход пропущен: %s", e)
        else:
            try:
                n = await asyncio.to_thread(sweep_scripts, keep, ttl)
                if n:
                    log.info("[scripts] cleaner удалил %d скриптов старше %ss", n, ttl)
            except Exception as e:
                log.error("[scripts] cleaner ошибка: %s", e)
        await asyncio.sleep(interval)
