import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
F = (Path(__file__).resolve().parent / "dist" / "augent_scrolly.html").as_uri()
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        await pg.goto(F, wait_until="domcontentloaded"); await pg.wait_for_timeout(800)
        await pg.evaluate("document.querySelector('#ch-redis .sc-step[data-step=\"1\"]').scrollIntoView({block:'center',behavior:'instant'})")
        for t in (1500, 3500):
            await pg.wait_for_timeout(t)
            print(await pg.evaluate("""() => { const r=document.querySelector('#rd-keys .sc-rrow'); const s=r.querySelector('span');
              const b=s.getBoundingClientRect(); return {html:s.innerHTML.slice(0,160), w:Math.round(b.width), h:Math.round(b.height), sw:s.scrollWidth,
              cols:getComputedStyle(r).gridTemplateColumns, step:document.querySelector('#redis-stage').dataset.step}; }"""))
        await b.close()
asyncio.run(main())
