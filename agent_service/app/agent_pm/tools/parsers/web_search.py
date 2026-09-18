import os
import asyncio
import logging
import httpx
from typing import List, Optional
from langchain.tools import tool
from agent_pm.core.config import SEARXNG_BASE_URL

log = logging.getLogger("searxng_tool")

# Базовый URL контейнера SearXNG (в docker-сети — по имени сервиса)
SEARXNG_SEARCH_URL = f"{SEARXNG_BASE_URL}/search"

# Таймаут клиента (сек). SearXNG сам агрегирует движки, поэтому держим с запасом.
SEARXNG_TIMEOUT = float(os.environ.get("SEARXNG_TIMEOUT", "60.0"))


def make_web_search():

    @tool
    async def search_internet(
            query: str,
            categories: Optional[List[str]] = None,
            engines: Optional[List[str]] = None,
            max_results: int = 10,
            language: Optional[str] = None,
            time_range: Optional[str] = None,
    ) -> str:
        """
        Выполняет поиск информации в интернете через контейнер SearXNG (JSON API).
        Используй инструмент для поиска свежих новостей, документации, статей или фактов.

        ВЫЗЫВАЙ ПЕРВЫМ ДЕЙСТВИЕМ на любой вопрос про актуальные данные:
        курсы валют, цены, котировки, новости, спорт, погоду, статусы
        событий, факты о людях/компаниях, «сегодня/сейчас/последние».
        Не отвечай на такие вопросы по памяти — твои знания устарели.

        После получения выдачи ОТКРЫВАЙ найденные ссылки через
        parse_web_page (2-4 штуки) и бери цифры/цитаты из текста страниц,
        а не из сниппетов.

        :param query: Поисковый запрос (до 512 символов).
        :param categories: Категории поиска, например ["general"] или ["it"]. Нельзя использовать вместе с engines.
        :param engines: Список конкретных движков, например ["google", "yandex", "duckduckgo"].
        :param max_results: Количество результатов от 1 до 20 (по умолчанию 10).
        :param language: Код языка выдачи, например "ru", "en" (опционально).
        :param time_range: Ограничение по времени: "day", "week", "month", "year" (опционально).
        """
        q = (query or "").strip()
        if not q:
            return "❌ Ошибка: пустой поисковый запрос."

        limit = max(1, min(int(max_results or 10), 20))

        params = {
            "q": q[:512],
            "format": "json",
            "pageno": 1,
        }

        # В SearXNG engines и categories передаются строкой через запятую.
        # Взаимоисключение оставляем: engines имеет приоритет.
        if engines:
            params["engines"] = ",".join(engines[:10])
        elif categories:
            params["categories"] = ",".join(categories[:5])

        if language:
            params["language"] = language
        if time_range in ("day", "week", "month", "year"):
            params["time_range"] = time_range

        headers = {
            # JSON API отдаётся только с адекватным User-Agent на многих инстансах
            "User-Agent": "catchpm-search/1.0",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=SEARXNG_TIMEOUT) as client:
            for attempt in range(2):  # attempt 0 и 1 (максимум 1 повтор)
                try:
                    response = await client.get(
                        SEARXNG_SEARCH_URL, params=params, headers=headers
                    )

                    # Успешный ответ
                    if response.status_code == 200:
                        try:
                            data = response.json()
                        except Exception:
                            # Инстанс мог не разрешить формат json (см. settings.yml -> search.formats)
                            snippet = response.text[:300]
                            return (
                                "❌ SearXNG вернул не-JSON ответ. "
                                "Проверь, что в settings.yml включён формат json "
                                f"(search.formats). Начало ответа: {snippet}"
                            )

                        results = data.get("results", []) or []
                        if not results:
                            return f"По запросу «{q}» ничего не найдено."

                        results = results[:limit]

                        formatted_out = []
                        for i, item in enumerate(results, 1):
                            title = item.get("title") or "Без названия"
                            url = item.get("url") or ""
                            content = item.get("content") or ""
                            # В SearXNG движок обычно в поле engine (строка),
                            # реже в engines (список) — поддержим оба.
                            engine = item.get("engine")
                            if not engine and isinstance(item.get("engines"), list):
                                engine = ", ".join(item["engines"])
                            engine = engine or ""

                            formatted_out.append(
                                f"[S{i}] {title}\n"
                                f"URL: {url}\n"
                                f"Движок: {engine}\n"
                                f"Сниппет: {content}\n"
                            )

                        return "\n---\n".join(formatted_out)

                    # Ошибочные ответы
                    detail = response.text[:500]
                    req_id = response.headers.get("X-Request-ID", "N/A")

                    if response.status_code == 403:
                        return (
                            f"❌ [HTTP 403 | {req_id}] Доступ запрещён. "
                            "Возможно, ограничен доступ к JSON API или сработал rate-limit."
                        )
                    if response.status_code == 429:
                        # too many requests — можно один раз повторить
                        log.warning("SearXNG 429 (req_id=%s): %s", req_id, detail)
                        if attempt == 0:
                            await asyncio.sleep(2.0)
                            continue
                        return f"❌ [HTTP 429 | {req_id}] Слишком много запросов (rate limit)."

                    log.warning(
                        "SearXNG HTTP %s (req_id=%s): %s",
                        response.status_code, req_id, detail
                    )

                    # Транзиентные серверные ошибки — один повтор
                    if response.status_code in (500, 502, 503, 504) and attempt == 0:
                        await asyncio.sleep(2.0)
                        continue

                    return f"❌ Ошибка поиска [HTTP {response.status_code} | ID: {req_id}]: {detail}"

                except httpx.RequestError as e:
                    log.error("Сетевая ошибка при запросе к SearXNG: %s", e)
                    if attempt == 0:
                        await asyncio.sleep(2.0)
                        continue
                    return f"❌ Сетевая ошибка при попытке связаться с SearXNG: {e}"

        return "❌ Не удалось выполнить поиск после повторной попытки."

    return search_internet
