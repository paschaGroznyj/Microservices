import asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
HERE=Path(__file__).resolve().parent; F=(HERE/"dist"/"augent_scrolly.html").as_uri(); OUT=HERE/"out"
async def go(pg, ch, st):
    await pg.evaluate(f"document.querySelector('#{ch} .sc-step[data-step=\"{st}\"]').scrollIntoView({{block:'center', behavior:'instant'}})")
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(args=["--no-sandbox"])
        for name,vw,vh,touch in [x for x in [("d",1440,900,False),("m",390,844,True)] if x[0] in __import__("os").environ.get("ONLY","dm")]:
            pg=await b.new_page(viewport={"width":vw,"height":vh},has_touch=touch,is_mobile=touch)
            errs=[]; pg.on("pageerror",lambda e:errs.append(str(e))); pg.on("console",lambda m:m.type=="error" and errs.append(m.text))
            await pg.goto(F,wait_until="domcontentloaded"); await pg.wait_for_timeout(800)
            # глава 1: таймер
            await go(pg,"ch-rest",1); await pg.wait_for_timeout(16000)
            t1=await pg.evaluate("document.querySelector('#rest-lane .sc-hold__t').textContent")
            await pg.wait_for_timeout(1500)
            t2=await pg.evaluate("[document.querySelector('#rest-lane .sc-hold__t').textContent, document.querySelector('#rest-lane .sc-hold__bar span').style.width, document.getElementById('rest-badge').textContent, document.querySelector('#rest-stage .chat-send-btn svg').innerHTML.slice(0,40)]")
            print(name,"rest timer",t1,t2)
            await pg.screenshot(path=str(OUT/f"fix_rest_{name}.png"))
            # глава 3, шаг 2: flow на fmRailCv
            await go(pg,"ch-redis",1)
            for i,w in enumerate([3500,9000,22000]):
                await pg.wait_for_timeout(w)
                info=await pg.evaluate("""()=>{const g=s=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]};
                  const S=window.FlowViz.state; return {step:document.querySelector('#redis-stage').dataset.step, frame:g('#redis-stage .sc-stage__frame'), rail:g('#fmRail'), railOn:document.getElementById('fmRail').classList.contains('is-on'),
                  mini:g('#fmMini'), miniOn:document.getElementById('fmMini').classList.contains('is-on'), phase:S.phase, tools:S.toolLog.length, docW:document.documentElement.scrollWidth, cmd:document.getElementById('rd-cmd').textContent.slice(0,60)}}""")
                print(name,"flow",i,json.dumps(info,ensure_ascii=False))
                await pg.screenshot(path=str(OUT/f"fix_flow_{name}{i}.png"))
            # шаг 4: скиллы медленнее
            await go(pg,"ch-redis",3); await pg.wait_for_timeout(2600)
            c1=await pg.evaluate("document.getElementById('rd-cmd').textContent.slice(0,50)")
            await pg.wait_for_timeout(5600)
            c2=await pg.evaluate("[document.getElementById('rd-cmd').textContent.slice(0,60), document.getElementById('rd-cap').textContent, Math.round(document.querySelector('#redis-stage .sc-stage__frame').getBoundingClientRect().height), window.FlowViz.state.phase, document.getElementById('fmRail').classList.contains('is-on')]")
            print(name,"skills",c1,"|",c2)
            await pg.screenshot(path=str(OUT/f"fix_skills_{name}.png"))
            print(name,"errors:",errs[:5])
        await b.close()
asyncio.run(main())
