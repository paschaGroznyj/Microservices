import os
import re
import time
import asyncio
import hashlib
import logging

from io import BytesIO
from datetime import date, timedelta, datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple

import arxiv
import requests
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclass — полный интерфейс из ARXIVParser
# ---------------------------------------------------------------------------

@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: dict           # {name: profile_url_or_None}
    tags: List[str]
    abstract: str
    file_article: str
    text_article: str
    pdf_bytes: bytes = b""


# ---------------------------------------------------------------------------
# Параметры поиска — интерфейс из ResearchPipeline
# ---------------------------------------------------------------------------

@dataclass
class SearchParams:
    keywords: List[str]
    date_start: Optional[str] = None   # "YYYY-MM-DD"
    date_end: Optional[str] = None     # "YYYY-MM-DD"
    limit: int = 10
    fetch_pdf: bool = False             # качать PDF и извлекать текст?


# ---------------------------------------------------------------------------
# Клиент
# ---------------------------------------------------------------------------

class ArxivClient:
    """
    Клиент на основе официальной библиотеки arxiv.
    Входной интерфейс — SearchParams (keywords + даты + limit).
    Выходной интерфейс — List[Article] (полный датакласс ARXIVParser).
    """
    name = "arxiv"
    # PM-keywords по умолчанию — берём из ARXIVParser
    DEFAULT_PM_KEYWORDS: List[str] = [
       # Ядро process mining
        "process mining",
        "process discovery",
        "conformance checking",
        "process enhancement",
        "process model",
        "task mining"

        # Логи и следы
        "event logs",
        "audit trail",
        "audit log analysis",

        # Контроль и аудит
        "process monitoring",
        "process compliance",
        "continuous auditing",
        "internal audit",
        "deviation analysis",
        "compliance monitoring",

        # Банковский контекст
        "process mining banking",
        "financial auditing"
    ]

    SOURCE = "https://arxiv.org"

    def __init__(
        self,
        params: Optional[SearchParams] = None,  # ← принимаем в конструктор
        page_size: int = 20,
        delay_seconds: float = 3.0,
        num_retries: int = 3,
        http_timeout: int = 20,
    ):
        self._params = params or SearchParams(keywords=[])
        self._lib_client = arxiv.Client(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=num_retries,
        )
        self._http_timeout = http_timeout
        self._http_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }

    # ------------------------------------------------------------------
    # Публичный sync-метод
    # ------------------------------------------------------------------

    def search(self, params: SearchParams) -> List[Article]:
        keywords = params.keywords or self.DEFAULT_PM_KEYWORDS
        date_start, date_end = self._resolve_dates(params.date_start, params.date_end)

        all_articles: List[Article] = []
        seen_ids: set = set()

        for keyword in keywords:
            query = self._build_query([keyword])
            logger.info("Поиск по ключевому слову: '%s' | dates: %s → %s", keyword, date_start, date_end)

            try:
                arxiv_search = arxiv.Search(
                    query=query,
                    max_results=params.limit,
                    sort_by=arxiv.SortCriterion.SubmittedDate,
                    sort_order=arxiv.SortOrder.Descending,
                )

                for result in self._lib_client.results(arxiv_search):
                    article = self._parse_result(result, fetch_pdf=params.fetch_pdf)

                    if date_start and article.date_sub < date_start:
                        logger.info("  Достигнута нижняя граница дат (%s), следующий keyword.", article.date_sub)
                        break
                    if date_end and article.date_sub > date_end:
                        continue

                    # Дедупликация на лету
                    key = article.external_id or article.url_article
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    all_articles.append(article)

            except Exception as exc:
                logger.warning("Ошибка при поиске по '%s': %s — пропускаем.", keyword, exc)
                continue

        logger.info("Итого найдено уникальных статей: %d", len(all_articles))
        return all_articles

    # ------------------------------------------------------------------
    # Публичный async-метод
    # ------------------------------------------------------------------

    async def asearch(self, params: SearchParams) -> List[Article]:
        """Асинхронная обёртка — не блокирует event loop."""
        return await asyncio.to_thread(self.search, params)

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _resolve_dates(
        self,
        date_start: Optional[str],
        date_end: Optional[str],
    ) -> Tuple[str, str]:
        today = date.today()
        default_start = today - timedelta(days=180)
        return (
            date_start or default_start.isoformat(),
            date_end or today.isoformat(),
        )

    def _build_query(self, keywords: List[str]) -> str:
        terms = []
        for kw in keywords:
            kw = kw.strip()
            if not kw:
                continue
            # Многословные фразы — оборачиваем в кавычки для точного совпадения
            if " " in kw:
                terms.append(f'all:"{kw}"')
            else:
                terms.append(f"all:{kw}")
        return " OR ".join(terms) if terms else "all:arxiv"

    def _parse_result(self, result: arxiv.Result, fetch_pdf: bool) -> Article:
        # external_id — убираем версию: 2605.02890v1 → 2605.02890
        external_id = result.entry_id.rsplit("/", 1)[-1].rsplit("v", 1)[0]

        # Дата
        date_sub = ""
        if result.published:
            date_sub = result.published.astimezone(timezone.utc).strftime("%Y-%m-%d")

        # Авторы: {name: None} — у arxiv.Result нет URL профиля
        authors: dict = {author.name: None for author in result.authors}

        # Теги / категории
        tags: List[str] = list(result.categories) if result.categories else []

        # Ссылки
        url_article = result.entry_id
        pdf_url = ""
        for link in result.links:
            if link.title == "pdf":
                pdf_url = link.href
                break

        # Имя файла
        safe_title = self._safe_filename(result.title or "")
        file_article = f"{date_sub}_{self._url_to_hash(url_article)}.md"

        # PDF-текст (опционально)
        text_article = ""
        pdf_bytes = b""
        if fetch_pdf and pdf_url:
            text_article, pdf_bytes = self._fetch_pdf_text(pdf_url)

        return Article(
            source=self.SOURCE,
            external_id=external_id,
            title=safe_title,
            date_sub=date_sub,
            url_article=url_article,
            pdf_url=pdf_url,
            authors=authors,
            tags=tags,
            abstract=(result.summary or "").strip(),
            file_article=file_article,
            text_article=text_article,
            pdf_bytes=pdf_bytes,
        )

    def _fetch_pdf_text(self, pdf_url: str, max_pages: int = 4) -> Tuple[str, bytes]:
        """Скачивает PDF и извлекает текст первых max_pages страниц."""
        try:
            response = requests.get(
                pdf_url,
                headers=self._http_headers,
                timeout=self._http_timeout,
            )
            response.raise_for_status()
            pdf_bytes = response.content
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "".join(
                page.extract_text() or ""
                for page in reader.pages[:max_pages]
            )
            time.sleep(3)  # вежливая пауза — как в ARXIVParser
            return text, pdf_bytes
        except Exception as exc:
            logger.warning("Не удалось скачать PDF %s: %s", pdf_url, exc)
            return "", b""

    @staticmethod
    def _safe_filename(title: str) -> str:
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)
        safe = re.sub(r"[_\s]+", "_", safe).strip("_")
        return safe

    @staticmethod
    def _url_to_hash(url: str, length: int = 12) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]

    @staticmethod
    def _deduplicate(articles: List[Article]) -> List[Article]:
        seen: set = set()
        result: List[Article] = []
        for article in articles:
            key = article.external_id or article.url_article or article.title.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            result.append(article)
        return result

    # ------------------------------------------------------------------
    # Удобный метод для получения dict-списка (как ARXIVParser.parsing)
    # ------------------------------------------------------------------

    def search_as_dicts(self, params: SearchParams) -> List[Dict[str, Any]]:
        articles = self.search(params)
        return [asdict(a) for a in articles]

    async def asearch_as_dicts(self, params: SearchParams) -> List[Dict[str, Any]]:
        articles = await self.asearch(params)
        return [asdict(a) for a in articles]

    async def arun(self) -> List[Dict[str, Any]]:
        """Интерфейс для ParserPipeline — без аргументов, возвращает list of dicts."""
        return await self.asearch_as_dicts(self._params)


# ---------------------------------------------------------------------------
# Пример использования
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    params = SearchParams(
        keywords=[],
        date_start="2026-04-28",
        date_end="2026-05-12",
        limit=50,
        fetch_pdf=False,
    )

    client = ArxivClient()
    results = client.search_as_dicts(params)

    with open("arxiv_results.json", "w", encoding="utf-8") as f:
        # pdf_bytes — bytes, не сериализуется в JSON; чистим перед дампом
        for r in results:
            r.pop("pdf_bytes", None)
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Найдено: {len(results)} статей")