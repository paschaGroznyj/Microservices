import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
F=(Path(__file__).parent/"dist/augent_scrolly.html").as_uri()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"]); pg=await b.new_page(viewport={"width":1440,"height":900}); await pg.goto(F); await pg.wait_for_timeout(500)
        for st in (2,3):
            await pg.evaluate(f"document.querySelector('#ch-rest .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center'}})"); await pg.wait_for_timeout(600)
            print(st, await pg.evaluate("""()=>[...document.querySelectorAll('#rest-stage .sc-win > *, #rest-stage .sc-win__body > *, #rest-lane')].map(e=>e.className+':'+Math.round(e.getBoundingClientRect().height))"""))
        await b.close()
asyncio.run(main())
