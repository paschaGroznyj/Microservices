import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse
import multiprocessing as mp

import requests
import fitz
import pymupdf4llm
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


@dataclass
class PipelineConfig:
    query: str
    days_limit: int
    max_pages: int
    max_pdf_pages: int
    profile_dir: str
    headless: bool
    fulltext: bool
    ui_lang: str
    stop_by_age: bool
    md_dir: Path
    report_json: Path
    http_timeout: int

    download_retries: int
    download_retry_sleep: float 

    download_total_timeout_s: float

    sleep_between_downloads: float
    max_bytes: int
    skip_existing: bool
    clean_markdown: bool
    verbose: bool
    max_items: Optional[int]
    user_agent: str

    convert_timeout_s: float



@dataclass
class ScholarItem:
    title: str
    article_url: str
    right_label: str
    right_url: str
    meta: str
    snippet: str
    age_days: Optional[int]
    pub_date: Optional[str]
    cluster_id: Optional[str]
    page_count: Optional[int] = None


PDF_URL_RE = re.compile(r"\.pdf(\?|$)", re.IGNORECASE)

def _convert_worker(pdf_bytes: bytes, filename: str, q: mp.Queue) -> None:
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            md = pymupdf4llm.to_markdown(doc, filename=filename) or ""
        q.put(md)
    except Exception:
        q.put(None)


def convert_with_timeout(
    pdf_bytes: bytes,
    filename: str,
    timeout_s: float,
) -> Optional[str]:
    q: mp.Queue = mp.Queue()
    p = mp.Process(
        target=_convert_worker,
        args=(pdf_bytes, filename, q),
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

def log(cfg: PipelineConfig, msg: str) -> None:
    if cfg.verbose:
        print(msg)


def log_skip(cfg: PipelineConfig, it: ScholarItem, reason: str) -> None:
    if not cfg.verbose:
        return
    title = it.title or "<no title>"
    url = it.right_url or "<no right_url>"
    print(f"Skip: {title} | {reason} | {url}")


def jitter_sleep(a: float = 1.6, b: float = 3.8) -> None:
    time.sleep(random.uniform(a, b))


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def slugify(s: str, max_len: int = 80) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:max_len] if s else "untitled"


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:10]


def safe_stem(i: int, title: str, url: str) -> str:
    return f"{i:04d}__{slugify(title)}__{sha1(url or title)}"


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


def build_scholar_url(query: str, start: int, ui_lang: str = "ru", fulltext: bool = False) -> str:
    params = {
        "hl": ui_lang,
        "as_sdt": "1,5" if fulltext else "0,5",
        "q": query,
        "scisbd": "1",
        "start": str(start),
    }
    print("https://scholar.google.com/scholar?" + urlencode(params))
    return "https://scholar.google.com/scholar?" + urlencode(params)



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


def extract_cluster_id(result_locator) -> Optional[str]:
    try:
        links = result_locator.locator("a").all()
        for a in links:
            href = a.get_attribute("href") or ""
            m = re.search(r"[?&]cluster=(\d+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


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


def is_probably_pdf(right_label: str, right_url: str) -> bool:
    if not right_url:
        return False
    label = (right_label or "").strip().lower()
    if "pdf" in label:
        return True
    return bool(PDF_URL_RE.search(right_url))


def parse_results_on_page(page) -> List[ScholarItem]:
    items: List[ScholarItem] = []
    results = page.locator("div.gs_r.gs_or.gs_scl")
    n = results.count()

    for i in range(n):
        r = results.nth(i)

        right_label, right_url = get_right_link(r)
        if not right_url:
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

        cluster_id = extract_cluster_id(r)

        if title:
            items.append(
                ScholarItem(
                    title=title,
                    article_url=article_url,
                    right_label=right_label,
                    right_url=right_url,
                    meta=meta,
                    snippet=snippet,
                    age_days=age_days,
                    pub_date=compute_pub_date_iso(age_days),
                    cluster_id=cluster_id,
                )
            )

    return items


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

        log(cfg, f"Retry {attempts + 1}/{max_attempts} after no PDF (ct={content_type or 'n/a'}), sleep={sleep_s:.2f}s")
        if sleep_s > 0:
            time.sleep(sleep_s)

    elapsed = time.perf_counter() - t0
    if stop_reason == "no_pdf":
        return None, final_url, content_type, attempts, elapsed, None, "no_pdf"
    if stop_reason == "deadline":
        return None, final_url, content_type, attempts, elapsed, last_exc, "deadline"
    return None, final_url, content_type, attempts, elapsed, last_exc, "error"


def clean_markdown(md: str) -> str:
    md = re.sub(r"-\n(?=[a-zA-Z])", "", md)

    lines = md.splitlines()
    out: List[str] = []

    for i, line in enumerate(lines):
        line = line.rstrip()

        if not line:
            out.append("")
            continue

        if i + 1 < len(lines):
            nxt = lines[i + 1].lstrip()
            hard_break = (
                nxt.startswith(("#", "-", "*", "+", "|", ">"))
                or re.match(r"\d+\.", nxt)
                or line.endswith(":")
            )
            if not hard_break:
                out.append(line + " ")
                continue

        out.append(line)

    md = "".join(out)
    md = re.sub(r"[ \t]+", " ", md)
    md = re.sub(r"\n{3,}", "\n\n", md)

    return md.strip() + "\n"


def scrape_scholar(cfg: PipelineConfig) -> List[ScholarItem]:
    uniq: Dict[str, ScholarItem] = {}

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=cfg.profile_dir,
            headless=cfg.headless,
            locale="ru-RU",
            user_agent=cfg.user_agent,
        )
        page = ctx.new_page()

        page.set_default_timeout(30_000)
        page.set_default_navigation_timeout(30_000)

        page.goto(
            build_scholar_url(cfg.query, start=0, ui_lang=cfg.ui_lang, fulltext=cfg.fulltext),
            wait_until="domcontentloaded",
        )
        jitter_sleep(2.5, 5.0)

        pages_parsed = 0
        while pages_parsed < cfg.max_pages:
            if likely_blocked(page):
                print("CAPTCHA detected. Solve it in the browser, then press Enter here...")
                try:
                    input()
                except KeyboardInterrupt:
                    print("\nInterrupted by user.")
                    break
                jitter_sleep(2.0, 4.0)

            try:
                page.wait_for_selector("div.gs_r.gs_or.gs_scl", timeout=20_000)
            except PlaywrightTimeoutError:
                print("No results selector found. Stopping.")
                break

            page_items = parse_results_on_page(page)
            kept_on_page = 0
            ages_all = [it.age_days for it in page_items if it.age_days is not None]

            for it in page_items:
                if it.age_days is None:
                    log_skip(cfg, it, "missing age_days")
                    continue
                if it.age_days > cfg.days_limit:
                    log_skip(cfg, it, f"age_days={it.age_days} > limit={cfg.days_limit}")
                    continue
                if not is_probably_pdf(it.right_label, it.right_url):
                    log_skip(cfg, it, "not pdf (label/url)")
                    continue

                key = it.cluster_id or sha1(f"{it.title}|{it.article_url}|{it.right_url}")
                if key not in uniq:
                    uniq[key] = it
                    kept_on_page += 1
                else:
                    log_skip(cfg, it, "duplicate")

            pages_parsed += 1
            pdf_candidates = sum(1 for it in page_items if is_probably_pdf(it.right_label, it.right_url))
            print(
                f"Страница {pages_parsed}: результатов {len(page_items)}, PDF-кандидатов {pdf_candidates}, "
                f"принято +{kept_on_page}, всего принято {len(uniq)}"
            )

            if cfg.stop_by_age and ages_all:
                youngest = min(ages_all)
                oldest = max(ages_all)
                if youngest > cfg.days_limit:
                    print(
                        f"Остановка по возрасту: самый свежий результат на странице — {youngest} дн. "
                        f"(> лимита {cfg.days_limit} дн.)."
                    )
                    break
                if kept_on_page == 0 and oldest > cfg.days_limit:
                    print(
                        f"Остановка по возрасту: на странице не добавлено новых элементов, "
                        f"самый старый — {oldest} дн. (> лимита {cfg.days_limit} дн.)."
                    )
                    break

            jitter_sleep(2.5, 5.5)
            if not click_next(page):
                break

            page.wait_for_load_state("domcontentloaded")
            jitter_sleep(2.0, 4.0)

        ctx.close()

    items = list(uniq.values())
    items.sort(key=lambda x: (x.age_days if x.age_days is not None else 10**9, x.title.lower()))
    return items


def scholar_item_to_dict(it: ScholarItem) -> Dict[str, str | int | None]:
    return {
        "title": it.title,
        "article_url": it.article_url,
        "right_label": it.right_label,
        "right_url": it.right_url,
        "meta": it.meta,
        "snippet": it.snippet,
        "age_days": it.age_days,
        "pub_date": it.pub_date,
        "cluster_id": it.cluster_id,
        "page_count": it.page_count,
    }


def save_scraped_items(
    cfg: PipelineConfig,
    items: List[ScholarItem],
    path: Optional[Path] = None,
) -> Path:
    if path is None:
        path = cfg.report_json.with_name("full_items.json")
    payload = [scholar_item_to_dict(it) for it in items]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def process_items(cfg: PipelineConfig, items: List[ScholarItem]) -> None:
    cfg.md_dir.mkdir(exist_ok=True)
    report: List[Dict[str, str | int | None]] = []

    if cfg.max_items is not None:
        items = items[:cfg.max_items]

    with requests.Session() as s:
        for i, it in enumerate(items, start=1):
            stem = safe_stem(i, it.title, it.right_url)
            md_path = cfg.md_dir / f"{stem}.md"
            pub_date = it.pub_date or compute_pub_date_iso(it.age_days)

            if cfg.skip_existing and md_path.exists():
                report.append(
                    {
                        "id": str(i),
                        "title": it.title,
                        "pub_date": pub_date,
                        "article_url": it.article_url,
                        "right_url": it.right_url,
                        "md_path": str(md_path),
                        "page_count": it.page_count,
                    }
                )
                continue

            try:
                (
                    pdf_bytes,
                    final_url,
                    content_type,
                    attempts,
                    elapsed,
                    err,
                    stop_reason,
                ) = download_pdf_with_retries(cfg, s, it.right_url)

                if pdf_bytes is None:
                    if stop_reason == "deadline":
                        log(cfg, f"DEADLINE {i:03d}: {it.title} url={final_url}")
                    elif stop_reason == "no_pdf":
                        log(cfg, f"NO_PDF {i:03d}: ct={content_type or 'n/a'} url={final_url}")
                    elif err is not None:
                        log(cfg, f"ERR {i:03d} download: {it.title} -> {repr(err)}")
                    else:
                        log(cfg, f"Skip {i:03d}: unknown download failure url={final_url}")

                    print(
                        f"Скачивание {i:03d} не удалось ({stop_reason}) за {elapsed:.2f} сек, попыток {attempts}"
                    )
                    continue

                print(f"Скачивание {i:03d}: {elapsed:.2f} сек, попыток {attempts}")

                with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                    it.page_count = doc.page_count
                    if it.page_count > cfg.max_pdf_pages:
                        log(cfg, f"Skip by pages: {it.page_count} > {cfg.max_pdf_pages} | {it.title}")
                        continue

                    print(f"Форматируется файл: {it.title}, {it.article_url}")
                    start = time.perf_counter()
                    md = convert_with_timeout(
                        pdf_bytes=pdf_bytes,
                        filename=f"{stem}.pdf",
                        timeout_s=cfg.convert_timeout_s,
                    )
                    elapsed = time.perf_counter() - start
                    print(f"Затраченное время на конвертацию: {elapsed:.4f}s")
                    if md is None:
                        print(f"SKIP {i:03d}: convert timeout")
                        continue

                if cfg.clean_markdown:
                    md = clean_markdown(md)

                md_path.write_text(md.strip() + "\n", encoding="utf-8")

                report.append(
                    {
                        "id": str(i),
                        "title": it.title,
                        "pub_date": pub_date,
                        "article_url": it.article_url,
                        "right_url": it.right_url,
                        "md_path": str(md_path),
                        "page_count": it.page_count,
                        "download_seconds": round(elapsed, 3),
                        "download_attempts": attempts,
                        "download_final_url": final_url,
                        "download_content_type": content_type,
                    }
                )
                log(cfg, f"OK {i:03d}: {it.title}")

            except Exception as e:
                log(cfg, f"ERR {i:03d}: {it.title} -> {repr(e)}")

            time.sleep(cfg.sleep_between_downloads)

    cfg.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_pipeline(cfg: PipelineConfig) -> None:
    items = scrape_scholar(cfg)
    save_scraped_items(cfg, items)
    process_items(cfg, items)


if __name__ == "__main__":
    cfg = PipelineConfig(
        query='"process mining"',
        # query='allintitle: "process mining"',
        days_limit=10, # от 21 января 2026 года по 30 сентября 2025
        max_pages=50,
        max_pdf_pages=20,
        profile_dir="pw_profile_scholar",
        headless=False,
        fulltext=False,
        ui_lang="ru",
        stop_by_age=True,
        md_dir=Path("md"),
        report_json=Path("final_items.json"),
        http_timeout=45,
        download_retries=2,
        download_retry_sleep=1.0,
        download_total_timeout_s=40.0, 
        sleep_between_downloads=1.2,
        max_bytes=30_000_000,
        skip_existing=True,
        clean_markdown=False,
        verbose=False,
        max_items=None,
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        convert_timeout_s=100.0,
    )
    run_pipeline(cfg)