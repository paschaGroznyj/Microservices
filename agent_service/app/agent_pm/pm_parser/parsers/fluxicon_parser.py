import json
import asyncio
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
import re

from .models import Article
from .base import BaseParser

logger = logging.getLogger("parser.fluxicon")


class FluxiconParser(BaseParser):
    name = "fluxicon"

    def __init__(self, date_range: Optional[tuple[str, str]] = None):
        self.base_url = "https://www.fluxicon.com"
        self.date_range = date_range

    def parse(self) -> List[Article]:
        response = requests.get(
            f"{self.base_url}/blog/",
            timeout=20,
            verify=False,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        soup = BeautifulSoup(response.text, "html.parser")

        articles = soup.find_all("div", class_="entry")
        results: List[Article] = []

        for article in articles:
            header = article.find(class_="entry-header")
            if not header or not header.a:
                continue

            link = header.a["href"]
            title = header.get_text(strip=True)
            title = self._safe_title(title)

            # --- дата ---
            date_span = article.find("span", class_="entry-date")
            if not date_span:
                continue

            day = date_span.text.split(" ")[0].zfill(2)
            ym = link.strip("/").split("/")[1:3]
            date_sub = f"{ym[0]}-{ym[1].zfill(2)}-{day}"

            if not self._in_date_range(date_sub):
                continue

            # --- текст ---
            text_blocks = []
            for child in article.children:
                if getattr(child, "name", None) == "p":
                    text_blocks.append(child.get_text(strip=True))
                elif getattr(child, "name", None) == "ul":
                    for li in child.find_all("li"):
                        text_blocks.append(f"- {li.get_text(strip=True)}")
                elif getattr(child, "name", None) == "h2":
                    text_blocks.append(child.get_text(strip=True))

            abstract = "\n".join(text_blocks)
            results.append(
                Article(
                    source="https://www.fluxicon.com",
                    external_id=link,
                    title=title,
                    date_sub=date_sub,
                    url_article=f"{self.base_url}{link}",
                    pdf_url="",
                    authors=None,
                    tags=None,
                    abstract=abstract,
                    file_article=None,
                    category=None,
                    text_article=abstract
                )
            )

        logger.info("Найдено %d статей", len(results))
        return results

    async def arun(self) -> List[Article]:
        return await asyncio.to_thread(self.parse)

    def _safe_title(self, text: str) -> str:
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
        return re.sub(r'[_\s]+', "_", text).strip("_")

    def _in_date_range(self, date_str: str) -> bool:
        if not self.date_range:
            return True

        try:
            article_date = datetime.fromisoformat(date_str)
            start = datetime.fromisoformat(self.date_range[0])
            end = datetime.fromisoformat(self.date_range[1])
            return start <= article_date <= end
        except Exception:
            return False


async def main():
    date_range = ('2026-01-01', '2026-01-30')
    flux_parser = FluxiconParser(date_range)
    results = await flux_parser.arun()
    with open('flux.json', 'w+', encoding='utf8') as jsonfile:
        json.dump([a.to_dict() for a in results], jsonfile, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())