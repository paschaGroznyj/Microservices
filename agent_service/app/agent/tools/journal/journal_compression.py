import json
import logging

log = logging.getLogger(__name__)

COMPRESSION_THRESHOLD = 8
KEEP_RECENT = 3

async def maybe_compress_journal(db, llm, chat_id: str, entry_type: str) -> dict | None:
    """
    Проверяет, не превышен ли порог записей entry_type под этим chat_id.
    Если да — сжимает старые записи (кроме identity и summary) в одну
    summary-запись, удаляет исходные.

    Вызывать после каждого успешного INSERT в journal_handler._do_insert.
    Возвращает None если компрессия не потребовалась, иначе dict с деталями.
    """
    if entry_type in ("identity", "summary"):
        # identity — всегда одна запись по смыслу, не накапливается.
        # summary — результат уже сжат, повторно сжимать нечего (и опасно рекурсией).
        return None

    async with db.pool.acquire() as conn:
        count = await conn.fetchval("""
            SELECT count(*) FROM agent_journal
            WHERE chat_id = $1 AND entry_type = $2
        """, chat_id, entry_type)

        if count is None or count <= COMPRESSION_THRESHOLD:
            return None

        # Берём все записи этого типа под этим chat_id, от старых к новым
        rows = await conn.fetch("""
            SELECT id, content, created_at
            FROM agent_journal
            WHERE chat_id = $1 AND entry_type = $2
            ORDER BY created_at ASC
        """, chat_id, entry_type)

    if len(rows) <= COMPRESSION_THRESHOLD:
        # Гонка: количество могло измениться между COUNT и SELECT — перепроверяем честно
        return None

    # Оставляем KEEP_RECENT самых свежих нетронутыми, сжимаем всё что старше
    to_compress = rows[:-KEEP_RECENT] if len(rows) > KEEP_RECENT else rows[:0]

    if len(to_compress) < 2:
        # Меньше 2 записей сжимать бессмысленно — компрессия не даёт выигрыша
        return None

    entries_text = "\n".join(
        f"[{r['created_at'].strftime('%Y-%m-%d %H:%M')}] {r['content']}"
        for r in to_compress
    )

    prompt = f"{entry_type}: {entries_text}"

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": "Ты — модуль сжатия бортового журнала. Отвечай только сводкой, без преамбул."},
            {"role": "user", "content": prompt},
        ])
        summary_text = response.content.strip()
    except Exception as e:
        log.error(f"[journal_compression] Не удалось сжать записи chat_id={chat_id} entry_type={entry_type}: {e}", exc_info=True)
        # Не роняем весь insert-flow из-за сбоя компрессии — просто пропускаем на этот раз
        return None

    compressed_ids = [r["id"] for r in to_compress]

    async with db.pool.acquire() as conn:
        async with conn.transaction():
            # Вставляем сводку как summary-запись
            summary_id = await conn.fetchval("""
                INSERT INTO agent_journal (chat_id, entry_type, content, context, created_at, updated_at)
                VALUES ($1, 'summary', $2, $3, NOW(), NOW())
                RETURNING id
            """, chat_id, summary_text, json.dumps({
                "source": "auto_compression",
                "compressed_entry_type": entry_type,
                "compressed_count": len(compressed_ids),
                "compressed_ids": compressed_ids,
            }))

            # Удаляем исходные записи одним запросом
            await conn.execute("""
                DELETE FROM agent_journal WHERE id = ANY($1::bigint[])
            """, compressed_ids)

    log.info(
        f"[journal_compression] chat_id={chat_id} entry_type={entry_type}: "
        f"сжато {len(compressed_ids)} записей в summary_id={summary_id}"
    )

    return {
        "compressed": True,
        "entry_type": entry_type,
        "compressed_count": len(compressed_ids),
        "summary_id": summary_id,
    }