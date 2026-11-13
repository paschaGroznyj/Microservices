"""
agent_sandbox/ws_diff.py — теневые git-снимки workspace для карточки «Изменённые файлы».

Пользовательские репозитории не трогаем: служебные git-dir лежат в
.ws_meta/shadow/<name>.git, а work-tree указывает на корень области или на
вложенный репозиторий первого уровня (repo/.git). Для вложенных репо
objects/info/alternates смотрит в их .git/objects, поэтому файлы из истории
не копируются второй раз.

Снимок = `git add -A && git write-tree` (без коммитов и user.name).
По request_id храним base (первый снимок запроса) и prev (последний снимок):
  diff(base → cur) — итог ответа для карточки,
  diff(prev → cur) — что изменил последний тул (для FlowMap / live).

state.json:
  {"requests": {rid: {"chat_id", "message_id", "at", "updated",
                      "base": {prefix: tree}, "prev": {prefix: tree},
                      "cloned": [prefix], "files": int, "add": int, "del": int}}}
Хранится последних KEEP_REQUESTS запросов, данные живут, пока жив workspace.
"""

from __future__ import annotations

import fcntl
import fnmatch
import json
import logging
import os
import re
import subprocess
import time
import uuid

log = logging.getLogger("sandbox.ws_diff")

SHADOW_REL = os.path.join(".ws_meta", "shadow")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

BIG_FILE_BYTES = 5 * 1024 * 1024      # крупнее — в снимок не берём
PATCH_FILE_MAX = 64 * 1024             # patch одного файла
PATCH_TOTAL_MAX = 256 * 1024           # patch всего ответа
MAX_FILES = 200                        # файлов в ответе
MAX_PATCH_FILES = 60                   # файлов, для которых считаем patch
KEEP_REQUESTS = 40
GIT_TIMEOUT = 60

# Папки, которые не обходим и не берём в снимок (по имени, на любой глубине).
SKIP_DIRS = {
    ".git", ".ws_meta", "node_modules", ".venv", "venv", ".venvt", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache", ".tasks", ".next",
}
# Служебные файлы в корне области.
ROOT_SERVICE_FILES = {".ws_hold", ".last_active", ".created_at"}
STATIC_EXCLUDE = [
    "/.ws_meta/", "/.ws_hold", "/.last_active", "/.created_at", "/.tasks/",
    "/output/.scripts/",
    "node_modules/", ".venv/", "venv/", ".venvt/", "__pycache__/", "*.pyc",
    ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", ".cache/", ".next/",
]
# Секреты: в снимок попадают (иначе не видно, что файл тронут), но patch не отдаём.
SECRET_GLOBS = [
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", ".netrc", ".pgpass", ".npmrc", ".pypirc",
    "credentials*.json", "secrets.*", "*secret*.json", "*.kdbx",
]
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


# ── git ────────────────────────────────────────────────────────────

def _git(git_dir: str, work_tree: str | None, *args: str, check: bool = True) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_DIR": git_dir,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    if work_tree:
        env["GIT_WORK_TREE"] = work_tree
    p = subprocess.run(
        ["git", "-c", "core.quotepath=false", "-c", "safe.directory=*", *args],
        env=env, cwd=work_tree or git_dir,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT,
    )
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {p.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return p.stdout.decode("utf-8", "surrogateescape")


def _shadow_dir(ws_path: str) -> str:
    return os.path.join(ws_path, SHADOW_REL)


def _state_path(ws_path: str) -> str:
    return os.path.join(_shadow_dir(ws_path), "state.json")


def _gitdir_name(prefix: str) -> str:
    return "root.git" if not prefix else (_SAFE_NAME.sub("_", prefix) or "repo") + ".git"


def _init_gitdir(git_dir: str, alt_objects: str | None) -> None:
    if not os.path.isfile(os.path.join(git_dir, "HEAD")):
        os.makedirs(git_dir, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", git_dir], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT)
        for k, v in (("core.bare", "false"), ("core.autocrlf", "false"),
                     ("core.filemode", "false"), ("gc.auto", "0"),
                     ("core.bigFileThreshold", "1m")):
            _git(git_dir, None, "config", k, v)
    alt_file = os.path.join(git_dir, "objects", "info", "alternates")
    if alt_objects and os.path.isdir(alt_objects):
        cur = open(alt_file, encoding="utf-8").read().strip() if os.path.exists(alt_file) else ""
        if cur != alt_objects:
            os.makedirs(os.path.dirname(alt_file), exist_ok=True)
            with open(alt_file, "w", encoding="utf-8") as f:
                f.write(alt_objects + "\n")


def _repo_objects(repo_dir: str) -> str | None:
    """objects/ пользовательского репо (поддержка .git-файла у worktree/submodule)."""
    dot = os.path.join(repo_dir, ".git")
    if os.path.isdir(dot):
        return os.path.join(dot, "objects")
    if os.path.isfile(dot):
        try:
            line = open(dot, encoding="utf-8").read().strip()
            if line.startswith("gitdir:"):
                gd = line.split(":", 1)[1].strip()
                gd = gd if os.path.isabs(gd) else os.path.normpath(os.path.join(repo_dir, gd))
                common = os.path.join(gd, "commondir")
                if os.path.isfile(common):
                    gd = os.path.normpath(os.path.join(gd, open(common).read().strip()))
                return os.path.join(gd, "objects")
        except OSError:
            return None
    return None


def _repos(ws_path: str) -> list[dict]:
    """Корень + вложенные репозитории первого уровня (dir/.git)."""
    out = [{"prefix": "", "work_tree": ws_path, "alt": _repo_objects(ws_path)}]
    try:
        entries = sorted(os.listdir(ws_path))
    except OSError:
        entries = []
    for name in entries:
        sub = os.path.join(ws_path, name)
        if name in SKIP_DIRS or name.startswith(".ws_") or os.path.islink(sub) or not os.path.isdir(sub):
            continue
        if os.path.exists(os.path.join(sub, ".git")):
            out.append({"prefix": name, "work_tree": sub, "alt": _repo_objects(sub)})
    for r in out:
        r["git_dir"] = os.path.join(_shadow_dir(ws_path), _gitdir_name(r["prefix"]))
    return out


def _big_files(work_tree: str, skip_top: set[str]) -> list[str]:
    big = []
    for root, dirs, files in os.walk(work_tree):
        rel_root = os.path.relpath(root, work_tree)
        if rel_root == ".":
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in skip_top]
        else:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                if not os.path.islink(fp) and os.path.getsize(fp) > BIG_FILE_BYTES:
                    big.append(os.path.relpath(fp, work_tree).replace(os.sep, "/"))
            except OSError:
                pass
    return big


def _glob_escape(path: str) -> str:
    return re.sub(r"([\[\]*?!#\\ ])", r"\\\1", path)


def _write_exclude(git_dir: str, nested: list[str], big: list[str]) -> None:
    lines = ["# ws_diff: служебное и тяжёлое", *STATIC_EXCLUDE]
    lines += ["# вложенные репозитории (свой теневой индекс)"] + [f"/{_glob_escape(n)}/" for n in nested]
    lines += [f"# файлы > {BIG_FILE_BYTES // (1024 * 1024)} МБ"] + ["/" + _glob_escape(b) for b in big[:2000]]
    path = os.path.join(git_dir, "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = "\n".join(lines) + "\n"
    try:
        if open(path, encoding="utf-8").read() == text:
            return
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ── состояние и блокировка ─────────────────────────────────────────

class _Lock:
    def __init__(self, ws_path: str):
        os.makedirs(_shadow_dir(ws_path), exist_ok=True)
        self.path = os.path.join(_shadow_dir(ws_path), ".lock")

    def __enter__(self):
        self.f = open(self.path, "a+")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close()


def _load_state(ws_path: str) -> dict:
    try:
        with open(_state_path(ws_path), encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("requests"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"requests": {}}


def _save_state(ws_path: str, st: dict) -> None:
    reqs = st.get("requests") or {}
    if len(reqs) > KEEP_REQUESTS:
        keep = sorted(reqs.items(), key=lambda kv: kv[1].get("updated") or 0)[-KEEP_REQUESTS:]
        st["requests"] = dict(keep)
    path = _state_path(ws_path)
    tmp = f"{path}.tmp{uuid.uuid4().hex[:6]}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, path)


# ── публичное API ──────────────────────────────────────────────────

def init_workspace(ws_path: str) -> None:
    """Вызывается из /workspace/create: теневой git корня + пустой стартовый снимок."""
    with _Lock(ws_path):
        r = _repos(ws_path)[0]
        _init_gitdir(r["git_dir"], r["alt"])
        _write_exclude(r["git_dir"], [], [])
        st = _load_state(ws_path)
        st.setdefault("created", time.time())
        _save_state(ws_path, st)


def _snapshot(ws_path: str) -> dict[str, str]:
    repos = _repos(ws_path)
    nested = [r["prefix"] for r in repos if r["prefix"]]
    trees: dict[str, str] = {}
    for r in repos:
        _init_gitdir(r["git_dir"], r["alt"])
        skip_top = set(nested) if not r["prefix"] else set()
        if not r["prefix"]:
            skip_top |= ROOT_SERVICE_FILES
        _write_exclude(r["git_dir"], nested if not r["prefix"] else [], _big_files(r["work_tree"], skip_top))
        _git(r["git_dir"], r["work_tree"], "add", "-A", "--ignore-errors", ".", check=False)
        trees[r["prefix"]] = _git(r["git_dir"], r["work_tree"], "write-tree").strip() or EMPTY_TREE
    return trees


def _clone_base(ws_path: str, prefix: str) -> tuple[str, bool]:
    """База для репо, появившегося во время запроса: дерево HEAD клона (иначе «+3000 файлов»)."""
    sub = os.path.join(ws_path, prefix)
    gd = os.path.join(_shadow_dir(ws_path), _gitdir_name(prefix))
    try:
        user_git = os.path.join(sub, ".git")
        if os.path.isdir(user_git):
            env_dir = user_git
        else:
            objs = _repo_objects(sub)
            env_dir = os.path.dirname(objs) if objs else None
        if env_dir:
            tree = _git(env_dir, sub, "rev-parse", "--verify", "-q", "HEAD^{tree}", check=False).strip()
            # дерево клона доступно теневому git через alternates
            if tree and subprocess.run(["git", "--git-dir", gd, "cat-file", "-e", tree],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return tree, True
    except Exception as e:  # noqa: BLE001
        log.debug("[ws_diff] clone base %s: %s", prefix, e)
    return EMPTY_TREE, False


def snap(ws_path: str, request_id: str, chat_id: str | None = None,
         message_id: str | None = None) -> dict:
    """Снимок после (или до) тула. Первый снимок запроса становится base."""
    if not request_id:
        raise ValueError("request_id обязателен")
    with _Lock(ws_path):
        cur = _snapshot(ws_path)
        st = _load_state(ws_path)
        req = st["requests"].get(request_id)
        now = time.time()
        if req is None:
            req = {"chat_id": str(chat_id) if chat_id is not None else None, "message_id": None,
                   "at": now, "base": dict(cur), "prev": dict(cur), "cloned": []}
            st["requests"][request_id] = req
        if chat_id is not None and not req.get("chat_id"):
            req["chat_id"] = str(chat_id)
        if message_id:
            req["message_id"] = str(message_id)
        prev = dict(req.get("prev") or {})
        for prefix in cur:
            if prefix not in req["base"]:
                tree, cloned = _clone_base(ws_path, prefix)
                req["base"][prefix] = tree
                prev.setdefault(prefix, tree)
                if cloned and prefix not in req["cloned"]:
                    req["cloned"].append(prefix)
        step = _numstat(ws_path, prev, cur)
        req["prev"] = dict(cur)
        req["updated"] = now
        full = diff_trees(ws_path, req["base"], cur, cloned=req.get("cloned") or [])
        req["files"], req["add"], req["del"] = full["files_count"], full["add"], full["del"]
        _save_state(ws_path, st)
    full["request_id"] = request_id
    full["chat_id"] = req.get("chat_id")
    full["message_id"] = req.get("message_id")
    full["base_at"] = req.get("at")
    full["updated"] = now
    full["step"] = step
    return full


def get_request_diff(ws_path: str, request_id: str) -> dict | None:
    st = _load_state(ws_path)
    req = st["requests"].get(request_id)
    if not req:
        return None
    full = diff_trees(ws_path, req.get("base") or {}, req.get("prev") or {}, cloned=req.get("cloned") or [])
    full.update({"request_id": request_id, "chat_id": req.get("chat_id"),
                 "message_id": req.get("message_id"), "base_at": req.get("at"),
                 "updated": req.get("updated")})
    return full


def attach_message(ws_path: str, request_id: str, message_id: str) -> bool:
    with _Lock(ws_path):
        st = _load_state(ws_path)
        req = st["requests"].get(request_id)
        if not req:
            return False
        req["message_id"] = str(message_id)
        _save_state(ws_path, st)
    return True


def requests_for_chat(ws_path: str, chat_id: str) -> list[tuple[str, dict]]:
    """(request_id, meta) этого чата с непустым diff."""
    st = _load_state(ws_path)
    out = []
    for rid, req in st["requests"].items():
        if str(req.get("chat_id")) == str(chat_id) and (req.get("files") or 0) > 0:
            out.append((rid, req))
    return out


def has_request(ws_path: str, request_id: str) -> bool:
    return request_id in _load_state(ws_path)["requests"]


# ── diff ───────────────────────────────────────────────────────────

def _is_secret(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return any(fnmatch.fnmatch(base, g) for g in SECRET_GLOBS)


def _parse_raw(out: str) -> list[dict]:
    """`diff-tree -r -M -z --raw --numstat`: сначала raw-записи, затем numstat."""
    parts = out.split("\0")
    items: list[dict] = []
    i = 0
    while i < len(parts) and parts[i].startswith(":"):
        meta = parts[i].split()
        status = meta[4] if len(meta) > 4 else "M"
        letter = status[0]
        if letter in ("R", "C"):
            old, new = parts[i + 1], parts[i + 2]
            i += 3
        else:
            old = new = parts[i + 1]
            i += 2
        items.append({"status": letter, "old_path": old, "path": new,
                      "old_mode": meta[0][1:], "new_mode": meta[1],
                      "old_blob": meta[2], "new_blob": meta[3]})
    k = 0
    while i < len(parts) and k < len(items):
        rec = parts[i]
        if not rec:
            i += 1
            continue
        cols = rec.split("\t")
        add, dele = (cols + ["", ""])[:2]
        if len(cols) >= 3 and cols[2] == "":   # rename: путь в следующих двух полях
            i += 3
        else:
            i += 1
        it = items[k]
        it["binary"] = add == "-"
        it["add"] = 0 if add == "-" else int(add or 0)
        it["del"] = 0 if dele == "-" else int(dele or 0)
        k += 1
    return items


def _diff_items(git_dir: str, a: str, b: str) -> list[dict]:
    if a == b:
        return []
    out = _git(git_dir, None, "diff-tree", "-r", "-M", "-z", "--raw", "--numstat", a, b)
    return _parse_raw(out)


def _numstat(ws_path: str, prev: dict, cur: dict) -> dict:
    files = add = dele = 0
    paths = []
    for prefix, tree in cur.items():
        gd = os.path.join(_shadow_dir(ws_path), _gitdir_name(prefix))
        base = prev.get(prefix, EMPTY_TREE)
        for it in _diff_items(gd, base, tree):
            files += 1
            add += it["add"]
            dele += it["del"]
            if len(paths) < 20:
                paths.append((prefix + "/" if prefix else "") + it["path"])
    return {"files_count": files, "add": add, "del": dele, "paths": paths}


def _blob_size(git_dir: str, blob: str) -> int | None:
    if not blob or set(blob) == {"0"}:
        return None
    try:
        return int(_git(git_dir, None, "cat-file", "-s", blob).strip())
    except Exception:  # noqa: BLE001
        return None


def _patch(git_dir: str, a: str, b: str, it: dict) -> tuple[str, bool]:
    paths = [it["path"]] if it["old_path"] == it["path"] else [it["old_path"], it["path"]]
    out = _git(git_dir, None, "diff-tree", "-p", "-M", "--no-color", "--no-ext-diff", "-U3",
               a, b, "--", *paths, check=False)
    at = out.find("\n@@")
    body = out[at + 1:] if at >= 0 else ""
    if len(body.encode("utf-8", "surrogateescape")) <= PATCH_FILE_MAX:
        return body, False
    cut = body.encode("utf-8", "surrogateescape")[:PATCH_FILE_MAX].decode("utf-8", "ignore")
    return cut[: cut.rfind("\n") + 1], True


def diff_trees(ws_path: str, base: dict, cur: dict, cloned: list | None = None) -> dict:
    files: list[dict] = []
    total_add = total_del = 0
    for prefix in sorted(set(base) | set(cur), key=lambda p: (p != "", p)):
        gd = os.path.join(_shadow_dir(ws_path), _gitdir_name(prefix))
        a, b = base.get(prefix, EMPTY_TREE), cur.get(prefix, EMPTY_TREE)
        if a == b or not os.path.isdir(gd):
            continue
        for it in _diff_items(gd, a, b):
            pre = prefix + "/" if prefix else ""
            rec = {
                "path": pre + it["path"],
                "old_path": pre + it["old_path"] if it["old_path"] != it["path"] else None,
                "status": it["status"],
                "add": it["add"], "del": it["del"],
                "binary": it["binary"],
                "secret": _is_secret(it["path"]),
                "_gd": gd, "_a": a, "_b": b, "_it": it,
            }
            total_add += it["add"]
            total_del += it["del"]
            files.append(rec)
    files_count = len(files)
    files = files[:MAX_FILES]
    budget = PATCH_TOTAL_MAX
    for n, rec in enumerate(files):
        gd, a, b, it = rec.pop("_gd"), rec.pop("_a"), rec.pop("_b"), rec.pop("_it")
        if rec["binary"]:
            rec["size_before"] = _blob_size(gd, it["old_blob"])
            rec["size_after"] = _blob_size(gd, it["new_blob"])
            continue
        if rec["secret"]:
            continue
        if n >= MAX_PATCH_FILES or budget <= 0:
            rec["patch"] = None
            rec["patch_omitted"] = True
            continue
        body, cut = _patch(gd, a, b, it)
        if len(body) > budget:
            body = body[:budget]
            body = body[: body.rfind("\n") + 1]
            cut = True
        budget -= len(body)
        rec["patch"] = body
        if cut:
            rec["patch_truncated"] = True
    return {"files": files, "files_count": files_count, "add": total_add, "del": total_del,
            "truncated": files_count > len(files), "cloned": list(cloned or [])}
