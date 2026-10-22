"""
Гейтвей-роутер MONACO-РЕДАКТОРА (/editor).

Лёгкий файловый редактор на Monaco БЕЗ исполнения кода и терминала.
Каждый пользователь видит и правит ТОЛЬКО свой воркспейс:
    EDITOR_ROOT/{user_id}/...

Безопасность целиком держится на валидации пути (resolve + проверка,
что target внутри базы пользователя). Shell/exec отсутствуют как класс.

Маршруты:
  GET  /editor                      — страница редактора (Monaco), за auth
  GET  /api/editor/tree             — дерево файлов воркспейса юзера
  GET  /api/editor/file?path=...    — прочитать файл (текст)
  PUT  /api/editor/file?path=...    — сохранить файл (атомарно)
  POST /api/editor/create           — создать файл/папку
  POST /api/editor/rename           — переименовать/переместить
  POST /api/editor/delete           — удалить файл/папку

Подключается в app.py:
    editor_gw.bind(get_current_user=get_current_user, templates=templates)
    app.include_router(editor_gw.router)
"""
import os
import shutil
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Корень всех воркспейсов. Переопределяется env EDITOR_ROOT.
EDITOR_ROOT = Path(os.getenv("EDITOR_ROOT", "/data/editor_workspaces")).resolve()

# Лимит размера файла на чтение/запись (байт)
MAX_FILE_BYTES = int(os.getenv("EDITOR_MAX_FILE_BYTES", str(5 * 1024 * 1024)))

# Директории, которые не показываем в дереве
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea"}

# выставляются из app.py через bind()
_get_current_user = None
_templates = None


def bind(get_current_user, templates):
    global _get_current_user, _templates
    _get_current_user = get_current_user
    _templates = templates


# ───────────────────────── БЕЗОПАСНОСТЬ ПУТЕЙ ─────────────────────────

async def _require_user(request: Request) -> dict:
    user = await _get_current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def _user_base(user_id) -> Path:
    base = (EDITOR_ROOT / str(user_id)).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _resolve(user_id, rel: str) -> Path:
    """
    Схлопывает rel внутри воркспейса юзера и гарантирует, что результат
    НЕ вылезает за base (защита от ../ и симлинков). resolve() вызывается
    ДО проверки, чтобы '..' и симлинки уже были развёрнуты.
    """
    base = _user_base(user_id)
    rel = (rel or "").lstrip("/").strip()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(status_code=403, detail="path escapes workspace")
    return target


def _guess_lang(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "javascript", ".tsx": "typescript", ".json": "json",
        ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
        ".md": "markdown", ".sql": "sql", ".sh": "shell", ".yml": "yaml",
        ".yaml": "yaml", ".xml": "xml", ".txt": "plaintext", ".ino": "cpp",
        ".c": "c", ".cpp": "cpp", ".h": "cpp", ".java": "java", ".go": "go",
        ".rs": "rust", ".toml": "ini", ".ini": "ini", ".env": "ini",
        ".dockerfile": "dockerfile",
    }.get(ext, "plaintext")


def _build_tree(path: Path, base: Path) -> list:
    out = []
    try:
        entries = sorted(
            path.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except (PermissionError, FileNotFoundError):
        return out
    for p in entries:
        if p.name in _SKIP_DIRS:
            continue
        rel = str(p.relative_to(base))
        if p.is_dir():
            out.append({
                "name": p.name, "path": rel, "type": "dir",
                "children": _build_tree(p, base),
            })
        else:
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append({
                "name": p.name, "path": rel, "type": "file",
                "size": size, "lang": _guess_lang(p.name),
            })
    return out


# ───────────────────────── МОДЕЛИ ─────────────────────────

class CreateReq(BaseModel):
    path: str
    type: str = "file"   # file | dir


class RenameReq(BaseModel):
    path: str
    new_path: str


class DeleteReq(BaseModel):
    path: str


# ───────────────────────── СТРАНИЦА ─────────────────────────

@router.get("/editor", response_class=HTMLResponse)
async def editor_page(request: Request):
    user = await _get_current_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login-page")
    return _templates.TemplateResponse("editor.html", {"request": request, "user": user})


# ───────────────────────── ФАЙЛОВОЕ API ─────────────────────────

@router.get("/api/editor/tree")
async def editor_tree(request: Request):
    user = await _require_user(request)
    base = _user_base(user["user_id"])
    return JSONResponse({"root": _build_tree(base, base)})


@router.get("/api/editor/file")
async def editor_read(request: Request, path: str):
    user = await _require_user(request)
    target = _resolve(user["user_id"], path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    try:
        text = target.read_text(encoding="utf-8")
        binary = False
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="binary file, not editable")
    return JSONResponse({
        "path": path, "content": text, "binary": binary,
        "lang": _guess_lang(target.name),
    })


@router.put("/api/editor/file")
async def editor_write(request: Request, path: str):
    user = await _require_user(request)
    target = _resolve(user["user_id"], path)
    body = await request.body()
    if len(body) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="content too large")
    if target.exists() and target.is_dir():
        raise HTTPException(status_code=409, detail="path is a directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    # атомарная запись: во временный файл + replace
    tmp = target.with_suffix(target.suffix + ".tmp~")
    tmp.write_bytes(body)
    os.replace(tmp, target)
    return JSONResponse({"ok": True, "path": path, "size": len(body)})


@router.post("/api/editor/create")
async def editor_create(request: Request, req: CreateReq):
    user = await _require_user(request)
    target = _resolve(user["user_id"], req.path)
    if target.exists():
        raise HTTPException(status_code=409, detail="already exists")
    if req.type == "dir":
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    return JSONResponse({"ok": True, "path": req.path, "type": req.type})


@router.post("/api/editor/rename")
async def editor_rename(request: Request, req: RenameReq):
    user = await _require_user(request)
    src = _resolve(user["user_id"], req.path)
    dst = _resolve(user["user_id"], req.new_path)
    if not src.exists():
        raise HTTPException(status_code=404, detail="source not found")
    if dst.exists():
        raise HTTPException(status_code=409, detail="target already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)
    return JSONResponse({"ok": True, "path": req.new_path})


@router.post("/api/editor/delete")
async def editor_delete(request: Request, req: DeleteReq):
    user = await _require_user(request)
    target = _resolve(user["user_id"], req.path)
    base = _user_base(user["user_id"])
    if target == base:
        raise HTTPException(status_code=403, detail="cannot delete workspace root")
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return JSONResponse({"ok": True, "path": req.path})
