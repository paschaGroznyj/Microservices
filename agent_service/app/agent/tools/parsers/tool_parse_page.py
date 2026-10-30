"""
agent_service/tools/parsers/tool_parse_page.py
───────────────────────────────────────────────
Tool для агента: открывает произвольную страницу через сервис парсеров,
возвращает чистый текст (заголовки, параграфы, ссылки).

Агент сам решает:
  - какой URL открыть
  - какие заголовки подставить (User-Agent, Referer и т.п.)

Подключение в langchain_agent.py:
    from tools.parsers.tool_parse_page import make_parse_page_tool
    parse_page = make_parse_page_tool(agent)
    # добавить parse_page в список tools
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
        headers: Optional[dict] = None,
    ) -> str:
        """
        Opens several web pages and extracts text content from them:
        headings (h1-h6), paragraphs, lists, tables, and links.
        HTML markup is removed, and the result is returned as plain text.
 
        Use this tool when you need to:
          - read the content of a specific website page
          - get a list of links from a page
          - extract text from a news article, schedule, employee profile, etc.
 
        Parameters:
          urls — a list of full page URLs (https://...)
          headers — HTTP request headers (optional). Examples:
                    {"User-Agent": "Mozilla/5.0 ...", "Accept-Language": "ru-RU,ru;q=0.9"}
                    If not specified, the service will use the default User-Agent.
 
        Returns the page text, up to approximately 12,000 characters.
        If the page doesn’t load, it will return an error message.

        Example call:
            parse_web_page(
                urls=["https://example1/", "https://example2/"],
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU"}
            )
        
        CALL IMMEDIATELY AFTER search_internet to read the real
        text of 2–4 found pages. Search engine snippets are insufficient  for exact numbers, dates, and quotes — take them only from there.
        """
        parsers_url = agent.services["parsers"]["url"]
 
        payload = {"urls": urls if isinstance(urls, list) else [urls], "headers": headers or {}}
 
        log.info("parse_web_page: %s", urls)

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