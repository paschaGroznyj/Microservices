"""
tools/journal/journal_handler.py
────────────────────────────────────────
Обработчик операций с agent_journal. Заменяет свободный SQL на
типизированные операции: insert / update / delete, привязанные к chat_id.
"""

import json
import logging
from .journal_compression import maybe_compress_journal

log = logging.getLogger(__name__)

VALID_ENTRY_TYPES = {"identity", "user_preference", "anomaly", "evolution", "summary"}
# VALID_ACTIONS = {"insert", "update", "delete"}
VALID_ACTIONS = {"insert", "update", "archive", "unarchive"}

# Для archive/unarchive entry_type не проверяется (операция по id), поэтому
# для них разрешаем служебное значение-заглушку.
_ID_ACTIONS = {"archive", "unarchive"}

async def handle_journal_operation(
        db,
        llm,
        chat_id: str,
        action: str,
        entry_type: str,
        content: str,
        replace: str | None = None,
        user_id: int | None = None,
        entry_id: int | None = None,
        archive_summary: str | None = None,
    ) -> dict:
    """
    Выполняет одну операцию с agent_journal в рамках конкретного chat_id.

    action:
        "insert" — новая запись, только если под этим chat_id+entry_type
                   ещё нет записи с похожим content (см. поиск ниже).
        "update" — находит запись под chat_id, где content LIKE %replace%,
                   заменяет replace на content (частичная замена внутри поля).
        "delete" — находит запись под chat_id, где content LIKE %replace%, удаляет.

    Для update/delete: replace обязателен — это подстрока, по которой ищем
    существующую запись через LIKE, не сам SQL.
    """
    if action not in VALID_ACTIONS:
        return {"ok": False, "error": f"Недопустимое action: {action}. Разрешены: {VALID_ACTIONS}"}

    # archive/unarchive работают по id записи, а не по entry_type/content.
    if action in _ID_ACTIONS:
        if entry_id is None:
            return {"ok": False, "error": f"Для action={action} обязателен параметр entry_id (id записи журнала)"}
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"entry_id должен быть целым числом, получено: {entry_id!r}"}
        try:
            if action == "archive":
                return await _do_archive(db, chat_id, entry_id, archive_summary)
            else:
                return await _do_unarchive(db, chat_id, entry_id)
        except Exception as e:
            log.error(f"Ошибка операции journal action={action} chat_id={chat_id}: {e}", exc_info=True)
            return {"ok": False, "error": f"Внутренняя ошибка: {type(e).__name__}: {e}"}

    if entry_type not in VALID_ENTRY_TYPES:
        return {"ok": False, "error": f"Недопустимый entry_type: {entry_type}. Разрешены: {VALID_ENTRY_TYPES}"}

    if not content or not content.strip():
        return {"ok": False, "error": "content не может быть пустым"}

    if action in ("update", "delete") and not replace:
        return {"ok": False, "error": f"Для action={action} обязателен параметр replace (что ищем)"}
    print(f"ТИП вставки: {action}")
    try:
        if action == "insert":
            result = await _do_insert(db, chat_id, entry_type, content, user_id)
            print(f"result {result}")
            if result.get("ok"):
                # Автокомпрессия — детерминированная, не зависит от того, вспомнит ли модель
                compression = await maybe_compress_journal(db, llm, chat_id, entry_type)
                if compression:
                    result["compression"] = compression
            return result
        elif action == "update":
            return await _do_update(db, chat_id, entry_type, content, replace)
        elif action == "delete":
            return await _do_delete(db, chat_id, entry_type, replace)
    except Exception as e:
        log.error(f"Ошибка операции journal action={action} chat_id={chat_id}: {e}", exc_info=True)
        return {"ok": False, "error": f"Внутренняя ошибка: {type(e).__name__}: {e}"}


async def _do_insert(db, chat_id: str, entry_type: str, content: str, user_id: int | None) -> dict:
    # Проверяем — нет ли уже похожей записи под этим chat_id (защита от дублей)
    existing = await db.pool.fetchrow("""
        SELECT id FROM agent_journal
        WHERE chat_id = $1 AND entry_type = $2 AND content = $3
        LIMIT 1
    """, chat_id, entry_type, content)

    if existing:
        return {"ok": False, "error": "Точно такая же запись уже существует под этим chat_id", "existing_id": existing["id"]}

    row_id = await db.pool.fetchval("""
        INSERT INTO agent_journal (chat_id, entry_type, content, context, user_id, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW(), NOW())
        RETURNING id
    """, chat_id, entry_type, content, json.dumps({"chat_id": chat_id}), user_id)

    return {"ok": True, "action": "insert", "id": row_id}


async def _do_update(db, chat_id: str, entry_type: str, content: str, replace: str) -> dict:
    # Ищем запись под этим chat_id, содержащую replace как подстроку
    row = await db.pool.fetchrow("""
        SELECT id, content FROM agent_journal
        WHERE chat_id = $1 AND entry_type = $2 AND content LIKE '%' || $3 || '%'
        ORDER BY updated_at DESC
        LIMIT 1
    """, chat_id, entry_type, replace)

    if not row:
        return {"ok": False, "error": f"Не найдена запись с подстрокой '{replace}' под chat_id={chat_id}, entry_type={entry_type}"}

    new_content = row["content"].replace(replace, content)

    await db.pool.execute("""
        UPDATE agent_journal
        SET content = $1, updated_at = NOW()
        WHERE id = $2
    """, new_content, row["id"])

    return {"ok": True, "action": "update", "id": row["id"], "replaced": replace, "with": content}


async def _do_delete(db, chat_id: str, entry_type: str, replace: str) -> dict:
    row = await db.pool.fetchrow("""
        SELECT id FROM agent_journal
        WHERE chat_id = $1 AND entry_type = $2 AND content LIKE '%' || $3 || '%'
        ORDER BY updated_at DESC
        LIMIT 1
    """, chat_id, entry_type, replace)

    if not row:
        return {"ok": False, "error": f"Не найдена запись с подстрокой '{replace}' под chat_id={chat_id}, entry_type={entry_type}"}

    await db.pool.execute("DELETE FROM agent_journal WHERE id = $1", row["id"])

    return {"ok": True, "action": "delete", "id": row["id"]}


async def _do_archive(db, chat_id: str, entry_id: int, archive_summary: str | None) -> dict:
    """
    Помечает запись is_archived=TRUE и пишет краткое одно-предложение summary.
    Запись перестаёт тянуться в основной контекст get_agent_journal,
    но остаётся видимой в компактном реестре архива (id + summary).
    """
    if not archive_summary or not archive_summary.strip():
        return {"ok": False, "error": "Для archive обязателен archive_summary — одно короткое предложение о содержимом записи"}

    row = await db.pool.fetchrow("""
        SELECT id, is_archived FROM agent_journal
        WHERE id = $1 AND chat_id = $2
        LIMIT 1
    """, entry_id, chat_id)

    if not row:
        return {"ok": False, "error": f"Запись id={entry_id} не найдена под chat_id={chat_id}"}

    if row["is_archived"]:
        # уже в архиве — просто обновим summary, идемпотентно
        await db.pool.execute("""
            UPDATE agent_journal
            SET archive_summary = $1, updated_at = NOW()
            WHERE id = $2
        """, archive_summary.strip(), entry_id)
        return {"ok": True, "action": "archive", "id": entry_id, "already_archived": True, "summary": archive_summary.strip()}

    await db.pool.execute("""
        UPDATE agent_journal
        SET is_archived = TRUE, archive_summary = $1, updated_at = NOW()
        WHERE id = $2
    """, archive_summary.strip(), entry_id)

    return {"ok": True, "action": "archive", "id": entry_id, "summary": archive_summary.strip()}


async def _do_unarchive(db, chat_id: str, entry_id: int) -> dict:
    """Возвращает запись в активный контекст (is_archived=FALSE)."""
    row = await db.pool.fetchrow("""
        SELECT id, is_archived FROM agent_journal
        WHERE id = $1 AND chat_id = $2
        LIMIT 1
    """, entry_id, chat_id)

    if not row:
        return {"ok": False, "error": f"Запись id={entry_id} не найдена под chat_id={chat_id}"}

    if not row["is_archived"]:
        return {"ok": True, "action": "unarchive", "id": entry_id, "already_active": True}

    await db.pool.execute("""
        UPDATE agent_journal
        SET is_archived = FALSE, updated_at = NOW()
        WHERE id = $1
    """, entry_id)

    return {"ok": True, "action": "unarchive", "id": entry_id}