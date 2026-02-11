"""
Точка входа для контейнера парсеров.
Запускает все парсеры, сохраняет результаты в локальную папку.

Использование:
    python app/run_parsers.py

Переменные окружения:
    DATE_RANGE_START  — начало диапазона (YYYY-MM-DD), по умолчанию 2026-01-01
    DATE_RANGE_END    — конец диапазона (YYYY-MM-DD), по умолчанию 2026-01-30
    STORAGE_PATH      — корневая папка хранилища, по умолчанию ./knowledge_base
    ARTICLES_FOLDER   — подпапка для статей, по умолчанию articles
    METADATA_FILE     — имя файла метаданных, по умолчанию metadata.json
    IEEE_LINK         — ссылка для IEEE парсера
    SCHOLAR_MAX_PAGES — кол-во страниц Scholar, по умолчанию 2
    LOG_LEVEL         — уровень логирования, по умолчанию INFO
"""

import os
import sys
import asyncio
import time
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Импорты из существующего кода парсеров
from agent_pm.pm_parser.base_pipeline import ParserPipeline
from agent_pm.pm_parser.parsers import (
    ARXIVParser,
    FluxiconParser,
    ScholarParser,
    IEEEParser,
    PipelineConfig,
    ArticleStorage,
    setup_logging,
)


DATE_RANGE_START = os.getenv("DATE_RANGE_START", "2026-01-01")
DATE_RANGE_END = os.getenv("DATE_RANGE_END", "2026-01-30")

STORAGE_PATH = os.getenv("STORAGE_PATH", "./knowledge_base")
ARTICLES_FOLDER = os.getenv("ARTICLES_FOLDER", "articles")
METADATA_FILE = os.getenv("METADATA_FILE", "metadata.json")

IEEE_LINK = os.getenv(
    "IEEE_LINK",
    "https://ieeexplore.ieee.org/search/searchresult.jsp?"
    "queryText=Process%20Mining&highlight=true&returnType=SEARCH"
    "&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1"
    "&refinements=PublicationTopics:Process%20Mining"
    "&ranges=2025_2025_Year&returnFacets=ALL",
)

SCHOLAR_MAX_PAGES = int(os.getenv("SCHOLAR_MAX_PAGES", "2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def build_parsers(date_range: tuple):
    """Собирает список парсеров из конфигурации."""
    articles_dir = Path(STORAGE_PATH) / ARTICLES_FOLDER

    cfg = PipelineConfig(
        max_pages=SCHOLAR_MAX_PAGES,
        md_dir=articles_dir,
        report_json=Path(STORAGE_PATH) / "metadata_scholar.json",
        profile_dir=Path("./pw_profile_scholar"),
        path_logs=Path("./logs"),
    )

    return [
        ARXIVParser(date_range),
        FluxiconParser(date_range),
        ScholarParser(date_range=date_range, config=cfg),
        IEEEParser(IEEE_LINK, date_range=date_range),
    ]


async def main():
    logger = logging.getLogger("parser.run")

    date_range = (DATE_RANGE_START, DATE_RANGE_END)
    logger.info("Диапазон дат: %s — %s", DATE_RANGE_START, DATE_RANGE_END)

    # Собираем парсеры
    parsers = build_parsers(date_range)
    logger.info("Парсеры: %s", [p.name for p in parsers])

    # Хранилище
    articles_dir = os.path.join(STORAGE_PATH, ARTICLES_FOLDER)
    metadata_path = os.path.join(STORAGE_PATH, METADATA_FILE)

    storage = ArticleStorage(
        articles_dir=articles_dir,
        metadata_path=metadata_path,
    )

    # Запуск пайплайна
    pipeline = ParserPipeline(parsers)
    saved_counts = await pipeline.run_and_save(storage=storage)

    # Итоги
    total = sum(saved_counts.values())
    logger.info("=" * 50)
    logger.info("Парсинг завершён. Результаты:")
    for parser_name, count in saved_counts.items():
        logger.info("  %s: %d новых статей", parser_name, count)
    logger.info("  Всего сохранено: %d", total)
    logger.info("=" * 50)

    return saved_counts


if __name__ == "__main__":
    setup_logging(
        logs_dir=Path("./logs"),
        log_file="parsers.log",
        level=LOG_LEVEL,
        also_console=True,
    )

    t0 = time.perf_counter()

    try:
        result = asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("parser.run").warning("Прервано пользователем")
        sys.exit(1)
    except Exception:
        logging.getLogger("parser.run").exception("Критическая ошибка пайплайна")
        sys.exit(1)

    elapsed = time.perf_counter() - t0
    logging.getLogger("parser.run").info("Общее время: %.1f сек", elapsed)
