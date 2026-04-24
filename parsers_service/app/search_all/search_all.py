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
from urllib.parse import urlencode, urlparse, quote_plus

from .core.agent_provider import get_model
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
    # obsidian_vault_path: Path = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault"))
    output_json_path: Path = Path(os.getenv("PIPELINE_OUTPUT_JSON", "./pipeline_results.json"))
    # profile_dir: Path = Path(os.getenv("PLAYWRIGHT_PROFILE_DIR", "./pw_profile_scholar"))
    http_timeout: int = int(os.getenv("HTTP_TIMEOUT", "45"))
    # max_pages_scholar: int = int(os.getenv("SCHOLAR_MAX_PAGES", "10"))
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


# @dataclass
# class ScholarItem:
#     article_url: str
#     title: str
#     pdf_url: str
#     pdf_label: str
#     age_days: Optional[int] = None
#     date_pub: Optional[str] = None
#     abstract: Optional[str] = None
#     authors: Optional[str] = None
#     tags: Optional[str] = None
#     category: Optional[str] = None
#     page_count: Optional[int] = None
#     source: str = "googlescholar"
#     pdf_bytes: Optional[bytes] = None


# =========================
# Utils
# =========================

DATE_PATTERN_IEEE = re.compile(
    r'\b(\d{1,2})\s+([Jj]anuary|[Ff]ebruary|[Mm]arch|[Aa]pril|[Mm]ay|[Jj]une|[Jj]uly|[Aa]ugust|[Ss]eptember|[Oo]ctober|[Nn]ovember|[Dd]ecember)\s+(\d{4})\b'
)
PDF_URL_RE = re.compile(r"\\.pdf(\\?|$)", re.IGNORECASE)

ConvertResult = Union[
    Tuple[Literal["ok"], int, str],
    Tuple[Literal["skip"], int],
    Tuple[Literal["err"], str],
]


def setup_logger(cfg: PipelineConfig) -> logging.Logger:
    logger = logging.getLogger("research_pipeline")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, cfg.log_level.upper(), logging.INFO))
    logger.propagate = False
    handler = logging.FileHandler(cfg.log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(stream)
    return logger


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


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
# Replace _chat() with your real provider call
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
        self.base_url = "https://arxiv.org/search/?"
        self.headers = {"User-Agent": cfg.user_agent}
        self.months = {
            'January': 1, 'February': 2, 'March': 3, 'April': 4,
            'May': 5, 'June': 6, 'July': 7, 'August': 8,
            'September': 9, 'October': 10, 'November': 11, 'December': 12
        }

    def _build_url(self, keywords: List[str], start: int = 0) -> str:
        query = " OR ".join([f'"{k}"' for k in keywords])
        params = {
            "query": query,
            "searchtype": "all",
            "abstracts": "show",
            "order": "-announced_date_first",
            "size": 50,
            "start": start,
        }
        return self.base_url + urlencode(params)

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
        start_date = parse_iso_date(params.date_start)
        end_date = parse_iso_date(params.date_end)
        results: List[Article] = []

        for offset in range(0, 1000, 50):
            if len(results) >= params.max_per_source:
                break
            url = self._build_url(params.keywords, offset)
            r = requests.get(url, headers=self.headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            items = soup.find_all("li", class_="arxiv-result")
            if not items:
                break

            for item in items:
                if len(results) >= params.max_per_source:
                    break
                title_tag = item.find("p", class_="title is-5 mathjax")
                date_tag = item.find("p", class_="is-size-7")
                authors_tag = item.find("p", class_="authors")
                list_title = item.find("p", class_="list-title is-inline-block")
                if not all([title_tag, date_tag, authors_tag, list_title]):
                    continue

                iso_date, article_date = self._convert_date(date_tag.text.strip())
                if article_date > end_date or article_date < start_date:
                    continue

                title = safe_title(title_tag.text.strip())
                authors = {a.text.strip(): a.get("href", "") for a in authors_tag.find_all("a")}
                article_url = ""
                pdf_url = ""
                arxiv_id = ""
                for a in list_title.find_all("a"):
                    href = a.get("href", "").strip()
                    text = a.text.strip().lower()
                    if "arxiv:" in a.text.lower():
                        article_url = href
                        arxiv_id = a.text.split(":")[-1].strip()
                    elif text == "pdf":
                        pdf_url = ensure_pdf_url(href)

                tags_block = item.find("div", class_="tags is-inline-block")
                tags = []
                if tags_block:
                    tags = [span.get("data-tooltip", "").strip() for span in tags_block.find_all("span")]

                abstract = self._parse_article_page(article_url) if article_url else ""
                text_article, pdf_bytes = self._get_pdf_text(pdf_url) if pdf_url else ("", b"")

                results.append(Article(
                    source="https://arxiv.org",
                    external_id=arxiv_id or url_to_hash(article_url or title),
                    title=title,
                    date_sub=iso_date,
                    url_article=article_url,
                    pdf_url=pdf_url,
                    authors=authors,
                    tags=tags,
                    abstract=abstract,
                    file_article=f"{iso_date}_{url_to_hash(article_url or title)}.md",
                    category=None,
                    text_article=text_article,
                    pdf_bytes=pdf_bytes,
                ))
        return results


# =========================
# Google Scholar parser
# =========================


# def build_scholar_url(query: str, start: int, ui_lang: str = "en", fulltext: bool = False) -> str:
#     params = {
#         "hl": ui_lang,
#         "as_sdt": "1,5" if fulltext else "0,5",
#         "q": query,
#         "scisbd": "1",
#         "start": str(start),
#     }
#     return "https://scholar.google.com/scholar?" + urlencode(params)


# def jitter_sleep(a: float = 1.6, b: float = 3.8) -> None:
#     time.sleep(random.uniform(a, b))


# def likely_blocked(page) -> bool:
#     try:
#         title = (page.title() or "").lower()
#     except Exception:
#         title = ""
#     try:
#         body = (page.locator("body").inner_text(timeout=1500) or "").lower()[:5000]
#     except Exception:
#         body = ""
#     return (
#         "unusual traffic" in body
#         or "captcha" in body
#         or ("sorry" in title and "scholar" not in title)
#         or "our systems have detected unusual traffic" in body
#         or "not a robot" in body
#     )


# def get_right_link(result_locator) -> Tuple[str, str]:
#     for css in ("div.gs_or_ggsm a", "div.gs_ggs a"):
#         try:
#             a = result_locator.locator(css).first
#             if a.count() > 0:
#                 label = normalize_ws(a.inner_text() or "")
#                 url = a.get_attribute("href") or ""
#                 if url:
#                     return label, url
#         except Exception:
#             continue
#     return "", ""


# def parse_relative_age_days(text: str) -> Optional[int]:
#     t = normalize_ws(text).lower()
#     if "сегодня" in t or "today" in t:
#         return 0
#     if "вчера" in t or "yesterday" in t:
#         return 1
#     patterns = [
#         (r"\b(\d+)\s*(?:нед\.|недел[яи]|недель)\s*назад\b", 7),
#         (r"\b(\d+)\s*weeks?\s*ago\b", 7),
#         (r"\b(\d+)\s*(?:дн\.|дня|дней)\s*назад\b", 1),
#         (r"\b(\d+)\s*days?\s*ago\b", 1),
#         (r"\b(\d+)\s*(?:мес\.|месяц|месяца|месяцев)\s*назад\b", 30),
#         (r"\b(\d+)\s*months?\s*ago\b", 30),
#     ]
#     for pattern, mul in patterns:
#         m = re.search(pattern, t)
#         if m:
#             return int(m.group(1)) * mul
#     if re.search(r"\b(\d+)\s*(?:час|часа|часов|hours?|мин\.|минута|минуты|минут|minutes?)\s*(?:назад|ago)\b", t):
#         return 0
#     return None


# def compute_pub_date_iso(age_days: Optional[int]) -> Optional[str]:
#     if age_days is None:
#         return None
#     return (date.today() - timedelta(days=age_days)).isoformat()


# def parse_results_on_page(page) -> List[ScholarItem]:
#     items = []
#     results = page.locator("div.gs_r.gs_or.gs_scl")
#     n = results.count()
#     for i in range(n):
#         r = results.nth(i)
#         pdf_label, pdf_url = get_right_link(r)
#         if not pdf_url:
#             continue
#         try:
#             h3 = r.locator("h3.gs_rt")
#             a = h3.locator("a")
#             if a.count() > 0:
#                 title = normalize_ws(a.first.inner_text() or "")
#                 article_url = a.first.get_attribute("href") or ""
#             else:
#                 title = normalize_ws(h3.inner_text() or "")
#                 article_url = ""
#         except Exception:
#             continue
#         try:
#             snippet = normalize_ws(r.locator("div.gs_rs").first.inner_text() or "")
#         except Exception:
#             snippet = ""
#         age_days = parse_relative_age_days(snippet)
#         if age_days is None:
#             try:
#                 age_days = parse_relative_age_days(normalize_ws(r.inner_text() or ""))
#             except Exception:
#                 age_days = None
#         items.append(ScholarItem(
#             title=title,
#             article_url=article_url,
#             pdf_url=pdf_url,
#             pdf_label=pdf_label,
#             age_days=age_days,
#             date_pub=compute_pub_date_iso(age_days),
#         ))
#     return items


# def is_probably_pdf(label: str, url: str) -> bool:
#     if not url:
#         return False
#     return "pdf" in (label or "").lower() or bool(PDF_URL_RE.search(url))


# def click_next(page) -> bool:
#     selectors = [
#         "td.b a[aria-label='Next']",
#         "a:has-text('Следующая')",
#         "a:has-text('Next')",
#     ]
#     for sel in selectors:
#         loc = page.locator(sel)
#         if loc.count() > 0:
#             loc.first.click()
#             return True
#     nav_links = page.locator("#gs_n td.b a")
#     if nav_links.count() > 0:
#         nav_links.nth(nav_links.count() - 1).click()
#         return True
#     return False


# def looks_like_pdf_bytes(b: bytes) -> bool:
#     return bool(b) and b[:5] == b"%PDF-"


# def is_pdf_content_type(ct: str) -> bool:
#     return "application/pdf" in (ct or "").lower()


# def url_looks_like_pdf(url: str) -> bool:
#     p = urlparse(url)
#     path = (p.path or "").lower()
#     return path.endswith(".pdf") or "/pdf/" in path or "content/pdf" in path or bool(PDF_URL_RE.search(url))


# def http_get_pdf(cfg: PipelineConfig, session: requests.Session, url: str) -> Tuple[Optional[bytes], str, str]:
#     headers = {
#         "User-Agent": cfg.user_agent,
#         "Accept": "application/pdf,*/*;q=0.8",
#         "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
#     }
#     r = session.get(url, headers=headers, timeout=(10, cfg.http_timeout), allow_redirects=True, stream=True)
#     r.raise_for_status()
#     ct = r.headers.get("Content-Type") or ""
#     final_url = r.url
#     data = bytearray()
#     for chunk in r.iter_content(chunk_size=65536):
#         if not chunk:
#             continue
#         data.extend(chunk)
#         if len(data) > cfg.max_bytes:
#             break
#     content = bytes(data)
#     if looks_like_pdf_bytes(content) or is_pdf_content_type(ct) or url_looks_like_pdf(final_url):
#         return content, final_url, ct
#     return None, final_url, ct


# def download_pdf_with_retries(cfg: PipelineConfig, session: requests.Session, url: str):
#     max_attempts = max(1, cfg.download_retries + 1)
#     attempts = 0
#     t0 = time.perf_counter()
#     deadline = t0 + float(cfg.download_total_timeout_s)
#     last_exc = None
#     final_url = url
#     content_type = ""
#     stop_reason = "error"

#     while attempts < max_attempts:
#         attempts += 1
#         if time.perf_counter() >= deadline:
#             stop_reason = "deadline"
#             break
#         try:
#             pdf_bytes, final_url, content_type = http_get_pdf(cfg, session, url)
#             if pdf_bytes is not None:
#                 return pdf_bytes, final_url, content_type, attempts, time.perf_counter() - t0, None, "ok"
#             stop_reason = "no_pdf"
#         except Exception as e:
#             last_exc = e
#             stop_reason = "error"
#         if attempts >= max_attempts:
#             break
#         sleep_s = cfg.download_retry_sleep * (2 ** (attempts - 1)) + random.uniform(0, 0.6)
#         sleep_s = min(sleep_s, max(0, deadline - time.perf_counter()))
#         if sleep_s > 0:
#             time.sleep(sleep_s)

#     return None, final_url, content_type, attempts, time.perf_counter() - t0, last_exc, stop_reason


# def _convert_worker_pypdf(pdf_bytes: bytes, q: mp.Queue, max_pages: int) -> None:
#     try:
#         reader = PdfReader(io.BytesIO(pdf_bytes))
#         page_count = len(reader.pages)
#         if page_count > max_pages:
#             q.put(("skip", page_count))
#             return
#         parts = []
#         for page in reader.pages[:3]:
#             txt = (page.extract_text() or "").strip()
#             if txt:
#                 parts.append(txt)
#         q.put(("ok", page_count, "\n\n".join(parts).strip()))
#     except Exception as e:
#         q.put(("err", repr(e)))


# def convert_with_timeout(pdf_bytes: bytes, timeout_s: float, max_pages: int) -> Optional[ConvertResult]:
#     q = mp.Queue()
#     p = mp.Process(target=_convert_worker_pypdf, args=(pdf_bytes, q, max_pages), daemon=True)
#     p.start()
#     p.join(timeout_s)
#     if p.is_alive():
#         p.terminate()
#         p.join(2)
#         return None
#     if q.empty():
#         return None
#     return q.get()


# def scholar_item_to_article(it: ScholarItem) -> Article:
#     clean_title = safe_title(it.title)
#     external_id = url_to_hash(it.article_url or it.pdf_url or clean_title)
#     return Article(
#         source=it.source or "googlescholar",
#         external_id=external_id,
#         title=clean_title,
#         date_sub=it.date_pub or "",
#         url_article=it.article_url,
#         pdf_url=it.pdf_url or "",
#         authors={"raw": it.authors} if it.authors else None,
#         tags=it.tags,
#         abstract=it.abstract or "",
#         file_article=f"{it.date_pub or 'undated'}_{external_id}.md",
#         category=it.category,
#         text_article=it.abstract or "",
#         pdf_bytes=it.pdf_bytes or b"",
#     )


# class ScholarParser(BaseParser):
#     name = "scholar"

#     def __init__(self, cfg: PipelineConfig, logger: logging.Logger):
#         self.cfg = cfg
#         self.logger = logger

#     def run(self, params: SearchParams) -> List[Article]:
#         start_date = parse_iso_date(params.date_start)
#         end_date = parse_iso_date(params.date_end)
#         today = date.today()
#         min_age_days = (today - end_date).days
#         max_age_days = (today - start_date).days

#         query = " OR ".join(params.keywords)
#         items_all: List[ScholarItem] = []

#         with sync_playwright() as p:
#             ctx = p.chromium.launch_persistent_context(
#                 user_data_dir=str(self.cfg.profile_dir),
#                 headless=self.cfg.headless,
#                 locale="en-En",
#                 user_agent=self.cfg.user_agent,
#             )
#             page = ctx.new_page()
#             page.set_default_timeout(30000)
#             page.set_default_navigation_timeout(30000)
#             page.goto(build_scholar_url(query, 0, self.cfg.ui_lang, self.cfg.fulltext), wait_until="domcontentloaded")
#             jitter_sleep(2.5, 5.0)
#             pages_parsed = 0

#             while pages_parsed < self.cfg.max_pages_scholar and len(items_all) < params.max_per_source * 3:
#                 if likely_blocked(page):
#                     raise RuntimeError(f"Google Scholar blocked or captcha detected: {page.url}")
#                 try:
#                     page.wait_for_selector("div.gs_r.gs_or.gs_scl", timeout=20000)
#                 except PlaywrightTimeoutError:
#                     break
#                 page_items = parse_results_on_page(page)
#                 for it in page_items:
#                     if len(items_all) >= params.max_per_source * 3:
#                         break
#                     if it.age_days is None:
#                         continue
#                     if it.age_days < min_age_days or it.age_days > max_age_days:
#                         continue
#                     if not is_probably_pdf(it.pdf_label, it.pdf_url):
#                         continue
#                     items_all.append(it)
#                 pages_parsed += 1
#                 jitter_sleep(2.0, 4.0)
#                 if not click_next(page):
#                     break
#                 page.wait_for_load_state("domcontentloaded")
#             ctx.close()

#         final_items: List[ScholarItem] = []
#         with requests.Session() as session:
#             for it in items_all:
#                 if len(final_items) >= params.max_per_source:
#                     break
#                 try:
#                     pdf_bytes, final_url, content_type, attempts, elapsed, err, stop_reason = download_pdf_with_retries(self.cfg, session, it.pdf_url)
#                     if pdf_bytes is None:
#                         continue
#                     res = convert_with_timeout(pdf_bytes, self.cfg.convert_timeout_s, self.cfg.max_pdf_pages_convert)
#                     if res is None:
#                         continue
#                     kind = res[0]
#                     if kind == "skip" or kind == "err":
#                         continue
#                     if kind == "ok":
#                         it.page_count, md = res[1], res[2]
#                         it.abstract = md
#                         it.pdf_bytes = pdf_bytes
#                         final_items.append(it)
#                 except Exception as e:
#                     self.logger.warning(f"Scholar item failed: {it.title} | {e}")
#                 time.sleep(self.cfg.sleep_between_downloads)

#         return [scholar_item_to_article(x) for x in final_items]


# # =========================
# # IEEE parser
# # =========================

# class IEEEParser(BaseParser):
#     name = "ieee"

#     def __init__(self, cfg: PipelineConfig, logger: logging.Logger):
#         self.cfg = cfg
#         self.logger = logger

#     @staticmethod
#     def find_link(element):
#         try:
#             return element.locator('.fw-bold').get_attribute('href')
#         except PlaywrightTimeoutError:
#             return False

#     @staticmethod
#     def is_suitable_search_result(element):
#         try:
#             1 / element.locator('.icon-access-open-access').count()
#             1 / element.locator('.fw-bold').count()
#         except ZeroDivisionError:
#             return False
#         return True

#     def _in_date_range(self, date_str: str, date_range: Tuple[str, str]) -> bool:
#         try:
#             article_date = datetime.fromisoformat(date_str)
#             start = datetime.fromisoformat(date_range[0])
#             end = datetime.fromisoformat(date_range[1])
#             return start <= article_date <= end
#         except Exception:
#             return False

#     def _build_search_url(self, params: SearchParams) -> str:
#         query = " ".join(params.keywords)
#         return f"https://ieeexplore.ieee.org/search/searchresult.jsp?queryText={quote_plus(query)}"

#     def _get_text_pdf_file(self, page, link: str, sleep_sec=1):
#         cookies = {c['name']: c['value'] for c in page.context.cookies(page.url)}
#         headers = {
#             "User-Agent": page.evaluate("navigator.userAgent;"),
#             "Referer": page.url,
#             "Accept": "application/pdf"
#         }
#         response = requests.get(link, headers=headers, cookies=cookies, timeout=30)
#         response.raise_for_status()
#         pdf_bytes = response.content
#         reader = PdfReader(BytesIO(pdf_bytes))
#         text = ""
#         for p in reader.pages[:3]:
#             extracted = p.extract_text()
#             if extracted:
#                 text += extracted + "\n"
#         time.sleep(sleep_sec)
#         return text, pdf_bytes

#     def run(self, params: SearchParams) -> List[Article]:
#         requested_link = self._build_search_url(params)
#         articles: List[Article] = []

#         with sync_playwright() as p:
#             ctx = p.chromium.launch_persistent_context(
#                 user_data_dir='.',
#                 headless=self.cfg.headless,
#                 locale="en-En",
#                 user_agent=self.cfg.user_agent
#             )
#             page = ctx.new_page()
#             page.set_default_timeout(30000)
#             page.set_default_navigation_timeout(30000)
#             page.goto(requested_link)

#             for i in range(4):
#                 if len(articles) >= params.max_per_source:
#                     break
#                 if i != 0:
#                     time.sleep(5)
#                 try:
#                     page.wait_for_selector('.List-results-items')
#                 except Exception:
#                     break
#                 search_results = page.locator(".List-results-items").all()
#                 suitable_results = [sr for sr in search_results if self.is_suitable_search_result(sr)]
#                 links = [self.find_link(sr) for sr in suitable_results]

#                 for link in links:
#                     if len(articles) >= params.max_per_source:
#                         break
#                     if not link:
#                         continue
#                     try:
#                         page.goto('https://ieeexplore.ieee.org' + link)
#                         content = page.content()
#                         found_dates = DATE_PATTERN_IEEE.findall(content)
#                         if not found_dates:
#                             continue
#                         date_string_raw = ' '.join(found_dates[0])
#                         dt = datetime.strptime(date_string_raw, '%d %B %Y').date()
#                         iso_date = dt.isoformat()
#                         if not self._in_date_range(iso_date, (params.date_start, params.date_end)):
#                             continue

#                         title = page.locator('.document-title').text_content() or ""
#                         abstract_candidates = [x.text_content() for x in page.locator('.u-mb-1').all() if 'Abstract' in (x.text_content() or '')]
#                         abstract = abstract_candidates[0] if abstract_candidates else ""

#                         authors = {}
#                         for author_element in page.locator('.authors-info').all():
#                             try:
#                                 a_link = author_element.locator('a').first.get_attribute('href')
#                                 name = author_element.locator('span').first.text_content()
#                                 if name:
#                                     authors[name] = a_link or ""
#                             except Exception:
#                                 pass

#                         tags = []
#                         try:
#                             page.locator('#keywords').click()
#                             for tc in page.locator('.doc-keywords-list-item').all():
#                                 try:
#                                     if tc.locator('strong').text_content() == 'IEEE Keywords':
#                                         tags = [item.text_content() for item in tc.locator('a').all()]
#                                 except Exception:
#                                     pass
#                         except Exception:
#                             pass

#                         pdf_button = page.locator('.xpl-btn-pdf')
#                         pdf_link = pdf_button.get_attribute('href')
#                         if not pdf_link:
#                             continue
#                         page.goto('https://ieeexplore.ieee.org' + pdf_link)
#                         frames = page.locator('iframe').all()
#                         direct_link = ""
#                         for frame in frames:
#                             try:
#                                 if 'iframe src' in frame.evaluate('el => el.outerHTML'):
#                                     direct_link = frame.get_attribute('src') or ""
#                                     break
#                             except Exception:
#                                 continue
#                         if not direct_link:
#                             continue
#                         text_article, pdf_bytes = self._get_text_pdf_file(page, direct_link)
#                         articles.append(Article(
#                             source='https://ieeexplore.ieee.org/',
#                             external_id=link.split('/')[-1],
#                             title=safe_title(title),
#                             date_sub=iso_date,
#                             url_article='https://ieeexplore.ieee.org' + link,
#                             pdf_url=direct_link,
#                             authors=authors,
#                             tags=tags,
#                             abstract=abstract,
#                             file_article=f'{iso_date}_{url_to_hash(link)}.md',
#                             category=None,
#                             text_article=text_article,
#                             pdf_bytes=pdf_bytes,
#                         ))
#                     except Exception as e:
#                         self.logger.warning(f"IEEE item failed: {link} | {e}")
#                 page.goto(requested_link)
#             ctx.close()
#         return articles


# # =========================
# # Fluxicon parser
# # =========================

# class FluxiconParser(BaseParser):
#     name = "fluxicon"

#     def __init__(self, cfg: PipelineConfig, logger: logging.Logger):
#         self.cfg = cfg
#         self.logger = logger
#         self.base_url = "https://www.fluxicon.com"

#     def _in_date_range(self, date_str: str, params: SearchParams) -> bool:
#         try:
#             article_date = datetime.fromisoformat(date_str)
#             start = datetime.fromisoformat(params.date_start)
#             end = datetime.fromisoformat(params.date_end)
#             return start <= article_date <= end
#         except Exception:
#             return False

#     def _html_to_pdf(self, url: str) -> bytes:
#         if HTML is None:
#             return b""
#         try:
#             resp = requests.get(url, timeout=20, verify=self.cfg.verify_ssl, headers={"User-Agent": self.cfg.user_agent})
#             resp.raise_for_status()
#             return HTML(string=resp.text, base_url=url).write_pdf()
#         except Exception as e:
#             self.logger.warning(f"Fluxicon PDF build failed: {url} | {e}")
#             return b""

#     def run(self, params: SearchParams) -> List[Article]:
#         response = requests.get(f"{self.base_url}/blog/", timeout=20, verify=self.cfg.verify_ssl, headers={"User-Agent": self.cfg.user_agent})
#         soup = BeautifulSoup(response.text, "html.parser")
#         entries = soup.find_all("div", class_="entry")
#         results: List[Article] = []
#         topic_text = " ".join([params.topic.lower()] + [k.lower() for k in params.keywords])

#         for article in entries:
#             if len(results) >= params.max_per_source:
#                 break
#             header = article.find(class_="entry-header")
#             if not header or not header.a:
#                 continue
#             link = header.a["href"]
#             title = safe_title(header.get_text(strip=True))
#             date_span = article.find("span", class_="entry-date")
#             if not date_span:
#                 continue
#             day = date_span.text.split(" ")[0].zfill(2)
#             ym = link.strip("/").split("/")[1:3]
#             if len(ym) < 2:
#                 continue
#             date_sub = f"{ym[0]}-{ym[1].zfill(2)}-{day}"
#             if not self._in_date_range(date_sub, params):
#                 continue

#             text_blocks = []
#             for child in article.children:
#                 name = getattr(child, "name", None)
#                 if name == "p":
#                     text_blocks.append(child.get_text(strip=True))
#                 elif name == "ul":
#                     for li in child.find_all("li"):
#                         text_blocks.append(f"- {li.get_text(strip=True)}")
#                 elif name == "h2":
#                     text_blocks.append(child.get_text(strip=True))
#             abstract = "\n".join(text_blocks)
#             combined = f"{title}\n{abstract}".lower()
#             if params.keywords and not any(k.lower() in combined for k in params.keywords) and params.topic.lower() not in combined:
#                 continue
#             url = f"{self.base_url}{link}"
#             pdf_bytes = self._html_to_pdf(url)
#             results.append(Article(
#                 source="https://www.fluxicon.com",
#                 external_id=link,
#                 title=title,
#                 date_sub=date_sub,
#                 url_article=url,
#                 pdf_url="",
#                 authors=None,
#                 tags=None,
#                 abstract=abstract,
#                 file_article=f"{date_sub}_{url_to_hash(url)}.md",
#                 category=None,
#                 text_article=abstract,
#                 pdf_bytes=pdf_bytes,
#             ))
#         return results


# =========================
# Obsidian export
# =========================

# class ObsidianExporter:
#     def __init__(self, vault_path: Path):
#         self.vault_path = Path(vault_path)

#     def save_article_review(self, folder: str, article: Article, review_md: str, topic: str) -> Path:
#         target_dir = self.vault_path / folder
#         target_dir.mkdir(parents=True, exist_ok=True)
#         tags = article.tags if isinstance(article.tags, list) else ([article.tags] if article.tags else [])
#         authors = list(article.authors.keys()) if article.authors else []
#         frontmatter = {
#             "title": article.title,
#             "authors": authors,
#             "source": article.source,
#             "date": article.date_sub,
#             "topic": topic,
#             "tags": ["paper", "review", article.source.split('//')[-1].split('/')[0]] + tags,
#             "url": article.url_article,
#             "pdf_url": article.pdf_url,
#             "external_id": article.external_id,
#         }
#         fm_lines = ["---"] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in frontmatter.items()] + ["---", ""]
#         content = "\n".join(fm_lines) + review_md.strip() + "\n"
#         filename = article.file_article or f"{article.date_sub}_{url_to_hash(article.url_article or article.title)}.md"
#         path = target_dir / filename
#         path.write_text(content, encoding="utf-8")
#         return path


# =========================
# Pipeline
# =========================

class ResearchPipeline:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.logger = setup_logger(cfg)
        self.llm = LLMClient()
        # self.exporter = ObsidianExporter(cfg.obsidian_vault_path)
        self.parsers = {
            "arxiv": ArxivParser(cfg, self.logger),
            # "scholar": ScholarParser(cfg, self.logger),
            # "ieee": IEEEParser(cfg, self.logger),
            # "fluxicon": FluxiconParser(cfg, self.logger),
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
        print(params)
        self.logger.info(f"Search params: {asdict(params)}")
        articles = await self.collect_articles(params)
        saved = []

        for article in articles:
            try:
                review_md = self.llm.generate_review(article, params)
                print(review_md)
                # path = self.exporter.save_article_review(params.obsidian_folder, article, review_md, params.topic)
                saved.append({
                    "title": article.title,
                    "source": article.source,
                    "date": article.date_sub,
                    "article_url": article.url_article,
                    "pdf_url": article.pdf_url,
                    # "saved_to": str(path),
                })
                # self.logger.info(f"Saved review: {path}")
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
