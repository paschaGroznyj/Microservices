import random
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Literal, Union
from urllib.parse import urlencode, urlparse
import multiprocessing as mp

import requests
import fitz
import hashlib
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import io
# from pypdf import PdfReader
from PyPDF2 import PdfReader
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dataclasses import dataclass, asdict


@dataclass
class PipelineConfig:
    query: str = (
        '"process mining" OR '
        '"process discovery" OR '
        '"event logs" OR '
        '"process model" OR '
        '"audit trail" OR '
        '"conformance checking" OR '
        '"process enhancement" OR '
        '"root cause analysis" OR '
        '"process monitoring" OR '
        '"compliance checking" OR '
        '"deviation analysis" OR '
        '"continuous auditing" OR '
        '"business process analysis" OR '
        '"process intelligence" OR '
        '"task mining" OR '
        '"operational analytics" OR '
        '"financial auditing" OR '
        '"internal audit" OR '
        '"process compliance" OR '
        '"audit log analysis"'
    )
    max_pages: int = 10
    max_pdf_pages: int = 30
    profile_dir: Path = Path("/app/pw_profile_scholar")  # Куки/мета-информация сессии, чтобы не ловить постоянно капчу
    headless: bool = True  # Включить или выключить визуальный браузер (удобно для дебага локально)
    fulltext: bool = False
    ui_lang: str = "ru"
    md_dir: Path = Path("/app/md")  # Папка для сохранения markdown, можно поменять потом или поставить общую для всего конфига (кстати я предусмотрел чтобы название статей еще были уникальные, то есть чтобы не оказались одни и те же статьи в папке)
    report_json: Path = Path("/app/final_items.json")  # Информация по всем статьям которые получилось скачать и найти
    http_timeout: int = 45
    download_retries: int = 2
    download_retry_sleep: float = 1.0
    download_total_timeout_s: float = 40.0
    sleep_between_downloads: float = 1.2
    max_bytes: int = 30_000_000
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    convert_timeout_s: float = 30.0  # Время на конвертацию
    path_logs: Path = Path("/app/logs")  # Папка с логами выполнения
    log_file: str = "scholar_parser.log"
    log_level: str = "INFO"
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5

@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: dict | None
    tags: str | List | None
    abstract: str
    file_article: str | None
    category: str | None
    text_article: str
    pdf_bytes: bytes = b""

@dataclass
class ScholarItem:
    article_url: str
    title: str
    pdf_url: str
    pdf_label: str
    age_days: Optional[int] = None
    date_pub: Optional[str] = None

    abstract: Optional[str] = None
    authors: Optional[str] = None
    tags: Optional[str] = None
    category: Optional[str] = None
    page_count: Optional[int] = None
    source: str = "googlescholar"
    pdf_bytes: Optional[bytes] = None


PDF_URL_RE = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)

ConvertResult = Union[
    Tuple[Literal["ok"], int, str],
    Tuple[Literal["skip"], int],
    Tuple[Literal["err"], str],
]


def setup_logger(cfg: PipelineConfig) -> logging.Logger:
    cfg.path_logs.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("scholar_parser")
    if logger.handlers:
        return logger  # чтобы не дублировать при повторных запусках

    level = getattr(logging, cfg.log_level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    fh = RotatingFileHandler(
        filename=str(cfg.path_logs / cfg.log_file),
        maxBytes=int(cfg.log_max_bytes),
        backupCount=int(cfg.log_backup_count),
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(processName)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    ))

    logger.addHandler(fh)
    return logger


def build_scholar_url(query: str, start: int, ui_lang: str = "ru", fulltext: bool = False) -> str:
    params = {
        "hl": ui_lang,
        "as_sdt": "1,5" if fulltext else "0,5",
        "q": query,
        "scisbd": "1",
        "start": str(start),
    }
    return "https://scholar.google.com/scholar?" + urlencode(params)


def jitter_sleep(a: float = 1.6, b: float = 3.8) -> None:
    time.sleep(random.uniform(a, b))


def likely_blocked(page) -> bool:
    try:
        title = (page.title() or "").lower()
    except Exception:
        title = ""
    try:
        body = (page.locator("body").inner_text(timeout=1500) or "").lower()
        body = body[:5000]
    except Exception:
        body = ""
    return (
            "unusual traffic" in body
            or "captcha" in body
            or ("sorry" in title and "scholar" not in title)
            or "our systems have detected unusual traffic" in body
            or "not a robot" in body
    )


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def get_right_link(result_locator) -> Tuple[str, str]:
    for css in ("div.gs_or_ggsm a", "div.gs_ggs a"):
        try:
            a = result_locator.locator(css).first
            if a.count() > 0:
                label = normalize_ws(a.inner_text() or "")
                url = a.get_attribute("href") or ""
                if url:
                    return label, url
        except Exception:
            continue
    return "", ""


def parse_relative_age_days(text: str) -> Optional[int]:
    t = normalize_ws(text).lower()

    if "сегодня" in t or "today" in t:
        return 0
    if "вчера" in t or "yesterday" in t:
        return 1

    m = re.search(
        r"\b(\d+)\s*(?:нед\.|недел[яи]|недель)\s*назад\b",
        t,
    )
    if m:
        return int(m.group(1)) * 7
    m = re.search(r"\b(\d+)\s*weeks?\s*ago\b", t)
    if m:
        return int(m.group(1)) * 7

    m = re.search(
        r"\b(\d+)\s*(?:дн\.|дня|дней)\s*назад\b",
        t,
    )
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d+)\s*days?\s*ago\b", t)
    if m:
        return int(m.group(1))

    m = re.search(
        r"\b(\d+)\s*(?:час|часа|часов)\s*назад\b",
        t,
    )
    if m:
        return 0
    m = re.search(r"\b(\d+)\s*hours?\s*ago\b", t)
    if m:
        return 0

    m = re.search(
        r"\b(\d+)\s*(?:мин\.|минута|минуты|минут)\s*назад\b",
        t,
    )
    if m:
        return 0
    m = re.search(r"\b(\d+)\s*minutes?\s*ago\b", t)
    if m:
        return 0

    m = re.search(
        r"\b(\d+)\s*(?:мес\.|месяц|месяца|месяцев)\s*назад\b",
        t,
    )
    if m:
        return int(m.group(1)) * 30
    m = re.search(r"\b(\d+)\s*months?\s*ago\b", t)
    if m:
        return int(m.group(1)) * 30

    return None


def compute_pub_date_iso(age_days: Optional[int]) -> Optional[str]:
    if age_days is None:
        return None
    return (date.today() - timedelta(days=age_days)).isoformat()


def parse_results_on_page(page) -> List[ScholarItem]:
    items: List[ScholarItem] = []
    results = page.locator("div.gs_r.gs_or.gs_scl")
    n = results.count()

    for i in range(n):
        r = results.nth(i)

        pdf_label, pdf_url = get_right_link(r)
        if not pdf_url:
            continue

        title = ""
        article_url = ""
        try:
            h3 = r.locator("h3.gs_rt")
            a = h3.locator("a")
            if a.count() > 0:
                title = normalize_ws(a.first.inner_text() or "")
                article_url = a.first.get_attribute("href") or ""
            else:
                title = normalize_ws(h3.inner_text() or "")
                article_url = ""
        except Exception:
            continue

        try:
            meta = normalize_ws(r.locator("div.gs_a").first.inner_text() or "")
        except Exception:
            meta = ""

        try:
            snippet = normalize_ws(r.locator("div.gs_rs").first.inner_text() or "")
        except Exception:
            snippet = ""

        age_days = parse_relative_age_days(snippet)
        if age_days is None:
            try:
                whole = normalize_ws(r.inner_text() or "")
                age_days = parse_relative_age_days(whole)
            except Exception:
                age_days = None
        date_pub = compute_pub_date_iso(age_days)

        items.append(
            ScholarItem(
                title=title,
                article_url=article_url,
                pdf_url=pdf_url,
                age_days=age_days,
                pdf_label=pdf_label,
                date_pub=date_pub
            )
        )
        if title:
            pass

    return items


def is_probably_pdf(right_label: str, right_url: str) -> bool:
    if not right_url:
        return False
    label = (right_label or "").strip().lower()
    if "pdf" in label:
        return True
    return bool(PDF_URL_RE.search(right_url))


def click_next(page) -> bool:
    loc = page.locator("td.b a[aria-label='Next']")
    if loc.count() > 0:
        loc.first.click()
        return True

    loc = page.locator("a:has-text('Следующая')")
    if loc.count() > 0:
        loc.first.click()
        return True

    loc = page.locator("a:has-text('Next')")
    if loc.count() > 0:
        loc.first.click()
        return True

    nav_links = page.locator("#gs_n td.b a")
    if nav_links.count() > 0:
        nav_links.nth(nav_links.count() - 1).click()
        return True

    return False


def safe_stem(date, title: str) -> str:
    return f"{date}__{title}"


def looks_like_pdf_bytes(b: bytes) -> bool:
    return bool(b) and b[:5] == b"%PDF-"


def is_pdf_content_type(ct: str) -> bool:
    return "application/pdf" in (ct or "").lower()


def url_looks_like_pdf(url: str) -> bool:
    p = urlparse(url)
    path = (p.path or "").lower()
    return path.endswith(".pdf") or "/pdf/" in path or "content/pdf" in path or bool(PDF_URL_RE.search(url))


def http_get_pdf(cfg: PipelineConfig, s: requests.Session, url: str) -> Tuple[Optional[bytes], str, str]:
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    r = s.get(
        url,
        headers=headers,
        timeout=(10, cfg.http_timeout),
        allow_redirects=True,
        stream=True,
    )
    r.raise_for_status()

    ct = r.headers.get("Content-Type") or ""
    final_url = r.url

    data = bytearray()
    for chunk in r.iter_content(chunk_size=1024 * 64):
        if not chunk:
            continue
        data.extend(chunk)
        if len(data) > cfg.max_bytes:
            break

    content = bytes(data)

    if looks_like_pdf_bytes(content):
        return content, final_url, ct

    if is_pdf_content_type(ct):
        return content, final_url, ct

    if url_looks_like_pdf(final_url):
        return content, final_url, ct

    return None, final_url, ct


def download_pdf_with_retries(
        cfg: PipelineConfig,
        s: requests.Session,
        url: str,
) -> Tuple[Optional[bytes], str, str, int, float, Optional[Exception], str]:
    max_attempts = max(1, cfg.download_retries + 1)
    attempts = 0
    t0 = time.perf_counter()
    deadline = t0 + float(cfg.download_total_timeout_s)

    last_exc: Optional[Exception] = None
    final_url = url
    content_type = ""
    stop_reason = "error"

    while attempts < max_attempts:
        attempts += 1

        now = time.perf_counter()
        if now >= deadline:
            stop_reason = "deadline"
            break

        remaining = max(0.5, deadline - now)
        req_timeout = max(3, int(min(cfg.http_timeout, remaining)))

        try:
            orig_timeout = cfg.http_timeout
            cfg.http_timeout = req_timeout
            pdf_bytes, final_url, content_type = http_get_pdf(cfg, s, url)
        finally:
            cfg.http_timeout = orig_timeout

        if pdf_bytes is not None:
            elapsed = time.perf_counter() - t0
            return pdf_bytes, final_url, content_type, attempts, elapsed, None, "ok"

        last_exc = None
        stop_reason = "no_pdf"

        if attempts >= max_attempts:
            break

        base = max(0.0, cfg.download_retry_sleep)
        sleep_s = base * (2 ** (attempts - 1)) + random.uniform(0, 0.6)
        time_left = deadline - time.perf_counter()
        if time_left <= 0:
            stop_reason = "deadline"
            break
        sleep_s = min(sleep_s, time_left)

        if sleep_s > 0:
            time.sleep(sleep_s)

    elapsed = time.perf_counter() - t0
    if stop_reason == "no_pdf":
        return None, final_url, content_type, attempts, elapsed, None, "no_pdf"
    if stop_reason == "deadline":
        return None, final_url, content_type, attempts, elapsed, last_exc, "deadline"
    return None, final_url, content_type, attempts, elapsed, last_exc, "error"


def _convert_worker_fast(pdf_bytes: bytes, q: mp.Queue, max_pages: int) -> None:
    try:
        md_parts = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page_count = doc.page_count
            if page_count > max_pages:
                q.put(("skip", page_count))
                return

            for page in doc:
                text = page.get_text("text")
                if text:
                    md_parts.append(text.strip())
            md = "\n\n".join(md_parts)
        q.put(("ok", page_count, md))
    except Exception as e:
        q.put(("err", repr(e)))


def _convert_worker_pypdf(pdf_bytes: bytes, q: mp.Queue, max_pages: int) -> None:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)

        if page_count > max_pages:
            q.put(("skip", page_count))
            return

        parts: List[str] = []
        for page in reader.pages[:3]:
            txt = page.extract_text() or ""
            txt = txt.strip()
            if txt:
                parts.append(txt)

        md = "\n\n".join(parts).strip()
        q.put(("ok", page_count, md))
    except Exception as e:
        q.put(("err", repr(e)))


def convert_with_timeout(
        pdf_bytes: bytes,
        timeout_s: float,
        max_pages: int
) -> Optional[ConvertResult]:
    q: mp.Queue = mp.Queue()
    p = mp.Process(
        target=_convert_worker_pypdf,
        args=(pdf_bytes, q, max_pages),
        daemon=True,
    )
    p.start()
    p.join(timeout_s)

    if p.is_alive():
        p.terminate()
        p.join(2)
        return None

    if q.empty():
        return None

    return q.get()

def __url_to_hash(url: str, length: int = 12) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]
def __clear_title_article(title: str):
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
    safe_title = re.sub(r'[_\s]+', '_', safe_title).strip('_')
    return str(safe_title)

def scholar_item_to_article(it: ScholarItem) -> dict:
    """Обертка над ScholarItem -> Article"""
    clear_tittle = __clear_title_article(it.title)
    external_id = __url_to_hash(it.article_url)
    file_article = f"{it.date_pub}_{external_id}.md"


    return asdict(Article(
        source=it.source or "googlescholar",
        external_id=external_id,            # стабильный ID
        title=clear_tittle,
        date_sub=it.date_pub or "",
        url_article=it.article_url,
        pdf_url=it.pdf_url or "",
        authors={"raw": it.authors} if it.authors else None,
        tags=it.tags,
        abstract=it.abstract or "",
        file_article=file_article,
        category=it.category,
        text_article=it.abstract or "",
        pdf_bytes=it.pdf_bytes or b""
    ))

class ScholarParser:
    name = "scholar"

    def __init__(
            self,
            date_range: Tuple[str, str],
            config: PipelineConfig
    ):
        self.start_date = datetime.strptime(date_range[0], "%Y-%m-%d").date()
        self.end_date = datetime.strptime(date_range[1], "%Y-%m-%d").date()
        # self.start_date = start_s
        # self.end_date = end_s
        self.config = config
        # self.config.md_dir.mkdir(parents=True, exist_ok=True)
        # self.log = setup_logger(self.config)

        self.today = date.today()
        self.min_age_days = (self.today - self.end_date).days
        self.max_age_days = (self.today - self.start_date).days
        print(self.min_age_days, self.max_age_days)

        if self.min_age_days < 0 or self.max_age_days < 0:
            raise ValueError("date_range должен быть в прошлом или сегодня.")
        if self.min_age_days > self.max_age_days:
            self.min_age_days, self.max_age_days = self.max_age_days, self.min_age_days

    def run(self) -> List[ScholarItem]:
        start = time.time()
        items = self._scrape()
        end = time.time()
        print("Общее время скрапинга: %.2f sec" % (end - start))
        items = self._process(items)
        return items

    def _scrape(self) -> List[ScholarItem]:
        print("Начало скрапинга на Playwright (sync)")
        items_all: List[ScholarItem] = []

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=self.config.profile_dir,
                headless=self.config.headless,
                locale="ru-RU",
                user_agent=self.config.user_agent,
            )
            page = ctx.new_page()

            page.set_default_timeout(30_000)
            page.set_default_navigation_timeout(30_000)

            page.goto(
                build_scholar_url(self.config.query, start=0, ui_lang=self.config.ui_lang,
                                  fulltext=self.config.fulltext),
                wait_until="domcontentloaded",
            )
            jitter_sleep(2.5, 5.0)

            pages_parsed = 0
            while pages_parsed < self.config.max_pages:
                if likely_blocked(page):
                    if self.config.headless:
                        self.config.path_logs.mkdir(parents=True, exist_ok=True)
                        shot = self.config.path_logs / f"captcha_{int(time.time())}.png"
                        page.screenshot(path=str(shot), full_page=True)
                        print("Найдена CAPTCHA на странице %s (screenshot: %s)", page.url, shot)
                        raise RuntimeError(f"Найдена CAPTCHA на странице {page.url} (screenshot: {shot})")
                    else:
                        print("Нашел CAPTCHA. Решите капчу!!! И нажмите Enter")
                        input()

                try:
                    page.wait_for_selector("div.gs_r.gs_or.gs_scl", timeout=20_000)  # ждем пока загрузится страница
                except PlaywrightTimeoutError as e:
                    print("Не удалось дождаться загрузки результатов поиска. Завершение работы: %s", str(e))
                    break

                page_items = parse_results_on_page(page)
                kept_on_page = 0
                ages_all = [it.age_days for it in page_items if it.age_days is not None]

                for it in page_items:
                    print("Article", it.age_days)
                    if it.age_days is None:
                        continue
                    if it.age_days < self.min_age_days:
                        print("Skip min_age_days")
                        continue
                    if it.age_days > self.max_age_days:
                        print("Skip max_age_days")
                        continue
                    if not is_probably_pdf(it.pdf_label, it.pdf_url):
                        print("Skip тут")
                        continue

                    items_all.append(it)
                    kept_on_page += 1

                pages_parsed += 1
                pdf_candidates = sum(1 for it in page_items if is_probably_pdf(it.pdf_label, it.pdf_url))

                print(
                    "Страница %d: результатов %d, PDF-кандидатов %d, принято +%d, всего принято %d" %
                    (pages_parsed, len(page_items), pdf_candidates, kept_on_page, len(items_all))
                )

                if ages_all:
                    youngest = min(ages_all)
                    if youngest > self.max_age_days:
                        print(
                            "Остановка по возрасту: самый старый результат на странице — %d дн. (> лимита %d дн.)." %
                            (youngest, self.max_age_days)
                        )
                        break

                jitter_sleep(2.5, 5.5)
                if not click_next(page):
                    break

                page.wait_for_load_state("domcontentloaded")
                jitter_sleep(2.0, 4.0)

            ctx.close()

        items_all.sort(key=lambda x: (x.age_days if x.age_days is not None else 10 ** 9, x.title.lower()))
        return items_all

    def _process(self, items: List[ScholarItem]) -> List[ScholarItem]:
        # self.config.md_dir.mkdir(exist_ok=True)
        final_items = []

        with requests.Session() as s:
            for it in items:
                stem = safe_stem(it.date_pub, it.title)
                # md_path = self.config.md_dir / f"{stem}.md"

                try:
                    (
                        pdf_bytes,
                        final_url,
                        content_type,
                        attempts,
                        elapsed,
                        err,
                        stop_reason,
                    ) = download_pdf_with_retries(self.config, s, it.pdf_url)

                    if pdf_bytes is None:
                        print(
                            "Скачивание %s не удалось (%s) за %.2f сек, попыток %d" %
                            (it.title, stop_reason, elapsed, attempts)
                        )
                        continue

                    print("Скачивание %s: %.2f сек, попыток %d" % (it.title, elapsed, attempts))

                    print("Форматируется файл: %s, %s" % (it.title, it.article_url))
                    res = convert_with_timeout(
                        pdf_bytes=pdf_bytes,
                        timeout_s=self.config.convert_timeout_s,
                        max_pages=self.config.max_pdf_pages
                    )

                    if res is None:
                        print("Пропускаем %s: долгая конверктация" % (it.title))
                        continue

                    kind = res[0]
                    print("Статус по статье", kind)
                    if kind == "skip":
                        it.page_count = res[1]
                        print("Пропускаем %s: много страниц (%d)" % (it.title, it.page_count))
                        continue

                    if kind == "err":
                        print("Ошибка %s: ошибка конвертации %s" % (it.title, res[1]))
                        continue

                    if kind == "ok":
                        it.page_count, md = res[1], res[2]
                        it.abstract = md
                        it.pdf_bytes = pdf_bytes
                        # md_path.write_text(md.strip() + "\n", encoding="utf-8")
                        final_items.append(it)
                    else:
                        continue

                except Exception as e:
                    print("Ошибка сессии скачивания файла %s: %s" % (it.title, str(e)))

                time.sleep(self.config.sleep_between_downloads)

        print("Парсер завершил работу. Найдено статей: %d" % (len(final_items)))
        return final_items

    async def arun(self) -> list[dict]:
        items: list[ScholarItem] = await asyncio.to_thread(self.run)
        return [scholar_item_to_article(it) for it in items]
#
# async def main():
#     import json
#     data_range = ('2026-01-01', '2026-01-30')
#     cfg = PipelineConfig()
#     cfg.max_pages = 2
#     google_s = ScholarParser(date_range=data_range, config=cfg)
#     results = await asyncio.gather(
#         google_s.arun(),
#         return_exceptions=True
#     )
#     with open('google_sc.json', 'w+', encoding='utf8') as jsonfile:
#         json.dump(results, jsonfile, indent=4, ensure_ascii=False)
#
# if __name__ == "__main__":
#     asyncio.run(main())