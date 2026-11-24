"""
tools/journal/journal_handler.py
────────────────────────────────────────
Обработчик операций с agent_journal. Заменяет свободный SQL на
типизированные операции insert / update / archive / unarchive / expire,
привязанные к chat_id. Приоритет 1–5 и срок действия expires_at задаёт модель.
"""

import json
import logging
from datetime import datetime, timezone

from .journal_compression import maybe_compress_journal, MAX_SUMMARIES

log = logging.getLogger(__name__)

VALID_ENTRY_TYPES = {"identity", "user_preference", "anomaly", "evolution", "summary"}
VALID_ACTIONS = {"insert", "update", "archive", "unarchive", "expire"}

# archive/unarchive/expire работают по id записи, entry_type/content не нужны.
_ID_ACTIONS = {"archive", "unarchive", "expire"}

DEFAULT_PRIORITY = 3
# expires_at="none" в update снимает срок действия.
_CLEAR_EXPIRY = {"none", "null", "clear", "-"}


def _parse_priority(priority) -> int | None:
    """0/None — не задан. Иначе целое 1..5, иначе ValueError."""
    if priority in (None, "", 0, "0"):
        return None
    try:
        value = int(priority)
    except (TypeError, ValueError):
        raise ValueError(f"priority должен быть целым 1–5, получено: {priority!r}")
    if not 1 <= value <= 5:
        raise ValueError(f"priority должен быть от 1 (наивысший) до 5, получено: {value}")
    return value


def _parse_expiry(expires_at, tz):
    """
    "" — не задано (None); "none" — снять срок (возвращает False);
    иначе aware datetime по местному времени пользователя (tz).
    """
    from agent.core.timezone_utils import parse_local_datetime

    raw = (expires_at or "").strip() if isinstance(expires_at, str) else expires_at
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=tz or timezone.utc)
    if raw.lower() in _CLEAR_EXPIRY:
        return False
    return parse_local_datetime(raw, tz)


def _fmt_local(dt, tz) -> str:
    from agent.core.timezone_utils import format_local
    return format_local(dt, tz)


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
        priority: int | None = None,
        expires_at: str | None = None,
        tz=None,
    ) -> dict:
    """
    Выполняет одну операцию с agent_journal в рамках конкретного chat_id.

    action:
        "insert"    — новая запись (точный дубль под chat_id+entry_type отклоняется).
                      priority 1–5 (по умолчанию 3), expires_at — срок для разовых записей.
        "update"    — по entry_id или по подстроке replace (в пределах entry_type):
                      замена replace→content и/или новые priority / expires_at.
        "archive"   — свернуть запись в архив по entry_id (+ archive_summary).
        "unarchive" — вернуть запись из архива по entry_id.
        "expire"    — пометить запись истёкшей по entry_id (мягкое удаление).

    expires_at — время по часам пользователя (tz), см. parse_local_datetime.
    """
    if action not in VALID_ACTIONS:
        return {"ok": False, "error": f"Недопустимое action: {action}. Разрешены: {sorted(VALID_ACTIONS)}"}

    try:
        prio = _parse_priority(priority)
        expiry = _parse_expiry(expires_at, tz)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if entry_id not in (None, "", 0):
        try:
            entry_id = int(entry_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"entry_id должен быть целым числом, получено: {entry_id!r}"}
    else:
        entry_id = None

    try:
        if action in _ID_ACTIONS:
            if entry_id is None:
                return {"ok": False, "error": f"Для action={action} обязателен параметр entry_id (id записи журнала)"}
            if action == "archive":
                return await _do_archive(db, chat_id, entry_id, archive_summary)
            if action == "unarchive":
                return await _do_unarchive(db, chat_id, entry_id)
            return await _do_expire(db, chat_id, entry_id)

        if action == "insert":
            if entry_type not in VALID_ENTRY_TYPES:
                return {"ok": False, "error": f"Недопустимый entry_type: {entry_type}. Разрешены: {sorted(VALID_ENTRY_TYPES)}"}
            if not content or not content.strip():
                return {"ok": False, "error": "content не может быть пустым"}
            if expiry is False:
                expiry = None
            if expiry is not None and expiry <= datetime.now(timezone.utc):
                return {"ok": False, "error": f"expires_at уже прошло ({_fmt_local(expiry, tz)}): разовую запись не создаю"}
            result = await _do_insert(db, chat_id, entry_type, content, user_id,
                                      prio or DEFAULT_PRIORITY, expiry)
            if result.get("ok"):
                if expiry is not None:
                    result["expires_at"] = _fmt_local(expiry, tz)
                if entry_type == "summary":
                    capped = await _cap_summaries(db, chat_id)
                    if capped:
                        result["archived_summaries"] = capped
                # Автокомпрессия — детерминированная, не зависит от того, вспомнит ли модель
                compression = await maybe_compress_journal(db, llm, chat_id, entry_type)
                if compression:
                    result["compression"] = compression
            return result

        # update
        if entry_id is None:
            if entry_type not in VALID_ENTRY_TYPES:
                return {"ok": False, "error": f"Недопустимый entry_type: {entry_type}. Разрешены: {sorted(VALID_ENTRY_TYPES)}"}
            if not replace:
                return {"ok": False, "error": "Для update нужен entry_id или replace (подстрока, по которой ищем запись)"}
        if replace and (not content or not content.strip()):
            return {"ok": False, "error": "content не может быть пустым: это текст, которым заменяется replace"}
        if not replace and prio is None and expiry is None:
            return {"ok": False, "error": "Нечего обновлять: укажите replace+content, priority или expires_at"}
        if expiry is not None and expiry is not False and expiry <= datetime.now(timezone.utc):
            return {"ok": False, "error": f"expires_at уже прошло ({_fmt_local(expiry, tz)}); для снятия записи используйте action=\"expire\""}
        return await _do_update(db, chat_id, entry_type, content, replace, entry_id, prio, expiry, tz)
    except Exception as e:
        log.error(f"Ошибка операции journal action={action} chat_id={chat_id}: {e}", exc_info=True)
        return {"ok": False, "error": f"Внутренняя ошибка: {type(e).__name__}: {e}"}


async def _do_insert(db, chat_id: str, entry_type: str, content: str, user_id: int | None,
                     priority: int = DEFAULT_PRIORITY, expires_at=None) -> dict:
    # Проверяем — нет ли уже такой же живой записи под этим chat_id (защита от дублей)
    existing = await db.pool.fetchrow("""
        SELECT id FROM agent_journal
        WHERE chat_id = $1 AND entry_type = $2 AND content = $3 AND is_expired = FALSE
        LIMIT 1
    """, chat_id, entry_type, content)

    if existing:
        return {"ok": False, "error": "Точно такая же запись уже существует под этим chat_id", "existing_id": existing["id"]}

    row_id = await db.pool.fetchval("""
        INSERT INTO agent_journal (chat_id, entry_type, content, context, user_id, priority, expires_at,
                                   created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())
        RETURNING id
    """, chat_id, entry_type, content, json.dumps({"chat_id": chat_id}), user_id, priority, expires_at)

    return {"ok": True, "action": "insert", "id": row_id, "priority": priority}


async def _cap_summaries(db, chat_id: str) -> list[int]:
    """Ручная summary-запись: оставляем MAX_SUMMARIES свежих сводок, старые — в архив."""
    rows = await db.pool.fetch("""
        UPDATE agent_journal j
        SET is_archived = TRUE, updated_at = NOW(),
            archive_summary = 'Сводка журнала: ' || left(regexp_replace(j.content, '\\s+', ' ', 'g'), 180)
        WHERE j.id IN (
            SELECT id FROM agent_journal
            WHERE chat_id = $1 AND entry_type = 'summary' AND is_archived = FALSE AND is_expired = FALSE
            ORDER BY created_at DESC, id DESC
            OFFSET $2
        )
        RETURNING j.id
    """, chat_id, MAX_SUMMARIES)
    return sorted(r["id"] for r in rows)


async def _do_update(db, chat_id: str, entry_type: str, content: str, replace: str | None,
                     entry_id: int | None, priority: int | None, expiry, tz=None) -> dict:
    if entry_id is not None:
        row = await db.pool.fetchrow("""
            SELECT id, content FROM agent_journal
            WHERE id = $1 AND chat_id = $2
            LIMIT 1
        """, entry_id, chat_id)
        if not row:
            return {"ok": False, "error": f"Запись id={entry_id} не найдена под chat_id={chat_id}"}
        if replace and replace not in row["content"]:
            return {"ok": False, "error": f"В записи id={entry_id} нет подстроки '{replace}'"}
    else:
        # Ищем запись под этим chat_id, содержащую replace как подстроку
        row = await db.pool.fetchrow("""
            SELECT id, content FROM agent_journal
            WHERE chat_id = $1 AND entry_type = $2 AND content LIKE '%' || $3 || '%'
            ORDER BY updated_at DESC
            LIMIT 1
        """, chat_id, entry_type, replace)
        if not row:
            return {"ok": False, "error": f"Не найдена запись с подстрокой '{replace}' под chat_id={chat_id}, entry_type={entry_type}"}

    new_content = row["content"].replace(replace, content) if replace else row["content"]
    clear_expiry = expiry is False
    new_expiry = None if clear_expiry else expiry

    # Новый срок в будущем (или снятый срок) возвращает истёкшую запись в контекст.
    await db.pool.execute("""
        UPDATE agent_journal
        SET content = $1,
            priority = COALESCE($2, priority),
            expires_at = CASE WHEN $4 THEN NULL WHEN $3::timestamptz IS NOT NULL THEN $3 ELSE expires_at END,
            is_expired = CASE WHEN $4 OR $3::timestamptz IS NOT NULL THEN FALSE ELSE is_expired END,
            expired_at = CASE WHEN $4 OR $3::timestamptz IS NOT NULL THEN NULL ELSE expired_at END,
            updated_at = NOW()
        WHERE id = $5
    """, new_content, priority, new_expiry, clear_expiry, row["id"])

    result = {"ok": True, "action": "update", "id": row["id"]}
    if priority is not None:
        result["priority"] = priority
    if clear_expiry:
        result["expires_at"] = None
    elif new_expiry is not None:
        result["expires_at"] = _fmt_local(new_expiry, tz)
    return result


async def _do_expire(db, chat_id: str, entry_id: int) -> dict:
    """Мягкое удаление: запись остаётся в БД, но не попадает в контекст и сжатие."""
    row = await db.pool.fetchrow("""
        SELECT id, entry_type, is_expired FROM agent_journal
        WHERE id = $1 AND chat_id = $2
        LIMIT 1
    """, entry_id, chat_id)
    if not row:
        return {"ok": False, "error": f"Запись id={entry_id} не найдена под chat_id={chat_id}"}
    if row["is_expired"]:
        return {"ok": True, "action": "expire", "id": entry_id, "already_expired": True}
    await db.pool.execute("""
        UPDATE agent_journal
        SET is_expired = TRUE, expired_at = NOW(), updated_at = NOW()
        WHERE id = $1
    """, entry_id)
    return {"ok": True, "action": "expire", "id": entry_id}


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
        SELECT id, is_archived, is_expired FROM agent_journal
        WHERE id = $1 AND chat_id = $2
        LIMIT 1
    """, entry_id, chat_id)

    if not row:
        return {"ok": False, "error": f"Запись id={entry_id} не найдена под chat_id={chat_id}"}

    if row["is_expired"]:
        return {"ok": False, "error": (
            f"Запись id={entry_id} истекла. Вернуть её можно через action=\"update\" "
            f"с entry_id и новым expires_at (или expires_at=\"none\")")}

    if not row["is_archived"]:
        return {"ok": True, "action": "unarchive", "id": entry_id, "already_active": True}

    await db.pool.execute("""
        UPDATE agent_journal
        SET is_archived = FALSE, updated_at = NOW()
        WHERE id = $1
    """, entry_id)

    return {"ok": True, "action": "unarchive", "id": entry_id}