from agent_pm.pm_parser.arxiv_parser import ARXIVParser
from datetime import datetime
import asyncio

data_range = ('2026-01-15', '2026-01-27')
arx = ARXIVParser(data_range)

async def main():
    await arx.run()

if __name__ == "__main__":
    asyncio.run(main())