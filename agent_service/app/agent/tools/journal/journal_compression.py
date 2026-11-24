"""
tools/journal/journal_compression.py
────────────────────────────────────────
Автосжатие бортового журнала без потери данных.

Сжимаются только обычные записи (всё, кроме identity и summary) — живые:
не в архиве и не истёкшие. Когда их больше COMPRESSION_THRESHOLD, активными
остаются KEEP_RECENT самых важных: сначала по приоритету (1 — наивысший),
затем по свежести. Поэтому запись с priority=1 уходит в архив последней,
даже если она старая. Остальные сворачиваются:
  * одним запросом к модели получаем JSON со сводкой и однострочными
    описаниями для каждой свёрнутой записи;
  * сводка пишется summary-записью со ссылками на id исходников;
  * активных сводок не больше MAX_SUMMARIES: лишние старые сводки
    передаются модели и вливаются в новую, а сами уходят в архив;
  * исходники НЕ удаляются, а уходят в архив (is_archived = TRUE,
    archive_summary = описание) и возвращаются через action="unarchive".

Если ответ модели не прошёл проверку, журнал не меняется: сжатие
пропускается до следующего insert.
"""

import json
import logging
import re

log = logging.getLogger(__name__)

COMPRESSION_THRESHOLD = 8   # живых обычных записей (без identity и summary)
KEEP_RECENT = 3             # сколько самых важных записей остаются активными
MAX_SUMMARIES = 2           # активных summary-записей в журнале
SKIP_TYPES = ("identity", "summary")
MAX_ARCHIVE_SUMMARY_CHARS = 300
MIN_SUMMARY_CHARS = 40

SYSTEM_PROMPT = """Ты сворачиваешь старые записи бортового журнала AI-агента в архив.
Исходные записи сохранятся в архиве; в контексте агента останутся твоя сводка
и по одному предложению на каждую запись. Поэтому точность важнее краткости.

У каждой записи есть тип и приоритет 1–5 (1 — самый важный). Факты из записей
с приоритетом 1–2 переноси в сводку полностью, без сокращений.
Если переданы предыдущие сводки, новая сводка ЗАМЕНЯЕТ их: перенеси из них все
факты со ссылками (#id), объединяя повторы, и добавь факты из новых записей.

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


def _fmt_dt(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M") if value else None


def build_prompt(rows, previous_summaries=()) -> str:
    entries = [
        {
            "id": r["id"],
            "type": r["entry_type"],
            "priority": r["priority"],
            "created_at": _fmt_dt(r["created_at"]),
            "content": r["content"],
        }
        for r in rows
    ]
    parts = []
    if previous_summaries:
        prev = [{"id": r["id"], "created_at": _fmt_dt(r["created_at"]), "content": r["content"]}
                for r in previous_summaries]
        parts.append(
            f"Предыдущие сводки, которые надо влить в новую (JSON):\n"
            f"{json.dumps(prev, ensure_ascii=False, indent=1)}\n"
        )
    # Блок записей всегда последний: его ключи и только их ждёт парсер.
    parts.append(
        f"Количество записей: {len(entries)}.\n"
        f"Записи (JSON):\n{json.dumps(entries, ensure_ascii=False, indent=1)}"
    )
    return "\n".join(parts)


class _Race(Exception):
    """Записи изменились между выборкой и архивацией."""


EXPIRE_DUE_SQL = """
    UPDATE agent_journal
    SET is_expired = TRUE, expired_at = NOW(), updated_at = NOW()
    WHERE chat_id = $1 AND is_expired = FALSE
      AND expires_at IS NOT NULL AND expires_at <= NOW()
"""


async def maybe_compress_journal(db, llm, chat_id: str, entry_type: str | None = None) -> dict | None:
    """
    Проверяет порог живых обычных записей под chat_id и при превышении
    сворачивает наименее важные записи в архив + summary-запись.

    Вызывать после каждого успешного INSERT в journal_handler.
    None — сжатие не требовалось; {"compressed": True, ...} — выполнено;
    {"compressed": False, "reason": ...} — пропущено, журнал не изменён.
    """
    if entry_type in SKIP_TYPES:
        # identity — правила поведения, не сворачиваем.
        # summary — результат сжатия, повторно не сжимаем (рекурсия).
        return None

    await db.pool.execute(EXPIRE_DUE_SQL, chat_id)

    # Порядок «важности»: приоритет, затем свежесть. Голова списка остаётся.
    rows = await db.pool.fetch("""
        SELECT id, entry_type, content, priority, created_at
        FROM agent_journal
        WHERE chat_id = $1 AND entry_type <> ALL($2::text[])
          AND is_archived = FALSE AND is_expired = FALSE
        ORDER BY priority ASC, created_at DESC, id DESC
    """, chat_id, list(SKIP_TYPES))

    if len(rows) <= COMPRESSION_THRESHOLD:
        return None

    to_compress = sorted(rows[KEEP_RECENT:], key=lambda r: (r["created_at"], r["id"]))
    compressed_ids = [r["id"] for r in to_compress]
    compressed_types = sorted({r["entry_type"] for r in to_compress})

    # Сводок после вставки новой должно остаться не больше MAX_SUMMARIES:
    # самые свежие MAX_SUMMARIES - 1 остаются, остальные вливаются в новую.
    summaries = await db.pool.fetch("""
        SELECT id, content, created_at
        FROM agent_journal
        WHERE chat_id = $1 AND entry_type = 'summary'
          AND is_archived = FALSE AND is_expired = FALSE
        ORDER BY created_at DESC, id DESC
    """, chat_id)
    to_merge = sorted(summaries[MAX_SUMMARIES - 1:], key=lambda r: (r["created_at"], r["id"]))
    merged_ids = [r["id"] for r in to_merge]

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(to_compress, to_merge)},
        ])
        summary_text, archive_map = parse_compression_response(_response_text(response), compressed_ids)
    except Exception as e:
        # Не роняем insert и ничего не трогаем: попробуем при следующем insert.
        log.warning(
            f"[journal_compression] пропуск chat_id={chat_id}: {type(e).__name__}: {e}"
        )
        return {"compressed": False, "reason": f"{type(e).__name__}: {e}"}

    try:
        summary_id = await _apply(db, chat_id, compressed_ids, compressed_types,
                                  archive_map, summary_text, merged_ids)
    except _Race:
        log.warning(f"[journal_compression] гонка chat_id={chat_id}, откат")
        return {"compressed": False, "reason": "записи изменились параллельно"}

    log.info(
        f"[journal_compression] chat_id={chat_id}: {len(compressed_ids)} записей в архив, "
        f"сводок влито {len(merged_ids)}, summary_id={summary_id}"
    )
    return {
        "compressed": True,
        "compressed_count": len(compressed_ids),
        "archived_ids": compressed_ids,
        "merged_summary_ids": merged_ids,
        "summary_id": summary_id,
    }


async def _apply(db, chat_id, compressed_ids, compressed_types, archive_map, summary_text,
                 merged_ids=()) -> int:
    """Одна транзакция: архивирует исходники и старые сводки, вставляет новую сводку."""
    merged_ids = list(merged_ids)
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            # Если параллельный insert уже свернул часть этих записей,
            # обновится меньше строк — откатываем всю транзакцию.
            archived = await conn.fetch("""
                UPDATE agent_journal j
                SET is_archived = TRUE, archive_summary = s.summary, updated_at = NOW()
                FROM unnest($1::bigint[], $2::text[]) AS s(id, summary)
                WHERE j.id = s.id AND j.chat_id = $3
                  AND j.is_archived = FALSE AND j.is_expired = FALSE
                RETURNING j.id
            """, compressed_ids, [archive_map[i] for i in compressed_ids], chat_id)

            if len(archived) != len(compressed_ids):
                raise _Race()

            summary_id = await conn.fetchval("""
                INSERT INTO agent_journal (chat_id, entry_type, content, context, priority,
                                           created_at, updated_at)
                VALUES ($1, 'summary', $2, $3, 2, NOW(), NOW())
                RETURNING id
            """, chat_id, summary_text, json.dumps({
                "source": "auto_compression",
                "chat_id": chat_id,
                "compressed_entry_types": compressed_types,
                "compressed_count": len(compressed_ids),
                "compressed_ids": compressed_ids,
                "merged_summary_ids": merged_ids,
                "archived": True,
            }))

            if merged_ids:
                merged = await conn.fetch("""
                    UPDATE agent_journal
                    SET is_archived = TRUE, archive_summary = $2, updated_at = NOW()
                    WHERE id = ANY($1::bigint[]) AND chat_id = $3
                      AND entry_type = 'summary' AND is_archived = FALSE
                    RETURNING id
                """, merged_ids, f"Сводка журнала, влита в сводку #{summary_id}.", chat_id)
                if len(merged) != len(merged_ids):
                    raise _Race()

            return summary_id
