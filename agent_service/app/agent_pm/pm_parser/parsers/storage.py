import json
import logging
from pathlib import Path
from typing import List, Set

from .models import Article, safe_title

logger = logging.getLogger(__name__)


class ArticleStorage:
    """
    Единообразное сохранение статей и метаданных.

    Отвечает за:
    - Запись .md файлов в articles_dir
    - Обновление metadata.json (дедупликация по external_id)
    - Генерацию имён файлов
    """

    def __init__(self, articles_dir: str, metadata_path: str):
        self.articles_dir = Path(articles_dir)
        self.metadata_path = Path(metadata_path)
        self.articles_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)

    def load_existing_ids(self) -> Set[str]:
        """Возвращает множество external_id из metadata.json."""
        metadata = self._read_metadata()
        return {entry.get("external_id", "") for entry in metadata}

    def save(self, articles: List[Article]) -> int:
        """
        Сохраняет новые статьи (пропускает дубли по external_id).

        Для каждой новой статьи:
        - Записывает .md файл с text_article
        - Добавляет запись в metadata.json

        Возвращает количество реально сохранённых (новых) статей.
        """
        existing_ids = self.load_existing_ids()
        metadata = self._read_metadata()
        saved_count = 0

        for article in articles:
            if article.external_id in existing_ids:
                logger.info(f"Пропуск дубля: {article.external_id}")
                continue

            # Генерируем имя файла
            filename = article.generate_filename()
            article.file_article = filename

            # Сохраняем .md файл
            self._save_md(filename, article.text_article)

            # Добавляем в метаданные (без text_article — он в .md файле)
            metadata.append(self._article_to_meta(article))
            existing_ids.add(article.external_id)
            saved_count += 1

            logger.info(f"Сохранена статья: {filename}")

        # Записываем обновлённый metadata.json
        self._write_metadata(metadata)
        logger.info(f"Сохранено {saved_count} новых статей из {len(articles)}")

        return saved_count

    def _save_md(self, filename: str, content: str) -> None:
        """Записывает .md файл в articles_dir."""
        filepath = self.articles_dir / filename
        filepath.write_text(content, encoding="utf-8")

    def _article_to_meta(self, article: Article) -> dict:
        """
        Конвертирует Article в запись для metadata.json.
        Исключает text_article и abstract — они хранятся в .md файле.
        """
        return {
            "source": article.source,
            "external_id": article.external_id,
            "title": article.title,
            "date_sub": article.date_sub,
            "url_article": article.url_article,
            "pdf_url": article.pdf_url,
            "authors": article.authors,
            "tags": article.tags,
            "category": article.category,
            "file_article": article.file_article,
        }

    def _read_metadata(self) -> list:
        """Читает metadata.json. Возвращает пустой список если файла нет."""
        if not self.metadata_path.exists():
            return []
        try:
            with open(self.metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            logger.warning(f"Не удалось прочитать {self.metadata_path}, начинаю с пустого списка")
            return []

    def _write_metadata(self, metadata: list) -> None:
        """Записывает metadata.json."""
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
