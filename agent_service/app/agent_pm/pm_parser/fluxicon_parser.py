import warnings
import requests
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
warnings.filterwarnings("ignore")
from bs4 import BeautifulSoup
from pathlib import Path
import os
import re
import json
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

base_dir = Path(__file__).resolve().parent.parent  # final_agent
name_doc_dir = base_dir / os.getenv("STORAGE_PATH") / os.getenv("ARTICLES_FOLDER")
name_meta_dir = base_dir / os.getenv("STORAGE_PATH")

os.makedirs(name_doc_dir, exist_ok=True)



class BaseParser(ABC):
    def __init__(self, date_range: tuple[str, str] | None = None):
        self.date_range = date_range
    @abstractmethod
    def parse(self) -> List[Dict]:
        """Возвращает список статей в arxiv-формате"""
        pass

    async def run(self) -> List[Dict]:
        articles = [self._normalize_article(a) for a in self.parse()]

        existing = self._read_existing_metadata()
        new_articles = []

        for art in articles:
            ext_id = art["external_id"]
            if not ext_id or ext_id in existing:
                continue

            file_name = self._save_article_md(art)
            art["file_article"] = file_name

            existing[ext_id] = art
            new_articles.append(art)

        self._save_metadata(existing)
        return new_articles

    def _save_article_md(self, article: Dict) -> str:
        title = article["title"] or "untitled"
        safe_title = self._safe_filename(title)

        date_prefix = article["date_sub"] or "no_date"
        filename = f"{date_prefix}_{safe_title}.md"

        path = name_doc_dir / filename

        content = article["abstract"] or ""
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return filename

    def _save_metadata(self, data: dict):
        cleaned = []

        for item in data.values():
            meta = dict(item)
            meta.pop("abstract", None)
            cleaned.append(meta)

        path = name_meta_dir / "metadata.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)

    def _safe_filename(self, text: str) -> str:
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', text)
        return re.sub(r'[_\s]+', '_', text).strip('_')

    def _read_existing_metadata(self) -> dict:
        path = name_meta_dir / "metadata.json"
        if not path.exists():
            return {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                print({item["external_id"]: item for item in data})
                return {item["external_id"]: item for item in data}
        except Exception:
            return {}

    def _normalize_article(self, article: Dict) -> Dict:
        """Гарантирует наличие всех arxiv-полей"""
        return {
            "source": article.get("source"),
            "external_id": article.get("external_id"),
            "title": article.get("title"),
            "date_sub": article.get("date_sub"),
            "url_article": article.get("url_article"),
            "pdf_url": article.get("pdf_url"),
            "authors": article.get("authors"),
            "tags": article.get("tags"),
            "category": article.get("category"),
            "abstract": article.get("abstract"),
            "file_article": article.get("file_article"),
        }

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None

        try:
            parts = date_str.split("-")
            if len(parts) == 3:
                y, m, d = parts
                return datetime(int(y), int(m), int(d))
        except Exception:
            return None

        return None

    def _in_date_range(self, date_str: str) -> bool:
        if not self.date_range:
            return True

        article_date = self._parse_date(date_str)
        if not article_date:
            return False

        start = datetime.fromisoformat(self.date_range[0])
        end = datetime.fromisoformat(self.date_range[1])

        return start <= article_date <= end
class FluxiconParser(BaseParser):

    def __init__(self, date_range=None):
        super().__init__(date_range)
        self.base_url = "https://www.fluxicon.com"

    def parse(self):
        response = requests.get(self.base_url + "/blog/", verify=False)
        soup = BeautifulSoup(response.text, "html.parser")

        articles = soup.find_all("div", "entry")
        results = []

        for article in articles:
            link = article.find(class_="entry-header").a["href"]
            title = article.find("span", "entry-header").text.strip()

            day = article.find("span", "entry-date").text.split(" ")[0]
            ym = link.split("/")[2:4]
            day = day.zfill(2)  # Добавление нуля вначале
            month = str(ym[1]).zfill(2)
            date_sub = f"{ym[0]}-{month}-{day}"
            # 🔥 ФИЛЬТРАЦИЯ ПО ДАТЕ
            if not self._in_date_range(date_sub):
                continue

            text_blocks = []
            for child in article.children:
                if child.name == "p":
                    text_blocks.append(child.get_text(strip=True))
                elif child.name == "ul":
                    for li in child.find_all("li"):
                        text_blocks.append(f"- {li.get_text(strip=True)}")
                elif child.name == "h2":
                    text_blocks.append(child.get_text(strip=True))

            abstract = "\n".join(text_blocks) if text_blocks else None

            results.append({
                "source": "fluxicon",
                "external_id": link,          # вместо arxiv_id
                "title": title,
                "date_sub": date_sub,
                "url_article": self.base_url + link,
                "pdf_url": None,
                "authors": None,
                "tags": None,
                "category": None,
                "abstract": abstract,
                "file_article": None
            })

        return results

# data_range = ('2026-01-01', '2026-01-30')
#
# fxs = FluxiconParser(data_range)
# fxs.run()
