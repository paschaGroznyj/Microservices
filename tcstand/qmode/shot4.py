import asyncio, json, os
from playwright.async_api import async_playwright
H=os.path.abspath('.')
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"])
        res={}
        for name,html,vp in [("m390","mobile.html",(390,844)),("m360","mobile.html",(360,780)),("desk","light.html",(1280,720)),("dark","dark.html",(1280,720))]:
            pg=await b.new_page(viewport={"width":vp[0],"height":vp[1]},device_scale_factor=2)
            errs=[]; pg.on("pageerror",lambda e: errs.append(str(e)))
            await pg.goto(f"file://{H}/{html}",wait_until="domcontentloaded"); await pg.wait_for_timeout(300)
            await pg.click(".qm-pill"); await pg.wait_for_timeout(400)
            g=await pg.evaluate("""()=>{const m=document.querySelector('.qm-menu').getBoundingClientRect();const p=document.querySelector('.qm-pill');const bx=p.closest('.chat-input-box,.start-input-box').getBoundingClientRect();return {menuL:m.left,menuR:m.right,boxL:bx.left,boxR:bx.right,gap:bx.top-m.bottom}}""")
            g["errors"]=errs; res[name]=g
            await pg.screenshot(path=f"out_v4/{name}.png")
            await pg.close()
        print(json.dumps(res,indent=1))
        await b.close()
asyncio.run(main())
