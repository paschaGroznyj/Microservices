import re
import time
import asyncio
import hashlib
import logging
from io import BytesIO
from dataclasses import dataclass, asdict
from typing import Optional, List

import requests
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)


@dataclass
class Article:
    source: str
    external_id: str
    title: str
    date_sub: str
    url_article: str
    pdf_url: str
    authors: dict
    tags: str | List
    abstract: str
    file_article: str
    category: str | None
    text_article: str
    pdf_bytes: bytes = b""


class YandexDiskParser:
    """
    Парсер для скачивания PDF-статей из публичной папки Яндекс.Диска.
    Рекурсивно обходит все подпапки, скачивает PDF и извлекает текст.
    Путь до файла включается в имя (например workshops-COMINDS-filename).
    """

    name = "yandex_disk"

    API_BASE = "https://cloud-api.yandex.net/v1/disk/public/resources"

    def __init__(self, public_url: str, date_range: tuple, conference_name: str = "ICPM"):
        """
        Args:
            public_url: Публичная ссылка на папку Яндекс.Диска
            date_range: Кортеж (start, end) — дата конференции, используется как date_sub
            conference_name: Название конференции для тегов
        """
        self.public_url = public_url
        self.conference_name = conference_name
        # Берём первую дату из диапазона как дату публикации
        self.date_sub = date_range[0] if isinstance(date_range[0], str) else date_range[0].strftime("%Y-%m-%d")
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def _list_folder(self, path: str = "/", limit: int = 100, offset: int = 0) -> dict:
        """Получает содержимое публичной папки по пути."""
        params = {
            "public_key": self.public_url,
            "path": path,
            "limit": limit,
            "offset": offset,
        }
        resp = requests.get(self.API_BASE, params=params, headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get_download_link(self, path: str) -> str:
        """Получает прямую ссылку на скачивание файла."""
        params = {
            "public_key": self.public_url,
            "path": path,
        }
        resp = requests.get(f"{self.API_BASE}/download", params=params, headers=self.headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["href"]

    def _collect_pdf_files(self, path: str = "/") -> List[dict]:
        """Рекурсивно собирает все PDF-файлы из папки."""
        pdf_files = []
        try:
            data = self._list_folder(path)
        except Exception as e:
            logger.error(f"Ошибка при листинге {path}: {e}")
            return pdf_files

        items = data.get("_embedded", {}).get("items", [])

        for item in items:
            item_path = item.get("path", "")
            item_type = item.get("type", "")
            item_name = item.get("name", "")

            if item_type == "dir":
                logger.info(f"Обход папки: {item_path}")
                pdf_files.extend(self._collect_pdf_files(item_path))
            elif item_type == "file" and item_name.lower().endswith(".pdf"):
                pdf_files.append({
                    "name": item_name,
                    "path": item_path,
                })

        # Пагинация
        total = data.get("_embedded", {}).get("total", 0)
        if len(items) < total:
            offset = len(items)
            while offset < total:
                try:
                    data = self._list_folder(path, offset=offset)
                    extra_items = data.get("_embedded", {}).get("items", [])
                    for item in extra_items:
                        item_path = item.get("path", "")
                        item_type = item.get("type", "")
                        item_name = item.get("name", "")
                        if item_type == "dir":
                            pdf_files.extend(self._collect_pdf_files(item_path))
                        elif item_type == "file" and item_name.lower().endswith(".pdf"):
                            pdf_files.append({"name": item_name, "path": item_path})
                    offset += len(extra_items)
                except Exception as e:
                    logger.error(f"Ошибка пагинации {path} offset={offset}: {e}")
                    break

        return pdf_files

    def _path_to_prefix(self, path: str) -> str:
        """
        Превращает путь вида /demo/paper_239.pdf -> demo
        или /workshops/COMINDS/file.pdf -> workshops-COMINDS
        Возвращает все папки (без имени файла) через дефис.
        """
        # path может быть вида: disk:/demo/paper.pdf или /demo/paper.pdf
        clean = path.split(":/")[-1] if ":/" in path else path
        parts = clean.strip("/").split("/")
        # Все папки без имени файла
        folders = parts[:-1]
        if folders:
            return "-".join(folders)
        return ""

    def _download_pdf(self, path: str) -> bytes:
        """Скачивает PDF по пути в публичной папке."""
        try:
            href = self._get_download_link(path)
            resp = requests.get(href, headers=self.headers, timeout=60)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.error(f"Не удалось скачать {path}: {e}")
            return b""

    def _extract_text(self, pdf_bytes: bytes, max_pages: int = 4) -> str:
        """Извлекает текст из первых N страниц PDF."""
        if not pdf_bytes:
            return ""
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages[:max_pages]:
                text += page.extract_text() or ""
            return text
        except Exception as e:
            logger.error(f"Ошибка извлечения текста из PDF: {e}")
            return ""

    @staticmethod
    def _url_to_hash(url: str, length: int = 12) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()[:length]

    @staticmethod
    def _clean_title(title: str) -> str:
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title)
        safe = re.sub(r'[_\s]+', '_', safe).strip('_')
        return safe

    def _extract_authors_from_filename(self, filename: str) -> str:
        """Пытается извлечь авторов из имени файла (формат: 'Author et al - Title.pdf')."""
        match = re.match(r'^(.+?)\s*-\s*', filename)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_title_from_filename(self, filename: str) -> str:
        """Извлекает название из имени файла."""
        name = filename.rsplit('.', 1)[0]  # убираем .pdf
        match = re.match(r'^.+?\s*-\s*(.+)$', name)
        if match:
            return match.group(1).strip()
        return name.strip()

    def parsing(self) -> List[dict]:
        logger.info(f"Начинаем сбор PDF из Яндекс.Диска: {self.public_url}")

        pdf_files = self._collect_pdf_files("/")
        logger.info(f"Найдено {len(pdf_files)} PDF-файлов")

        result = []
        for i, pdf_info in enumerate(pdf_files):
            name = pdf_info["name"]
            path = pdf_info["path"]

            logger.info(f"[{i+1}/{len(pdf_files)}] Обработка: {name}")

            prefix = self._path_to_prefix(path)
            title_raw = self._extract_title_from_filename(name)
            title = self._clean_title(title_raw)
            authors_str = self._extract_authors_from_filename(name)

            external_id = self._url_to_hash(path)

            pdf_bytes = self._download_pdf(path)
            text_article = self._extract_text(pdf_bytes)

            # Пытаемся достать abstract из первых строк текста
            abstract = ""
            if text_article:
                lines = text_article.strip().split("\n")
                # Берём первые ~500 символов как abstract
                abstract = " ".join(lines)[:500]

            file_article = f"{self.date_sub}_{external_id}.md"

            article = Article(
                source=f"yandex_disk:{self.conference_name}",
                external_id=external_id,
                title=f"{prefix}-{title}" if prefix else title,
                date_sub=self.date_sub,
                url_article=self.public_url,
                pdf_url=path,
                authors={authors_str: ""} if authors_str else {},
                tags=[self.conference_name, prefix] if prefix else [self.conference_name],
                abstract=abstract,
                file_article=file_article,
                category=None,
                text_article=text_article,
                pdf_bytes=pdf_bytes,
            )
            result.append(asdict(article))
            time.sleep(0.5)  # Пауза между запросами

        logger.info(f"Готово. Собрано {len(result)} статей из Яндекс.Диска")
        return result

    async def arun(self) -> list[dict]:
        return await asyncio.to_thread(self.parsing)
