import json
import asyncio
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, asdict
import re


@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: dict | None
    tags: str | List | None
    abstract: str
    file_article: str | None
    category: str | None
    text_article: str


class FluxiconParser:
    name = "fluxicon"

    def __init__(self, date_range: Optional[tuple[str, str]] = None):
        self.base_url = "https://www.fluxicon.com"
        self.date_range = date_range

    def parse(self) -> List[dict]:
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
            file_article = f"{date_sub}_{title}.md"
            results.append(
                Article(
                    source="https://www.fluxicon.com",
                    external_id=link,
                    title=self._safe_title(title),
                    date_sub=date_sub,
                    url_article=f"{self.base_url}{link}",
                    pdf_url="",
                    authors=None,
                    tags=None,
                    abstract=abstract,
                    file_article=file_article,
                    category=None,
                    text_article=abstract
                )
            )

        return [asdict(a) for a in results]


    async def arun(self) -> List[dict]:
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
    data_range = ('2026-01-01', '2026-01-30')
    flux_parser = FluxiconParser(data_range)
    results = await asyncio.gather(
        flux_parser.arun(),
        return_exceptions=True
    )
    with open('flux.json', 'w+', encoding='utf8') as jsonfile:
        json.dump(results, jsonfile, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())