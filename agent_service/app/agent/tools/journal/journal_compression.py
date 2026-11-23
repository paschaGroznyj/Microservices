"""
tools/journal/journal_compression.py
────────────────────────────────────────
Автосжатие бортового журнала без потери данных.

Когда активных записей одного типа становится больше COMPRESSION_THRESHOLD,
все, кроме KEEP_RECENT самых свежих, сворачиваются:
  * одним запросом к модели получаем JSON со сводкой и однострочными
    описаниями для каждой свёрнутой записи;
  * сводка пишется отдельной summary-записью со ссылками на id исходников;
  * исходники НЕ удаляются, а уходят в архив (is_archived = TRUE,
    archive_summary = описание) и возвращаются через action="unarchive".

Если ответ модели не прошёл проверку, журнал не меняется: сжатие
пропускается до следующего insert.
"""

import json
import logging
import re

log = logging.getLogger(__name__)

COMPRESSION_THRESHOLD = 8
KEEP_RECENT = 3
MAX_ARCHIVE_SUMMARY_CHARS = 300
MIN_SUMMARY_CHARS = 40

SYSTEM_PROMPT = """Ты сворачиваешь старые записи бортового журнала AI-агента в архив.
Исходные записи сохранятся в архиве; в контексте агента останутся твоя сводка
и по одному предложению на каждую запись. Поэтому точность важнее краткости.

Верни ТОЛЬКО один JSON-объект, без Markdown-обёртки и пояснений:
{"summary": "<сводка>", "archive": {"<id>": "<одно предложение>", ...}}

summary:
- Markdown на русском, сгруппированный по темам; у каждого факта ссылка на исходную запись в виде (#id).
- Дословно сохраняй пути, имена файлов, ID, хеши коммитов, ветки, команды, шаблоны, числа, даты и имена.
- Ничего не выдумывай и не добавляй от себя. Без вступлений и рассуждений о своей роли.

archive:
- Ключ — id записи строкой; ровно по одному ключу на КАЖДУЮ переданную запись, других ключей нет.
- Значение — одно предложение до 200 символов: о чём запись и её главные идентификаторы."""

# Ответы, которые означают, что модель не поняла задачу.
_PREAMBLE_RE = re.compile(
    r"^\s*(я\s*[—–-]|я\s+(модуль|готов|ассистент)|ожидаю|готов|i\s+am|i'm|waiting)",
    re.IGNORECASE,
)


def _response_text(response) -> str:
    """content у LangChain бывает строкой или списком блоков (Anthropic)."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        content = "".join(parts)
    return content.strip() if isinstance(content, str) else ""


def _extract_json(text: str) -> dict:
    """Достаёт JSON-объект из ответа: допускает ```json-обёртку и текст вокруг."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("в ответе нет JSON-объекта")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("JSON не является объектом")
    return obj


def parse_compression_response(text: str, expected_ids: list[int]) -> tuple[str, dict[int, str]]:
    """Проверяет ответ модели. Возвращает (summary, {id: archive_summary}) или бросает ValueError."""
    data = _extract_json(text)

    summary = data.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < MIN_SUMMARY_CHARS:
        raise ValueError("summary пустая или слишком короткая")
    summary = summary.strip()
    if _PREAMBLE_RE.match(summary):
        raise ValueError(f"summary похожа на преамбулу: {summary[:80]!r}")

    archive = data.get("archive")
    if not isinstance(archive, dict):
        raise ValueError("archive отсутствует или не объект")

    result: dict[int, str] = {}
    for key, value in archive.items():
        try:
            entry_id = int(str(key).strip().lstrip("#"))
        except ValueError:
            raise ValueError(f"archive: ключ {key!r} не id")
        if not isinstance(value, str):
            raise ValueError(f"archive[{entry_id}] не строка")
        sentence = " ".join(value.split())
        if not sentence:
            raise ValueError(f"archive[{entry_id}] пустое")
        if len(sentence) > MAX_ARCHIVE_SUMMARY_CHARS:
            raise ValueError(f"archive[{entry_id}] длиннее {MAX_ARCHIVE_SUMMARY_CHARS} символов")
        if _PREAMBLE_RE.match(sentence):
            raise ValueError(f"archive[{entry_id}] похоже на преамбулу")
        result[entry_id] = sentence

    expected = set(expected_ids)
    missing = expected - result.keys()
    extra = result.keys() - expected
    if missing or extra:
        raise ValueError(f"archive: нет id {sorted(missing)}, лишние id {sorted(extra)}")
    return summary, result


def build_prompt(entry_type: str, rows) -> str:
    entries = [
        {
            "id": r["id"],
            "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else None,
            "content": r["content"],
        }
        for r in rows
    ]
    return (
        f"Тип записей: {entry_type}. Количество: {len(entries)}.\n"
        f"Записи (JSON):\n{json.dumps(entries, ensure_ascii=False, indent=1)}"
    )


class _Race(Exception):
    """Записи изменились между выборкой и архивацией."""


async def maybe_compress_journal(db, llm, chat_id: str, entry_type: str) -> dict | None:
    """
    Проверяет порог активных записей entry_type под chat_id и при превышении
    сворачивает старые записи в архив + summary-запись.

    Вызывать после каждого успешного INSERT в journal_handler.
    None — сжатие не требовалось; {"compressed": True, ...} — выполнено;
    {"compressed": False, "reason": ...} — пропущено, журнал не изменён.
    """
    if entry_type in ("identity", "summary"):
        # identity — правила поведения, не сворачиваем.
        # summary — результат сжатия, повторно не сжимаем (рекурсия).
        return None

    rows = await db.pool.fetch("""
        SELECT id, content, created_at
        FROM agent_journal
        WHERE chat_id = $1 AND entry_type = $2 AND is_archived = FALSE
        ORDER BY created_at ASC, id ASC
    """, chat_id, entry_type)

    if len(rows) <= COMPRESSION_THRESHOLD:
        return None

    # KEEP_RECENT самых свежих остаются активными, остальные сворачиваются.
    to_compress = rows[:-KEEP_RECENT]
    compressed_ids = [r["id"] for r in to_compress]

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(entry_type, to_compress)},
        ])
        summary_text, archive_map = parse_compression_response(_response_text(response), compressed_ids)
    except Exception as e:
        # Не роняем insert и ничего не трогаем: попробуем при следующем insert.
        log.warning(
            f"[journal_compression] пропуск chat_id={chat_id} entry_type={entry_type}: "
            f"{type(e).__name__}: {e}"
        )
        return {"compressed": False, "entry_type": entry_type, "reason": f"{type(e).__name__}: {e}"}

    try:
        summary_id = await _apply(db, chat_id, entry_type, compressed_ids, archive_map, summary_text)
    except _Race:
        log.warning(f"[journal_compression] гонка chat_id={chat_id} entry_type={entry_type}, откат")
        return {"compressed": False, "entry_type": entry_type, "reason": "записи изменились параллельно"}

    log.info(
        f"[journal_compression] chat_id={chat_id} entry_type={entry_type}: "
        f"{len(compressed_ids)} записей в архив, summary_id={summary_id}"
    )
    return {
        "compressed": True,
        "entry_type": entry_type,
        "compressed_count": len(compressed_ids),
        "archived_ids": compressed_ids,
        "summary_id": summary_id,
    }


async def _apply(db, chat_id, entry_type, compressed_ids, archive_map, summary_text) -> int:
    """Одна транзакция: архивирует исходники и вставляет summary-запись."""
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            # Если параллельный insert уже свернул часть этих записей,
            # обновится меньше строк — откатываем всю транзакцию.
            archived = await conn.fetch("""
                UPDATE agent_journal j
                SET is_archived = TRUE, archive_summary = s.summary, updated_at = NOW()
                FROM unnest($1::bigint[], $2::text[]) AS s(id, summary)
                WHERE j.id = s.id AND j.chat_id = $3 AND j.is_archived = FALSE
                RETURNING j.id
            """, compressed_ids, [archive_map[i] for i in compressed_ids], chat_id)

            if len(archived) != len(compressed_ids):
                raise _Race()

            return await conn.fetchval("""
                INSERT INTO agent_journal (chat_id, entry_type, content, context, created_at, updated_at)
                VALUES ($1, 'summary', $2, $3, NOW(), NOW())
                RETURNING id
            """, chat_id, summary_text, json.dumps({
                "source": "auto_compression",
                "chat_id": chat_id,
                "compressed_entry_type": entry_type,
                "compressed_count": len(compressed_ids),
                "compressed_ids": compressed_ids,
                "archived": True,
            }))
