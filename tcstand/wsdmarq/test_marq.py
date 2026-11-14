"""Стенд: строки файлов ws-diff на мобиле — путь не налезает на бейджи/счётчики, длинный путь едет бегущей строкой."""
import asyncio, json, os
from playwright.async_api import async_playwright
R = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
S = R + '/api_gateway/static'; D = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(R + '/tcstand/wsdplace/chat_425826_v2.json'))
IT = [m for m in DATA['items'] if len(m['files']) == 10][0]

def html(theme):
    css = ''.join(f'<link rel="stylesheet" href="file://{S}/css/{c}.css">' for c in ('theme', 'chat', 'ws-diff'))
    return f"""<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{css}
<script src="file://{S}/js/vendor/highlight.min.js"></script></head><body><div class="chat-main-inner chat-active"><div class="chat-messages" style="max-width:820px;margin:0 auto;padding:16px 12px">
<div class="agent-message" data-message-id="{IT['message_id']}"><div data-seg-kind="md" class="seg-text"><p>Ответ.</p></div><div class="message-actions"><button>Копировать</button></div></div>
</div></div><script src="file://{S}/js/ws-diff.js"></script></body></html>"""

GEO = """()=>[...document.querySelectorAll('.wsd-fhead')].map(h=>{const p=h.querySelector('.wsd-path'),r=p.getBoundingClientRect();
 const nx=[...h.children].filter(c=>c.compareDocumentPosition(p)&Node.DOCUMENT_POSITION_PRECEDING&&c.getBoundingClientRect().width>0)[0];
 return {path:p.title,run:p.classList.contains('is-run'),right:r.right,next:nx?nx.getBoundingClientRect().left:null,nextCls:nx?nx.className:null,
  hr:h.getBoundingClientRect().right,last:[...h.children].pop().getBoundingClientRect().right}})"""

async def run(pw, w, theme, errs, shots):
    b = await pw.chromium.launch(args=['--no-sandbox'])
    p = await b.new_page(viewport={'width': w, 'height': 800}, device_scale_factor=3)
    p.on('pageerror', lambda e: errs.append(str(e))); p.on('console', lambda m: m.type == 'error' and errs.append(m.text))
    f = f'/tmp/wsdmarq_{w}_{theme}.html'; open(f, 'w').write(html(theme))
    await p.goto('file://' + f, wait_until='domcontentloaded')
    await p.evaluate("(d)=>{window.fetch=async()=>({ok:true,json:async()=>d});}", {'items': [IT], 'running_request_id': None})
    await p.evaluate("WsDiff.init({transport:'ws'}).restore('425826')"); await p.wait_for_timeout(400)
    await p.evaluate("document.querySelectorAll('.wsd-file').forEach(x=>x.classList.remove('is-open'))"); await p.wait_for_timeout(200)
    g = await p.evaluate(GEO)
    for r in g:
        assert r['next'] is None or r['right'] <= r['next'] + 0.5, ('overlap', w, r)
        assert r['last'] <= r['hr'] + 0.5, ('out', w, r)
    cnt = await p.evaluate("(()=>{const c=document.querySelector('.wsd-count');return [c.textContent,c.scrollWidth,c.clientWidth]})()")
    runs = [r['path'] for r in g if r['run']]
    tx = await p.evaluate("""async()=>{const e=document.querySelector('.wsd-path.is-run .wsd-pin');if(!e)return null;const a=getComputedStyle(e).transform;
      await new Promise(r=>setTimeout(r,2500));return [a,getComputedStyle(e).transform]}""")
    if runs: assert tx and tx[0] != tx[1], tx
    assert await p.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'hscroll'
    print(w, theme, 'run:', len(runs), [x[-28:] for x in runs], 'count:', cnt, 'tx:', tx)
    await p.add_style_tag(content='.wsd-path.is-run .wsd-pin,.wsd-path.is-run{animation-play-state:paused!important;animation-delay:0s!important}')
    await p.evaluate("document.querySelectorAll('.wsd-path.is-run .wsd-pin,.wsd-path.is-run').forEach(e=>{e.style.animation='none';e.offsetWidth;e.style.animation='';})")
    await p.wait_for_timeout(100)
    el = await p.query_selector('.wsd'); s = f'{D}/out_{w}_{theme}.png'; await el.screenshot(path=s); shots.append(s)
    run1 = await p.query_selector('.wsd-path.is-run')
    if run1:
        h = await run1.evaluate_handle('e=>e.closest(".wsd-fhead")'); s2 = f'{D}/row_{w}_{theme}.png'; await h.as_element().screenshot(path=s2); shots.append(s2)
        await p.add_style_tag(content='.wsd-path.is-run .wsd-pin,.wsd-path.is-run{animation-delay:calc(var(--wsd-dur) * -0.72)!important}')
        await p.wait_for_timeout(100); s3 = f'{D}/row_{w}_{theme}_mid.png'; await h.as_element().screenshot(path=s3); shots.append(s3)
        el = await p.query_selector('.wsd'); s4 = f'{D}/out_{w}_{theme}_mid.png'; await el.screenshot(path=s4); shots.append(s4)
    await b.close()

async def main():
    errs, shots = [], []
    async with async_playwright() as pw:
        for w, t in ((360, 'dark'), (390, 'light'), (412, 'dark'), (1440, 'dark')):
            await run(pw, w, t, errs, shots)
    print('errors:', errs); assert not errs; print('OK')
asyncio.run(main())
