import os
import asyncio
import logging
import httpx
from typing import List, Optional
from langchain.tools import tool

log = logging.getLogger("searxng_tool")

SEARXNG_SEARCH_URL = os.environ.get("SEARXNG_SEARCH_URL", "http://87.242.120.231:8080/v1/search")
SEARXNG_API_TOKEN = os.environ.get("SEARXNG_API_TOKEN", "s4F6DbKqyJ0X4zri4uh4ejIRh1i72iwc0jxJMMg9NRDqFZ6OuyI7cyeoJ3wgcJlS")

def make_web_search():

    @tool
    async def search_internet(
            query: str,
            categories: Optional[List[str]] = None,
            engines: Optional[List[str]] = None,
            max_results: int = 10
    ) -> str:
        """
        Выполняет поиск информации в интернете через внутренний шлюз SearXNG.
        Используй инструмент для поиска свежих новостей, документации, статей или фактов.

        :param query: Поисковый запрос (до 512 символов).
        :param categories: Категории поиска, например ["general"] или ["it"]. Нельзя использовать вместе с engines.
        :param engines: Список конкретных движков, например ["google cse", "yandex", "duckduckgo"].
        :param max_results: Количество результатов от 1 до 20 (по умолчанию 10).
        """
        if not SEARXNG_API_TOKEN:
            return "❌ Ошибка: Не задан SEARXNG_API_TOKEN в переменных окружения."

        headers = {
            "Authorization": f"Bearer {SEARXNG_API_TOKEN}",
            "Content-Type": "application/json"
        }

        # Формируем валидный payload по гайду
        payload = {
            "query": query[:512],
            "max_results": max(1, min(max_results, 20))
        }

        # По документу engines и categories взаимно исключают друг друга
        if engines:
            payload["engines"] = engines[:10]
        elif categories:
            payload["categories"] = categories[:5]

        # Таймаут на клиентской стороне согласно гайду — 60 секунд
        async with httpx.AsyncClient(timeout=60.0) as client:
            for attempt in range(2):  # Допускается максимум 1 повтор (attempt 0 и 1)
                try:
                    response = await client.post(SEARXNG_SEARCH_URL, json=payload, headers=headers)

                    # Обработка успешного ответа (HTTP 200)
                    if response.status_code == 200:
                        data = response.json()
                        results = data.get("results", [])

                        if not results:
                            return f"По запросу «{query}» ничего не найдено."

                        formatted_out = []
                        for item in results:
                            cit_id = item.get("citation_id", "S?")
                            title = item.get("title", "Без названия")
                            url = item.get("url", "")
                            content = item.get("content", "")
                            engine_list = ", ".join(item.get("engines", []))

                            formatted_out.append(
                                f"[{cit_id}] {title}\n"
                                f"URL: {url}\n"
                                f"Движки: {engine_list}\n"
                                f"Сниппет: {content}\n"
                            )

                        return "\n---\n".join(formatted_out)

                    # Обработка ошибок RFC 9457 (problem+json)
                    error_data = {}
                    try:
                        error_data = response.json()
                    except Exception:
                        pass

                    code = error_data.get("code", "unknown_error")
                    detail = error_data.get("detail", response.text)
                    req_id = error_data.get("request_id", response.headers.get("X-Request-ID", "N/A"))
                    retryable = error_data.get("retryable", False)

                    # Необратимые ошибки по спецификации
                    if response.status_code == 401:
                        return f"❌ [HTTP 401 | {req_id}] Ошибка авторизации. Проверьте SEARXNG_API_TOKEN."
                    if response.status_code == 403:
                        return f"❌ [HTTP 403 | {req_id}] Превышена квота (residential quota exceeded). Повтор запрещен."

                    log.warning("SearXNG Search Error %s (code=%s, req_id=%s): %s", response.status_code, code, req_id,
                                detail)

                    # Повторяем ТОЛЬКО если retryable == True и это первая попытка
                    if retryable and attempt == 0:
                        await asyncio.sleep(2.0)  # Джиттер/пауза перед повтором
                        continue

                    return f"❌ Ошибка поиска [{code} | ID: {req_id}]: {detail}"

                except httpx.RequestError as e:
                    log.error("Сетевая ошибка при запросе к SearXNG: %s", e)
                    if attempt == 0:
                        await asyncio.sleep(2.0)
                        continue
                    return f"❌ Сетевая ошибка при попытке связаться с поисковым шлюзом: {e}"

        return "❌ Не удалось выполнить поиск после повторной попытки."
    
    return search_internet