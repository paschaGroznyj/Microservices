"""
parsers_service/page_parser.py
───────────────────────────────
Универсальный парсер страницы через Playwright.
Вытаскивает текст из p, h1-h6, li, a[href] и возвращает
чистый текст без HTML-разметки.

Используется из эндпоинта POST /parse/page
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page

log = logging.getLogger(__name__)

# Теги из которых берём текст
_TEXT_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "blockquote"]

# Минимальная длина строки чтобы не тащить мусор типа "•" или одиночных символов
_MIN_LINE_LEN = 10


def _clean(text: str) -> str:
    """Схлопывает пробелы/переносы, убирает невидимые символы."""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def browser_parser(
    url: str,
    headers: Optional[dict] = None,
    *,
    max_chars: int = 12_000,
    wait_ms: int = 1500,
) -> str:
    """
    Открывает url через Playwright, собирает текст со страницы.

    Возвращает строку вида:
        [URL: https://...]

        ## Заголовки / параграфы
        ...текст...

        ## Ссылки
        - Название → https://...

    :param url:       URL страницы
    :param headers:   HTTP-заголовки (User-Agent и т.п.), агент генерит сам
    :param max_chars: максимум символов в результате (чтобы не взрывать контекст LLM)
    :param wait_ms:   пауза после загрузки страницы (мс) для JS-рендера
    """
    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
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
            page: Page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(wait_ms)
            except Exception as e:
                log.warning("Ошибка загрузки страницы %s: %s", url, e)
                return f"Ошибка загрузки страницы: {e}"

            # ── Текстовые блоки ─────────────────────────────────────────────────
            text_lines: list[str] = []

            for tag in _TEXT_TAGS:
                elements = await page.locator(tag).all()
                for el in elements:
                    try:
                        txt = (await el.inner_text()).strip()
                        if len(txt) >= _MIN_LINE_LEN:
                            # Для заголовков добавляем маркер
                            if tag.startswith("h") and tag[1:].isdigit():
                                level = int(tag[1])
                                text_lines.append("#" * level + " " + txt)
                            else:
                                text_lines.append(txt)
                    except Exception:
                        continue

            # ── Ссылки ──────────────────────────────────────────────────────────
            link_lines: list[str] = []
            anchors = await page.locator("a[href]").all()
            seen_hrefs: set[str] = set()

            for a in anchors:
                try:
                    href  = await a.get_attribute("href") or ""
                    label = (await a.inner_text()).strip()

                    # Пропускаем якоря, пустые, дубли, js-ссылки
                    if (
                        not href
                        or href.startswith("#")
                        or href.startswith("javascript:")
                        or href in seen_hrefs
                        or len(label) < 3
                    ):
                        continue

                    # Делаем абсолютную ссылку если relative
                    if href.startswith("/"):
                        from urllib.parse import urlparse
                        parsed = urlparse(url)
                        href = f"{parsed.scheme}://{parsed.netloc}{href}"

                    seen_hrefs.add(href)
                    link_lines.append(f"- {label} → {href}")
                except Exception:
                    continue

        finally:
            await browser.close()

    # ── Сборка результата ────────────────────────────────────────────────────
    parts: list[str] = [f"[URL: {url}]\n"]

    if text_lines:
        parts.append("## Содержимое страницы\n")
        parts.append("\n".join(text_lines))

    if link_lines:
        parts.append("\n\n## Ссылки на странице\n")
        parts.append("\n".join(link_lines[:100]))   # не более 100 ссылок

    result = _clean("\n".join(parts))

    # Обрезаем чтобы не взрывать контекст LLM
    if len(result) > max_chars:
        result = result[:max_chars] + f"\n\n... (обрезано, всего {len(result)} символов)"

    log.info("Страница %s: извлечено %d символов", url, len(result))
    return result