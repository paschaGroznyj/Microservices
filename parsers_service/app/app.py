import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import asdict
from pydantic import BaseModel
from fastapi import FastAPI, Response, HTTPException, Request
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import logging
import httpx
from datetime import datetime, timedelta
from pathlib import Path
import json
import re

from core.db import DataBase
from core.repository import ResearchRepository
from parsers.arxiv_all import start_search
# from parsers.scholar_parser import PipelineConfig, ScholarParser
# from parsers.ieee_parser_pw import IEEEParser
# from parsers.fluxicon_parser import FluxiconParser
from parsers.arxiv_parser import ARXIVParser
# from parsers.ieee_parser import IEEEParser

from parsers.yandex_disk_parser import YandexDiskParser
from botocore_client import OBSWorker

# Конфигурация
from core.config import (
    PORT_PARSERS_SERVICE,
    PORT_LLM_CONSUMER,
    ARTICLES_FOLDER,
    PDFS_FOLDER,
    STORAGE_PATH,
    METADATA_FILE,
    SYNC_INTERVAL_SCHEDULER
)


db = DataBase()
research = ResearchRepository(db)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

parsing_active = False

# Пути
base_dir = Path(__file__).resolve().parent
ARTICLES_DIR = base_dir / STORAGE_PATH / ARTICLES_FOLDER
PDFS_DIR = base_dir / STORAGE_PATH / PDFS_FOLDER
METADATA_PATH = base_dir / STORAGE_PATH / METADATA_FILE
STATE_FILE = Path("logs") / "state.json"




# Вместо dataclass используем BaseModel
class FavoriteStatus(BaseModel):
    username: str
    article_id: int
    is_favorite: bool

obs = OBSWorker()

PROCESS_MINING_KEYWORDS = [
    "process mining",
    "process discovery",
    "event logs",
    "process model",
    "audit trail",
    "conformance checking",
    "process enhancement",
    "root cause analysis",
    "process monitoring",
    "compliance checking",
    "deviation analysis",
    "continuous auditing",
    "business process analysis",
    "process intelligence",
    "task mining",
    "operational analytics",
    "financial auditing",
    "internal audit",
    "process compliance",
    "audit log analysis",
]


escaped_keywords = [re.escape(kw).replace(r"\ ", r"\s+") for kw in PROCESS_MINING_KEYWORDS]
pattern_str = "|".join(escaped_keywords)

FULL_PATTERN = re.compile(f"{pattern_str}", re.IGNORECASE)

def is_process_mining_related(text: Optional[str]) -> bool:
    """Проверяет наличие ключевых слов Process Mining в тексте."""
    if not text:
        return False
    return bool(FULL_PATTERN.search(text))

def load_last_end_date() -> datetime:
    """Загружает последнюю обработанную дату. Если нет — возвращает сегодня минус 14 дней."""
    if not STATE_FILE.exists():
        # Первый запуск: начать с 14 дней назад
        return datetime.today() - timedelta(days=14)

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
    os.makedirs(PDFS_DIR, exist_ok=True)

    existing_meta = _read_existing_metadata()
    new_articles = []
    skipped_count = 0
    filtered_count = 0

    for art in articles:
        external_id = art["external_id"]
        if external_id in existing_meta:
            logger.info(f"Пропускаем дубликат: {external_id} ({art.get('title', 'N/A')})")
            continue
        text_body = art.get("text_article", "")
        abstract_body = art.get("abstract", "")

        combined_text = f"{abstract_body}\n{text_body}"

        if not is_process_mining_related(combined_text):
            logger.debug(f"Отфильтровано (не PM): {external_id} - {art.get('title', 'N/A')}")
            filtered_count += 1
            continue
        logger.info("Проходим дальше по статье")
        filename = art["file_article"]
        if not filename:
            filename = f"{external_id}.md"
        article_path = ARTICLES_DIR / filename

        with open(article_path, "w", encoding="utf-8") as f:
            f.write(art.get("text_article", ""))

        # Сохраняем оригинальный PDF на диск
        pdf_bytes = art.get("pdf_bytes", b"")
        if pdf_bytes:
            pdf_filename = Path(filename).stem + ".pdf"
            pdf_path = PDFS_DIR / pdf_filename
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            logger.info(f"Сохранён PDF: {pdf_filename}")

        meta_entry = {k: v for k, v in art.items() if k not in ("text_article", "pdf_bytes")}
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
            return []


async def perform_parsing_cycle():
    global parsing_active
    try:
        parsing_active = True
        logger.info("=== Начало цикла синхронизации ===")

        # # # 1. Скачиваем существующие данные из OBS
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

        date_range = (
            start_date.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d")
        )


        # date_range = ('2026-01-01', '2026-03-24')
        logger.info(f"Парсинг диапазона: {date_range}")
        link_ieee = (
            "https://ieeexplore.ieee.org/search/searchresult.jsp?"
            "queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&"
            "refinementName=Publication%20Topics&pageNumber=1&"
            "refinements=PublicationTopics:Process%20Mining&"
            "ranges=2026_2026_Year&returnFacets=ALL"
        )
        cfg = PipelineConfig()
        cfg.max_pages = 60

        parsers = [
            ARXIVParser(date_range),
            # FluxiconParser(date_range), # Почему то умер
            ScholarParser(date_range=date_range, config=cfg),
            IEEEParser(link_ieee, date_range=date_range),
        ]

        pipeline = ParserPipeline(parsers)
        raw_articles = await pipeline.run()

        logger.info(f"Обработка {len(raw_articles)} статей...")
        _save_articles_and_metadata(raw_articles)

        save_last_end_date(end_date)

        logger.info("Загрузка результатов в OBS...")
        await obs.upload_all_md()
        await obs.upload_all_pdfs()
        await obs.put_json()

        logger.info("=== Цикл синхронизации завершён ===")

        try:
            async with httpx.AsyncClient() as client:
                logger.info(SERVICES['agent_service']['url'])
                response = await client.post(
                    f"{SERVICES['agent_service']['url']}/make_reviews",
                    json={"status": "success"})
                response.raise_for_status()

                logger.info(f"Агент подтвердил задачу: {response.json()}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Агент вернул ошибку: {e.response.status_code} - {e.response.text}")
            raise HTTPException(status_code=502, detail=f"Agent service reported error: {e.response.text}")
        except httpx.RequestError as e:
            logger.error(f"Не удалось связаться с агентом: {e}")
            raise HTTPException(status_code=503, detail=f"Agent service unreachable: {str(e)}")


    except Exception as e:
        logger.exception(f"Критическая ошибка в цикле парсинга: {e}")
    finally:
        parsing_active = False


async def scheduled_parsing_loop():
    while True:
        # interval = int(SYNC_INTERVAL_SCHEDULER)
        interval = int(SYNC_INTERVAL_SCHEDULER) * 60 * 24
        logger.info(f"== Интервал == {interval}")
        await perform_parsing_cycle()
        await asyncio.sleep(interval)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # logger.info("Запуск фоновой задачи парсинга...")
    # task = asyncio.create_task(scheduled_parsing_loop())
    # yield
    # logger.info("Завершение работы сервиса...")
    # task.cancel()
    # try:
    #     await task
    # except asyncio.CancelledError:
    #     pass

    global parsing_active
    # Startup
    parsing_active = True
    await db.init_db_pool()
    yield
    # Shutdown
    parsing_active = False
    await db.pool.close()


app = FastAPI(title="Parsers Service", lifespan=lifespan)

SERVICES = {
    "agent_service": {
        "url": f"http://agent_service:{PORT_LLM_CONSUMER}"
    }
}

@app.post("/run")
async def run_parsing():
    if parsing_active:
        return {"status": "already_running"}
    asyncio.create_task(perform_parsing_cycle())
    return {"status": "started"}

@app.post("/run_icpm")
async def run_icpm():
    """Разовый запуск парсера ICPM с Яндекс.Диска."""
    if parsing_active:
        return {"status": "already_running"}

    async def _run_icpm():
        global parsing_active
        try:
            parsing_active = True
            logger.info("=== Разовый запуск ICPM парсера ===")

            await obs.download_all_md()
            await obs.download_metadata()

            icpm_url = os.getenv("YANDEX_DISK_ICPM_URL", "https://disk.yandex.ru/d/t3YC0BQjOA1sBA")
            icpm_date = os.getenv("ICPM_CONFERENCE_DATE", "2025-10-01")
            date_range = (icpm_date, icpm_date)

            parser = YandexDiskParser(public_url=icpm_url, date_range=date_range, conference_name="ICPM")
            pipeline = ParserPipeline([parser])
            raw_articles = await pipeline.run()

            logger.info(f"ICPM: обработка {len(raw_articles)} статей...")
            _save_articles_and_metadata(raw_articles)

            await obs.upload_all_md()
            await obs.upload_all_pdfs()
            await obs.put_json()

            logger.info("=== ICPM парсинг завершён ===")

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{SERVICES['agent_service']['url']}/make_reviews",
                        json={"status": "success"})
                    response.raise_for_status()
                    logger.info(f"Агент подтвердил задачу: {response.json()}")
            except Exception as e:
                logger.error(f"Не удалось уведомить агента: {e}")

        except Exception as e:
            logger.exception(f"Ошибка ICPM парсинга: {e}")
        finally:
            parsing_active = False

    asyncio.create_task(_run_icpm())
    return {"status": "icpm_started"}


@app.post("/favorites")
async def search_articles(data: FavoriteStatus):
    return await research.toggle_favorite(data.username, data.article_id, data.is_favorite)
    logger.info(f"{data.username}, {data.article_id}, {data.is_favorite}")
@app.post("/search/articles")
async def search_articles(request: Dict[str, Any]):
    return await start_search(request, db)

@app.post("/favorites/select")
async def select_favorites(request: Dict[str, Any]):
    username = request['username']
    rows = await research.get_favorites(username)

    articles = []
    for row in rows:
        articles.append({
            "article_id": row["article_id"],
            "external_id": row["external_id"],
            "title": row["title"],
            "date": row["date_sub"],  # ← единое название как у парсера
            "link": row["url_article"],  # ← единое название
            "authors": json.loads(row["authors_json"]),  # строка → список
            "tags": json.loads(row["tags_json"]),  # строка → список
            "abstract": row["abstract"],
            "category": row["category"],
        })

    return {"articles": articles}

@app.get("/health")
async def health():
    status = "active" if parsing_active else "idle"
    return {"service": "parsers_service", "status": status}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(PORT_PARSERS_SERVICE), log_level="info")
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PARSERS_SERVICE), log_level="info")