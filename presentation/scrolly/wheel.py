import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
F=(Path(__file__).resolve().parent/"dist"/"augent_scrolly.html").as_uri()
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(args=["--no-sandbox"])
    for vw,vh,touch in [(1440,900,False),(390,844,True)]:
      pg=await b.new_page(viewport={"width":vw,"height":vh},has_touch=touch,is_mobile=touch)
      errs=[]; pg.on("pageerror",lambda e:errs.append(str(e)))
      await pg.goto(F,wait_until="domcontentloaded"); await pg.wait_for_timeout(1500)
      await pg.mouse.move(vw/2,vh/2)
      for _ in range(6): await pg.mouse.wheel(0,600); await pg.wait_for_timeout(250)
      y1=await pg.evaluate("scrollY")
      await pg.keyboard.press("End"); await pg.wait_for_timeout(1200)
      y2=await pg.evaluate("[scrollY, document.documentElement.scrollHeight-innerHeight, document.documentElement.scrollWidth, getComputedStyle(document.body).overflowY, document.documentElement.dataset.theme]")
      print(vw,"wheel->",y1,"End->",y2,"errs",errs)
    await b.close()
asyncio.run(main())
