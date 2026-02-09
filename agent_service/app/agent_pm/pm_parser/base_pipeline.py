import asyncio
import json
from typing import List
from parsers.scholar_parser import PipelineConfig, ScholarParser
from parsers.fluxicon_parser import FluxiconParser
from parsers.arxiv_parser import ARXIVParser
from parsers.ieee_parser import IEEEParser


class ParserPipeline:
    def __init__(self, parsers: List):
        self.parsers = parsers

    async def run(self) -> dict:
        tasks = []
        for parser in self.parsers:
            tasks.append(self._safe_run(parser))

        results = await asyncio.gather(*tasks)

        return {
            parser.name: result
            for parser, result in zip(self.parsers, results)
        }

    async def _safe_run(self, parser):
        try:
            print(f"Запуск парсера: {parser.name}")
            res = await parser.arun()
            print(f"Парсер {parser.name} завершён")
            return res
        except Exception as e:
            print(f"Парсер {parser.name} упал: {e}")
            return {
                "error": str(e),
                "source": parser.name
            }


async def main():
    data_range = ('2026-01-01', '2026-01-30')
    link_ieee = 'https://ieeexplore.ieee.org/search/searchresult.jsp?queryText=Process%20Mining&highlight=true&returnType=SEARCH&matchPubs=true&refinementName=Publication%20Topics&pageNumber=1&refinements=PublicationTopics:Process%20Mining&ranges=2025_2025_Year&returnFacets=ALL'
    cfg = PipelineConfig()
    cfg.max_pages = 2

    parsers = [
        ARXIVParser(data_range),
        FluxiconParser(data_range),
        ScholarParser(date_range=data_range, config=cfg),
        IEEEParser(link_ieee)
    ]

    pipeline = ParserPipeline(parsers)
    results = await pipeline.run()

    with open("all_sources.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
