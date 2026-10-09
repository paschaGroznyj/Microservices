
import os
import httpx
from langchain.tools import tool

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")

# Лимиты — согласованы с фабрикой
MAX_READ_CONTENT = 200_000   # сколько текста максимум тащить в контекст модели


def make_workspace_codeedit_tools(agent):

    @tool
    async def read_workspace_file(
        workspace_id: str,
        path: str,
        offset: int = 0,
        limit: int = 0,
    ) -> str:
        """
        Reads a text file (code/config) INSIDE the workspace on the factory.

        Unlike read_file (which reads the local agent file system /app and /sandbox),
        this tool pulls the file from the persistent workspace of the factory via HTTP.

        Arguments:
            workspace_id: workspace ID (from create_workspace)
            path:         relative path inside the workspace, e.g.
                          "src/app.py" or "static/js/fluid.js"
            offset:       from which line to read (0 = from the beginning)
            limit:        how many lines to read (0 = to the end).
                          For large files, read in chunks: offset=0 limit=200,
                          check total_lines, then offset=200 limit=200, etc.

        Returns the file text with metadata (total_lines, shown_lines).
        Use before edit_workspace_file to copy the exact
        fragment for old_str.
        """
        params = {"path": path, "offset": offset, "limit": limit}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(
                    f"{SANDBOX_URL}/workspace/{workspace_id}/read",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
            })
        except httpx.ConnectError:
            return str({"ok": False, "error": "Sandbox-сервис недоступен."})
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        content = data.get("content", "")
        if len(content) > MAX_READ_CONTENT:
            content = content[:MAX_READ_CONTENT] + "\n...(truncated by agent)..."
            data["content"] = content
            data["agent_truncated"] = True

        return str(data)

    @tool
    async def edit_workspace_file(
        workspace_id: str,
        path: str,
        old_str: str,
        new_str: str,
    ) -> str:
        """
        Edits the file INSIDE the workspace in the factory (find & replace).
        The replacement itself is performed atomically ON THE FACTORY SIDE — the agent sends
        only {path, old_str, new_str}, the entire file is not chased back and forth.

        Unlike edit_sandbox_file (local /sandbox), it edits the file
        in the persistent workspace where the code for execute_python_code lives /
        execute_shell_commands.

        Arguments:
            workspace_id: workspace ID
            path:         relative path to the file within the workspace
            old_str:      exact fragment to replace. Must occur
                          EXACTLY once — otherwise, the factory will return error 409
                          (0 occurrences or >1). If >1, add more context.
            new_str:      replacement text. An empty line deletes old_str.

        How to work: read_workspace_file first, copy the exact piece.
        (with indents and spaces) in old_str, then edit_workspace_file.
        """
        payload = {"path": path, "old_str": old_str, "new_str": new_str}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{SANDBOX_URL}/workspace/{workspace_id}/edit",
                    json=payload,
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPStatusError as e:
            # 409 — неоднозначная/отсутствующая замена; текст detail информативен
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}",
            })
        except httpx.ConnectError:
            return str({"ok": False, "error": "Sandbox-сервис недоступен."})
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

        return str(result)

    return [read_workspace_file, edit_workspace_file]
