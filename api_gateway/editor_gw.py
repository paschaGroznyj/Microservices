"""
Гейтвей-роутер веб-редактора (Monaco, read-only).

Вариант А: файлы живут в песочнице (agent_sandbox), gateway стучится туда по workspace_id.
Реестр «workspace_id → user_id» живёт в chats_service (editor_workspaces).

Маршруты:
  GET /editor                         — страница редактора (за auth)
  GET /api/editor/workspaces          — список воркспейсов юзера (дерево проекта)
  GET /api/editor/tree?workspace_id=  — дерево файлов конкретного воркспейса
  GET /api/editor/file?workspace_id=&path=  — содержимое файла (read-only)

Подключается в app.py: editor_gw.bind(http_client, chats_url, sandbox_url, get_current_user, templates)
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_http = None
_chats_url = None
_sandbox_url = None
_get_current_user = None
_templates = None


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
    return _templates.TemplateResponse("editor.html", {"request": request, "chat_id": chat_id})


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
