import asyncio, json, os
from pathlib import Path
from playwright.async_api import async_playwright
HERE=Path(__file__).resolve().parent; F=(HERE/"dist"/"augent_scrolly.html").as_uri(); OUT=HERE/"out"
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"])
        for name,vw,vh,touch in [("d",1440,900,False),("m",390,844,True)]:
            pg=await b.new_page(viewport={"width":vw,"height":vh},has_touch=touch,is_mobile=touch)
            errs=[]; pg.on("pageerror",lambda e:errs.append(str(e))); pg.on("console",lambda m:m.type=="error" and errs.append(m.text))
            await pg.goto(F,wait_until="domcontentloaded"); await pg.wait_for_timeout(800)
            hs=[]
            for st in range(5):
                await pg.evaluate(f"document.querySelector('#ch-redis .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center', behavior:'instant'}})")
                await pg.wait_for_timeout(16000 if st==1 else 1500)
                hs.append(await pg.evaluate("[document.querySelector('#redis-stage').dataset.step, Math.round(document.querySelector('#redis-stage .sc-stage__frame').getBoundingClientRect().height), [...document.querySelectorAll('#rd-keys .sc-rrow b, #rd-keys .sc-rrow span')].filter(e=>e.scrollWidth>e.clientWidth+1).length]"))
                if st==1: await pg.screenshot(path=str(OUT/f"flow2_{name}.png"))
            print(name, hs, "docW", await pg.evaluate("document.documentElement.scrollWidth"), "errors", errs[:5])
        await b.close()
asyncio.run(main())
