"""
task_events_consumer.py
══════════════════════════════════════════════════════════════════════
Дополнительный Kafka consumer для app.py (agent_service).
Слушает topic "task-events" от sandbox-сервиса.

Подключение в app.py (lifespan):

    from agent_pm.task_events_consumer import start_task_events_consumer

    # В lifespan, после kafka_task:
    task_events_task = asyncio.create_task(start_task_events_consumer())

    # В shutdown:
    task_events_task.cancel()
    await asyncio.gather(task_events_task, return_exceptions=True)

Поток данных:

    Sandbox: /submit завершён
      → Kafka("task-events", {task_id, chat_id, workspace_id, label, status, ...})
      → task_events_consumer() ловит
      → Kafka("chat-messages", {is_from_bot: True, chat_id, message: "..."})
      → Telegram Bot → User

Особенности:
  • Не блокирует основной kafka_consumer("chat-messages")
  • При рестарте app.py — offset в Kafka не теряется (group_id фиксирует позицию)
  • Проверяет все ли задачи в workspace завершены — если да, шлёт "все готовы"
  • Если агент ещё в диалоге с пользователем — уведомление всё равно уходит
    (пользователь видит его когда вернётся в чат)
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from agent_pm.core.config import KAFKA_BROKER

log = logging.getLogger("task_events")

TASK_EVENTS_TOPIC = "task-events"
CHAT_MESSAGES_TOPIC = "chat-messages"
TASK_EVENTS_GROUP = "task-events-group"

# In-memory кэш: workspace_id → {completed_count, total_expected, labels_done, labels_pending}
# Используется чтобы знать, сколько задач ожидать в workspace.
# Агент регистрирует ожидание через register_workspace_expectation()
_workspace_expectations: dict[str, dict] = {}


def register_workspace_expectation(
    workspace_id: str,
    task_ids: list[str],
    labels: list[str],
):
    """
    Вызывается агентом ПЕРЕД постановкой задач (до /submit).
    Чтобы consumer знал, сколько задач ожидать и какие лейблы у pending.

    Пример:
        register_workspace_expectation(
            workspace_id="abc123",
            task_ids=["t1", "t2", "t3"],
            labels=["АКРА", "НКР", "Эксперт РА"],
        )
    """
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
    """Возвращает статус workspace или None если нет ожидания."""
    exp = _workspace_expectations.get(workspace_id)
    if exp is None:
        return None

    total = len(exp["task_ids"])
    done = len(exp["completed"]) + len(exp["failed"])
    pending_labels = [
        exp["labels"][tid]
        for tid in exp["task_ids"]
        if tid not in exp["completed"] and tid not in exp["failed"]
    ]
    done_labels = list(
        exp["labels"][tid] for tid in exp["completed"]
    ) + list(
        exp["labels"][tid] for tid in exp["failed"]
    )

    return {
        "total": total,
        "done": done,
        "pending_labels": pending_labels,
        "done_labels": done_labels,
        "all_done": done >= total,
    }


async def _send_chat_message(
    producer: AIOKafkaProducer,
    chat_id: int,
    message: str,
    action: str = "task_notification",
):
    """Отправляет сообщение в chat-messages (→ Telegram Bot → User)."""
    message_id = f"msg-{int(time.time() * 1000)}-{str(uuid.uuid4())[:8]}"
    bot_message = {
        "user_id": -1,
        "chat_id": chat_id,
        "message": message,
        "message_id": message_id,
        "is_from_bot": True,
        "type_agent": "pm",
        "action": action,
    }
    await producer.send_and_wait(CHAT_MESSAGES_TOPIC, value=bot_message)
    log.info("[task-events] Уведомление отправлено в чат %s: %s", chat_id, message[:80])


async def start_task_events_consumer():
    """
    Главный consumer. Запускается как asyncio task в lifespan.

    Слушает topic "task-events". При получении события:
    1. Обновляет статус в _workspace_expectations
    2. Формирует уведомление
    3. Отправляет в "chat-messages" (→ бот → пользователь)
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

    # Producer для отправки уведомлений в chat-messages
    producer = AIOKafkaProducer(
        bootstrap_servers=[KAFKA_BROKER],
        key_serializer=lambda k: str(k).encode("utf-8"),
        value_serializer=lambda m: json.dumps(m).encode("utf-8"),
    )
    await producer.start()

    try:
        async for message in consumer:
            event = message.value
            log.info("[task-events] Получено событие: %s", event)

            task_id = event.get("task_id")
            chat_id = event.get("chat_id")
            workspace_id = event.get("workspace_id")
            label = event.get("label", "задача")
            status = event.get("status", "unknown")
            ok = event.get("ok", False)
            error = event.get("error")
            duration_ms = event.get("duration_ms", 0)
            files = event.get("files", [])
            stdout_preview = event.get("stdout_preview", "")

            # Обновляем ожидание
            ws_status = None
            if workspace_id and workspace_id in _workspace_expectations:
                exp = _workspace_expectations[workspace_id]
                if status == "completed":
                    exp["completed"].add(task_id)
                elif status == "failed":
                    exp["failed"].add(task_id)
                ws_status = _check_workspace_status(workspace_id)

            # Формируем уведомление
            if ws_status and ws_status["all_done"]:
                # Все задачи завершены
                failed_count = len(
                    _workspace_expectations[workspace_id]["failed"]
                )

                if failed_count > 0:
                    notification = (
                        f"Все задачи завершены ({ws_status['total']} из {ws_status['total']}).\n"
                        f"Успешно: {ws_status['total'] - failed_count}, с ошибками: {failed_count}.\n"
                        f"Могу собрать результат из доступных данных."
                    )
                else:
                    notification = (
                        f"Все {ws_status['total']} задач завершены успешно.\n"
                        f"Готов собрать итоговый файл. Напиши «собери» или дождись — "
                        f"я начну автоматически."
                    )

                # Очищаем ожидание
                del _workspace_expectations[workspace_id]

            elif ws_status:
                # Частичное завершение
                pending = ", ".join(ws_status["pending_labels"])
                notification = (
                    f"Задача «{label}» {'завершена' if ok else 'завершилась с ошибкой'} "
                    f"({duration_ms // 1000}с).\n"
                    f"Готово: {ws_status['done']}/{ws_status['total']}. "
                    f"Ожидаем: {pending}."
                )

            else:
                # Нет зарегистрированного ожидания — просто уведомляем
                if ok:
                    files_info = ""
                    if files:
                        files_info = f" Файлы: {', '.join(f['filename'] for f in files)}"
                    notification = (
                        f"Задача «{label}» завершена ({duration_ms // 1000}с).{files_info}"
                    )
                    if stdout_preview:
                        notification += f"\nПревью: {stdout_preview[:200]}"
                else:
                    notification = (
                        f"Задача «{label}» завершилась с ошибкой ({duration_ms // 1000}с).\n"
                        f"Ошибка: {error or 'неизвестно'}"
                    )

            await _send_chat_message(producer, chat_id, notification)

    except asyncio.CancelledError:
        log.info("[task-events] Consumer остановлен")
    except Exception as e:
        log.exception("[task-events] Неожиданная ошибка в consumer: %s", e)
    finally:
        await consumer.stop()
        await producer.stop()
        log.info("[task-events] Consumer и producer остановлены")
