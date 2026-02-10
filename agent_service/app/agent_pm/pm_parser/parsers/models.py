from dataclasses import dataclass, asdict
from typing import List, Optional
from datetime import datetime
import re


def normalize_date(date_str: str) -> str:
    """
    Приводит дату к формату ISO YYYY-MM-DD.
    Поддерживает форматы:
      - "2026-01-15" (уже ISO — возвращает как есть)
      - "15 January 2026" (IEEE формат)
    """
    date_str = date_str.strip()
    if not date_str:
        return ""

    # Уже ISO формат
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str

    # "15 January 2026" или "1 Jan 2026"
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return date_str


def safe_title(title: str) -> str:
    """
    Очищает заголовок для использования в имени файла.
    Заменяет спецсимволы на '_', убирает дубли.
    """
    cleaned = re.sub(r'[^\w\s-]', '_', title)
    cleaned = re.sub(r'[\s]+', '_', cleaned)
    cleaned = re.sub(r'_+', '_', cleaned)
    return cleaned.strip('_')


@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: Optional[dict]
    tags: Optional[List[str]]
    abstract: str
    file_article: Optional[str]
    category: Optional[str]
    text_article: str

    def to_dict(self) -> dict:
        return asdict(self)

    def generate_filename(self) -> str:
        """Генерирует имя .md файла: {date_sub}_{safe_title}.md"""
        return f"{self.date_sub}_{safe_title(self.title)}.md"
