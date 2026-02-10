import asyncio
import json
from typing import List, Dict
from pathlib import Path
import logging

from .parsers import (
    BaseParser,
    Article,
    ArticleStorage,
    PipelineConfig,
    ScholarParser,
    FluxiconParser,
    ARXIVParser,
    IEEEParser,
    setup_logging
)

class ParserPipeline:
    def __init__(self, parsers: List[BaseParser]):
        self.parsers = parsers
        self.logger = logging.getLogger("parser.pipeline")

    async def run(self) -> Dict[str, List[Article]]:
        """Запускает все парсеры параллельно, возвращает dict[name, list[Article]]."""
        tasks = [self._safe_run(parser) for parser in self.parsers]
        results = await asyncio.gather(*tasks)

        return {
            parser.name: result
            for parser, result in zip(self.parsers, results)
        }

    async def run_and_save(self, storage: ArticleStorage) -> Dict[str, int]:
        """
        Запускает парсеры + сохраняет результаты через storage.
        Возвращает dict[parser_name, количество сохранённых статей].
        """
        all_results = await self.run()
        saved_counts = {}

        for parser_name, articles in all_results.items():
            if isinstance(articles, list):
                count = storage.save(articles)
                saved_counts[parser_name] = count
                self.logger.info("%s: сохранено %d новых статей", parser_name, count)
            else:
                saved_counts[parser_name] = 0
                self.logger.warning("%s: пропущен (ошибка)", parser_name)

        return saved_counts

    async def _safe_run(self, parser: BaseParser):
        try:
            self.logger.info("Запуск парсера: %s", parser.name)
            res = await parser.arun()
            self.logger.info("Парсер %s завершён, найдено %d статей", parser.name, len(res))
            return res
        except Exception as e:
            self.logger.exception("Парсер %s упал", parser.name)
            return {
                "error": str(e),
                "source": parser.name
            }


async def main():
    date_range = ('2026-01-01', '2026-01-30')
    link_ieee = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1&refinements=PublicationTopics:Process%20Mining&ranges=2025_2025_Year&returnFacets=ALL'
    cfg = PipelineConfig()
    cfg.max_pages = 2

    parsers = [
        ARXIVParser(date_range),
        FluxiconParser(date_range),
        ScholarParser(date_range=date_range, config=cfg),
        IEEEParser(link_ieee)
    ]

    pipeline = ParserPipeline(parsers)
    storage = ArticleStorage(
        articles_dir="knowledge_base/articles",
        metadata_path="knowledge_base/metadata.json",
    )
    await pipeline.run_and_save(storage=storage)


if __name__ == "__main__":
    setup_logging(Path("./logs"), log_file="parsers.log", level="INFO")
    asyncio.run(main())
