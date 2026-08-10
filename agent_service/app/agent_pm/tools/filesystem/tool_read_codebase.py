"""
tools/filesystem/tool_read_codebase.py
────────────────────────────────────────
Read-only доступ агента к файловой системе сервиса.
Инструменты: list_tree, read_file, grep_search, find_files.

Подключение в langchain_agent.py:
    from tools.filesystem.tool_read_codebase import make_filesystem_tools
    fs_tools = make_filesystem_tools(agent)
    tools += fs_tools
"""

import logging
import os
import subprocess

from langchain.tools import tool

log = logging.getLogger(__name__)

# ── Конфиг безопасности ──────────────────────────────────────────────
ALLOWED_ROOTS = ["/app", "/sandbox"] # Корневая папка и песочница
ROOT = "/app"

BLOCKED_SUBSTR = [
    ".env", "credentials", "secret", "token", "password", ".ssh", "id_rsa",
]

IGNORE_DIRS = [
    "__pycache__", "faiss_index", "digests", "digests_all",
    "knowledge_base", "venv", ".git", "*.pyc", "tokens.json", "work_with_db.txt",
    "work_with_filesystem.md"
]
IGNORE_PATTERN = "|".join(IGNORE_DIRS)

MAX_FILE_SIZE = 51_200       # 50 KB — лимит на полное чтение файла
MAX_DEPTH_DEFAULT = 5
MAX_DEPTH_LIMIT = 7
MAX_GREP_MATCHES = 200        # чтобы не залить контекст модели
SUBPROCESS_TIMEOUT = 15


# ── Общие проверки ───────────────────────────────────────────────────
def _valid_path(path: str) -> bool:
    abs_path = os.path.abspath(path)
    if not any(abs_path == r or abs_path.startswith(r + os.sep) for r in ALLOWED_ROOTS):
        return False
    low = abs_path.lower()
    return not any(b in low for b in BLOCKED_SUBSTR)


def _safe_join(rel_path: str) -> str | None:
    # если путь уже абсолютный и валиден — используем как есть
    if os.path.isabs(rel_path):
        candidate = os.path.abspath(rel_path)
        return candidate if _valid_path(candidate) else None
    # относительный путь по умолчанию резолвим от /app (обратная совместимость)
    candidate = os.path.abspath(os.path.join(ROOT, rel_path))
    return candidate if _valid_path(candidate) else None


# ── Реализации ────────────────────────────────────────────────────────
def _tree_view(path: str, depth: int) -> dict:
    depth = min(max(depth, 1), MAX_DEPTH_LIMIT)
    r = subprocess.run(
        ["tree", "-L", str(depth), "-I", IGNORE_PATTERN, path],
        capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT,
    )
    if r.returncode != 0:
        return {"error": r.stderr.strip() or "tree failed"}
    return {"path": path, "depth": depth, "tree": r.stdout}


def _read_file(path: str, offset: int = 0, limit: int = 0) -> dict:
    if not os.path.isfile(path):
        return {"error": f"Not a file: {path}"}

    size = os.path.getsize(path)

    with open(path, errors="replace") as f:
        all_lines = f.readlines()

    total_lines = len(all_lines)

    # Явное окно от модели — приоритетнее автотрюка с head/tail
    if offset or limit:
        start = max(offset, 0)
        end = start + limit if limit > 0 else total_lines
        window = all_lines[start:end]
        content = "".join(window)
        return {
            "path": path,
            "size": size,
            "total_lines": total_lines,
            "shown_lines": f"{start+1}-{min(end, total_lines)}",
            "truncated": end < total_lines,
            "content": content,
        }

    # Без явного окна — старое поведение для маленьких файлов,
    # и голова/хвост как безопасный дефолт для больших
    if size > MAX_FILE_SIZE:
        head_n, tail_n = 150, 80
        content = (
            "".join(all_lines[:head_n])
            + f"\n... (файл {total_lines} строк / {size} байт, "
              f"показаны первые {head_n} и последние {tail_n} строк; "
              f"используй offset/limit чтобы прочитать середину) ...\n"
            + "".join(all_lines[-tail_n:])
        )
        truncated = True
    else:
        content = "".join(all_lines)
        truncated = False

    return {
        "path": path,
        "size": size,
        "total_lines": total_lines,
        "truncated": truncated,
        "content": content,
    }

def _grep_search(pattern: str, path: str, exts: list[str] | None) -> dict:
    prune_args = []
    for d in IGNORE_DIRS:
        if "*" in d:
            continue
        prune_args += ["-path", f"*/{d}", "-prune", "-o"]

    args = ["grep", "-rn", "--exclude-dir=" + IGNORE_PATTERN.replace("|", " --exclude-dir=")]
    # grep не умеет несколько --exclude-dir через один список аккуратно строкой,
    # поэтому строим явный список аргументов:
    args = ["grep", "-rn"]
    for d in IGNORE_DIRS:
        if "*" not in d:
            args.append(f"--exclude-dir={d}")
        else:
            args.append(f"--exclude={d}")

    if exts:
        for e in exts:
            args.append(f"--include=*{e}")

    args += [pattern, path]

    r = subprocess.run(args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)

    if r.returncode not in (0, 1):  # 1 = совпадений не найдено, это не ошибка
        return {"error": r.stderr.strip() or "grep failed"}

    lines = [l for l in r.stdout.strip().split("\n") if l]
    truncated = len(lines) > MAX_GREP_MATCHES
    lines = lines[:MAX_GREP_MATCHES]

    return {
        "pattern": pattern,
        "path": path,
        "matches_count": len(lines),
        "truncated": truncated,
        "matches": lines,
    }


def _find_files(path: str, name_pattern: str, depth: int) -> dict:
    depth = min(max(depth, 1), MAX_DEPTH_LIMIT)

    exclude_dirs = [d for d in IGNORE_DIRS if "*" not in d]

    prune_expr = ["("]
    for i, d in enumerate(exclude_dirs):
        if i > 0:
            prune_expr.append("-o")
        prune_expr += ["-path", f"*/{d}"]
    prune_expr.append(")")

    args = (
        ["find", path, "-maxdepth", str(depth)]
        + prune_expr
        + ["-prune", "-o", "-type", "f", "-iname", name_pattern, "-print"]
    )

    r = subprocess.run(args, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    if r.returncode != 0:
        return {"error": r.stderr.strip() or "find failed"}

    files = []
    for fp in r.stdout.strip().split("\n"):
        if fp and _valid_path(fp):
            try:
                files.append({"path": fp, "size": os.path.getsize(fp)})
            except OSError:
                pass

    return {"path": path, "pattern": name_pattern, "count": len(files), "files": files}


# ── Langchain tools ───────────────────────────────────────────────────
def make_filesystem_tools(agent):

    @tool
    async def list_tree(path: str = ROOT, depth: int = MAX_DEPTH_DEFAULT) -> str:
        """
        Показывает дерево директорий и файлов проекта (аналог `tree`).
        Служебные и мусорные папки (__pycache__, digests, faiss_index, knowledge_base, venv)
        автоматически исключены из вывода.

        Аргументы:
            path: путь внутри /app (по умолчанию корень проекта)
            depth: глубина вложенности, 1-7 (по умолчанию 5)

        Используй для первого знакомства со структурой проекта или конкретной папки.

        Для чтения файлов из песочницы (/sandbox) указывай ПОЛНЫЙ абсолютный путь,
        например "/sandbox/draft.py". Короткие относительные пути (без "/")
        резолвятся относительно /app, а не /sandbox.
        """
        target = _safe_join(path)
        if target is None:
            return f"Ошибка: путь '{path}' вне разрешённой зоны или заблокирован."
        result = _tree_view(target, depth)
        return str(result)

    @tool
    async def read_file(path: str, offset: int = 0, limit: int = 0) -> str:
        """
        Читает содержимое файла (аналог `cat` / `sed -n`).

        Аргументы:
            path: путь к файлу внутри /app, относительный или абсолютный
            offset: с какой строки читать (0 = с начала). Используй вместе с limit
                    для скользящего окна по большому файлу.
            limit: сколько строк прочитать. 0 = до конца файла (или до лимита по умолчанию).

        Поведение:
            - Если offset и limit не заданы, а файл < 50KB — вернёт файл целиком.
            - Если offset и limit не заданы, а файл > 50KB — вернёт первые 150
            и последние 80 строк, остальное будет truncated.
            - Если задан offset/limit — вернёт ровно запрошенный диапазон строк,
            независимо от размера файла. Так можно прочитать большой файл по частям:
            сначала offset=0 limit=200, увидеть total_lines в ответе, затем
            offset=200 limit=200 и так далее.
        """
        target = _safe_join(path)
        if target is None:
            return f"Ошибка: путь '{path}' вне разрешённой зоны или заблокирован."
        result = _read_file(target, offset, limit)
        return str(result)

    @tool
    async def grep_search(pattern: str, path: str = ROOT, exts: list[str] | None = None) -> str:
        """
        Ищет текст внутри файлов проекта (аналог `grep -rn`).
        Возвращает путь, номер строки и саму строку для каждого совпадения.
        Максимум 200 совпадений за раз — уточняй pattern или path, если упёрлось в лимит.

        Аргументы:
            pattern: строка или regex для поиска (grep -E синтаксис, если нужен regex — передавай -E явно в pattern не нужно, используется базовый grep)
            path: где искать, по умолчанию весь /app
            exts: список расширений для фильтра, например [".py", ".md"]. Если не задано — ищет во всех файлах.

        Используй для поиска функций, классов, переменных, упоминаний конкретных сущностей в коде.
        Пример: pattern="def analyze_article" чтобы найти определение функции.
        """
        target = _safe_join(path)
        if target is None:
            return f"Ошибка: путь '{path}' вне разрешённой зоны или заблокирован."
        result = _grep_search(pattern, target, exts)
        return str(result)

    @tool
    async def find_files(name_pattern: str, path: str = ROOT, depth: int = MAX_DEPTH_DEFAULT) -> str:
        """
        Ищет файлы по маске имени (аналог `find -iname`).

        Аргументы:
            name_pattern: маска имени файла, например "*.py" или "*config*"
            path: где искать, по умолчанию весь /app
            depth: глубина поиска, 1-7

        Используй, когда знаешь примерное имя файла, но не знаешь, где он лежит.
        """
        target = _safe_join(path)
        if target is None:
            return f"Ошибка: путь '{path}' вне разрешённой зоны или заблокирован."
        result = _find_files(target, name_pattern, depth)
        return str(result)

    return [list_tree, read_file, grep_search, find_files]