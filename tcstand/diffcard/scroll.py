import asyncio, json, pathlib
from playwright.async_api import async_playwright
HERE=pathlib.Path(__file__).resolve().parent; OUT=HERE/"out"
URL=(HERE/"stand.html").as_uri()
ST="""() => {const c=[...document.querySelectorAll('[data-wsd=main] .wsd-file.is-open .wsd-code')].find(x=>x.scrollHeight>x.clientHeight+2)||document.querySelector('[data-wsd=main] .wsd-file.is-open .wsd-code');
 const el=c.parentElement, cr=c.getBoundingClientRect(), hs=[...c.querySelectorAll('tr.h')].map(r=>Math.round(r.getBoundingClientRect().top-cr.top));
 const fb=el.querySelector('.wsd-fade'), fr=fb.getBoundingClientRect();
 return {path:el.dataset.path,cH:c.clientHeight,sH:c.scrollHeight,top:Math.round(c.scrollTop),oy:getComputedStyle(c).overflowY,maxh:getComputedStyle(c).maxHeight,
  more:el.querySelector('.wsd-more')?.textContent||null,fade:fb.classList.contains('on'),fadeBottomGap:Math.round(cr.top+c.clientHeight-fr.bottom),
  scrolled:el.classList.contains('is-scrolled'),hunkTops:hs,stuck:hs.filter(t=>t===0).length,pageY:Math.round(scrollY),docW:document.documentElement.scrollWidth,vw:innerWidth,
  codes:[...document.querySelectorAll('[data-wsd=main] .wsd-file.is-open .wsd-code')].map(x=>[x.parentElement.dataset.path.split('/').pop(),x.clientHeight,x.scrollHeight])}}"""
async def main():
  errs=[];res={}
  async with async_playwright() as p:
    b=await p.chromium.launch(args=["--no-sandbox"])
    for name,vw,vh,th in [("d_light",1440,900,"light"),("d_dark",1440,900,"dark"),("m_dark",390,844,"dark"),("m_light",390,844,"light")]:
      pg=await b.new_page(viewport={"width":vw,"height":vh},device_scale_factor=2 if vw<500 else 1)
      pg.on("console",lambda m:m.type=="error" and errs.append(m.text));pg.on("pageerror",lambda e:errs.append(str(e)))
      await pg.goto(URL+"?theme="+th,wait_until="domcontentloaded");await pg.evaluate("document.fonts.ready");await pg.wait_for_timeout(300)
      r={"init":await pg.evaluate(ST)}
      path=r["init"]["path"]; sel=f'[data-wsd=main] .wsd-file[data-path="{path}"]'
      await pg.evaluate(f"document.querySelector('{sel}').scrollIntoView({{block:'start'}})");await pg.wait_for_timeout(100)
      box=await (await pg.query_selector(sel+" .wsd-code")).bounding_box()
      y0=await pg.evaluate("scrollY")
      await pg.mouse.move(box["x"]+box["width"]/2,box["y"]+box["height"]/2);await pg.mouse.wheel(0,260);await pg.wait_for_timeout(350)
      r["wheel"]=await pg.evaluate(ST);r["wheel"]["pageMoved"]=round(await pg.evaluate("scrollY")-y0)
      shot=await pg.query_selector(sel)
      await shot.screenshot(path=str(OUT/f"scr_{name}.png"))
      await pg.evaluate(f"document.querySelector('{sel} .wsd-code').scrollTop=1e5");await pg.wait_for_timeout(100)
      r["end"]=await pg.evaluate(ST)
      if r["init"]["more"]:
        await pg.click(sel+" .wsd-more");await pg.wait_for_timeout(100);r["full"]=await pg.evaluate(ST)
        await pg.click(sel+" .wsd-more");await pg.wait_for_timeout(100);r["back"]=await pg.evaluate(ST)
      res[name]=r;await pg.close()
    await b.close()
  print(json.dumps({"errors":errs,**res},ensure_ascii=False,indent=0))
asyncio.run(main())
