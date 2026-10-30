"""
parsers_service/page_parser.py
───────────────────────────────
Универсальный парсер страницы через Playwright.
Вытаскивает текст из p, h1-h6, li, a[href] и возвращает
чистый текст без HTML-разметки.

Используется из эндпоинта POST /parse/page
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import re
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page, Playwright

log = logging.getLogger(__name__)

_TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "blockquote"]
_MIN_LINE_LEN = 10
_GOTO_TIMEOUT_MS = 11_000
_ELEMENT_TIMEOUT_MS = 2_000

_pool_lock: Optional[asyncio.Lock] = None
_pool_loop: Optional[asyncio.AbstractEventLoop] = None
_pw_instance: Optional[Playwright] = None
_browser_instance: Optional[Browser] = None
_active_contexts: int = 0


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
            _browser_instance = await _pw_instance.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
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


async def browser_parser(
    url: str,
    headers: Optional[dict] = None,
    *,
    browser: Optional[Browser] = None,
    max_chars: int = 12_000,
    wait_ms: int = 1000,
    goto_timeout_ms: int = _GOTO_TIMEOUT_MS,
) -> str:
    """
    Открывает url через Playwright, собирает текст со страницы.
    При параллельном вызове через asyncio.gather разделяет один экземпляр браузера.
    """
    async def _parse_with_browser(b: Browser) -> tuple[list[str], list[str]]:
        context = await b.new_context(
            extra_http_headers=headers or {},
            user_agent=(
                headers.get("User-Agent")
                if headers and "User-Agent" in headers
                else (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            ),
        )
        context.set_default_timeout(_ELEMENT_TIMEOUT_MS)
        try:
            page: Page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout_ms)
            if wait_ms > 0:
                await page.wait_for_timeout(wait_ms)

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

    if len(result) > max_chars:
        result = result[:max_chars] + f"\n\n... (обрезано, всего {len(result)} символов)"

    log.info("Страница %s: извлечено %d символов", url, len(result))
    return result
