
import asyncio,pathlib
from playwright.async_api import async_playwright
async def main():
  async with async_playwright() as p:
    b=await p.chromium.launch(args=["--no-sandbox"]);errs=[]
    for th,vw in [("light",1440),("dark",1440),("dark",390)]:
      pg=await b.new_page(viewport={"width":vw,"height":900},device_scale_factor=2 if vw<500 else 1)
      pg.on("pageerror",lambda e:errs.append(str(e)));pg.on("console",lambda m:m.type=="error" and errs.append(m.text))
      await pg.goto(pathlib.Path("out/ws_diff_mockup.html").resolve().as_uri()+"?theme="+th);await pg.evaluate("document.fonts.ready");await pg.wait_for_timeout(300)
      f=await pg.evaluate("[...document.fonts].filter(f=>f.status=='loaded').map(f=>f.family).join(',')")
      kw=await pg.evaluate("getComputedStyle(document.querySelector('.hljs-keyword')).color")
      print(th,vw,f[:80],kw)
      await pg.screenshot(path=f"out/inl_{th}_{vw}.png",full_page=(vw<500))
      if vw<500: await (await pg.query_selector('[data-wsd=main]')).screenshot(path="out/inl_m_card.png")
    print("errors",errs);await b.close()
asyncio.run(main())