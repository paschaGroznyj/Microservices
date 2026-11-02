"""
parsers_service/page_parser.py
───────────────────────────────
Универсальный парсер страницы через Playwright с кэшированием нарезанных чанков.
Вытаскивает текст в DOM-порядке (h1-h6, p, li, таблицы, pre, blockquote) и ссылки a[href],
нарезает длинные страницы на пронумерованные кусочки (0, 1, 2, ...) и кэширует их в памяти.

Используется из эндпоинта POST /parse/page
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
import re
import time
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page, Playwright

log = logging.getLogger(__name__)

_TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "blockquote"]
_MIN_LINE_LEN = 10
_GOTO_TIMEOUT_MS = 11_000
_ELEMENT_TIMEOUT_MS = 2_000

DEFAULT_CHUNK_CHARS = 20_000
MAX_TOTAL_PAGE_CHARS = 400_000
CACHE_TTL_SEC = 1800
MAX_CACHED_PAGES = 256

_pool_lock: Optional[asyncio.Lock] = None
_pool_loop: Optional[asyncio.AbstractEventLoop] = None
_pw_instance: Optional[Playwright] = None
_browser_instance: Optional[Browser] = None
_active_contexts: int = 0


@dataclass
class CachedPageChunks:
    url: str
    chunks: list[str]
    total_chars: int
    chunk_size: int
    created_at: float


_page_chunks_cache: OrderedDict[str, CachedPageChunks] = OrderedDict()


def _evict_expired_cache(now: Optional[float] = None) -> None:
    ts = now if now is not None else time.monotonic()
    expired_keys = [
        k for k, v in _page_chunks_cache.items()
        if (ts - v.created_at) > CACHE_TTL_SEC
    ]
    for k in expired_keys:
        _page_chunks_cache.pop(k, None)
    while len(_page_chunks_cache) > MAX_CACHED_PAGES:
        _page_chunks_cache.popitem(last=False)


def split_into_chunks(text: str, chunk_size: int = DEFAULT_CHUNK_CHARS) -> list[str]:
    """Режет полный текст страницы на кусочки 0, 1, 2... по границам абзацев/строк/слов."""
    text = text.strip()
    if not text:
        return [""]
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    pos = 0
    n = len(text)
    min_cut_offset = max(1, int(chunk_size * 0.65))

    while pos < n:
        remaining = n - pos
        if remaining <= chunk_size:
            tail = text[pos:].strip()
            if tail:
                chunks.append(tail)
            break

        window_end = pos + chunk_size
        cut = -1
        for sep in ("\n\n", "\n", ". ", " "):
            idx = text.rfind(sep, pos + min_cut_offset, window_end)
            if idx != -1:
                cut = idx + (len(sep) if sep == ". " else 0)
                break

        if cut <= pos:
            cut = window_end

        piece = text[pos:cut].strip()
        if piece:
            chunks.append(piece)
        pos = cut
        while pos < n and text[pos] in (" ", "\t", "\r", "\n"):
            pos += 1

    return chunks or [text[:chunk_size]]


def get_cached_page(url: str, chunk_size: int = DEFAULT_CHUNK_CHARS) -> Optional[CachedPageChunks]:
    now = time.monotonic()
    _evict_expired_cache(now)
    entry = _page_chunks_cache.get(url)
    if entry is None or entry.chunk_size != chunk_size:
        return None
    _page_chunks_cache.move_to_end(url)
    return entry


def store_cached_page(
    url: str,
    full_text: str,
    chunk_size: int = DEFAULT_CHUNK_CHARS,
) -> CachedPageChunks:
    now = time.monotonic()
    _evict_expired_cache(now)
    chunks = split_into_chunks(full_text, chunk_size=chunk_size)
    entry = CachedPageChunks(
        url=url,
        chunks=chunks,
        total_chars=len(full_text),
        chunk_size=chunk_size,
        created_at=now,
    )
    _page_chunks_cache[url] = entry
    _page_chunks_cache.move_to_end(url)
    return entry


def clear_page_cache() -> None:
    _page_chunks_cache.clear()


def format_page_chunks(
    url: str,
    entry: CachedPageChunks,
    chunk_indices: Optional[list[int]] = None,
) -> str:
    """Форматирует запрошенные по индексам кусочки страницы и добавляет подсказку по навигации."""
    total_chunks = len(entry.chunks)
    raw_indices = chunk_indices if chunk_indices else [0]

    requested: list[int] = []
    for idx in raw_indices:
        if isinstance(idx, int) and idx not in requested:
            requested.append(idx)
    if not requested:
        requested = [0]

    valid_parts: list[str] = []
    invalid_indices: list[int] = []

    for idx in requested:
        if 0 <= idx < total_chunks:
            chunk_text = entry.chunks[idx]
            if total_chunks > 1:
                header = f"[URL: {url} | Фрагмент {idx} из {total_chunks} (индексы 0..{total_chunks - 1})]"
                if chunk_text.startswith(f"[URL: {url}]"):
                    chunk_text = header + chunk_text[len(f"[URL: {url}]"):]
                else:
                    chunk_text = f"{header}\n{chunk_text}"
            valid_parts.append(chunk_text)
        else:
            invalid_indices.append(idx)

    body = "\n\n---\n\n".join(valid_parts)

    if invalid_indices:
        inv_str = ", ".join(str(i) for i in invalid_indices)
        msg = (
            f"[URL: {url}]\nФрагмент(ы) с индексом [{inv_str}] вне диапазона. "
            f"Доступные индексы для этой страницы: 0..{total_chunks - 1} (всего {total_chunks} шт., {entry.total_chars} символов)."
        )
        body = f"{body}\n\n{msg}" if body else msg

    if total_chunks > 1:
        shown_valid = [i for i in requested if 0 <= i < total_chunks]
        shown_str = ", ".join(str(i) for i in shown_valid) if shown_valid else "нет"
        max_shown = max(shown_valid) if shown_valid else -1
        remaining = [i for i in range(max_shown + 1, total_chunks)]
        if not remaining:
            remaining = [i for i in range(total_chunks) if i not in shown_valid]

        if remaining:
            next_idx = remaining[0]
            next_pair = remaining[:2]
            body += (
                f"\n\n... [Обрезано: показан фрагмент [{shown_str}] из {total_chunks} "
                f"(доступны индексы: 0..{total_chunks - 1}, всего {entry.total_chars} символов). "
                f"Все кусочки страницы уже закэшированы в парсере — чтобы прочитать следующую часть "
                f"без повторной загрузки ссылки, вызови parse_web_page(urls=['{url}'], chunk_indices=[{next_idx}]) "
                f"(или сразу несколько: chunk_indices={next_pair}).]"
            )
        else:
            body += (
                f"\n\n... [Показаны фрагменты [{shown_str}] из {total_chunks} "
                f"(доступны индексы: 0..{total_chunks - 1}, всего {entry.total_chars} символов). "
                f"Страница в кэше парсера.]"
            )

    return body


def _get_lock() -> asyncio.Lock:
    global _pool_lock, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool_lock is None or _pool_loop is not loop:
        _pool_lock = asyncio.Lock()
        _pool_loop = loop
    return _pool_lock


@asynccontextmanager
async def _shared_browser() -> AsyncIterator[Browser]:
    global _pw_instance, _browser_instance, _active_contexts
    lock = _get_lock()
    async with lock:
        if _browser_instance is None or not _browser_instance.is_connected():
            if _pw_instance is not None:
                try:
                    await _pw_instance.stop()
                except Exception:
                    pass
            _pw_instance = await async_playwright().start()
            _browser_instance = await _pw_instance.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        _active_contexts += 1
        browser = _browser_instance

    try:
        yield browser
    finally:
        async with lock:
            _active_contexts = max(0, _active_contexts - 1)
            if _active_contexts == 0:
                b, pw = _browser_instance, _pw_instance
                _browser_instance = None
                _pw_instance = None
                if b is not None:
                    try:
                        await b.close()
                    except Exception:
                        pass
                if pw is not None:
                    try:
                        await pw.stop()
                    except Exception:
                        pass


def _clean(text: str) -> str:
    """Схлопывает пробелы/переносы, убирает невидимые символы."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_DOM_EXTRACT_JS = """
(minLen) => {
    const textLines = [];
    const linkLines = [];
    const seenTexts = new Set();
    const seenHrefs = new Set();
    const skipTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'SVG', 'CANVAS', 'IFRAME', 'TEMPLATE']);

    const isHidden = (el) => {
        if (!el || el.nodeType !== 1) return true;
        if (el.hidden || el.getAttribute('aria-hidden') === 'true') return true;
        const style = window.getComputedStyle(el);
        return style.display === 'none' || style.visibility === 'hidden';
    };

    const blocks = document.querySelectorAll('h1, h2, h3, h4, h5, h6, p, li, tr, pre, blockquote');
    for (const el of blocks) {
        if (el.closest('script, style, noscript, svg, template')) continue;
        if (isHidden(el)) continue;
        const tag = el.tagName.toLowerCase();

        if ((tag === 'li' || tag === 'blockquote') && el.querySelector('p, h1, h2, h3, h4, h5, h6, tr, pre')) {
            continue;
        }
        if (tag === 'p' && el.closest('tr')) {
            continue;
        }

        let txt = '';
        if (tag === 'tr') {
            const cells = Array.from(el.querySelectorAll(':scope > th, :scope > td'))
                .map(c => (c.innerText || '').replace(/\s+/g, ' ').trim())
                .filter(Boolean);
            if (!cells.length) continue;
            txt = cells.join(' | ');
        } else {
            txt = (el.innerText || '').trim();
        }

        if (!txt) continue;
        const isHeading = /^h[1-6]$/.test(tag);
        const isTableRow = tag === 'tr';
        if (!isHeading && !isTableRow && txt.length < minLen) continue;

        if (isHeading) {
            const level = parseInt(tag[1], 10);
            txt = '#'.repeat(level) + ' ' + txt.replace(/\s+/g, ' ');
        }

        if (seenTexts.has(txt)) continue;
        seenTexts.add(txt);
        textLines.push(txt);
    }

    const anchors = document.querySelectorAll('a[href]');
    for (const a of anchors) {
        const rawHref = (a.getAttribute('href') || '').trim();
        const label = (a.innerText || '').replace(/\s+/g, ' ').trim();
        if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('javascript:') || label.length < 3) {
            continue;
        }
        let resolved = rawHref;
        try {
            resolved = new URL(rawHref, document.baseURI).href;
        } catch (_) {}
        if (seenHrefs.has(resolved)) continue;
        seenHrefs.add(resolved);
        linkLines.push(`- ${label} → ${resolved}`);
    }

    return { textLines, linkLines };
}
"""


async def browser_parser(
    url: str,
    headers: Optional[dict] = None,
    *,
    browser: Optional[Browser] = None,
    max_chars: Optional[int] = None,
    wait_ms: int = 1000,
    goto_timeout_ms: int = _GOTO_TIMEOUT_MS,
) -> str:
    """
    Открывает url через Playwright, собирает текст со страницы в DOM-порядке.
    При параллельном вызове через asyncio.gather разделяет один экземпляр Chromium.
    """
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    if headers:
        default_headers.update(headers)

    async def _parse_with_browser(b: Browser) -> tuple[list[str], list[str]]:
        context = await b.new_context(extra_http_headers=default_headers)
        context.set_default_timeout(_ELEMENT_TIMEOUT_MS)
        try:
            page: Page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout_ms)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)

            try:
                extracted = await page.evaluate(_DOM_EXTRACT_JS, _MIN_LINE_LEN)
                if isinstance(extracted, dict) and "textLines" in extracted:
                    return extracted.get("textLines") or [], extracted.get("linkLines") or []
            except Exception:
                pass

            text_lines: list[str] = []
            for tag in _TEXT_TAGS:
                elements = await page.locator(tag).all()
                for el in elements:
                    try:
                        txt = (await el.inner_text(timeout=_ELEMENT_TIMEOUT_MS)).strip()
                        if len(txt) >= _MIN_LINE_LEN:
                            if tag.startswith("h") and tag[1:].isdigit():
                                level = int(tag[1])
                                text_lines.append("#" * level + " " + txt)
                            else:
                                text_lines.append(txt)
                    except Exception:
                        continue

            link_lines: list[str] = []
            anchors = await page.locator("a[href]").all()
            seen_hrefs: set[str] = set()

            for a in anchors:
                try:
                    href = await a.get_attribute("href", timeout=_ELEMENT_TIMEOUT_MS) or ""
                    label = (await a.inner_text(timeout=_ELEMENT_TIMEOUT_MS)).strip()

                    if (
                        not href
                        or href.startswith("#")
                        or href.startswith("javascript:")
                        or href in seen_hrefs
                        or len(label) < 3
                    ):
                        continue

                    if href.startswith("/"):
                        parsed = urlparse(url)
                        href = f"{parsed.scheme}://{parsed.netloc}{href}"

                    seen_hrefs.add(href)
                    link_lines.append(f"- {label} → {href}")
                except Exception:
                    continue

            return text_lines, link_lines
        finally:
            await context.close()

    try:
        if browser is not None:
            text_lines, link_lines = await _parse_with_browser(browser)
        else:
            async with _shared_browser() as shared_b:
                text_lines, link_lines = await _parse_with_browser(shared_b)
    except Exception as e:
        log.warning("Ошибка загрузки страницы %s: %s", url, e)
        return f"[URL: {url}]\nОшибка загрузки страницы: {e}"

    parts: list[str] = [f"[URL: {url}]\n"]

    if text_lines:
        parts.append("## Содержимое страницы\n")
        parts.append("\n".join(text_lines))

    if link_lines:
        parts.append("\n\n## Ссылки на странице\n")
        parts.append("\n".join(link_lines[:100]))

    result = _clean("\n".join(parts))

    if len(result) > MAX_TOTAL_PAGE_CHARS:
        result = result[:MAX_TOTAL_PAGE_CHARS]

    if max_chars is not None and len(result) > max_chars:
        result = result[:max_chars] + f"\n\n... (обрезано, всего {len(result)} символов)"

    log.info("Страница %s: извлечено %d символов", url, len(result))
    return result
