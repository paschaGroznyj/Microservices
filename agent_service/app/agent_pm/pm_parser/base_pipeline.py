import asyncio
import json
from typing import List, Dict

from .parsers import (
    BaseParser,
    Article,
    ArticleStorage,
    PipelineConfig,
    ScholarParser,
    FluxiconParser,
    ARXIVParser,
    IEEEParser,
)


class ParserPipeline:
    def __init__(self, parsers: List[BaseParser]):
        self.parsers = parsers

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
                print(f"{parser_name}: сохранено {count} новых статей")
            else:
                saved_counts[parser_name] = 0
                print(f"{parser_name}: пропущен (ошибка)")

        return saved_counts

    async def _safe_run(self, parser: BaseParser):
        try:
            print(f"Запуск парсера: {parser.name}")
            res = await parser.arun()
            print(f"Парсер {parser.name} завершён, найдено {len(res)} статей")
            return res
        except Exception as e:
            print(f"Парсер {parser.name} упал: {e}")
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
    results = await pipeline.run()

    # Сохраняем как JSON для проверки
    serializable = {
        name: [a.to_dict() for a in articles] if isinstance(articles, list) else articles
        for name, articles in results.items()
    }
    with open("all_sources.json", "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
