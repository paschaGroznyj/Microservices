"""
agent_service/tools/parsers/tool_parse_page.py
───────────────────────────────────────────────
Tool для агента: открывает произвольную страницу через сервис парсеров,
возвращает чистый текст (заголовки, параграфы, таблицы, ссылки) по фрагментам (чанкам).

Полный текст страницы нарезается на кусочки 0, 1, 2... по ~20 000 символов
и кэшируется на стороне parsers_service. При первом вызове возвращается кусочек 0;
если страница длиннее, агент может запросить следующие кусочки через chunk_indices
без повторной загрузки страницы.
"""

import logging
from typing import Optional

import httpx
from langchain.tools import tool

log = logging.getLogger(__name__)


def make_parse_page_tool(agent):
    """
    Фабрика. agent используется только для доступа к конфигу сервисов (SERVICES).
    """

    @tool
    async def parse_web_page(
        urls: str | list[str],
        chunk_indices: Optional[int | list[int]] = None,
        headers: Optional[dict] = None,
    ) -> str:
        """
        Opens web pages and extracts text content in document order:
        headings (h1-h6), paragraphs, lists, tables, code blocks, and links.
        HTML markup is removed, and the result is returned as plain text.

        Long pages are automatically split into numbered chunks (0, 1, 2, ...) of ~20,000 characters
        and cached in the parser service. By default, chunk 0 is returned.
        If a page has more chunks, a notice at the end of the output shows the available
        chunk indices (0..N). To read subsequent parts without re-fetching the page,
        call parse_web_page again with the same URL and chunk_indices=[1] (or [1, 2]).

        Parameters:
          urls — a page URL or list of full page URLs (https://...)
          chunk_indices — optional chunk index or list of indices to retrieve from the cached
                          page (e.g. 1 or [1, 2]). Defaults to [0] (the first part).
          headers — HTTP request headers (optional). Examples:
                    {"User-Agent": "Mozilla/5.0 ...", "Accept-Language": "ru-RU,ru;q=0.9"}

        Example calls:
            # Initial read (returns chunk 0 and caches all chunks):
            parse_web_page(urls=["https://example1/", "https://example2/"])

            # Read the next part(s) of a long page from cache by index:
            parse_web_page(urls=["https://example1/"], chunk_indices=[1])
            parse_web_page(urls=["https://example1/"], chunk_indices=[1, 2])

        CALL IMMEDIATELY AFTER search_internet to read the real
        text of 2–4 found pages. Search engine snippets are insufficient for exact numbers, dates, and quotes — take them only from there.
        """
        parsers_url = agent.services["parsers"]["url"]

        normalized_indices: Optional[list[int]] = None
        if chunk_indices is not None:
            if isinstance(chunk_indices, int):
                normalized_indices = [chunk_indices]
            elif isinstance(chunk_indices, list):
                normalized_indices = [int(i) for i in chunk_indices if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit()]

        payload = {
            "urls": urls if isinstance(urls, list) else [urls],
            "headers": headers or {},
        }
        if normalized_indices:
            payload["chunk_indices"] = normalized_indices

        log.info("parse_web_page: urls=%s chunk_indices=%s", urls, normalized_indices)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{parsers_url}/parse/page",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("text", "Сервис парсеров не вернул текст.")

        except httpx.HTTPStatusError as e:
            log.error("parse_web_page HTTP %s: %s", e.response.status_code, e.response.text[:200])
            return f"Ошибка парсера: HTTP {e.response.status_code}"

        except httpx.TimeoutException:
            log.error("parse_web_page timeout:")
            return "Ошибка: страница не загрузилась за 30 секунд."

        except Exception as e:
            log.error("parse_web_page failed: %s", e)
            return f"Ошибка: {e}"

    return parse_web_page
