"""
tools/telegram/tool_send_file.py
────────────────────────────────────────
Явная отправка одного или нескольких уже существующих файлов пользователю.
Не создаёт файлы — только валидирует пути (или материализует image_key)
и передаёт их дальше по контракту saved_files за один вызов.
"""

import logging
import os
import re
import tempfile
from typing import List, Optional
import httpx

from langchain.tools import tool

log = logging.getLogger(__name__)

# Файлы для отправки лежат в /app/generated_files. /sandbox не разрешён:
# это общая служебная папка всех чатов (архив скриптов, библиотека кода).
ALLOWED_SEND_ROOTS = ["/app/generated_files"]

MAX_SEND_FILE_SIZE = 20 * 1024 * 1024


def _valid_send_path(path: str) -> str | None:
    real = os.path.realpath(path)
    if not any(real.startswith(os.path.realpath(r) + os.sep) for r in ALLOWED_SEND_ROOTS):
        return None
    return real


# Находит пути вида: /tmp/exec/workspaces/<workspace_id>/<filepath>
WORKSPACE_PATH_REGEX = re.compile(
    r".*?/workspaces/(?P<ws_id>[a-f0-9\-]+)/(?P<rel_path>.+)"
)
SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")


def _extract_workspace_info(path: str):
    """Извлекает workspace_id и относительный путь из строки пути."""
    match = WORKSPACE_PATH_REGEX.match(path)
    if match:
        return match.group("ws_id"), match.group("rel_path")
    return None, None


async def _fetch_file_from_sandbox(
    workspace_id: str, filepath: str, local_dest: str
) -> bool:
    """Скачивает файл из эндпоинта песочницы и сохраняет его локально."""
    url = f"{SANDBOX_URL}/workspace/{workspace_id}/file/{filepath}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                os.makedirs(os.path.dirname(local_dest), exist_ok=True)
                with open(local_dest, "wb") as f:
                    f.write(response.content)
                return True
            return False
    except Exception as e:
        print(f"[send_file_to_user] Ошибка при скачивании из sandbox: {e}")
        return False


async def _materialize_image_key(agent, image_key: str) -> dict | None:
    """Достаёт сгенерированную картинку из БД (generated_images) по image_key
    и пишет её во ВРЕМЕННЫЙ файл на диск, чтобы транспорт мог отдать её так же,
    как обычный saved_files. БД остаётся источником истины — файл эфемерный.

    Возвращает {"path", "name", "size_bytes"} или None, если не нашли/ошибка.
    """
    db = getattr(agent, "db", None)
    pool = getattr(db, "pool", None) if db else None
    if pool is None:
        log.warning("send_file_to_user: нет пула БД для материализации image_key=%s", image_key)
        return None

    key = str(image_key).strip()
    if not key:
        return None

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT image_data, file_name, mime_type FROM generated_images "
                "WHERE image_key::text = $1 LIMIT 1",
                key,
            )
    except Exception as e:
        log.error("send_file_to_user: ошибка запроса image_key=%s: %s", key, e)
        return None

    if not row or not row["image_data"]:
        return None

    try:
        raw = bytes(row["image_data"])
        fname = row["file_name"] or f"{key}.jpg"
        ext = os.path.splitext(fname)[1].lower() or ".jpg"
        dest_dir = os.path.join("/app/generated_files", "_img_keys")
        os.makedirs(dest_dir, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=f"gi_{key[:8]}_", suffix=ext, dir=dest_dir)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        return {"path": path, "name": os.path.basename(fname), "size_bytes": len(raw)}
    except Exception as e:
        log.error("send_file_to_user: не смог материализовать image_key=%s: %s", key, e)
        return None


def _normalize_str_list(*values) -> List[str]:
    result: List[str] = []
    seen = set()
    for val in values:
        if not val:
            continue
        items = val if isinstance(val, (list, tuple, set)) else [val]
        for item in items:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                result.append(s)
    return result


def _detect_media_kind(target: str) -> str:
    _ext = os.path.splitext(target)[1].lower()
    if _ext in (".ogg", ".oga", ".opus"):
        return "voice"
    if _ext in (".mp4", ".mov", ".webm", ".mkv"):
        return "video"
    if _ext in (".mp3", ".m4a", ".aac", ".flac", ".wav"):
        return "audio"
    if _ext in (".png", ".jpg", ".jpeg", ".webp"):
        return "image"
    return "file"


async def _resolve_single_path(raw_path: str) -> tuple[dict | None, str | None]:
    target = _valid_send_path(raw_path)

    if target is None or not os.path.isfile(target):
        ws_id, rel_path = _extract_workspace_info(raw_path)
        print(f"ИЗВЛЕЧЕННЫЕ ПУТИ: id={ws_id}, path={rel_path}")
        if ws_id and rel_path:
            download_dir = f"/app/generated_files/{ws_id}"
            filename = os.path.basename(rel_path)
            downloaded_target = os.path.join(download_dir, filename)

            success = await _fetch_file_from_sandbox(
                workspace_id=ws_id,
                filepath=rel_path,
                local_dest=downloaded_target,
            )

            if success:
                target = downloaded_target
            else:
                return None, f"Файл не найден локально и не удалось скачать из workspace '{ws_id}': {rel_path}"
        else:
            if target is None:
                return None, (
                    f"Путь '{raw_path}' недоступен для отправки. Отправлять можно файлы из "
                    f"/app/generated_files (path из saved_files) или из workspace: "
                    f"/tmp/exec/workspaces/<workspace_id>/output/<file>."
                )
            return None, (
                f"Файл '{raw_path}' не найден. Возьми path из saved_files последнего "
                f"execute_python_code или download_workspace_file; для файлов в workspace "
                f"проверь имя через list_workspace_files."
            )

    try:
        size = os.path.getsize(target)
    except OSError as e:
        return None, f"Не удалось получить размер файла '{raw_path}': {e}"

    if size > MAX_SEND_FILE_SIZE:
        return None, f"Файл '{os.path.basename(target)}' слишком большой для отправки ({size} байт, лимит {MAX_SEND_FILE_SIZE})."

    return {
        "filename": os.path.basename(target),
        "name": os.path.basename(target),
        "path": target,
        "size_bytes": size,
        "kind": _detect_media_kind(target),
    }, None


def make_send_file_tool(agent):

    @tool
    async def send_file_to_user(
        paths: Optional[List[str]] = None,
        image_keys: Optional[List[str]] = None,
        path: str = "",
        image_key: str = "",
    ) -> dict:
        """
        Sends one or multiple existing files from the sandbox or generated_files
        folder to the user in a single call. Always pass all ready files together
        via `paths` (or `image_keys`) instead of calling the tool multiple times.

        For images created by generate_image: pass `image_keys` (list of UUIDs from
        the generate_image report) or `image_key` instead of file paths — generated
        pictures live in the DB (generated_images) and have no disk path.

        WORKING WITH FILES/PRESENTATIONS/MEDIA: if you need to give the user
        artifacts assembled at the factory (presentation, video, archive, doc):
        create a workspace (create_workspace), assemble the files there, place them in output/,
        and call this tool once with `paths=["/tmp/exec/workspaces/{workspace_id}/output/<file1>", ...]`
        — all listed files will be auto-downloaded from the factory and sent.
        If a file does not exist, inform the user instead of retrying.

        Arguments:
            paths: list of full paths to the files to send in one batch, for example
                ["/app/generated_files/{run_id}/chart.png", "/tmp/exec/workspaces/{workspace_id}/output/report.pdf"].
            image_keys: list of UUIDs of pictures created by generate_image to send in one batch.
            path: (optional, backward-compatible) single file path. Prefer `paths` when sending multiple files.
            image_key: (optional, backward-compatible) single UUID of a generated image. Prefer `image_keys` for multiple images.
        """
        all_paths = _normalize_str_list(paths, path)
        all_image_keys = _normalize_str_list(image_keys, image_key)

        if not all_paths and not all_image_keys:
            return {
                "ok": False,
                "error": "Не переданы ни paths/path, ни image_keys/image_key. "
                         "Укажи список путей в paths или ключи картинок в image_keys.",
            }

        saved_files: List[dict] = []
        errors: List[str] = []

        for key in all_image_keys:
            mat = await _materialize_image_key(agent, key)
            if mat is None:
                errors.append(
                    f"Картинка с image_key '{key}' не найдена в хранилище. "
                    f"Проверь ключ через find_generated_image."
                )
            else:
                saved_files.append(
                    {
                        "filename": mat["name"],
                        "name": mat["name"],
                        "path": mat["path"],
                        "size_bytes": mat["size_bytes"],
                        "kind": "image",
                    }
                )

        for raw_p in all_paths:
            file_entry, err = await _resolve_single_path(raw_p)
            if err:
                errors.append(err)
            elif file_entry:
                saved_files.append(file_entry)

        if not saved_files:
            return {
                "ok": False,
                "error": "; ".join(errors) if errors else "Не удалось подготовить файлы к отправке.",
            }

        res = {
            "ok": True,
            "saved_files": saved_files,
        }
        if errors:
            res["errors"] = errors
        return res

    return send_file_to_user
