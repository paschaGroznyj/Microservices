import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
F=(Path(__file__).parent/"dist/augent_scrolly.html").as_uri()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"])
        for vw,vh in [(1440,900),(390,844)]:
            pg=await b.new_page(viewport={"width":vw,"height":vh}); await pg.goto(F); await pg.wait_for_timeout(800)
            r=[]
            for st in range(4):
                await pg.evaluate(f"document.querySelector('#ch-rest .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center'}})"); await pg.wait_for_timeout(700)
                r.append(await pg.evaluate("""()=>{const w=document.querySelector('#rest-stage .sc-win').getBoundingClientRect();
                 const ph=document.querySelector('#rest-ph canvas').getBoundingClientRect();
                 const bd=document.querySelector('#rest-badge').getBoundingClientRect();
                 return {winH:Math.round(w.height),winTop:Math.round(w.top),ph:[Math.round(ph.width),Math.round(ph.height)],badgeH:Math.round(bd.height)}}"""))
            print(vw, json.dumps(r))
        await b.close()
asyncio.run(main())
