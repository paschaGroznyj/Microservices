from typing import List, Dict, Any, Optional
from dataclasses import asdict
from pydantic import BaseModel
from fastapi import FastAPI, Response, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import logging
import json
import shutil
import re
import os

from universal.page_parser import browser_parser

from core.config import PORT_PARSERS_SERVICE


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = FastAPI(title="Parsers Service")

class ParsePageRequest(BaseModel):
    urls: list[str]
    headers: Optional[dict] = None
 
@app.post("/parse/page")
async def parse_page(req: ParsePageRequest) -> JSONResponse:
    """
    Парсит список страниц параллельно в одном браузере, возвращает единый текст агенту.
    headers — агент генерирует сам (User-Agent, Accept-Language и т.п.)
    """
    if not req.urls:
        return JSONResponse({"urls": [], "text": ""})

    results = await asyncio.gather(
        *(browser_parser(url, req.headers) for url in req.urls),
        return_exceptions=True,
    )
    parts: list[str] = [
        f"[URL: {url}]\nОшибка парсинга страницы: {res}" if isinstance(res, Exception) else str(res)
        for url, res in zip(req.urls, results)
    ]

    combined = "\n\n" + ("\n\n" + "─" * 60 + "\n\n").join(parts)
    return JSONResponse({"urls": req.urls, "text": combined})
    
@app.get("/health")
async def health():
    return {"service": "parsers_service", "status": "ok"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(PORT_PARSERS_SERVICE), log_level="info", reload=True)
    # uvicorn.run(app, host="127.0.0.1", port=int(PORT_PARSERS_SERVICE), log_level="info")