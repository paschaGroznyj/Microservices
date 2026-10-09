"""
tool_submit_task.py
══════════════════════════════════════════════════════════════════════
Тулы для асинхронного планировщика задач.

Добавляет 2 новые тулы к существующему набору:
  • submit_task — асинхронный запуск кода в sandbox (не блокирует)
  • check_task_status — проверка статуса задач

После завершения задачи sandbox шлёт Kafka event → task_events_consumer
→ уведомление в чат.

Регистрация в langchain_agent.py:
    from .tools.sandbox.tool_submit_task import make_submit_tools
    submit_tools = make_submit_tools(agent)
    tools += submit_tools

Возвращает список: [submit_task, check_task_status]
"""

import json
import logging
import os
import uuid

import httpx
from langchain.tools import tool

log = logging.getLogger(__name__)

SANDBOX_URL = os.environ.get("SANDBOX_SERVICE_URL", "http://agent_sandbox:8005")
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 1800


def make_submit_tools(agent):
    """Фабрика тул для асинхронного планировщика."""

    # ══════════════════════════════════════════════════════════════
    # 1. SUBMIT_TASK
    # ══════════════════════════════════════════════════════════════

    @tool
    async def submit_task(
        code: str,
        workspace_id: str,
        chat_id: str,
        label: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict:
        """
        Sets the task for asynchronous execution in the sandbox.
        IF it DOESN'T BLOCK it, it returns the task_id immediately.

        After completion, the sandbox will automatically send a notification to the chat.
        via Kafka. No need for polling — you will receive a message when the task is completed.
        She'll be ready.

        Use it for long-term tasks (parsing, data collection, large data processing
        files) that take more than 30 seconds. While the task is running
        — You can communicate with the user.

        Files are saved in the persistent workspace (output/) and are available
        for subsequent calls to execute_python_code with the same workspace_id.

        Arguments:
            code: Python code to execute. Write print() — the output will be returned
                  in the notification (first 500 characters).
            workspace_id: workspace ID (create via create_workspace)
            chat_id: chat ID for sending a completion notification
            label: human‑readable task name (e.g., "AKRA", "NKR")
            timeout: timeout in seconds, max 1800 (default 1800)

        Returns: task_id, status, workspace_id

        Example scenario:
            1. create_workspace() → workspace_id="abc123"
            2. submit_task(code_acra, "abc123", chat_id, "AKRA") → task_id=1
            3. submit_task(code_nkr, "abc123", chat_id, "НКР") → task_id=2
            4. submit_task(code_expert, "abc123", chat_id, "RA Expert") → task_id=3
            5. [You wait for notifications, communicate with the user]
            6. [You receive: "All 3 tasks are completed"]
            7. execute_python_code(workspace_id="abc123", code="# merge → xlsx")
            8. download_workspace_file("abc123", "output/result.xlsx")
            9. delete_workspace("abc123")
        """
        task_id = str(uuid.uuid4())

        timeout = min(max(timeout, 1), MAX_TIMEOUT)

        payload = {
            "code": code,
            "timeout": timeout,
            "workspace_id": workspace_id,
            "task_id": task_id,
            "chat_id": chat_id,
            "label": label,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{SANDBOX_URL}/submit", json=payload)
                resp.raise_for_status()
                result = resp.json()

            log.info(
                "[submit_task] task=%s workspace=%s label='%s' → %s",
                task_id, workspace_id, label, result.get("status"),
            )

            return str({
                "ok": True,
                "task_id": task_id,
                "status": result.get("status", "pending"),
                "workspace_id": workspace_id,
                "label": label,
                "message": (
                    f"Задача '{label}' поставлена на исполнение. "
                    f"Уведомление придёт в чат {chat_id} после завершения. "
                    f"Пока можешь продолжить диалог — я не блокируюсь."
                ),
            })

        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"Sandbox /submit error {e.response.status_code}: "
                         f"{e.response.text[:300]}",
            })
        except httpx.ConnectError:
            return str({
                "ok": False,
                "error": "Sandbox-сервис недоступен. Проверь что agent_sandbox запущен.",
            })
        except Exception as e:
            log.exception("[submit_task] Неожиданная ошибка")
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

    # ══════════════════════════════════════════════════════════════
    # 2. CHECK_TASK_STATUS
    # ═══════════════════з═══════════════════════════════════════════

    @tool
    async def check_task_status(
        workspace_id: str = "",
        task_id: str = "",
    ) -> dict:
        """
        Checks the status of tasks in the sandbox.

        You can check:
          • A specific task — pass the task_id
          • All tasks in the workspace — pass the workspace_id

        Statuses: pending → running → completed | failed

        Arguments:
            workspace_id: workspace ID (to check all tasks in it)
            task_id: ID of a specific task (if you need to view details)

        Returns:
            For workspace: a list of tasks with statuses, the all_done flag
            For task_id: the full result (stdout, stderr, files, duration)

        FORBIDDEN:
            Checking the task status in the event_loop loop > 5 times in a row
            (The task may take a long time to complete).

        IMPORTANT (return to chat):
            If the task is still pending/running — DO NOT run checks in a loop.
            sandbox itself will send a notification to the chat via Kafka upon completion.
            After 1-2 checks with the pending/running status, RETURN to the CHAT.:
            inform the user that the task is being completed, and continue.
            communication — you will return to the result when the notification arrives.

        Use it when the user asks "what's up with the tasks?"
        or before the final build of the result.
        """
        try:
            if task_id:
                # Запрос по конкретной задаче
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(f"{SANDBOX_URL}/status/{task_id}")
                    resp.raise_for_status()
                    result = resp.json()

                return str({
                    "ok": True,
                    "task_id": task_id,
                    "status": result.get("status"),
                    "label": result.get("label"),
                    "workspace_id": result.get("workspace_id"),
                    "duration_ms": result.get("duration_ms"),
                    "error": result.get("error"),
                    "files": result.get("files", []),
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                })

            elif workspace_id:
                # Запрос всех задач workspace
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(f"{SANDBOX_URL}/workspace/{workspace_id}/tasks")
                    resp.raise_for_status()
                    result = resp.json()

                return str({
                    "ok": True,
                    "workspace_id": workspace_id,
                    "tasks": result.get("tasks", []),
                    "total": result.get("total", 0),
                    "completed": result.get("completed", 0),
                    "all_done": result.get("all_done", False),
                })

            else:
                return str({
                    "ok": False,
                    "error": "Нужно указать workspace_id или task_id",
                })

        except httpx.HTTPStatusError as e:
            return str({
                "ok": False,
                "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}",
            })
        except Exception as e:
            return str({"ok": False, "error": f"{type(e).__name__}: {e}"})

    return [submit_task, check_task_status]
