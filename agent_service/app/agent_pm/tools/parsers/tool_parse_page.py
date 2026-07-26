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
        Открывает несколько веб-страниц и извлекает из них текстовое содержимое:
        заголовки (h1-h6), параграфы, списки, таблицы и ссылки.
        HTML-разметка удаляется, результат возвращается как чистый текст.
 
        Используй этот инструмент когда нужно:
          - прочитать содержимое конкретной страницы сайта
          - получить список ссылок со страницы
          - вытащить текст из новостной статьи, расписания, профиля сотрудника и т.п.
 
        Параметры:
          urls     — список полных URL страниц (https://...)
          headers — HTTP-заголовки запроса (необязательно). Примеры:
                    {"User-Agent": "Mozilla/5.0 ...", "Accept-Language": "ru-RU,ru;q=0.9"}
                    Если не указаны — сервис подставит дефолтный User-Agent.
 
        Возвращает текст страницы объёмом до ~12 000 символов.
        Если страница не загрузилась — вернёт сообщение об ошибке.
 
        Пример вызова:
            parse_web_page(
                urls=["https://example1/", "https://example2/"],
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU"}
            )
        """
        parsers_url = agent.services["parsers"]["url"]
 
        payload = {"urls": urls if isinstance(urls, list) else [urls], "headers": headers or {}}
 
        log.info("parse_web_page: %s", urls)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
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
            return "Ошибка: страница не загрузилась за 60 секунд."

        except Exception as e:
            log.error("parse_web_page failed: %s", e)
            return f"Ошибка: {e}"

    return parse_web_page