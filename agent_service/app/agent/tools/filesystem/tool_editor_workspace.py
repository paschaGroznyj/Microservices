"""
tools/filesystem/tool_editor_workspace.py
───────────────────────────────────────────
Универсальный тул: агент закрепляет воркспейс песочницы за пользователем,
чтобы тот мог открыть его файлы в веб-редакторе /editor (read-only).

Запись идёт в таблицу editor_workspaces (общая БД). Редактор читает
дерево и файлы из песочницы по workspace_id (вариант А: файлы живут там).

Подключение в event_loop.py:
    from .tools.filesystem.tool_editor_workspace import make_editor_workspace_tool
    register_editor_workspace = make_editor_workspace_tool(agent, chat_id, user_id)
"""
import logging
from urllib.parse import quote
from langchain.tools import tool
from agent.core.config import EDITOR_BASE_URL

log = logging.getLogger(__name__)


def make_editor_workspace_tool(agent, chat_id, user_id):

    @tool
    async def register_editor_workspace(workspace_id: str, title: str = "") -> str:
        """
        Pin a sandbox workspace to the current user so they can open its files
        in the web editor (/editor, read-only Monaco).

        WHEN TO USE: after you created files in a sandbox workspace (via
        execute_python_code / submit_task with a workspace_id) and you want the
        user to be able to BROWSE and READ those files in a real code editor in
        the browser — e.g. you generated a .py / .json / config / report and the
        user wants to look at it in the editor rather than as a chat attachment.

        Pass the SAME workspace_id you used to create the files. Optionally give a
        human-readable title (shown in the editor's workspace dropdown).

        The editor is READ-WRITE: the user can browse the file tree, open and
        EDIT files, and save changes back to the workspace (atomic write). It is
        the main-gateway web interface, not reachable from the corporate network.

        Returns a short status string that ALREADY CONTAINS the ready-to-open
        editor link (domain + chat_id). Pass this link straight to the user.
        """
        if not workspace_id:
            return "Ошибка: нужен workspace_id."
        if user_id is None:
            return "Ошибка: нет user_id — некому закрепить воркспейс."
        try:
            row = await agent.db.register_editor_workspace(
                user_id=user_id,
                workspace_id=str(workspace_id),
                chat_id=str(chat_id) if chat_id is not None else None,
                title=(title or None),
            )
            log.info(f"[editor] закреплён воркспейс {workspace_id} за user={user_id}")
            label = (row or {}).get("title") or workspace_id
            base = EDITOR_BASE_URL.rstrip("/")
            link = f"{base}/editor"
            if chat_id is not None:
                link = f"{link}?chat_id={quote(str(chat_id))}"
            return (f"Воркспейс '{label}' закреплён за пользователем. "
                    f"Файлы можно открыть и редактировать в редакторе: {link}")
        except Exception as e:
            log.error(f"[editor] ошибка регистрации воркспейса: {e}")
            return f"Не удалось закрепить воркспейс: {e}"

    return register_editor_workspace
