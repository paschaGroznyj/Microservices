"""
tool_submit_task.py — фоновые задачи песочницы.

  • submit_task       — запуск кода в фоне (POST /submit), сразу возвращает task_id
  • check_task_status — снимок задачи или список задач workspace
  • cancel_task       — отмена по просьбе пользователя

Ход задачи песочница шлёт в Kafka task-events, его читает chats_service:
пишет в agent_tasks, рисует карточку в чате и после завершения сам будит
агента служебным сообщением (task_trigger.py рядом с main.py).
"""
import logging
import os
import uuid

import httpx
from langchain.tools import tool

log = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 3600
TASK_KINDS = ("script", "media")
MAX_STAGES = 12


def _owner(ctx: dict) -> dict:
    """Кто поставил задачу: по этим полям chats_service будит агента после неё."""
    owner_uid = ctx.get("owner_user_id") or ctx.get("user_id")
    return {
        "user_id": owner_uid,
        "agent_model": ctx.get("agent_model"),
        "selected_tools": list(ctx.get("selected_tools") or []),
        "context_size": ctx.get("context_size") or "medium",
        "gateway_target": ctx.get("gateway_target") or "main",
        "request_id": ctx.get("request_id"),
    }


def _norm_stages(stages) -> list[str]:
    if isinstance(stages, str):
        stages = [s for s in stages.replace(";", "\n").splitlines()]
    out = []
    for s in stages or []:
        s = str(s).strip()
        if s and s not in out:
            out.append(s[:80])
    return out[:MAX_STAGES]


def _http_error(e: Exception) -> dict:
    if isinstance(e, httpx.HTTPStatusError):
        return {"ok": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:300]}"}
    return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def make_submit_tools(agent):
    def _ctx() -> dict:
        try:
            return dict(agent._request_ctx.get() or {})
        except Exception:
            return {}

    @tool
    async def submit_task(
        code: str,
        workspace_id: str,
        label: str,
        kind: str = "script",
        stages: list[str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        notify_agent: bool = True,
        changed_files: str = "",
        chat_id: str = "",
    ) -> dict:
        """
        Runs Python code as a BACKGROUND task in the sandbox and returns task_id at once.
        Use it for work longer than ~30 s (parsing, scraping, batch media, big files).
        The user sees a live task card in the chat (stages, progress, elapsed time, result).
        When the task ends you are woken up AUTOMATICALLY with a service message
        (status, error, stdout/stderr tail, files) — do NOT poll check_task_status.
        While the task runs you may keep talking to the user; if they ask to stop it,
        call cancel_task(task_id).

        PROGRESS PROTOCOL (required): the script must print marker lines to stdout
        with flush=True. They drive the card and are removed from the final stdout:
            print("##STAGE Download pages", flush=True)        # a stage begins
            print("##PROGRESS 40 page 4/10", flush=True)       # percent 0-100 + short text
        - Pass the same stage titles in `stages` so the card shows the plan up front.
        - Emit ##STAGE at the start of every stage, ##PROGRESS inside long loops
          (at most every few seconds). A stage that is never started is shown as skipped.
        - Elapsed time is always shown, even if the script prints no markers.

        Files: the code runs in the persistent workspace; save results to output/.
        They stay available to execute_python_code / download_workspace_file
        with the same workspace_id.

        Args:
            code: full Python script (plain .py, no top-level await).
            workspace_id: workspace from create_workspace.
            label: short human title of the task for the card, in the user's language.
            kind: "script" — code, scraping, data processing (console-style card);
                  "media" — rendering/processing images, audio, video (media-style card).
            stages: planned stage titles in order, 2-6 items, e.g.
                    ["Сбор ссылок", "Парсинг страниц", "Сохранение CSV"].
            timeout: seconds, 1-3600 (default 120).
            notify_agent: wake you up after the task ends (default True).
            changed_files: files the task will change, for the user's live editor
                           ("*" if unknown).
            chat_id: not needed, taken from the current chat.
        """
        ctx = _ctx()
        cid = str(ctx.get("chat_id") or chat_id or "")
        if not cid:
            return {"ok": False, "error": "Не удалось определить chat_id текущего чата"}
        k = kind if kind in TASK_KINDS else "script"
        task_id = uuid.uuid4().hex
        body = {
            "code": code,
            "timeout": max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT)),
            "workspace_id": workspace_id,
            "task_id": task_id,
            "chat_id": cid,
            "label": (label or "Задача").strip()[:200],
            "kind": k,
            "stages": _norm_stages(stages),
            "notify_agent": bool(notify_agent),
            "owner": _owner(ctx),
            "changed_files": changed_files or "",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{SANDBOX_URL}/submit", json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.error("[submit_task] %s", e)
            return _http_error(e)
        log.info("[submit_task] task=%s ws=%s kind=%s chat=%s", task_id, workspace_id, k, cid)
        return {
            "ok": True,
            "task_id": task_id,
            "status": data.get("status", "pending"),
            "workspace_id": workspace_id,
            "kind": k,
            "stages": body["stages"],
            "message": (
                f"Задача «{body['label']}» поставлена, карточка с ходом выполнения уже в чате. "
                + ("После завершения ты получишь служебное сообщение с результатом, опрашивать статус не нужно."
                   if body["notify_agent"] else "Автоуведомление выключено, статус: check_task_status.")
            ),
        }

    @tool
    async def check_task_status(task_id: str = "", workspace_id: str = "") -> dict:
        """
        Snapshot of one background task (task_id) or the list of tasks of a workspace.
        Not needed for waiting: you are woken up automatically when a task ends.
        Use it only when the user asks how the task is going.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if task_id:
                    resp = await client.get(f"{SANDBOX_URL}/status/{task_id}")
                    resp.raise_for_status()
                    r = resp.json()
                    return {
                        "ok": True, "task_id": task_id,
                        **{k: r.get(k) for k in (
                            "status", "label", "kind", "workspace_id", "elapsed_ms", "progress",
                            "stages", "exit_code", "error", "files", "stdout_tail", "stderr_tail")},
                    }
                if workspace_id:
                    resp = await client.get(f"{SANDBOX_URL}/workspace/{workspace_id}/tasks")
                    resp.raise_for_status()
                    return {"ok": True, "workspace_id": workspace_id, **resp.json()}
        except Exception as e:
            return _http_error(e)
        return {"ok": False, "error": "Нужно указать task_id или workspace_id"}

    @tool
    async def cancel_task(task_id: str) -> dict:
        """
        Cancels a running background task (SIGTERM, then SIGKILL after 5 s).
        Call it only when the user asks to stop the task. The card switches to
        "cancelled"; you will not be woken up for a cancelled task.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{SANDBOX_URL}/tasks/{task_id}/cancel")
                resp.raise_for_status()
                return {"ok": True, **resp.json()}
        except Exception as e:
            return _http_error(e)

    return [submit_task, check_task_status, cancel_task]
