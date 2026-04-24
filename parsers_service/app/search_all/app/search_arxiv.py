import os
import re
import io
import json
import time
import asyncio
import random
import hashlib
import logging
import multiprocessing as mp
from io import BytesIO
from pathlib import Path
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any, Tuple, Union, Literal
from repository import ResearchRepository
import urllib.parse
import xml.etree.ElementTree as ET

from agent_provider import get_model
from langchain.schema import SystemMessage, HumanMessage

import requests
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

try:
    import fitz
except Exception:
    fitz = None

try:
    from weasyprint import HTML
except Exception:
    HTML = None


load_dotenv()


# =========================
# Models
# =========================

@dataclass
class SearchParams:
    user_query: str
    topic: str
    keywords: List[str]
    date_start: str
    date_end: str
    max_articles_total: int = 20
    max_per_source: int = 20
    sources: List[str] = field(default_factory=lambda: ["arxiv", "scholar", "ieee", "fluxicon"])
    review_language: str = "en"
    tags_: List[str] = field(default_factory=lambda: [])


@dataclass
class PipelineConfig:
    output_json_path: Path = Path(os.getenv("PIPELINE_OUTPUT_JSON", "./pipeline_results.json"))
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "45"))
    max_pdf_pages_extract: int = int(os.getenv("MAX_PDF_PAGES_EXTRACT", "10"))
    max_pdf_pages_convert: int = int(os.getenv("MAX_PDF_PAGES_CONVERT", "30"))
    headless: bool = os.getenv("HEADLESS", "true").lower() == "true"
    fulltext: bool = os.getenv("SCHOLAR_FULLTEXT", "false").lower() == "true"
    ui_lang: str = os.getenv("UI_LANG", "en")
    sleep_between_downloads: float = float(os.getenv("SLEEP_BETWEEN_DOWNLOADS", "1.2"))
    download_retries: int = int(os.getenv("DOWNLOAD_RETRIES", "2"))
    download_retry_sleep: float = float(os.getenv("DOWNLOAD_RETRY_SLEEP", "1.0"))
    download_total_timeout_s: float = float(os.getenv("DOWNLOAD_TOTAL_TIMEOUT_S", "40.0"))
    max_bytes: int = int(os.getenv("MAX_BYTES", "30000000"))
    convert_timeout_s: float = float(os.getenv("CONVERT_TIMEOUT_S", "30.0"))
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_path: Path = Path(os.getenv("LOG_PATH", "./research_pipeline.log"))
    verify_ssl: bool = os.getenv("VERIFY_SSL", "true").lower() == "true"


@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: Dict[str, str] | None
    tags: List[str] | str | None
    abstract: str
    file_article: str | None
    category: str | None
    text_article: str
    pdf_bytes: bytes = b""


# =========================
# Utils
# =========================

def setup_logger(cfg: PipelineConfig) -> logging.Logger:
    logger = logging.getLogger("research_pipeline")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    logger.propagate = False
    # handler = logging.FileHandler(cfg.log_path, encoding="utf-8")
    # handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    # logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(stream)
    return logger


def url_to_hash(url: str, length: int = 12) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]


def safe_title(text: str) -> str:
    text = re.sub(r'[<>:"/\\\\|?*\x00-\x1f]', "_", text or "untitled")
    return re.sub(r'[_\s]+', "_", text).strip("_")


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def deduplicate_articles(items: List[Article]) -> List[Article]:
    seen = set()
    result = []
    for art in items:
        key = art.external_id or art.url_article or art.title.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(art)
    return result


def truncate_text(text: str, limit: int = 12000) -> str:
    text = text or ""
    return text[:limit]


def ensure_pdf_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://arxiv.org" + url
    return url


# =========================
# LLM Client
# =========================

class LLMClient:
    def __init__(self):
        
        self.gigachat = get_model()

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        response = self.gigachat.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ])
        return response.content.strip()

    def interpret_query(self, user_query: str) -> SearchParams:
        today = date.today()
        default_start = today - timedelta(days=180)

        system_prompt = """Ты помогаешь строить поисковый запрос для поиска научных и отраслевых статей.
Верни только JSON без пояснений.
Обязательный формат:
{
  "topic": "string",
  "keywords": ["string", "string"],
  "date_start": "YYYY-MM-DD",
  "date_end": "YYYY-MM-DD",
  "max_articles_total": 12,
  "max_per_source": 5,
  "sources": ["arxiv", "scholar", "ieee", "fluxicon"],
  "review_language": "en",
  "tags_": ["string", "string"]
}
Правила:
- Если пользователь не указал даты, используй последние 180 дней.
- keywords должны быть короткими и пригодными для поиска.
- Если тема техническая, сохраняй английские термины.
- sources выбирай из: arxiv, scholar, ieee, fluxicon.
- Для общих тем process mining включай fluxicon, для CS/AI включай arxiv, scholar, ieee.
- Не добавляй markdown.
- Все делай на английском, т.е. для keywords и topic используй английский.

Пример 1: "Что нового по computer vision в плане детекции":
{'user_query': 'Что нового по computer vision в плане детекции', 'topic': 'detection in computer vision', 'keywords': ["computer vision", "detection", "Pattern Recognition"],
 'date_start': '2025-10-24', 'date_end': '2026-04-22',
 'max_articles_total': 12, 'max_per_source': 5, 'sources': 
['arxiv', 'scholar', 'ieee', 'fluxicon'], 'review_language': 'en',
'tags': ['Computer Vision', 'Pattern Recognition', 'Machine Learning']}
Пример 2: "Что нового по computer vision в плане детекции на сайте arxiv":
{'user_query': 'Что нового по computer vision в плане детекции', 'topic': 'detection in computer vision', 'keywords': ["computer vision", "detection", "Pattern Recognition"],
 'date_start': '2025-10-24', 'date_end': '2026-04-22',
 'max_articles_total': 12, 'max_per_source': 5, 'sources': 
['arxiv'], 'review_language': 'en',
'tags_': ['Computer Vision', 'Pattern Recognition', 'Machine Learning']}
"""

        user_prompt = f"""Пользовательский запрос: {user_query}
Текущая дата: {today.isoformat()}
Дата по умолчанию с: {default_start.isoformat()}
"""

        try:
            raw = self._chat(system_prompt, user_prompt)
            data = json.loads(raw)
            return SearchParams(
                user_query=user_query,
                topic=data.get("topic", user_query),
                keywords=data.get("keywords", [user_query]),
                date_start=data.get("date_start", default_start.isoformat()),
                date_end=data.get("date_end", today.isoformat()),
                max_articles_total=int(data.get("max_articles_total", 12)),
                max_per_source=int(data.get("max_per_source", 5)),
                sources=data.get("sources", ["arxiv", "scholar", "ieee", "fluxicon"]),
                review_language=data.get("review_language", "en"),
                tags_ = data.get("tags_", [])
                # obsidian_folder=data.get("obsidian_folder", f"Research/AutoReviews/{safe_title(user_query.lower())}")
            )
        except Exception:
            return SearchParams(
                user_query=user_query,
                topic=user_query,
                keywords=[user_query],
                date_start=default_start.isoformat(),
                date_end=today.isoformat(),
                max_articles_total=12,
                max_per_source=5,
                sources=["arxiv", "scholar", "ieee", "fluxicon"],
                review_language="en",
                tags_ = []
                # obsidian_folder=f"Research/AutoReviews/{safe_title(user_query.lower())}"
            )

    def generate_review(self, article: Article, search_params: SearchParams) -> str:
        authors_str = ", ".join(article.authors.keys()) if article.authors else "Не указаны"
        tags_str = ", ".join(article.tags) if isinstance(article.tags, list) else (article.tags or "")
        source_text = article.text_article or article.abstract or ""
        # print(source_text)

        system_prompt = """Ты делаешь структурированный review статьи на русском языке.
Верни только markdown.
Структура строго такая:
# {title}

## Метаданные
- Авторы: ...
- Дата: ...
- Источник: ...
- Теги: ...
- Ссылка: ...
- PDF: ...

## Цель статьи

## Какая проблема решается

## Какие задачи ставят авторы

## Подход

## Как реализуется подход

## Пример из статьи

## Ограничения

## Выводы

## Краткая заметка для себя
2-4 пункта, коротко, в практическом стиле.

Правила:
- Не выдумывай факты, которых нет в тексте.
- Все пиши на русском языке, кроме определений, названий инструментов и т.д. - их в оригинале оставляй.
- Если информации недостаточно, пиши "Не удалось достоверно извлечь из доступного текста".
- Пиши ясно, сжато и по делу.
- Не добавляй вступление и заключение вне шаблона.
"""

        user_prompt = f"""
Тема поиска: {search_params.topic}
Язык review: {search_params.review_language}

Название: {article.title}
Авторы: {authors_str}
Дата: {article.date_sub}
Источник: {article.source}
Теги: {tags_str}
Ссылка на статью: {article.url_article}
PDF: {article.pdf_url}

Аннотация:
{truncate_text(article.abstract, 5000)}

Текст статьи / фрагмент:
{truncate_text(source_text, 12000)}
"""
        return self._chat(system_prompt, user_prompt)


# =========================
# Base parser
# =========================

class BaseParser:
    name = "base"

    async def arun(self, params: SearchParams) -> List[Article]:
        return await asyncio.to_thread(self.run, params)

    def run(self, params: SearchParams) -> List[Article]:
        raise NotImplementedError


# =========================
# arXiv parser
# =========================

class ArxivParser(BaseParser):
    name = "arxiv"

    def __init__(self, cfg: PipelineConfig, logger: logging.Logger):
        self.cfg = cfg
        self.logger = logger
        self.base_url = "http://export.arxiv.org/api/query?"
        self.headers = {"User-Agent": cfg.user_agent}
        self.months = {
            'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }
    
    def build_arxiv_query(self, keywords: List[str], start_date: str, end_date: str) -> str:
        parts = []
        # Keywords: (all:kw1 OR all:kw2 OR ...)
        if keywords:
            kw_terms = [f"all:{kw.replace(' ', '+')}" for kw in keywords]
            parts.append(f"({' OR '.join(kw_terms)})")
        # Date range
        date_part = f"submittedDate:[{start_date.replace('-', '')}0000 TO {end_date.replace('-', '')}2359]"
        parts.append(date_part)
        search_query = " AND ".join(parts)
        return f"search_query={requests.utils.quote(search_query)}&start=0&max_results=100"
    
    def parse_with_lxml(self, xml_content: bytes) -> List[Dict]:
        root = ET.fromstring(xml_content)
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'arxiv': 'http://arxiv.org/schemas/atom',
        }
        entries = []
        for entry in root.findall('.//atom:entry', ns):
            article = {
                "external_id": entry.find('atom:id', ns).text.rsplit('/', 1)[-1].rsplit('v', 1)[0],
                "title": entry.find('atom:title', ns).text or '',
                "date_sub": entry.find('atom:published', ns).text[:10],
                "abstract": entry.find('atom:summary', ns).text or '',
                "authors": {
                    author.find('atom:name', ns).text: ""
                    for author in entry.findall('atom:author', ns)
                },
                "tags": [cat.get('term') for cat in entry.findall('atom:category', ns)],
                "category": (
                    entry.find('arxiv:primary_category', ns).get('term')
                    if entry.find('arxiv:primary_category', ns) is not None
                    else None
                ),
                "pdf_url": None,
                "url_article": None,
            }
            # Links
            for link in entry.findall('atom:link', ns):
                if link.get('title') == 'pdf':
                    article["pdf_url"] = link.get('href')
                elif link.get('rel') == 'alternate':
                    article["url_article"] = link.get('href')
            entries.append(article)
        return entries

    def _convert_date(self, raw: str) -> Tuple[str, date]:
        submitted_date = raw.split(";")[0]
        parts = submitted_date.split(" ")
        day = parts[1].zfill(2)
        month = str(self.months[parts[2].strip(",")]).zfill(2)
        year = parts[3]
        iso = f"{year}-{month}-{day}"
        return iso, parse_iso_date(iso)

    def _parse_article_page(self, url_article: str) -> str:
        try:
            r = requests.get(url_article, headers=self.headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            block = soup.find("blockquote")
            return block.text.strip() if block else ""
        except Exception as e:
            self.logger.warning(f"arXiv abstract parse failed: {url_article} | {e}")
            return ""

    def _get_pdf_text(self, pdf_url: str) -> Tuple[str, bytes]:
        try:
            r = requests.get(pdf_url, headers=self.headers, timeout=30)
            r.raise_for_status()
            pdf_bytes = r.content
            reader = PdfReader(BytesIO(pdf_bytes))
            text = []
            for page in reader.pages[: self.cfg.max_pdf_pages_extract]:
                text.append(page.extract_text() or "")
            return "\n".join(text), pdf_bytes
        except Exception as e:
            self.logger.warning(f"arXiv pdf parse failed: {pdf_url} | {e}")
            return "", b""

    def run(self, params: SearchParams) -> List[Article]:
        start_date = params.date_start
        end_date = params.date_end
        keywords = params.keywords
        results: List[Article] = []

        query_str = self.build_arxiv_query(keywords, start_date, end_date)
        url = f"{self.base_url}{query_str}&sortBy=submittedDate&sortOrder=descending"

        try:
            r = requests.get(url, headers=self.headers, timeout=self.cfg.http_timeout)
            r.raise_for_status()
            xml_data = r.content
        except Exception as e:
            self.logger.exception(f"arXiv API request failed: {e}")
            return []
        
        api_articles = self.parse_with_lxml(xml_data)


        for raw in api_articles:
            if len(results) >= params.max_per_source:
                break

            # Стандартные поля из API
            external_id = raw["external_id"]
            title = safe_title(raw["title"])
            date_sub = raw["date_sub"]
            abstract = raw["abstract"]
            authors = raw["authors"] or {}
            tags = raw["tags"] or []
            category = raw["category"]
            pdf_url = raw["pdf_url"] or ""
            url_article = raw["url_article"] or ""

            # Формируем file_article и text_article как в оригинальном codebase
            text_article, pdf_bytes = self._get_pdf_text(pdf_url) if pdf_url else ("", b"")

            results.append(Article(
                source="https://arxiv.org",
                external_id=external_id,
                title=title,
                date_sub=date_sub,
                url_article=url_article,
                pdf_url=pdf_url,
                authors=authors,
                tags=tags,
                abstract=abstract,
                file_article=f"{date_sub}_{url_to_hash(url_article or title)}.md",
                category=category,
                text_article=text_article,
                pdf_bytes=pdf_bytes,
            ))
        return results


# =========================
# Pipeline
# =========================

class ResearchPipeline:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.logger = setup_logger(cfg)
        self.llm = LLMClient()
        self.repo = ResearchRepository()
        self.parsers = {
            "arxiv": ArxivParser(cfg, self.logger),
        }

    async def collect_articles(self, params: SearchParams) -> List[Article]:
        collected: List[Article] = []
        for source in params.sources:
            parser = self.parsers.get(source)
            if not parser:
                self.logger.warning(f"Unknown parser source: {source}")
                continue
            try:
                self.logger.info(f"Running parser: {source}")
                items = await parser.arun(params)
                collected.extend(items)
                self.logger.info(f"Parser {source} returned {len(items)} items")
            except Exception as e:
                self.logger.exception(f"Parser failed: {source} | {e}")
        collected = deduplicate_articles(collected)
        collected.sort(key=lambda x: x.date_sub or "", reverse=True)
        return collected[: params.max_articles_total]

    async def run(self, user_query: str) -> List[Dict[str, Any]]:
        params = self.llm.interpret_query(user_query)
        self.logger.info(f"Search params: {asdict(params)}")

        run_id = self.repo.create_run(
            user_query=params.user_query,
            topic=params.topic,
        )

        articles = await self.collect_articles(params)
        print(len(articles))
        saved = []

        for article in articles:
            try:
                article_id = self.repo.upsert_article(article)
                review_md = self.llm.generate_review(article, params)
                review_id = self.repo.save_review(
                    run_id=run_id,
                    article_id=article_id,
                    review_markdown=review_md,
                    review_language="ru",
                )
                saved.append({
                    "review_id": review_id,
                    "article_id": article_id,
                    "title": article.title,
                    "source": article.source,
                    "date": article.date_sub,
                    "article_url": article.url_article,
                    "pdf_url": article.pdf_url,
                })
            except Exception as e:
                self.logger.exception(f"Review/export failed for {article.title}: {e}")

        self.cfg.output_json_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.output_json_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        return saved


# =========================
# CLI
# =========================

async def main():
    query = input("Введите тему или вопрос для поиска статей: ").strip()
    if not query:
        print("Пустой запрос")
        return
    cfg = PipelineConfig()
    pipeline = ResearchPipeline(cfg)
    result = await pipeline.run(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
