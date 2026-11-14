import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
F = (Path(__file__).resolve().parent / "dist" / "augent_scrolly.html").as_uri()
JS = """() => { const r = {}; for (const sel of ['#bus-stage .sc-win__bar','#bus-stage .chat-messages','#bus-stage .message-input-container','#bus-k','#bus-cap','#bus-stage .sc-mcard','#bus-stage .sc-reply','#bus-ph','#bus-k .sc-krow--topic','[data-g=agent]','[data-g=db]','[data-g=sigma]','#bus-stage .sc-win__body','#bus-stage .sc-stage__frame'])
 { const e=document.querySelector(sel); if(e){const b=e.getBoundingClientRect(); r[sel]=[Math.round(b.width),Math.round(b.height)];} }
 const wide=[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,6).map(e=>e.tagName+'.'+[...e.classList].join('.')+':'+Math.round(e.getBoundingClientRect().right));
 return {r, wide, vw: innerWidth}; }"""
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for vw, vh, touch in [(390, 844, True)]:
            pg = await b.new_page(viewport={"width": vw, "height": vh}, has_touch=touch, is_mobile=touch)
            await pg.goto(F, wait_until="domcontentloaded"); await pg.wait_for_timeout(800)
            for st in (1, 2):
                await pg.evaluate(f"document.querySelector('#ch-bus .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center', behavior:'instant'}})")
                await pg.wait_for_timeout(1800)
                print(vw, st, json.dumps(await pg.evaluate(JS), ensure_ascii=False))
        await b.close()
asyncio.run(main())
