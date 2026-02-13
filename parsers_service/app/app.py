import os
import json
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import asdict
from fastapi import FastAPI
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
import json


from parsers.scholar_parser import PipelineConfig, ScholarParser
from parsers.fluxicon_parser import FluxiconParser
from parsers.arxiv_parser import ARXIVParser
from parsers.ieee_parser import IEEEParser
from botocore_client import OBSWorker

# Конфигурация
from core.config import (
    PORT_PARSERS_SERVICE,
    ARTICLES_FOLDER,
    STORAGE_PATH,
    METADATA_FILE,
    SYNC_INTERVAL_SCHEDULER
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

parsing_active = False

# Пути
base_dir = Path(__file__).resolve().parent
ARTICLES_DIR = base_dir / STORAGE_PATH / ARTICLES_FOLDER
METADATA_PATH = base_dir / STORAGE_PATH / METADATA_FILE
STATE_FILE = Path("logs") / "state.json"


obs = OBSWorker()

def load_last_end_date() -> datetime:
    """Загружает последнюю обработанную дату. Если нет — возвращает сегодня минус 14 дней."""
    if not STATE_FILE.exists():
        # Первый запуск: начать с 14 дней назад
        return datetime.today() - timedelta(days=10)

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return datetime.strptime(data["last_end_date"], "%Y-%m-%d")
    except (json.JSONDecodeError, KeyError, ValueError):
        logger.warning("Некорректный state.json — используем дату по умолчанию")
        return datetime.today() - timedelta(days=14)


def save_last_end_date(end_date: datetime):
    """Сохраняет дату завершения текущего окна."""
    os.makedirs(STATE_FILE.parent, exist_ok=True)
    logger.info(STATE_FILE.parent)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_end_date": end_date.strftime("%Y-%m-%d")}, f, indent=2)


def _read_existing_metadata() -> Dict[str, Dict]:
    if not METADATA_PATH.exists():
        return {}
    try:
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["external_id"]: item for item in data}
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        logger.warning("Не удалось прочитать metadata.json — создаём новый")
        return {}

def _save_articles_and_metadata(articles: List[Dict]):
    """Сохраняет тексты статей и обновляет metadata.json без дубликатов и без text_article"""
    os.makedirs(ARTICLES_DIR, exist_ok=True)

    existing_meta = _read_existing_metadata()
    new_articles = []

    for art in articles:
        external_id = art["external_id"]
        if external_id in existing_meta:
            logger.info(f"Пропускаем дубликат: {external_id} ({art.get('title', 'N/A')})")
            continue

        filename = art["file_article"]
        if not filename:
            filename = f"{external_id}.md"
        article_path = ARTICLES_DIR / filename

        with open(article_path, "w", encoding="utf-8") as f:
            f.write(art.get("text_article", ""))

        meta_entry = {k: v for k, v in art.items() if k != "text_article"}
        existing_meta[external_id] = meta_arch = meta_entry
        new_articles.append(meta_arch)

    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(
            list(existing_meta.values()),
            f,
            ensure_ascii=False,
            indent=2
        )

    logger.info(f"Сохранено {len(new_articles)} новых статей")


class ParserPipeline:
    def __init__(self, parsers: List):
        self.parsers = parsers

    async def run(self) -> List[Dict]:
        tasks = [self._safe_run(parser) for parser in self.parsers]
        results = await asyncio.gather(*tasks)
        # logger.info(f"Тип возвращаемых объектов: {type(results)}")
        # for res in results:
        #     logger.info(f"Тема: {res}")
        # Собираем все Article-объекты из всех парсеров
        all_articles = []
        for result in results:
            # print("РЕЗУЛЬАТ ПАРСИНГА", result)
            all_articles.extend(result)

        return all_articles

    async def _safe_run(self, parser):
        try:
            logger.info(f"Запуск парсера: {parser.name}")
            res = await parser.arun()
            logger.info(f"Парсер {parser.name} завершён")
            return res
        except Exception as e:
            logger.exception(f"Парсер {parser.name} упал: {e}")
            return {"error": str(e), "source": parser.name}


async def perform_parsing_cycle():
    global parsing_active
    try:
        parsing_active = True
        logger.info("=== Начало цикла синхронизации ===")

        # # 1. Скачиваем существующие данные из OBS
        logger.info("Скачивание данных из OBS...")
        await obs.download_all_md()
        await obs.download_metadata()


        # 2. Определяем диапазон дат
        last_end = load_last_end_date()
        start_date = last_end + timedelta(days=1)
        end_date = start_date + timedelta(days=13)

        today = datetime.today()
        if start_date > today:
            logger.info("Диапазон выходит за пределы текущей даты — пропускаем")
            return

        end_date = min(end_date, today) # Чтобы не брать дни из будущего

        data_range = (
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )
        

        # data_range = ('2026-02-05', '2026-02-10')
        logger.info(f"Парсинг диапазона: {data_range}")
        link_ieee = (
            "https://ieeexplore.ieee.org/search/searchresult.jsp?"
            "queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&"
            "refinementName=Publication%20Topics&pageNumber=1&"
            "refinements=PublicationTopics:Process%20Mining&"
            "ranges=2026_2026_Year&returnFacets=ALL"
        )
        cfg = PipelineConfig()
        cfg.max_pages = 2

        parsers = [
            ARXIVParser(data_range),
            FluxiconParser(data_range),
            ScholarParser(date_range=data_range, config=cfg),
            IEEEParser(link_ieee)
        ]

        pipeline = ParserPipeline(parsers)
        raw_articles = await pipeline.run()

        logger.info(f"Обработка {len(raw_articles)} статей...")
        _save_articles_and_metadata(raw_articles)

        save_last_end_date(end_date)

        logger.info("Загрузка результатов в OBS...")
        await obs.upload_all_md()
        await obs.put_json()

        logger.info("=== Цикл синхронизации завершён ===")

    except Exception as e:
        logger.exception(f"Критическая ошибка в цикле парсинга: {e}")
    finally:
        parsing_active = False


async def scheduled_parsing_loop():
    while True:
        # interval = int(SYNC_INTERVAL_SCHEDULER) * 60 * 24 * 14
        interval = int(SYNC_INTERVAL_SCHEDULER) * 60 * 24 * 14
        logger.info(f"== Интервал == {interval}")
        await perform_parsing_cycle()
        await asyncio.sleep(interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск фоновой задачи парсинга...")
    task = asyncio.create_task(scheduled_parsing_loop())
    yield
    logger.info("Завершение работы сервиса...")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Parsers Service", lifespan=lifespan)


@app.get("/health")
async def health():
    status = "active" if parsing_active else "error"
    return {"service": "parsers_service", "status": status}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(PORT_PARSERS_SERVICE), log_level="info")