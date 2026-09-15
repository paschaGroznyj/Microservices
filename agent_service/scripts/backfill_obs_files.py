#!/usr/bin/env python3
"""
Backfill локальных файлов в OBS по дампу таблицы files.

ИСТОЧНИК ИСТИНЫ — JSON-дамп таблицы files (рядом со скриптом):
  agent_service/scripts/files_dump.json

Каждая запись дампа несёт свой chat_id и file_path, поэтому chat_id
берётся ИЗ ЗАПИСИ (а не из общего аргумента). Скрипт строит тот же
детерминированный ключ, что и рантайм:
  catchpm/{chat_id}/{run_id}__{basename}

Где run_id:
  - для /app/generated_files/<run_id>/<file>  → <run_id>
  - для /sandbox/<file>                        → "sandbox"

ТРАНСЛЯЦИЯ ПУТЕЙ (важно):
  В БД file_path записан как КОНТЕЙНЕРНЫЙ путь agent_service:
    /app/generated_files/<run_id>/<file>   (том ./agent_service:/app)
    /sandbox/<file>                        (том ./agent_service/sandbox:/sandbox)
  На ХОСТЕ этих путей нет: физически файлы лежат в
    <repo>/agent_service/generated_files/<run_id>/<file>
    <repo>/agent_service/sandbox/<file>
  Скрипт транслирует контейнерный путь в хостовый (resolve_local_path).
  Если запущен ВНУТРИ контейнера — контейнерный путь используется как есть.

ПОЛИТИКА (по договорённости):
  - Идём строго по записям JSON.
  - Файла нет на диске       → лог [missing] + ПРОПУСК. Мусор в OBS не грузим.
  - Дубликат ключа (коллизия) → НЕ скипается. Ключ дополняется id записи
    (catchpm/{chat_id}/{run_id}__{name}__id{id}{ext}) и файл всё равно
    грузится, с логом [collision-renamed].

Пример:
  python agent_service/scripts/backfill_obs_files.py
  python agent_service/scripts/backfill_obs_files.py --dry-run
  python agent_service/scripts/backfill_obs_files.py --dump /path/to/files_dump.json --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from aiobotocore.session import AioSession
from botocore.client import Config

from dotenv import load_dotenv

load_dotenv()

# У новых версий botocore/aiobotocore по умолчанию включён расчёт
# дополнительной контрольной суммы (CRC32) с отправкой тела чанками
# (aws-chunked + trailer). Huawei OBS (и/или прокси перед ним) это
# не поддерживает корректно -> сервер пересчитывает sha256 от
# изменённого тела -> XAmzContentSHA256Mismatch. Возвращаемся к
# классической схеме (Content-MD5 / UNSIGNED-PAYLOAD, без chunked-trailer).
os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
os.environ.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")

OBS_FILES_PREFIX = "catchpm"
GENERATED_FILES_ROOT = "/app/generated_files"
SANDBOX_ROOT = "/app/sandbox"
DEFAULT_DUMP = str(Path(__file__).with_name("files_dump.json"))


def _obs_params() -> dict:
    return {
        "service_name": "s3",
        "aws_access_key_id": os.getenv("OBS_ACCESS_KEY"),
        "aws_secret_access_key": os.getenv("OBS_SECRET_KEY"),
        "region_name": os.getenv("OBS_REGION"),
        "endpoint_url": os.getenv("OBS_ENDPOINT"),
        "config": Config(
            s3={"addressing_style": "virtual", "payload_signing_enabled": False},
            signature_version="s3v4",
        ),
    }


def _bucket() -> str:
    b = os.getenv("OBS_OPEN_BUCKET")
    if not b:
        raise RuntimeError("OBS_BUCKET не задан")
    return b


def _validate_obs_env() -> None:
    required = ["OBS_ACCESS_KEY", "OBS_SECRET_KEY", "OBS_REGION", "OBS_ENDPOINT", "OBS_BUCKET"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Не заданы переменные окружения OBS: {', '.join(missing)}")


def _run_id_and_name(local_path: str) -> tuple[str, str]:
    real = os.path.realpath(local_path)
    name = os.path.basename(real)
    gen_root = os.path.realpath(GENERATED_FILES_ROOT)
    if real.startswith(gen_root + os.sep):
        rel = os.path.relpath(real, gen_root)
        parts = rel.split(os.sep)
        run_id = parts[0] if len(parts) >= 2 else "root"
        return run_id, name
    return "sandbox", name


def build_obs_key(local_path: str, chat_id: Any, prefix: str = OBS_FILES_PREFIX) -> str:
    """Тот же детерминированный ключ, что и в рантайме."""
    run_id, name = _run_id_and_name(local_path)
    return f"{prefix}/{chat_id}/{run_id}__{name}"


def build_obs_key_unique(
    local_path: str, chat_id: Any, row_id: Any, prefix: str = OBS_FILES_PREFIX, suffix: str = ""
) -> str:
    """Ключ для случая коллизии: тот же формат, но с id записи (и, при
    необходимости, доп. суффиксом-счётчиком) перед расширением, чтобы
    гарантированно не потерять файл."""
    run_id, name = _run_id_and_name(local_path)
    stem, ext = os.path.splitext(name)
    tag = f"__id{row_id}" + (f"_{suffix}" if suffix else "")
    return f"{prefix}/{chat_id}/{run_id}__{stem}{tag}{ext}"


# Контейнерные корни (как пишется в БД внутри agent_service).
CONTAINER_GENERATED = "/app/generated_files"
CONTAINER_SANDBOX = "/sandbox"


def _default_repo_root() -> Path:
    # <repo>/agent_service/scripts/backfill_obs_files.py -> <repo>
    return Path(__file__).resolve().parents[2]


def resolve_local_path(file_path: str, repo_root: Path) -> Path:
    """Контейнерный путь из БД -> реальный путь на хосте.

    /app/generated_files/<run_id>/<f> -> <repo>/agent_service/generated_files/<run_id>/<f>
    /sandbox/<f>                      -> <repo>/agent_service/sandbox/<f>
    Если путь уже существует как есть (запуск ВНУТРИ контейнера) —
    возвращаем его без трансляции.
    """
    if not file_path:
        return Path(file_path or "")
    # Запуск внутри контейнера: контейнерный путь валиден.
    if os.path.isfile(file_path):
        return Path(file_path)
    agent_root = repo_root / "agent_service"
    if file_path.startswith(CONTAINER_GENERATED + "/"):
        rel = file_path[len(CONTAINER_GENERATED) + 1:]
        return agent_root / "generated_files" / rel
    if file_path == CONTAINER_SANDBOX or file_path.startswith(CONTAINER_SANDBOX + "/"):
        rel = file_path[len(CONTAINER_SANDBOX) + 1:] if len(file_path) > len(CONTAINER_SANDBOX) else ""
        return agent_root / "sandbox" / rel
    # Неизвестный префикс — отдаём как есть (будет отловлено как missing).
    return Path(file_path)


def _load_dump(dump_path: str) -> list[dict]:
    p = Path(dump_path)
    if not p.exists():
        raise RuntimeError(f"Дамп не найден: {dump_path}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise RuntimeError("Ожидался JSON-массив записей таблицы files")
    return data


async def upload_file(client, bucket: str, file_path: Path, key: str) -> int:
    body = file_path.read_bytes()
    await client.put_object(Bucket=bucket, Key=key, Body=body)
    return len(body)


async def run(args: argparse.Namespace) -> int:
    _validate_obs_env()

    rows = _load_dump(args.dump)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else _default_repo_root()
    print(f"[dump] {args.dump}: {len(rows)} записей")
    print(f"[repo-root] {repo_root} (agent_service/generated_files, agent_service/sandbox)")

    plan: list[tuple[Path, str, Any]] = []
    seen: dict[str, str] = {}
    missing = 0
    no_path = 0
    no_chat = 0
    collisions = 0

    for row in rows:
        file_path = row.get("file_path")
        chat_id = row.get("chat_id")
        row_id = row.get("id")
        if not file_path:
            no_path += 1
            print(f"[skip-no-path] id={row_id} нет file_path")
            continue
        if chat_id in (None, "", "-1"):
            no_chat += 1
            print(f"[skip-no-chat] id={row_id} file={file_path} chat_id={chat_id!r}")
            continue

        fp = resolve_local_path(file_path, repo_root)
        if not fp.exists() or not fp.is_file():
            missing += 1
            print(f"[missing] id={row_id} db={file_path} host={fp} — нет на диске, пропуск")
            continue

        key = build_obs_key(file_path, chat_id, args.prefix)
        if key in seen:
            collisions += 1
            orig_owner = seen[key]
            # Коллизия НЕ скипается: гоним под уникальным ключом с id записи.
            unique_key = build_obs_key_unique(file_path, chat_id, row_id, args.prefix)
            bump = 0
            while unique_key in seen:
                bump += 1
                unique_key = build_obs_key_unique(
                    file_path, chat_id, row_id, args.prefix, suffix=str(bump)
                )
            print(
                f"[collision-renamed] id={row_id} {file_path} -> {key} "
                f"занят (владелец: {orig_owner}); грузим как {unique_key}"
            )
            key = unique_key

        seen[key] = file_path
        plan.append((fp, key, chat_id))

    print(
        f"[plan] rows={len(rows)} to_upload={len(plan)} "
        f"missing={missing} no_path={no_path} no_chat={no_chat} collisions_renamed={collisions}"
    )

    if args.dry_run:
        for fp, key, chat_id in plan[:50]:
            print(f"[dry-run] chat={chat_id} {fp} -> obs://{os.getenv('OBS_BUCKET')}/{key}")
        if len(plan) > 50:
            print(f"[dry-run] ... ещё {len(plan) - 50}")
        return 0

    if not plan:
        print("[done] Нечего загружать")
        return 0

    bucket = _bucket()
    uploaded = 0
    bytes_total = 0
    failed = 0
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async with AioSession().create_client(**_obs_params()) as client:
        async def worker(fp: Path, key: str):
            nonlocal uploaded, bytes_total, failed
            async with semaphore:
                try:
                    n = await upload_file(client, bucket, fp, key)
                    uploaded += 1
                    bytes_total += n
                    if uploaded % 50 == 0:
                        print(f"[progress] uploaded={uploaded}/{len(plan)}")
                except Exception as e:
                    failed += 1
                    print(f"[error] {fp} -> {key}: {e}")

        await asyncio.gather(*(worker(fp, key) for fp, key, _ in plan))

    print(
        f"[done] uploaded={uploaded} failed={failed} missing={missing} "
        f"bytes={bytes_total} bucket={bucket}"
    )
    return 0 if failed == 0 else 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill в OBS по дампу таблицы files (JSON — источник истины)")
    p.add_argument("--dump", default=DEFAULT_DUMP, help=f"путь к JSON-дампу (default: {DEFAULT_DUMP})")
    p.add_argument("--repo-root", default="",
                   help="корень репо (где лежит agent_service/). По умолчанию вычисляется от скрипта")
    p.add_argument("--prefix", default=OBS_FILES_PREFIX, help="OBS prefix (default: catchpm)")
    p.add_argument("--concurrency", type=int, default=8, help="кол-во параллельных загрузок")
    p.add_argument("--dry-run", action="store_true", help="только показать план, без upload")
    return p.parse_args()


def _run_with_clean_loop(coro) -> int:
    """Запуск с ручным управлением циклом.

    asyncio.run на Python 3.12 при GC aiobotocore/aiohttp-транспортов
    дёргает колбэки на уже закрытом loop -> шумит
    "TypeError: NoneType object is not callable / Exception in callback None()".
    На результат не влияет, но засоряет вывод. Гасим корректным дренажом:
    сначала прогоняем корутину, затем shutdown_asyncgens + один прогон
    отложенных колбэков ДО close(), чтобы транспорты закрылись на живом loop.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        rc = loop.run_until_complete(coro)
        loop.run_until_complete(loop.shutdown_asyncgens())
        # Дать отложенным call_soon (закрытие коннекторов) отработать на живом loop.
        loop.run_until_complete(asyncio.sleep(0.25))
        return rc
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _silence_loop_teardown_noise() -> None:
    """Глушим конкретный мусорный лог 'Exception in callback None()' от asyncio
    на этапе закрытия — это не наши ошибки, а GC чужих транспортов."""
    import logging as _logging
    _logging.getLogger("asyncio").setLevel(_logging.CRITICAL)


if __name__ == "__main__":
    _silence_loop_teardown_noise()
    try:
        raise SystemExit(_run_with_clean_loop(run(parse_args())))
    except KeyboardInterrupt:
        print("\n[abort] interrupted", file=sys.stderr)
        raise SystemExit(130)