"""
tools/storage/tool_obs_fetch.py
-------------------------------
fetch_from_obs: универсальная выгрузка файла из OBS без повторной генерации.

Всё, что агент отдал пользователю (send_file_to_user, generate_video/audio/music),
лежит в OBS под ключом catchpm/{chat_id}/{run_id}__{name}. Локальная копия в
/app/generated_files живёт ограниченно (30 мин, платные медиа — 3 ч). Этот тул
достаёт файл обратно:
  - в /app/generated_files (дальше send_file_to_user / analyze_image / upload_to_workspace);
  - либо сразу в workspace фабрики (дальше ffmpeg/монтаж там).

Источник задаётся одним из:
  obs_key   — catchpm/<chat_id>/<run_id>__<name>
  url       — публичный URL OBS (https://<bucket>.<host>/<key>) или /web/file?path=…&chat_id=…
  file_path — прежний локальный путь /app/generated_files/<run_id>/<name>
              (ключ выводится так же, как при заливке)
  name      — имя файла + run_id (как в saved/attached files)

Доступ: только объекты текущего чата (catchpm/{chat_id}/…). Админ может явно
указать другой chat_id и любой ключ под catchpm/.
"""

import logging
import os
import re
import uuid
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from langchain.tools import tool

from ...core import obs_file_storage as obs

log = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")
MAX_FETCH_BYTES = int(os.getenv("OBS_FETCH_MAX_BYTES", str(100 * 1024 * 1024)))  # = лимит фабрики
MEDIA_EXT = {".mp4", ".webm", ".mov", ".ogg", ".mp3", ".wav", ".m4a", ".opus"}
_WS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_SAFE_NAME_RE = re.compile(r"[^\w.\-() ]+", re.UNICODE)


def _safe_name(name: str) -> str:
    n = _SAFE_NAME_RE.sub("_", os.path.basename(name or "")).strip(" .")
    return n[:180] or "file.bin"


def _display_name(key: str) -> str:
    """catchpm/1/<run>__video.mp4 -> video.mp4"""
    base = key.rsplit("/", 1)[-1]
    return base.split("__", 1)[1] if "__" in base else base


def _key_from_url(url: str) -> tuple[str | None, str | None]:
    """(obs_key, error) из публичного URL OBS или /web/file?path=…&chat_id=…"""
    u = urlparse(url.strip())
    if u.path.rstrip("/").endswith("/web/file") or u.path == "/web/file":
        q = parse_qs(u.query)
        path = (q.get("path") or [None])[0]
        cid = (q.get("chat_id") or [None])[0]
        if not path or not cid:
            return None, "в /web/file URL нужны path и chat_id"
        return obs.build_obs_key(path, cid), None
    if u.scheme not in ("http", "https") or not u.netloc:
        return None, "url должен быть http(s) URL OBS или /web/file?path=…&chat_id=…"
    host = u.netloc.lower()
    bucket = (os.getenv("OBS_OPEN_BUCKET") or "").lower()
    base = (os.getenv("OBS_PUBLIC_BASE") or "").rstrip("/")
    path = unquote(u.path.lstrip("/"))
    if base and url.startswith(base + "/"):
        return unquote(url[len(base) + 1:].split("?", 1)[0]), None
    if bucket and host.startswith(bucket + "."):
        return path, None  # virtual-host: https://bucket.host/key
    if bucket and path.startswith(bucket + "/"):
        return path[len(bucket) + 1:], None  # path-style: https://host/bucket/key
    return None, f"хост {host} не похож на OBS-бакет этого сервиса"


def resolve_obs_key(*, obs_key=None, url=None, file_path=None, name=None,
                    run_id=None, chat_id=None) -> tuple[str | None, str | None]:
    """Источник -> ключ OBS. Ровно один способ, в порядке приоритета."""
    if obs_key:
        return str(obs_key).strip().lstrip("/"), None
    if url:
        return _key_from_url(str(url))
    if file_path:
        if chat_id is None:
            return None, "chat_id неизвестен"
        return obs.build_obs_key(str(file_path).strip(), chat_id), None
    if name and run_id:
        if chat_id is None:
            return None, "chat_id неизвестен"
        return obs.build_obs_key_from_name(chat_id, str(run_id).strip(), str(name)), None
    return None, "укажи obs_key, url, file_path или name+run_id"


def check_access(key: str, chat_id, is_admin: bool) -> str | None:
    """None если можно, иначе текст ошибки. Ключ уже нормализован."""
    if ".." in key.split("/") or "\\" in key:
        return "недопустимый ключ"
    prefix = obs.OBS_FILES_PREFIX + "/"
    if not key.startswith(prefix):
        return f"доступны только объекты под {prefix}"
    if is_admin:
        return None
    if chat_id is None or not key.startswith(f"{prefix}{chat_id}/"):
        return "файл принадлежит другому чату"
    return None


def _local_dest(key: str, dest_name: str | None) -> str:
    name = _safe_name(dest_name or _display_name(key))
    if os.path.splitext(name)[1].lower() in MEDIA_EXT:
        return obs.paid_media_path(name)  # медиа живёт 3 ч, как после генерации
    d = os.path.join(obs.GENERATED_FILES_ROOT, f"obs_{uuid.uuid4().hex[:16]}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def _is_admin(agent) -> bool:
    owner = agent._ctx_get("media_key_owner") if hasattr(agent, "_ctx_get") else None
    return bool(isinstance(owner, dict) and owner.get("is_admin"))


def make_obs_fetch_tool(agent):
    """Factory: fetch_from_obs."""

    @tool
    async def fetch_from_obs(
        obs_key: str | None = None,
        url: str | None = None,
        file_path: str | None = None,
        name: str | None = None,
        run_id: str | None = None,
        chat_id: str | None = None,
        workspace_id: str | None = None,
        dest: str = "",
        dest_name: str | None = None,
    ) -> str:
        """Download a file that was already delivered/generated earlier from OBS storage.

        Use it when a file from send_file_to_user or generate_video/audio/music is
        no longer on disk (local copies expire: 30 min, paid media 3 h). It
        NEVER regenerates anything, so it is the cheap way to get a paid video back.

        Source (give ONE, priority order):
            obs_key: catchpm/<chat_id>/<run_id>__<name> (from a tool report).
            url: public OBS URL of the file, or a /web/file?path=...&chat_id=... link.
            file_path: the old local path /app/generated_files/<run_id>/<name>.
            name + run_id: file name and its run folder.

        chat_id: only for admins, to fetch from another chat. Default: current chat.

        Destination:
            workspace_id empty -> saved into /app/generated_files/...; then use
                send_file_to_user, analyze_image or upload_to_workspace.
            workspace_id set -> uploaded straight into that factory workspace
                under `dest` (subfolder, e.g. "assets/video"), ready for
                execute_shell_commands (ffmpeg etc.).
            dest_name: optional new file name.

        Returns the local path or the workspace path, or a clear error.
        """
        cur_chat = agent._ctx_get("chat_id", getattr(agent, "current_chat_id", None))
        admin = _is_admin(agent)
        use_chat = cur_chat
        if chat_id and str(chat_id) != str(cur_chat):
            if not admin:
                return "fetch_from_obs error: чужой chat_id доступен только админу."
            use_chat = chat_id

        key, err = resolve_obs_key(obs_key=obs_key, url=url, file_path=file_path,
                                   name=name, run_id=run_id, chat_id=use_chat)
        if err:
            return f"fetch_from_obs error: {err}"
        err = check_access(key, use_chat, admin)
        if err:
            return f"fetch_from_obs error: {err} ({key})"

        data, err = await obs.get_object_bytes(key, MAX_FETCH_BYTES)
        if err == "not_found":
            return (f"fetch_from_obs error: в OBS нет объекта {key}. Проверь имя/run_id; "
                    f"файлы, которые не уходили пользователю, в OBS не заливаются.")
        if err and err.startswith("too_large:"):
            return (f"fetch_from_obs error: объект {err.split(':', 1)[1]} байт больше "
                    f"лимита {MAX_FETCH_BYTES}.")
        if err or data is None:
            return f"fetch_from_obs error: OBS недоступен: {err}"

        fname = _safe_name(dest_name or _display_name(key))
        if workspace_id:
            ws = str(workspace_id).strip()
            if not _WS_ID_RE.match(ws):
                return "fetch_from_obs error: некорректный workspace_id."
            sub = (dest or "").strip().strip("/")
            if ".." in sub.split("/"):
                return "fetch_from_obs error: dest не может содержать '..'."
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(
                        f"{SANDBOX_URL}/workspace/{ws}/upload",
                        files={"files": (fname, data, "application/octet-stream")},
                        data={"dest": sub, "unpack": "false"},
                    )
                    r.raise_for_status()
            except httpx.HTTPStatusError as e:
                return (f"fetch_from_obs error: фабрика ответила HTTP "
                        f"{e.response.status_code}: {e.response.text[:200]}")
            except httpx.HTTPError as e:
                return f"fetch_from_obs error: фабрика недоступна: {e}"
            ws_rel = f"{sub}/{fname}" if sub else fname
            return (f"Fetched {key} ({len(data) // 1024} KB) into workspace {ws}: {ws_rel} "
                    f"(/tmp/exec/workspaces/{ws}/{ws_rel}).")

        path = _local_dest(key, fname)
        with open(path, "wb") as f:
            f.write(data)
        return (f"Fetched {key} ({len(data) // 1024} KB) to {path}. "
                f"Use send_file_to_user / analyze_image / upload_to_workspace with this path.")

    return fetch_from_obs
