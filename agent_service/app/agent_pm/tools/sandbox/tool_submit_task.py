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
        Ставит задачу на асинхронное исполнение в sandbox.
        НЕ БЛОКИРУЕТ — сразу возвращает task_id.

        После завершения sandbox автоматически отправит уведомление в чат
        через Kafka. Не нужно polling — ты получишь сообщение когда задача
        будет готова.

        Используй для долгих задач (парсинг, сбор данных, обработка больших
        файлов), которые занимают больше 30 секунд. Пока задача выполняется
        — ты можешь общаться с пользователем.

        Файлы сохраняются в persistent workspace (output/) и доступны
        для последующих вызовов execute_python_code с тем же workspace_id.

        Аргументы:
            code: Python-код для исполнения. Пиши print() — вывод вернётся
                  в уведомлении (первые 500 символов).
            workspace_id: ID workspace (создай через create_workspace)
            chat_id: ID чата для отправки уведомления о завершении
            label: Человекочитаемое имя задачи (например "АКРА", "НКР")
            timeout: таймаут в секундах, макс 1800 (по умолчанию 1800)

        Возвращает: task_id, status, workspace_id

        Пример сценария:
            1. create_workspace() → workspace_id="abc123"
            2. submit_task(code_acra, "abc123", chat_id, "АКРА") → task_id=1
            3. submit_task(code_nkr, "abc123", chat_id, "НКР") → task_id=2
            4. submit_task(code_expert, "abc123", chat_id, "Эксперт РА") → task_id=3
            5. [ждёшь уведомлений, общаешься с пользователем]
            6. [прилетает: "Все 3 задачи завершены"]
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
        Проверяет статус задач в sandbox.

        Можно проверить:
          • Конкретную задачу — передай task_id
          • Все задачи в workspace — передай workspace_id

        Статусы: pending → running → completed | failed

        Аргументы:
            workspace_id: ID workspace (проверить все задачи в нём)
            task_id: ID конкретной задачи (если нужно посмотреть детали)

        Возвращает:
            Для workspace: список задач с статусами, флаг all_done
            Для task_id: полный результат (stdout, stderr, files, duration)

        ЗАПРЕЩЕНО:
            Проверять статус задачи в event_loop цикле > 5 раз подряд
            (Задача может быть длительная по выполнению).

        ВАЖНО (возврат в чат):
            Если задача ещё pending/running — НЕ крути проверки в цикле.
            sandbox сам пришлёт уведомление в чат через Kafka по завершении.
            После 1-2 проверок со статусом pending/running — ВЕРНИСЬ В ЧАТ:
            сообщи пользователю, что задача выполняется, и продолжай
            общение — к результату вернёшься, когда прилетит уведомление.

        Используй когда пользователь спрашивает «ну что там с задачами?»
        или перед финальной сборкой результата.
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
