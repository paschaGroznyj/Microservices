import os
import logging
import requests
import xml.etree.ElementTree as ET

from datetime import date, timedelta
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from core.db import DataBase
from core.repository import ResearchRepository
from fastapi import HTTPException

import asyncio
import httpx
import arxiv
from datetime import timezone

import random

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36',
    'ArticlesAnalyzer/1.0 (research tool; mailto:your@email.com)',
    'Python-requests/2.31.0',
]

@dataclass
class SearchParams:
    keywords: List[str]
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    limit: int = 10


@dataclass
class RequestPayload:
    user_name: str
    user_query: str
    request_id: int | None
    params: SearchParams


@dataclass
class Article:
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: List[str]
    tags: List[str]
    abstract: str
    category: Optional[str]
    source: str = "arxiv"


class ArxivLibClient:
    """Клиент через официальную библиотеку arxiv — с автоматическим rate limiting"""

    def __init__(self):
        self.client = arxiv.Client(
            page_size=100,
            delay_seconds=3,
            num_retries=3
        )

    def search(
        self,
        keywords: List[str],
        date_start: Optional[str],
        date_end: Optional[str],
        limit: int
    ) -> List[Article]:

        query = self._build_query(keywords)

        search = arxiv.Search(
            query=query,
            max_results=limit,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        articles = []
        for result in self.client.results(search):
            article = self._parse_result(result)

            # Фильтрация по датам на стороне клиента
            if date_start and article.date_sub < date_start:
                continue
            if date_end and article.date_sub > date_end:
                continue

            articles.append(article)

        return self._deduplicate(articles)

    def _build_query(self, keywords: List[str]) -> str:
        if not keywords:
            return "all:arxiv"
        terms = [f"all:{kw.strip()}" for kw in keywords if kw.strip()]
        return " OR ".join(terms)

    def _parse_result(self, result: arxiv.Result) -> Article:
        # external_id из entry_id — убираем версию
        # http://arxiv.org/abs/2605.02890v1 → 2605.02890
        external_id = result.entry_id.rsplit("/", 1)[-1].rsplit("v", 1)[0]

        # Дата публикации
        date_sub = ""
        if result.published:
            date_sub = result.published.astimezone(timezone.utc).strftime("%Y-%m-%d")

        # Авторы
        authors = [author.name for author in result.authors]

        # Теги / категории
        tags = list(result.categories) if result.categories else []

        # Основная категория
        category = result.primary_category if result.primary_category else (tags[0] if tags else None)

        # Ссылки
        url_article = result.entry_id  # основная ссылка
        pdf_url = ""
        for link in result.links:
            if link.title == "pdf":
                pdf_url = link.href

        return Article(
            external_id=external_id,
            title=(result.title or "").strip(),
            date_sub=date_sub,
            url_article=url_article,
            pdf_url=pdf_url,
            authors=authors,
            tags=tags,
            abstract=(result.summary or "").strip(),
            category=category,
        )

    def _deduplicate(self, articles: List[Article]) -> List[Article]:
        seen = set()
        result = []
        for article in articles:
            key = article.external_id or article.url_article or article.title.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            result.append(article)
        return result

class ArxivClient:
    BASE_URL = "https://export.arxiv.org/api/query?"

    def __init__(self):
        self.timeout = int(os.getenv("HTTP_TIMEOUT", "45"))
        # self.headers = {
        #     "User-Agent": os.getenv(
        #         "USER_AGENT",
        #         "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        #         "AppleWebKit/537.36 (KHTML, like Gecko) "
        #         "Chrome/120.0.0.0 Safari/537.36",
        #
        #     )
        # }
    
    def _get_headers(self):
        return {'User-Agent': random.choice(USER_AGENTS)}



    async def search(self, keywords: List[str], date_start: Optional[str], date_end: Optional[str], limit: int) -> List[Article]:
        date_start, date_end = self._resolve_dates(date_start, date_end)
        query = self._build_query(keywords, date_start, date_end, limit)
        url = f"{self.BASE_URL}{query}"
        
        content = await self._request_with_retry(url)
        return self._parse_feed(content)

    async def _request_with_retry(self, url: str) -> bytes:
        delays = [5, 15, 45]

        for attempt, delay in enumerate(delays):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url, headers=self._get_headers())

                    if response.status_code == 200:
                        return response.content

                    if response.status_code == 429:
                        logging.warning(f"Rate limit arXiv, ждём {delay}с (попытка {attempt+1})")
                        await asyncio.sleep(delay)
                        continue

                    if response.status_code in (502, 503):
                        logging.warning(f"arXiv недоступен ({response.status_code}), ждём {delay}с")
                        await asyncio.sleep(delay)
                        continue

                    # Любой другой не-200 — сразу падаем
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"arXiv API error: {response.status_code}"
                    )

            except HTTPException:
                raise  # пробрасываем как есть
            except Exception as e:
                logging.error(f"Попытка {attempt+1} упала: {e}")
                if attempt < len(delays) - 1:
                    await asyncio.sleep(delay)

        raise HTTPException(status_code=503, detail="arXiv недоступен после нескольких попыток")

    def _resolve_dates(self, date_start: Optional[str], date_end: Optional[str]) -> tuple[str, str]:
        today = date.today()
        default_start = today - timedelta(days=180)
        return (
            date_start or default_start.isoformat(),
            date_end or today.isoformat(),
        )

    def _build_query(self, keywords: List[str], date_start: str, date_end: str, limit: int) -> str:
        query_parts = []

        if keywords:
            keyword_terms = [f"all:{kw.strip().replace(' ', '+')}" for kw in keywords if kw.strip()]
            if keyword_terms:
                query_parts.append(f"({' OR '.join(keyword_terms)})")

        query_parts.append(
            f"submittedDate:[{date_start.replace('-', '')}0000 TO {date_end.replace('-', '')}2359]"
        )

        search_query = " AND ".join(query_parts)

        return (
            f"search_query={requests.utils.quote(search_query)}"
            f"&start=0"
            f"&max_results={limit}"
            f"&sortBy=submittedDate"
            f"&sortOrder=descending"
        )

    def _parse_feed(self, xml_content: bytes) -> List[Article]:
        root = ET.fromstring(xml_content)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        articles: List[Article] = []

        for entry in root.findall(".//atom:entry", ns):
            article_id = entry.find("atom:id", ns)
            title = entry.find("atom:title", ns)
            published = entry.find("atom:published", ns)
            summary = entry.find("atom:summary", ns)

            external_id = article_id.text.rsplit("/", 1)[-1].rsplit("v", 1)[0] if article_id is not None else ""
            title_text = (title.text or "").strip() if title is not None else ""
            date_sub = published.text[:10] if published is not None and published.text else ""
            abstract = (summary.text or "").strip() if summary is not None else ""

            authors = []
            for author in entry.findall("atom:author", ns):
                name_node = author.find("atom:name", ns)
                if name_node is not None and name_node.text:
                    authors.append(name_node.text.strip())

            tags = [cat.get("term") for cat in entry.findall("atom:category", ns) if cat.get("term")]

            primary_category_node = entry.find("arxiv:primary_category", ns)
            category = primary_category_node.get("term") if primary_category_node is not None else None

            url_article = ""
            pdf_url = ""

            for link in entry.findall("atom:link", ns):
                if link.get("rel") == "alternate":
                    url_article = link.get("href", "")
                elif link.get("title") == "pdf":
                    pdf_url = link.get("href", "")

            articles.append(
                Article(
                    external_id=external_id,
                    title=title_text,
                    date_sub=date_sub,
                    url_article=url_article,
                    pdf_url=pdf_url,
                    authors=authors,
                    tags=tags,
                    abstract=abstract,
                    category=category,
                )
            )

        return self._deduplicate(articles)

    def _deduplicate(self, articles: List[Article]) -> List[Article]:
        seen = set()
        result = []

        for article in articles:
            key = article.external_id or article.url_article or article.title.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            result.append(article)

        return result


class ResearchPipeline:
    def __init__(self, db: DataBase):
        self.logger = self._setup_logger()
        self.repo = ResearchRepository(db)
        self.arxiv_http = ArxivClient()
        self.arxiv_lib  = ArxivLibClient()   # новый через библиотеку

    async def run(self, payload: RequestPayload) -> Dict[str, Any]:
        self.logger.info(f"user_name={payload.user_name}, keywords={payload.params.keywords}")
        
        articles = self.arxiv_lib.search(
                    keywords=payload.params.keywords,
                    date_start=payload.params.date_start,
                    date_end=payload.params.date_end,
                    limit=payload.params.limit,
                )
        # try:
        #     # Сначала пробуем HTTP клиент
        #     articles = await self.arxiv_http.search(
        #         keywords=payload.params.keywords,
        #         date_start=payload.params.date_start,
        #         date_end=payload.params.date_end,
        #         limit=payload.params.limit,
        #     )
        # except HTTPException as e:
        #     if e.status_code in (429, 503):
        #         # Fallback на библиотеку
        #         self.logger.warning(f"HTTP клиент упал ({e.status_code}), переключаемся на arxiv lib")
        #         articles = self.arxiv_lib.search(
        #             keywords=payload.params.keywords,
        #             date_start=payload.params.date_start,
        #             date_end=payload.params.date_end,
        #             limit=payload.params.limit,
        #         )
        #     else:
        #         raise

        return await self.repo.save_search_results(
            user_name=payload.user_name,
            user_query=payload.user_query,
            params=payload.params,
            articles=articles,
            request_id=payload.request_id,
        )

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("research_pipeline")
        if logger.handlers:
            return logger

        logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
        logger.propagate = False

        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(stream)

        return logger


def build_payload(post_body: Dict[str, Any]) -> RequestPayload:
    params_raw = post_body.get("params", {}) or {}

    return RequestPayload(
        user_name=post_body["user_name"],
        user_query=post_body["query"],
        request_id=post_body["request_id"],
        params=SearchParams(
            keywords=params_raw.get("keywords", []),
            date_start=params_raw.get("date_start"),
            date_end=params_raw.get("date_end"),
            limit=params_raw.get("limit", 20),
        ),
    )


async def main(post_body: Dict[str, Any]):
    db = DataBase()
    await db.init_db_pool()
    try:
        payload = build_payload(post_body)
        pipeline = ResearchPipeline(db)
        return await pipeline.run(payload)
    finally:
        await db.close()

async def start_search(post_body: Dict[str, Any], db: DataBase):
    payload = build_payload(post_body)
    pipeline = ResearchPipeline(db)
    return await pipeline.run(payload)
