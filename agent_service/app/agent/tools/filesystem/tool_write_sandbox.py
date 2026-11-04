"""
tools/filesystem/tool_write_sandbox.py
────────────────────────────────────────
Запись файлов строго в /sandbox. Никакого доступа к /app на запись.
"""

import logging
import os
import time

from langchain.tools import tool

log = logging.getLogger(__name__)

SANDBOX_ROOT = "/sandbox"
MAX_CONTENT_SIZE = 200_000  # 200KB на файл, tmpfs у нас всего 64MB
MAX_FILES_IN_SANDBOX = 500   # защита от захламления

def _valid_sandbox_path(path: str) -> str | None:
    """Только внутри /sandbox, без выхода через .."""
    candidate = path if os.path.isabs(path) else os.path.join(SANDBOX_ROOT, path)
    candidate = os.path.abspath(candidate)
    log.info(candidate)
    if candidate != SANDBOX_ROOT and not candidate.startswith(SANDBOX_ROOT + os.sep):
        return None
    return candidate


def _write_file(path: str, content: str, mode: str) -> dict:
    if len(content.encode("utf-8")) > MAX_CONTENT_SIZE:
        return {"error": f"Content too large, max {MAX_CONTENT_SIZE} bytes"}

    existing = [f for f in os.listdir(SANDBOX_ROOT) if os.path.isfile(os.path.join(SANDBOX_ROOT, f))]
    if len(existing) >= MAX_FILES_IN_SANDBOX and not os.path.exists(path):
        return {"error": f"Sandbox full, max {MAX_FILES_IN_SANDBOX} files. Delete something first."}

    os.makedirs(os.path.dirname(path), exist_ok=True)

    file_mode = "a" if mode == "append" else "w"
    with open(path, file_mode, encoding="utf-8") as f:
        f.write(content)

    return {
        "path": path,
        "mode": mode,
        "bytes_written": len(content.encode("utf-8")),
        "written_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

def make_sandbox_write_tool(agent):
    
    @tool
    async def write_sandbox_file(path: str, content: str, mode: str = "write") -> str:
        """
        Writes the file STRICTLY inside /sandbox. There is no recording access to /app —
        This is a draft area for writing and displaying code, not for changing the actual service.

        Arguments:
            path: a relative path inside /sandbox, for example "draft_tool.py "
            content: file content
            mode: "write" (overwrite) or "append" (add to the end)

        Limit: 200KB per file, 50 files at a time.

        Use to prepare a draft of the code or file to show to the user —
        they will decide whether to move it to the real project.
        DO NOT use when working with the workspace
        """
        target = _valid_sandbox_path(path)
        if target is None:
            return {"error": f"Путь '{path}' вне /sandbox."}
        if mode not in ("write", "append"):
            return {"error": "mode должен быть 'write' или 'append'."}
        return _write_file(target, content, mode)

    return write_sandbox_file
