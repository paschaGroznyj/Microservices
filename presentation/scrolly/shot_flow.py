import asyncio, json, sys
from pathlib import Path
from playwright.async_api import async_playwright
HERE = Path(__file__).resolve().parent
F = (HERE / "dist" / "augent_scrolly.html").as_uri()
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for name, vw, vh, touch in [("d", 1440, 900, False), ("m", 390, 844, True)]:
            pg = await b.new_page(viewport={"width": vw, "height": vh}, has_touch=touch, is_mobile=touch, device_scale_factor=1)
            errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
            await pg.goto(F, wait_until="domcontentloaded"); await pg.wait_for_timeout(800)
            await pg.evaluate("""document.querySelector('#ch-redis .sc-step[data-step="1"]').scrollIntoView({block:'center', behavior:'instant'})""")
            for i, t in enumerate([1800, 2600, 4000]):
                await pg.wait_for_timeout(t)
                await pg.screenshot(path=str(OUT / f"flow_{name}{i}.png"))
                info = await pg.evaluate("""() => ({snap: !!window.SC_FLOW, n: window.SC_FLOW && window.SC_FLOW.events.length,
                  step: document.querySelector('#redis-stage').dataset.step, theme: document.documentElement.dataset.theme,
                  cmd: document.getElementById('rd-cmd').textContent.slice(0,110), cap: document.getElementById('rd-cap').textContent,
                  row0: document.querySelector('#rd-keys .sc-rrow').textContent.slice(0,120),
                  over: [...document.querySelectorAll('#rd-keys .sc-rrow b, #rd-keys .sc-rrow span')].filter(e=>e.scrollWidth>e.clientWidth+1).length,
                  frameH: Math.round(document.querySelector('#redis-stage .sc-stage__frame').getBoundingClientRect().height), docW: document.documentElement.scrollWidth})""")
                print(name, i, json.dumps(info, ensure_ascii=False))
            print(name, "errors:", errs[:5])
        await b.close()
asyncio.run(main())
