import asyncio, json
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
            await pg.goto(F, wait_until="domcontentloaded"); await pg.wait_for_timeout(1200)
            for st in range(4):
                await pg.evaluate(f"document.querySelector('#ch-bus .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center', behavior:'instant'}})")
                await pg.wait_for_timeout(2600 if st == 3 else 1800)
                await pg.screenshot(path=str(OUT / f"bus_{name}{st}.png"))
                info = await pg.evaluate("""() => { const s=document.querySelector('#bus-stage .sc-stage__frame').getBoundingClientRect();
                  return {theme: document.documentElement.dataset.theme, step: document.querySelector('#bus-stage').dataset.step,
                  frameTop: Math.round(s.top), frameH: Math.round(s.height), vh: innerHeight, docW: document.documentElement.scrollWidth,
                  log: [...document.querySelectorAll('#bus-log i')].map(i=>i.className||'-').join(','),
                  toc: [...document.querySelectorAll('.sc-toc a')].map(a=>a.textContent+(a.classList.contains('is-on')?'*':'')).join(' | ')}; }""")
                print(name, st, json.dumps(info, ensure_ascii=False))
            print(name, "errors:", errs[:5])
        await b.close()
asyncio.run(main())
