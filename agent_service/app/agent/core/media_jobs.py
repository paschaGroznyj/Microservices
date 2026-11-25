"""
core/media_jobs.py
------------------
Реестр платных генераций RouterAPI (видео) в Redis, чтобы не платить дважды.

generate_video создаёт задачу у провайдера и сразу пишет её сюда. Если ждать
дольше VIDEO_WAIT_SEC, тул возвращает task_id, а check_video_status потом
забирает готовый ролик без повторного POST /videos. Повторный generate_video
с теми же параметрами в том же чате тоже не создаёт новую задачу: по отпечатку
параметров находится уже запущенная или готовая.

Ключ RouterAPI в Redis НЕ пишем: при проверке статуса он заново берётся из
vault владельца (resolve_router_api_key), как и при создании.

  media:job:{chat_id}:{task_id}      -> JSON задачи (TTL JOB_TTL_SECONDS)
  media:jobfp:{chat_id}:{fp}         -> task_id     (TTL DEDUP_TTL_SECONDS)
  media:joblock:{chat_id}:{task_id}  -> 1           (кто сейчас скачивает mp4)
"""

import hashlib
import json
import logging
import os
import time

log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
JOB_TTL_SECONDS = 24 * 60 * 60
DEDUP_TTL_SECONDS = int(os.getenv("MEDIA_DEDUP_TTL_SECONDS", str(3 * 60 * 60)))
LOCK_TTL_SECONDS = 300

_redis = None
_redis_failed = False


async def _r():
    global _redis, _redis_failed
    if _redis is not None or _redis_failed:
        return _redis
    try:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:  # fail-open: без Redis генерация работает как раньше
        _redis_failed = True
        log.warning("[media_jobs] Redis недоступен: %s", e)
    return _redis


def fingerprint(payload: dict) -> str:
    """Отпечаток параметров генерации. Data URI картинок сворачиваются в sha256."""
    def norm(v):
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, list):
            return [norm(x) for x in v]
        if isinstance(v, str) and v.startswith("data:") and len(v) > 256:
            return "sha256:" + hashlib.sha256(v.encode()).hexdigest()
        return v
    raw = json.dumps(norm(payload), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _k_job(chat_id, task_id) -> str:
    return f"media:job:{chat_id}:{task_id}"


def _k_fp(chat_id, fp) -> str:
    return f"media:jobfp:{chat_id}:{fp}"


def _k_lock(chat_id, task_id) -> str:
    return f"media:joblock:{chat_id}:{task_id}"


async def save_job(chat_id, job: dict, fp: str | None = None) -> None:
    r = await _r()
    if r is None or not job.get("task_id"):
        return
    job = dict(job)
    job.setdefault("created_at", time.time())
    job["updated_at"] = time.time()
    try:
        await r.set(_k_job(chat_id, job["task_id"]), json.dumps(job), ex=JOB_TTL_SECONDS)
        if fp:
            await r.set(_k_fp(chat_id, fp), job["task_id"], ex=DEDUP_TTL_SECONDS)
    except Exception as e:
        log.warning("[media_jobs] save %s: %s", job.get("task_id"), e)


async def get_job(chat_id, task_id: str) -> dict | None:
    r = await _r()
    if r is None or not task_id:
        return None
    try:
        raw = await r.get(_k_job(chat_id, str(task_id).strip()))
    except Exception as e:
        log.warning("[media_jobs] get %s: %s", task_id, e)
        return None
    return json.loads(raw) if raw else None


async def find_by_fingerprint(chat_id, fp: str) -> dict | None:
    """Живая (не упавшая) задача с теми же параметрами в этом чате."""
    r = await _r()
    if r is None:
        return None
    try:
        task_id = await r.get(_k_fp(chat_id, fp))
    except Exception:
        return None
    if not task_id:
        return None
    job = await get_job(chat_id, task_id)
    if not job or job.get("status") == "failed":
        return None
    return job


async def update_job(chat_id, task_id: str, **fields) -> dict | None:
    job = await get_job(chat_id, task_id)
    if job is None:
        return None
    job.update(fields)
    await save_job(chat_id, job)
    return job


async def drop_fingerprint(chat_id, fp: str | None) -> None:
    r = await _r()
    if r is None or not fp:
        return
    try:
        await r.delete(_k_fp(chat_id, fp))
    except Exception:
        pass


async def acquire_lock(chat_id, task_id: str) -> bool:
    """Скачивать mp4 должен кто-то один (фон или check_video_status). Без Redis — True."""
    r = await _r()
    if r is None:
        return True
    try:
        return bool(await r.set(_k_lock(chat_id, task_id), "1", nx=True, ex=LOCK_TTL_SECONDS))
    except Exception:
        return True


async def release_lock(chat_id, task_id: str) -> None:
    r = await _r()
    if r is None:
        return
    try:
        await r.delete(_k_lock(chat_id, task_id))
    except Exception:
        pass
