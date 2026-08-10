"""
migrate_reviews.py
──────────────────
Единоразовый скрипт: перекладывает ревью из локальных папок digests/
в таблицу reviews, привязывая каждый файл к papers_metadata по полю file_article.

Запуск:
    python migrate_reviews.py --digests-dir /path/to/digests [--dry-run]
"""

import asyncio
import argparse
import logging
import os
import sys
from pathlib import Path

import asyncpg

from dotenv import load_dotenv

load_dotenv()

# ── Настройки подключения — возьми из своего config или передай через env ──────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "your_db")

print(DB_HOST, DB_PASSWORD)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── DDL ────────────────────────────────────────────────────────────────────────

DDL_REVIEWS = """
CREATE TABLE IF NOT EXISTS reviews (
    id              BIGSERIAL PRIMARY KEY,
    external_id     TEXT NOT NULL REFERENCES papers_metadata(external_id) ON DELETE CASCADE,
    review_text     TEXT NOT NULL,
    category        TEXT,                       -- название папки-источника
    source_file     TEXT,                       -- оригинальное имя файла
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (external_id)                        -- одно ревью на статью
);
"""

DDL_ADD_FLAG = """
ALTER TABLE papers_metadata
    ADD COLUMN IF NOT EXISTS review_flag BOOLEAN NOT NULL DEFAULT FALSE;
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def collect_reviews(digests_dir: Path) -> list[dict]:
    """
    Обходит digests_dir.
    Файлы прямо в корне считаются категорией None (или 'root').
    Файлы в подпапках — категория = имя подпапки.

    Возвращает список:
        {
          "stem":     "2025-10-01_0cb5f8c3b2a6",   # без .md
          "filename": "2025-10-01_0cb5f8c3b2a6.md",
          "category": "PM_лог" | None,
          "text":     "...",
          "path":     Path(...),
        }
    """
    records = []

    for entry in digests_dir.rglob("*.md"):
        # категория = имя подпапки относительно корня digests
        rel = entry.relative_to(digests_dir)
        category = str(rel.parent) if str(rel.parent) != "." else None

        try:
            text = entry.read_text(encoding="utf-8")
        except Exception as e:
            log.warning(f"Не удалось прочитать {entry}: {e}")
            continue

        records.append(
            {
                "stem": entry.stem,          # "2025-10-01_0cb5f8c3b2a6"
                "filename": entry.name,      # "2025-10-01_0cb5f8c3b2a6.md"
                "category": category,
                "text": text,
                "path": entry,
            }
        )

    log.info(f"Найдено файлов: {len(records)}")
    return records


# ── Основная логика ────────────────────────────────────────────────────────────

async def migrate(digests_dir: Path, dry_run: bool = False):
    conn: asyncpg.Connection = await asyncpg.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME,
    )

    try:
        # 1. DDL
        if not dry_run:
            log.info("Применяю DDL...")
            await conn.execute(DDL_REVIEWS)
            await conn.execute(DDL_ADD_FLAG)
        else:
            log.info("[dry-run] DDL пропущен")

        # 2. Собираем файлы
        records = collect_reviews(digests_dir)
        if not records:
            log.warning("Файлов не найдено, выхожу.")
            return

        # 3. Загружаем все file_article из metadata для быстрого сопоставления
        #    file_article хранит что-то вроде "2025-10-01_0cb5f8c3b2a6.md"
        #    или просто "2025-10-01_0cb5f8c3b2a6" — проверяем оба варианта.
        rows = await conn.fetch(
            "SELECT external_id, file_article FROM papers_metadata WHERE file_article IS NOT NULL"
        )

        # строим два индекса: по stem и по полному filename
        by_stem: dict[str, str] = {}
        by_filename: dict[str, str] = {}
        for row in rows:
            fa: str = row["file_article"]
            eid: str = row["external_id"]
            p = Path(fa)
            by_stem[p.stem] = eid        # без расширения
            by_filename[p.name] = eid    # с расширением

        log.info(f"Записей в papers_metadata с file_article: {len(rows)}")

        # 4. Сопоставляем и вставляем
        inserted = skipped_no_match = skipped_exists = 0

        for rec in records:
            # ищем external_id
            external_id = (
                by_stem.get(rec["stem"])
                or by_filename.get(rec["filename"])
            )

            if not external_id:
                log.warning(
                    f"Нет совпадения в papers_metadata: {rec['path'].relative_to(digests_dir)}"
                )
                skipped_no_match += 1
                continue

            if dry_run:
                log.info(
                    f"[dry-run] {rec['filename']} → external_id={external_id} "
                    f"category={rec['category']}"
                )
                inserted += 1
                continue

            # Вставка с игнором дублей (UNIQUE external_id)
            result = await conn.execute(
                """
                INSERT INTO reviews (external_id, review_text, category, source_file)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (external_id) DO NOTHING
                """,
                external_id,
                rec["text"],
                rec["category"],
                rec["filename"],
            )

            # asyncpg возвращает "INSERT 0 1" или "INSERT 0 0"
            affected = int(result.split()[-1])
            if affected == 0:
                log.debug(f"Уже существует: {external_id}")
                skipped_exists += 1
            else:
                inserted += 1
                # Ставим флаг в metadata
                await conn.execute(
                    "UPDATE papers_metadata SET review_flag = TRUE WHERE external_id = $1",
                    external_id,
                )

        log.info(
            f"Готово. Вставлено: {inserted} | "
            f"Нет совпадения: {skipped_no_match} | "
            f"Уже было: {skipped_exists}"
        )

    finally:
        await conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Migrate review files to DB")
    parser.add_argument(
        "--digests-dir",
        default="digests",
        help="Путь к папке digests (default: ./digests)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать что будет сделано, без записи в БД",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    digests_dir = Path(args.digests_dir).expanduser().resolve()

    if not digests_dir.is_dir():
        log.error(f"Папка не найдена: {digests_dir}")
        sys.exit(1)

    asyncio.run(migrate(digests_dir, dry_run=args.dry_run))