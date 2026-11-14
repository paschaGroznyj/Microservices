import asyncio, json, sys, pathlib
from playwright.async_api import async_playwright
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
URL = (HERE / "stand.html").as_uri()
CHECK = """() => {
 const card=document.querySelector('[data-wsd=main]'), r=card.getBoundingClientRect(), msg=card.closest('.agent-message').getBoundingClientRect();
 const rows=[...card.querySelectorAll('.wsd-file')].map(f=>({p:f.dataset.path,open:f.classList.contains('is-open'),
   a:f.querySelectorAll('tr.a').length+f.querySelectorAll('td.cd.ad').length, d:f.querySelectorAll('tr.d').length+f.querySelectorAll('td.cd.dl').length,
   sa:f.querySelectorAll('.wsd-stat span')[0]?.textContent||'', sd:f.querySelectorAll('.wsd-stat span')[1]?.textContent||''}));
 const fh=[...card.querySelectorAll('.wsd-fhead')].map(h=>h.scrollWidth-h.clientWidth);
 const st=getComputedStyle(card.querySelector('tr.a td.cd')||card), sd=getComputedStyle(card.querySelector('tr.d td.cd')||card);
 return {vw:innerWidth, docW:document.documentElement.scrollWidth, card:[Math.round(r.left),Math.round(r.right)], msg:[Math.round(msg.left),Math.round(msg.right)],
  head:card.querySelector('.wsd-stat').textContent, rows, fheadOverflow:Math.max(...fh), addBg:st.backgroundColor, delBg:sd.backgroundColor,
  marks:card.querySelectorAll('mark').length, hl:card.querySelectorAll('.hljs-keyword,.hljs-string').length,
  clip:card.querySelectorAll('.wsd-code.is-clip').length, more:card.querySelector('.wsd-more')?.textContent, edge:card.querySelectorAll('.wsd-edge.on').length, wrap:card.classList.contains('is-wrap'),
  headH:Math.round(card.querySelector('.wsd-head').getBoundingClientRect().height), liveCount:document.querySelector('[data-wsd=live] .wsd-count').scrollWidth-document.querySelector('[data-wsd=live] .wsd-count').clientWidth,
  dirTail:[...card.querySelectorAll('.wsd-dir')].map(d=>d.scrollWidth>d.clientWidth).filter(Boolean).length,
  font:getComputedStyle(card.querySelector('.wsd-t')||card).fontFamily.slice(0,40)}
}"""
async def main():
    errs=[]; res={}
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for name, vw, vh, theme in [("d_light",1440,900,"light"),("d_dark",1440,900,"dark"),("m_dark",390,844,"dark"),("m_light",390,844,"light")]:
            pg = await b.new_page(viewport={"width":vw,"height":vh}, device_scale_factor=2 if vw<500 else 1)
            pg.on("console", lambda m: m.type=="error" and errs.append(m.text)); pg.on("pageerror", lambda e: errs.append(str(e)))
            await pg.goto(URL+"?theme="+theme, wait_until="domcontentloaded"); await pg.evaluate("document.fonts.ready"); await pg.wait_for_timeout(300)
            res[name]=await pg.evaluate(CHECK)
            await pg.screenshot(path=str(OUT/f"{name}.png"), full_page=True)
            if name=="d_dark":
                await pg.click('[data-wsd=main] .wsd-seg button[data-m=split]'); await pg.wait_for_timeout(150)
                res["split"]=await pg.evaluate(CHECK)
                res["split_cols"]=await pg.evaluate("document.querySelector('[data-wsd=main] .wsd-t.split tbody tr:not(.h)').children.length")
                await (await pg.query_selector('[data-wsd=main]')).screenshot(path=str(OUT/"split_dark.png"))
                await pg.click('[data-wsd=main] [data-act=fold]'); res["fold_open"]=await pg.evaluate("document.querySelectorAll('[data-wsd=main] .wsd-file.is-open').length")
                await pg.click('[data-wsd=main] .wsd-head'); res["collapsed"]=await pg.evaluate("document.querySelector('[data-wsd=main]').classList.contains('is-collapsed')")
                await pg.click('[data-wsd=live] .wsd-head'); await pg.click('[data-wsd=live] .wsd-fhead')
                res["live_open"]=await pg.evaluate("document.querySelector('[data-wsd=live] .wsd-file.is-open .wsd-code').innerHTML.length")
            if name=="d_light":
                await (await pg.query_selector('[data-wsd=main]')).screenshot(path=str(OUT/"card_light.png"))
            await pg.close()
        await b.close()
    bad=[]
    for k,v in res.items():
        if isinstance(v,dict):
            if v["docW"]>v["vw"]: bad.append(k+": docW")
            if v["card"][0]<v["msg"][0] or v["card"][1]>v["msg"][1]: bad.append(k+": card out of msg")
            if v["fheadOverflow"]>0: bad.append(k+": fhead overflow")
            for r in v["rows"]:
                if r["open"] and r["a"]+r["d"] and (("+%d"%r["a"])!=r["sa"] or ("\u2212%d"%r["d"])!=r["sd"]): bad.append(k+": count "+r["p"])
    print(json.dumps({"errors":errs,"bad":bad,**{k:({kk:vv for kk,vv in v.items() if kk!="rows"} if isinstance(v,dict) else v) for k,v in res.items()}}, ensure_ascii=False))
asyncio.run(main())
