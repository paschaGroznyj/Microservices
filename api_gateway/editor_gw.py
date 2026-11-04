"""
Гейтвей-роутер веб-редактора (Monaco, read-only).

Вариант А: файлы живут в песочнице (agent_sandbox), gateway стучится туда по workspace_id.
Реестр «workspace_id → user_id» живёт в chats_service (editor_workspaces).

Маршруты:
  GET /editor                         — страница редактора (за auth)
  GET /api/editor/workspaces          — список воркспейсов юзера (дерево проекта)
  GET /api/editor/tree?workspace_id=  — дерево файлов конкретного воркспейса
  GET /api/editor/file?workspace_id=&path=  — содержимое файла
  PUT /api/editor/file?workspace_id=&path=  — сохранить файл (main gateway)
  GET /api/editor/watch?workspace_id=  — SSE: live-изменения файлов (события пушит agent_sandbox)
  POST /internal/editor/notify         — приём событий от agent_sandbox (X-Editor-Token)

Подключается в app.py: editor_gw.bind(http_client, chats_url, sandbox_url, get_current_user, templates)
"""
import asyncio
import hmac
import json
import logging
import os
import re
from fastapi import APIRouter, Request, HTTPException
from urllib.parse import quote
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_http = None
_chats_url = None
_sandbox_url = None
_get_current_user = None
_templates = None
_WS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def bind(http_client, chats_url, sandbox_url, get_current_user, templates):
    global _http, _chats_url, _sandbox_url, _get_current_user, _templates
    _http = http_client
    _chats_url = chats_url
    _sandbox_url = sandbox_url
    _get_current_user = get_current_user
    _templates = templates


async def _require_user(request: Request):
    user = await _get_current_user(request)
    if not user or not user.get("user_id"):
        return None
    return user


async def _assert_owns(user_id: int, workspace_id: str):
    """Гейт: воркспейс должен быть закреплён за этим юзером в реестре."""
    try:
        r = await _http.get(f"{_chats_url}/editor/owns",
                            params={"user_id": user_id, "workspace_id": workspace_id})
        if r.status_code == 200 and r.json().get("owns"):
            return
    except Exception as e:
        logger.error(f"editor owns check failed: {e}")
        raise HTTPException(status_code=502, detail="Реестр недоступен")
    raise HTTPException(status_code=403, detail="Воркспейс не принадлежит пользователю")


@router.get("/editor", response_class=HTMLResponse)
async def editor_page(request: Request):
    user = await _require_user(request)
    if not user:
        return RedirectResponse(url="/login-page", status_code=302)
    chat_id = request.query_params.get("chat_id", "")
    workspace_id = request.query_params.get("workspace_id", "")
    return _templates.TemplateResponse("editor.html", {"request": request, "chat_id": chat_id, "workspace_id": workspace_id})


@router.get("/api/editor/workspaces")
async def api_workspaces(request: Request):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    chat_id = request.query_params.get("chat_id") or None
    r = await _http.get(f"{_chats_url}/editor/workspaces",
                        params={"user_id": user["user_id"], **({"chat_id": chat_id} if chat_id else {})})
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Реестр недоступен")
    return r.json()


@router.get("/api/editor/tree")
async def api_tree(request: Request, workspace_id: str):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    await _assert_owns(user["user_id"], workspace_id)
    r = await _http.get(f"{_sandbox_url}/workspace/{workspace_id}/editor/tree")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Песочница: дерево недоступно")
    return r.json()


@router.get("/api/editor/ttl")
async def api_ttl(request: Request, workspace_id: str):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    await _assert_owns(user["user_id"], workspace_id)
    r = await _http.get(f"{_sandbox_url}/workspace/{workspace_id}/editor/ttl")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Песочница: TTL недоступен")
    return r.json()


# ── Live-обновление: in-memory хаб подписок ──────────────────────
# agent_sandbox сам ничего не сканирует: после работы инструмента он шлёт
# POST /internal/editor/notify, хаб раздаёт событие SSE-подпискам этого workspace.
# Gateway работает одним uvicorn-воркером, поэтому хаб в памяти процесса.
EDITOR_NOTIFY_TOKEN = os.environ.get("EDITOR_NOTIFY_TOKEN", "")
_WATCH_MAX_PER_WS = 16
_WATCH_QUEUE = 64
_WATCH_HEARTBEAT = 20.0
_WATCH_EVENTS = {"change", "ttl", "gone"}
_PATH_CAP = 200
_watchers: dict[str, set[asyncio.Queue]] = {}


def publish(workspace_id: str, event: str, data: dict) -> int:
    """Кладёт событие во все очереди подписчиков workspace. Возвращает число подписчиков."""
    subs = _watchers.get(workspace_id)
    if not subs:
        return 0
    for q in list(subs):
        try:
            q.put_nowait((event, data))
        except asyncio.QueueFull:
            # клиент не успевает читать — схлопываем очередь в одно событие
            while not q.empty():
                q.get_nowait()
            if event == "gone":
                q.put_nowait(("gone", data))
            else:
                q.put_nowait(("change", {"created": [], "modified": [], "deleted": [],
                                         "tree_changed": True, "truncated": True}))
    return len(subs)


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


@router.get("/api/editor/watch")
async def api_watch(request: Request, workspace_id: str):
    """SSE-подписка на изменения workspace. Файлы не сканируются: события приходят от песочницы."""
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    if not _WS_ID_RE.match(workspace_id or ""):
        raise HTTPException(status_code=400, detail="Некорректный workspace_id")
    await _assert_owns(user["user_id"], workspace_id)
    if len(_watchers.get(workspace_id, ())) >= _WATCH_MAX_PER_WS:
        raise HTTPException(status_code=429, detail="Слишком много подписок на workspace")

    async def stream():
        q: asyncio.Queue = asyncio.Queue(maxsize=_WATCH_QUEUE)
        _watchers.setdefault(workspace_id, set()).add(q)
        try:
            yield b"retry: 3000\n\n"
            yield _sse("ready", {"workspace_id": workspace_id})
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=_WATCH_HEARTBEAT)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield b": ping\n\n"
                    continue
                yield _sse(event, data)
                if event == "gone":
                    break
        finally:
            subs = _watchers.get(workspace_id)
            if subs is not None:
                subs.discard(q)
                if not subs:
                    _watchers.pop(workspace_id, None)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _clean_paths(v) -> list[str]:
    if not isinstance(v, list):
        return []
    return [x[:1024] for x in v[:_PATH_CAP] if isinstance(x, str) and x]


@router.post("/internal/editor/notify")
async def internal_editor_notify(request: Request):
    """Приём событий от agent_sandbox. Закрыт общим секретом EDITOR_NOTIFY_TOKEN."""
    token = request.headers.get("x-editor-token", "")
    if not EDITOR_NOTIFY_TOKEN or not hmac.compare_digest(token.encode(), EDITOR_NOTIFY_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="forbidden")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="bad body")
    wid = body.get("workspace_id") or ""
    event = body.get("event") or ""
    raw = body.get("data") if isinstance(body.get("data"), dict) else {}
    if not _WS_ID_RE.match(wid) or event not in _WATCH_EVENTS:
        raise HTTPException(status_code=400, detail="bad event")
    if event == "change":
        data = {
            "created": _clean_paths(raw.get("created")),
            "modified": _clean_paths(raw.get("modified")),
            "deleted": _clean_paths(raw.get("deleted")),
            "tree_changed": bool(raw.get("tree_changed")),
            "truncated": bool(raw.get("truncated")),
            "source": str(raw.get("source") or "")[:32],
        }
    elif event == "ttl":
        data = {"permanent": bool(raw.get("permanent"))}
    else:
        data = {}
    return {"ok": True, "delivered": publish(wid, event, data)}


@router.get("/api/editor/file")
async def api_file(request: Request, workspace_id: str, path: str):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    await _assert_owns(user["user_id"], workspace_id)
    r = await _http.get(f"{_sandbox_url}/workspace/{workspace_id}/editor/read",
                        params={"path": path})
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Файл недоступен")
    return r.json()


class EditorSaveBody(BaseModel):
    workspace_id: str
    path: str
    content: str


@router.put("/api/editor/file")
async def api_file_save(request: Request, body: EditorSaveBody):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    await _assert_owns(user["user_id"], body.workspace_id)
    r = await _http.put(
        f"{_sandbox_url}/workspace/{body.workspace_id}/editor/write",
        json={"path": body.path, "content": body.content},
    )
    if r.status_code != 200:
        detail = "Песочница: запись не удалась"
        try:
            detail = r.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=r.status_code, detail=detail)
    # другие вкладки этого workspace подтянут сохранённый файл
    publish(body.workspace_id, "change", {"created": [], "modified": [body.path], "deleted": [],
                                       "tree_changed": False, "truncated": False, "source": "editor"})
    return r.json()


@router.get("/api/editor/download")
async def api_file_download(request: Request, workspace_id: str, path: str):
    user = await _require_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Не авторизован")
    await _assert_owns(user["user_id"], workspace_id)
    clean_path = path.lstrip("/")
    r = await _http.get(f"{_sandbox_url}/workspace/{workspace_id}/file/{clean_path}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Файл недоступен для выгрузки")
    file_name = os.path.basename(clean_path) or "file"
    ascii_name = file_name.encode("ascii", "replace").decode("ascii")
    return Response(
        content=r.content,
        media_type=r.headers.get("content-type", "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(file_name, safe='')}"
            )
        },
    )
