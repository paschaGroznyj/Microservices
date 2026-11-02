from typing import Optional
from pydantic import BaseModel
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import logging

from universal.page_parser import (
    browser_parser,
    get_cached_page,
    store_cached_page,
    format_page_chunks,
)

from core.config import PORT_PARSERS_SERVICE


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = FastAPI(title="Parsers Service")


class ParsePageRequest(BaseModel):
    urls: list[str]
    headers: Optional[dict] = None
    chunk_indices: Optional[list[int]] = None
    force_refresh: bool = False


def _is_error_text(text: str) -> bool:
    return "Ошибка загрузки страницы:" in text or "Ошибка парсинга страницы:" in text


@app.post("/parse/page")
async def parse_page(req: ParsePageRequest) -> JSONResponse:
    """
    Парсит список страниц параллельно в одном браузере, режет каждую страницу
    на кусочки (0, 1, 2, ...) и кэширует их в памяти.
    При повторном обращении к URL (например, с chunk_indices=[1]) отдаёт кусочки из кэша
    без повторного запуска браузера.
    """
    if not req.urls:
        return JSONResponse({"urls": [], "text": ""})

    to_fetch: list[str] = []
    seen_fetch: set[str] = set()
    for url in req.urls:
        if req.force_refresh or get_cached_page(url) is None:
            if url not in seen_fetch:
                seen_fetch.add(url)
                to_fetch.append(url)

    fetched_map: dict[str, str | Exception] = {}
    if to_fetch:
        results = await asyncio.gather(
            *(browser_parser(url, req.headers) for url in to_fetch),
            return_exceptions=True,
        )
        for url, res in zip(to_fetch, results):
            fetched_map[url] = res
            if not isinstance(res, Exception):
                text_res = str(res)
                if not _is_error_text(text_res):
                    store_cached_page(url, text_res)

    parts: list[str] = []
    for url in req.urls:
        cached = get_cached_page(url)
        if cached is not None:
            parts.append(format_page_chunks(url, cached, req.chunk_indices))
            continue

        raw = fetched_map.get(url)
        if isinstance(raw, Exception):
            parts.append(f"[URL: {url}]\nОшибка парсинга страницы: {raw}")
        else:
            parts.append(str(raw) if raw is not None else f"[URL: {url}]\nПустой ответ")

    combined = "\n\n" + ("\n\n" + "─" * 60 + "\n\n").join(parts)
    return JSONResponse({"urls": req.urls, "text": combined})


@app.get("/health")
async def health():
    return {"service": "parsers_service", "status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(PORT_PARSERS_SERVICE), log_level="info", reload=True)
