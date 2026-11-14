"""Стенд: карточка ws-diff над текстом ответа — живой ход и история после F5.
Настоящие theme.css/chat.css/ws-diff.css/ws-diff.js, ответ /ws_diff — снимок sandbox /diff/chat/425826."""
import asyncio, json, os, sys
from playwright.async_api import async_playwright
R = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
S = R + '/api_gateway/static'
D = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(D + '/chat_425826.json'))
HIST = [m for m in DATA['items'] if m.get('message_id')][:2]

def agent(mid=None, did=None, reasoning=False, text='Ответ агента: правки готовы.'):
    a = f' data-id="{did}"' if did else ''
    a += f' data-message-id="{mid}"' if mid else ''
    r = '<div class="reasoning-block reasoning-collapsed"><div class="reasoning-header">Размышления · 12 с</div></div>' if reasoning else ''
    return (f'<div class="agent-message"{a}>{r}<div data-seg-kind="md" class="seg-text"><p>{text}</p></div>'
            '<div class="message-actions"><button>Копировать</button></div></div>')

def page_html(theme, body):
    css = ''.join(f'<link rel="stylesheet" href="file://{S}/css/{c}.css">' for c in ('theme', 'chat', 'ws-diff'))
    return f'''<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{css}
<script src="file://{S}/js/vendor/highlight.min.js"></script></head>
<body><div class="chat-main-inner chat-active"><div class="chat-messages" style="max-width:820px;margin:0 auto;padding:16px">
<div class="user-message"><div class="message-text">Измени файл</div></div>{body}</div></div>
<script src="file://{S}/js/ws-diff.js"></script></body></html>'''

ORDER = """(sel)=>[...document.querySelectorAll(sel)].map(m=>[...m.children].map(c=>c.className.split(' ')[0]||c.tagName))"""

async def run(pw, w, theme, errs, shots):
    b = await pw.chromium.launch(args=['--no-sandbox'])
    p = await b.new_page(viewport={'width': w, 'height': 900})
    p.on('pageerror', lambda e: errs.append(str(e)))
    p.on('console', lambda m: m.type == 'error' and errs.append(m.text))
    body = agent(HIST[0]['message_id'], 33670) + '<div class="user-message"><div class="message-text">Ещё</div></div>' \
         + agent(HIST[1]['message_id'], 33674, reasoning=True)
    f = f'/tmp/wsdplace_{w}_{theme}.html'; open(f, 'w').write(page_html(theme, body))
    await p.goto('file://' + f, wait_until='domcontentloaded')
    await p.evaluate("""(d)=>{window.fetch=async()=>({ok:true,json:async()=>d});}""", {'items': HIST, 'running_request_id': None})
    # 1) F5: история, restore
    await p.evaluate("WsDiff.init({transport:'ws'}).restore('425826')")
    await p.wait_for_timeout(300)
    o = await p.evaluate(ORDER, '.agent-message')
    assert o[0][0] == 'wsd', o; assert o[1][:2] == ['reasoning-block', 'wsd'], o
    st = await p.evaluate("[...document.querySelectorAll('.wsd')].map(e=>e.dataset.state)")
    assert st == ['done', 'done'], st
    geo = await p.evaluate("""[...document.querySelectorAll('.agent-message')].map(m=>{const c=m.querySelector('.wsd').getBoundingClientRect(),t=m.querySelector('.seg-text').getBoundingClientRect();return [c.bottom<=t.top+0.5, c.right<=m.getBoundingClientRect().right+0.5]})""")
    assert all(a and r for a, r in geo), geo
    assert await p.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'hscroll'
    shot = f'/tmp/wsdplace_f5_{w}_{theme}.png'; await p.screenshot(path=shot, full_page=True); shots.append(shot)
    # 2) живой ход: рассуждения → событие → задачи → перерисовка текста → actions
    await p.evaluate("""()=>{const m=document.createElement('div');m.className='agent-message';m.id='live';
      m.innerHTML='<div class="reasoning-block"><div class="reasoning-header">Размышления</div></div><div class="live-block">пишу…</div>';
      document.querySelector('.chat-messages').append(m);}""")
    ev = dict(DATA['items'][0]); ev['request_id'] = 'live-1'; ev.pop('message_id', None)
    await p.evaluate("(e)=>WsDiff.handle(Object.assign({type:'ws_diff'},e))", ev)
    await p.wait_for_timeout(100)
    assert (await p.evaluate(ORDER, '#live'))[0] == ['reasoning-block', 'wsd', 'live-block']
    assert await p.evaluate("document.querySelector('#live .wsd').dataset.state") == 'live'
    # TaskCards.place кладёт задачи сразу после рассуждений → карточка встаёт обратно сразу за ними
    await p.evaluate("""()=>{const g=document.createElement('div');g.className='task-cards';g.textContent='задача';document.querySelector('#live .reasoning-block').after(g);}""")
    await p.wait_for_timeout(100)
    assert (await p.evaluate(ORDER, '#live'))[0][:3] == ['reasoning-block', 'wsd', 'task-cards']
    # финальная перерисовка без .wsd в белом списке (старый app.js) — observer возвращает карточку наверх
    await p.evaluate("""()=>{const m=document.querySelector('#live');m.querySelector('.wsd').remove();m.querySelector('.live-block').remove();
      m.insertAdjacentHTML('beforeend','<div data-seg-kind="md" class="seg-text"><p>Готово.</p></div><div class="message-actions"><button>Копировать</button></div>');}""")
    await p.wait_for_timeout(150)
    o = (await p.evaluate(ORDER, '#live'))[0]
    assert o[:2] == ['reasoning-block', 'wsd'] and o.index('wsd') < o.index('seg-text'), o
    assert await p.evaluate("document.querySelector('#live .wsd').dataset.state") == 'done'
    await b.close()

async def main():
    errs, shots = [], []
    async with async_playwright() as pw:
        for w, t in ((1440, 'light'), (1440, 'dark'), (390, 'dark')):
            await run(pw, w, t, errs, shots)
    print('errors:', errs); print('shots:', shots)
    assert not errs
    print('OK')
asyncio.run(main())
