import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
F=(Path(__file__).parent/"dist/augent_scrolly.html").as_uri()
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"]); pg=await b.new_page(viewport={"width":390,"height":844}); await pg.goto(F); await pg.wait_for_timeout(2500)
        print(await pg.evaluate("""()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>392).slice(0,8).map(e=>e.tagName+'.'+String(e.className).slice(0,40)+':'+Math.round(e.getBoundingClientRect().right))"""))
        await b.close()
asyncio.run(main())
