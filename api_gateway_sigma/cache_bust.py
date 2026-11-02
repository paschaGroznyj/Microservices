import hashlib
import json
import re
import time
from pathlib import Path
from fastapi import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

_FILE_HASH_CACHE: dict[str, tuple[int, int, str]] = {}
_BUILD_ID_CACHE: dict[str, object] = {"ts": 0.0, "sig": None, "build_id": "init"}

_STATIC_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:href|src|data-icon-light|data-icon-dark)\s*=\s*)(?P<q>["'])(?P<path>/static/[^"'?#]+\.(?:css|js|png|svg|ico|webp))(?P<q2>["'])""",
    re.IGNORECASE,
)
_HEAD_OPEN_RE = re.compile(r"(<head(?:\s[^>]*)?>)", re.IGNORECASE)


def _file_version(rel_static_path: str) -> str:
    clean = rel_static_path.lstrip("/")
    if clean.startswith("static/"):
        clean = clean[len("static/"):]
    fpath = (STATIC_DIR / clean).resolve()
    try:
        st = fpath.stat()
    except OSError:
        return get_build_id()
    key = str(fpath)
    cached = _FILE_HASH_CACHE.get(key)
    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
        return cached[2]
    try:
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()[:10]
    except OSError:
        digest = str(st.st_mtime_ns)[-10:]
    _FILE_HASH_CACHE[key] = (st.st_mtime_ns, st.st_size, digest)
    return digest


def get_build_id() -> str:
    now = time.monotonic()
    if now - float(_BUILD_ID_CACHE["ts"]) < 2.0 and _BUILD_ID_CACHE["build_id"] != "init":
        return str(_BUILD_ID_CACHE["build_id"])

    sig_parts: list[tuple[str, int, int]] = []
    for root in (STATIC_DIR, TEMPLATES_DIR):
        if not root.exists():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in (".js", ".css", ".html"):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(BASE_DIR)).replace("\\", "/")
            sig_parts.append((rel, st.st_mtime_ns, st.st_size))

    sig_tuple = tuple(sig_parts)
    if sig_tuple == _BUILD_ID_CACHE.get("sig") and _BUILD_ID_CACHE["build_id"] != "init":
        _BUILD_ID_CACHE["ts"] = now
        return str(_BUILD_ID_CACHE["build_id"])

    h = hashlib.sha256()
    for rel, mtime_ns, size in sig_parts:
        fp = BASE_DIR / rel
        try:
            file_hash = hashlib.sha256(fp.read_bytes()).hexdigest()[:12]
        except OSError:
            file_hash = f"{mtime_ns}:{size}"
        h.update(f"{rel}:{file_hash}\n".encode("utf-8"))

    build_id = h.hexdigest()[:12]
    _BUILD_ID_CACHE["ts"] = now
    _BUILD_ID_CACHE["sig"] = sig_tuple
    _BUILD_ID_CACHE["build_id"] = build_id
    return build_id


def inject_cache_bust_html(html: str) -> str:
    build_id = get_build_id()

    def _repl(m: re.Match) -> str:
        attr = m.group("attr")
        q = m.group("q")
        path = m.group("path")
        ver = _file_version(path)
        return f"{attr}{q}{path}?v={ver}{q}"

    updated = _STATIC_ATTR_RE.sub(_repl, html)

    js_dir = STATIC_DIR / "js"
    imports_map: dict[str, str] = {}
    if js_dir.exists():
        for jf in sorted(js_dir.glob("*.js")):
            rel_url = f"/static/js/{jf.name}"
            ver = _file_version(rel_url)
            imports_map[rel_url] = f"{rel_url}?v={ver}"

    meta_and_map = (
        f'\n    <meta name="app-build-id" content="{build_id}">'
        f'\n    <script type="importmap">{json.dumps({"imports": imports_map}, ensure_ascii=False)}</script>'
    )
    if 'name="app-build-id"' not in updated:
        updated = _HEAD_OPEN_RE.sub(lambda m: m.group(1) + meta_and_map, updated, count=1)

    return updated


class CacheBustTemplates(Jinja2Templates):
    def TemplateResponse(self, *args, **kwargs):
        resp = super().TemplateResponse(*args, **kwargs)
        try:
            raw = bytes(resp.body).decode("utf-8")
            patched = inject_cache_bust_html(raw).encode("utf-8")
            resp.body = patched
            resp.headers["content-length"] = str(len(patched))
        except Exception:
            pass
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers["X-Build-Id"] = get_build_id()
        return resp


class CacheBustStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        resp.headers["X-Build-Id"] = get_build_id()
        lower = path.lower()
        if lower.endswith((".js", ".css", ".html", ".json")):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            resp.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
        return resp


async def cache_bust_middleware(request: Request, call_next):
    resp = await call_next(request)
    build_id = get_build_id()
    resp.headers["X-Build-Id"] = build_id
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/html" in ctype:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp
