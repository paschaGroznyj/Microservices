import logging
from typing import List, Optional
from core.config import SCRAPINGDOG_API_KEY
import os
import requests
from dataclasses import dataclass
# from dotenv import load_dotenv
#
# load_dotenv()

@dataclass
class Article:
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: List[str]
    tags: List[str]
    abstract: str
    category: Optional[str]
    source: str = "scholar"


class ScholarClient:
    """Клиент для Google Scholar через ScrapingDog API"""

    BASE_URL = "https://api.scrapingdog.com/google_scholar"

    def __init__(self):
        self.api_key = SCRAPINGDOG_API_KEY

    def search(
        self,
        keywords: List[str],
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
        limit: int = 20,
        token: str = None
    ) -> List[Article]:

        query = " ".join(keywords)
        if token is None:
            token = self.api_key
        else:
            token = token
        response = requests.get(self.BASE_URL, params={
            "api_key": token,
            "query": query,
            "results": limit,
            "page": 0,
            "language": "en",
            "as_ylo": "2026",
            # "scisbd": "1",
            # "filter": "1"
        })
        response.raise_for_status()
        data = response.json()
        articles = []
        for item in data.get("scholar_results", []):
            article = self._parse_item(item)
            if article:
                articles.append(article)

        return articles

    def _parse_item(self, item: dict) -> Optional[Article]:
        # Берём только статьи у которых есть PDF
        pdf_url = ""
        for resource in item.get("resources", []):
            if resource.get("type") == "PDF":
                pdf_url = resource.get("link", "")
                break

        if not pdf_url:
            return None  # ← пропускаем без PDF
        # logging.info(item)
        title = item.get("title", "").strip()
        title_link = item.get("title_link", "")
        snippet = item.get("snippet", "").strip()
        scholar_id = item.get("id", "")

        authors = [
            a.get("name", "") for a in item.get("authors", [])
            if a.get("name")
        ]

        # Год из displayed_link — "Author - Journal, 2022 - publisher"
        year = self._extract_year(item.get("displayed_link", ""))
        date_sub = f"{year}-01-01" if year else ""

        # Цитирования как тег
        cited_by = item.get("inline_links", {}).get("cited_by", {}).get("total", "")
        tags = [cited_by] if cited_by else []

        return Article(
            external_id=scholar_id,
            title=title,
            date_sub=date_sub,
            url_article=title_link,
            pdf_url=pdf_url,
            authors=authors,
            tags=tags,
            abstract=snippet,  # ← snippet вместо abstract
            category="scholar",
            source="scholar",
        )

    def _extract_year(self, displayed_link: str) -> Optional[str]:
        import re
        match = re.search(r'\b(19|20)\d{2}\b', displayed_link)
        return match.group(0) if match else None

    def _deduplicate(self, articles: List[Article]) -> List[Article]:
        seen = set()
        result = []
        for article in articles:
            key = article.external_id or article.title.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            result.append(article)
        return result

# scholar = ScholarClient()
# print(scholar.api_key)
# query = scholar.search(keywords=['process mining'], limit=10)
# print(query)