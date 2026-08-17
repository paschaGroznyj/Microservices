import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from agent_pm.core.config import KAFKA_BROKER

log = logging.getLogger("apps_events")

TASK_EVENTS_TOPIC = "apps-events"
CHAT_MESSAGES_TOPIC = "chat-messages"
TASK_EVENTS_GROUP = "apps-events-group"

# Категории тулов, доступные автотриггеру из scheduled/sandbox задач.
# Явно БЕЗ "admin" — никакого execute_sql_query, работы с email,
# git, shell-команд для контента, инициированного не напрямую пользователем.
AUTO_TRIGGER_TOOLS = ["content", "internet", "sessions"]

_workspace_expectations: dict[str, dict] = {}


def register_workspace_expectation(workspace_id: str, task_ids: list[str], labels: list[str]):
    _workspace_expectations[workspace_id] = {
        "task_ids": set(task_ids),
        "labels": {tid: label for tid, label in zip(task_ids, labels)},
        "completed": set(),
        "failed": set(),
        "registered_at": time.time(),
    }
    log.info(
        "[task-events] Зарегистрировано ожидание: workspace=%s tasks=%d labels=%s",
        workspace_id, len(task_ids), labels,
    )


def _check_workspace_status(workspace_id: str) -> Optional[dict]:
    exp = _workspace_expectations.get(workspace_id)
    if exp is None:
        return None
    total = len(exp["task_ids"])
    done = len(exp["completed"]) + len(exp["failed"])
    pending_labels = [exp["labels"][tid] for tid in exp["task_ids"]
                       if tid not in exp["completed"] and tid not in exp["failed"]]
    return {
        "total": total, "done": done,
        "pending_labels": pending_labels,
        "all_done": done >= total,
    }


def _build_auto_trigger_prompt(event: dict, ws_status: dict | None) -> str:
    label = event.get("label", "задача")
    status = event.get("status", "unknown")
    ok = event.get("ok", False)
    error = event.get("error")
    duration_ms = event.get("duration_ms", 0)
    files = event.get("files", [])
    stdout_preview = event.get("stdout_preview", "")
    workspace_id = event.get("workspace_id")

    parts = [
        "[АВТОСОБЫТИЕ: завершение фоновой задачи, source_type=scheduled_task, "
        "инициировано НЕ пользователем напрямую — это автоматический триггер]",
        f"Задача «{label}» завершена со статусом: {status}.",
        f"Длительность: {duration_ms // 1000}с.",
    ]

    if not ok and error:
        parts.append(f"Ошибка: {error}")
    if stdout_preview:
        parts.append(f"Превью вывода: {stdout_preview[:300]}")
    if files:
        parts.append(f"Созданные файлы: {', '.join(f['filename'] for f in files)}")
    if workspace_id:
        parts.append(f"workspace_id={workspace_id}")

    if ws_status:
        if ws_status["all_done"]:
            parts.append(
                f"Все {ws_status['total']} задач в этом workspace завершены. "
                f"Если это часть общего результата (например, несколько источников для одного отчёта) — "
                f"собери итоговый файл и отправь пользователю через send_file_to_user."
            )
        else:
            pending = ", ".join(ws_status["pending_labels"])
            parts.append(f"Ещё ожидаются: {pending}. Не собирай итог, пока не завершится всё.")
    else:
        parts.append(
            "Сформулируй короткое уведомление пользователю о результате. "
            "Если есть готовый файл — отправь его через send_file_to_user."
        )

    return "\n".join(parts)


async def start_task_events_consumer(agent, database):
    """
    Главный consumer. Теперь вместо статичного текстового уведомления —
    вызывает agent.process_query с урезанным набором тулов (без admin).
    """
    consumer = AIOKafkaConsumer(
        TASK_EVENTS_TOPIC,
        bootstrap_servers=[KAFKA_BROKER],
        group_id=TASK_EVENTS_GROUP,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        auto_offset_reset="latest",
        session_timeout_ms=30000,
        heartbeat_interval_ms=5000,
        max_poll_interval_ms=300000,
        max_poll_records=1,
    )
    await consumer.start()
    log.info("[task-events] Consumer запущен, слушаю topic=%s", TASK_EVENTS_TOPIC)

    try:
        async for message in consumer:
            event = message.value
            log.info("[task-events] Получено событие: %s", event)

            chat_id = event.get("chat_id")
            workspace_id = event.get("workspace_id")

            if not chat_id:
                log.warning("[task-events] Событие без chat_id, пропускаю: %s", event)
                continue

            ws_status = None
            if workspace_id and workspace_id in _workspace_expectations:
                exp = _workspace_expectations[workspace_id]
                task_id = event.get("task_id")
                if event.get("status") == "completed":
                    exp["completed"].add(task_id)
                elif event.get("status") == "failed":
                    exp["failed"].add(task_id)
                ws_status = _check_workspace_status(workspace_id)
                if ws_status and ws_status["all_done"]:
                    del _workspace_expectations[workspace_id]

            auto_input = _build_auto_trigger_prompt(event, ws_status)

            try:
                # Явно урезанный selected_tools, source != "telegram" —
                # это не даёт allow_all_tools=True сработать в process_query
                result = await agent.process_query(
                    user_input=auto_input,
                    request_id=None,
                    user_id=56,  # системный/служебный user_id — не выдавай за реального пользователя
                    chat_id=chat_id,
                    database=database,
                    source="scheduled_task",   # НЕ "telegram" — не даёт allow_all_tools=True
                    selected_tools=AUTO_TRIGGER_TOOLS,
                    context_size="small",
                )
                log.info("[task-events] Автотриггер обработан для chat_id=%s: %s",
                          chat_id, result.get("message", "")[:100])

            except Exception as e:
                log.exception("[task-events] Ошибка автотриггера process_query: %s", e)

    except asyncio.CancelledError:
        log.info("[task-events] Consumer остановлен")
    except Exception as e:
        log.exception("[task-events] Неожиданная ошибка в consumer: %s", e)
    finally:
        await consumer.stop()
        log.info("[task-events] Consumer остановлен")