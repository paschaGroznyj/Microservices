import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright
HERE = Path(__file__).resolve().parent
FILE = (HERE / "dist" / "augent_scrolly.html").as_uri()
OUT = HERE / "out"

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for name, vw, vh in [("d", 1440, 900), ("m", 390, 844)]:
            pg = await b.new_page(viewport={"width": vw, "height": vh}, device_scale_factor=1)
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
            await pg.goto(FILE, wait_until="domcontentloaded")
            await pg.evaluate("document.fonts.ready")
            await pg.wait_for_timeout(3600)
            await pg.screenshot(path=str(OUT / f"{name}_hero.png"))
            for st in range(4):
                await pg.evaluate(f"document.querySelector('#ch-rest .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center', behavior:'instant'}})")
                await pg.wait_for_timeout(1500)
                await pg.screenshot(path=str(OUT / f"{name}_rest{st}.png"))
            info = await pg.evaluate("""() => ({theme: document.documentElement.dataset.theme,
              docW: document.documentElement.scrollWidth, vw: innerWidth,
              stage: document.querySelector('#rest-stage').dataset.step,
              heroH: document.querySelector('.chat-start-screen').getBoundingClientRect().height,
              font: getComputedStyle(document.querySelector('.sc-h2')).fontFamily.slice(0,40)})""")
            print(name, json.dumps(info, ensure_ascii=False), "errors:", errs[:5])
        await b.close()
asyncio.run(main())
