"""Стенд: после F5 карточка ws-diff и файлы свёрнуты; кнопка «Развернуть» не остаётся у свёрнутого файла.
Настоящие theme.css/chat.css/ws-diff.css/ws-diff.js, ответ /ws_diff — снимок sandbox /diff/chat/425826."""
import asyncio, json, os, sys
from playwright.async_api import async_playwright
R = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
S = R + '/api_gateway/static'
D = os.path.dirname(os.path.abspath(__file__))
DATA = json.load(open(D + '/../wsdplace/chat_425826_v2.json'))
HIST = [m for m in DATA['items'] if m['request_id'].startswith(('a8932665','1b8c28f3'))]

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


VIS = "(s)=>[...document.querySelectorAll(s)].filter(e=>e.offsetParent!==null).length"

async def run(pw, w, theme, errs, shots):
    b = await pw.chromium.launch(args=['--no-sandbox'])
    p = await b.new_page(viewport={'width': w, 'height': 900})
    p.on('pageerror', lambda e: errs.append(str(e)))
    p.on('console', lambda m: m.type == 'error' and errs.append(m.text))
    body = agent(HIST[0].get('message_id'), 1) + agent(HIST[1].get('message_id'), 2, reasoning=True)
    f = f'/tmp/wsdfold_{w}_{theme}.html'; open(f, 'w').write(page_html(theme, body))
    await p.goto('file://' + f, wait_until='domcontentloaded')
    await p.evaluate("(d)=>{window.fetch=async()=>({ok:true,json:async()=>d});}", {'items': HIST, 'running_request_id': None})
    await p.evaluate("WsDiff.init({transport:'ws'}).restore('425826')"); await p.wait_for_timeout(300)
    # 1) F5: всё свёрнуто
    n = await p.evaluate("document.querySelectorAll('.wsd').length"); assert n == 2, n
    assert await p.evaluate("[...document.querySelectorAll('.wsd')].every(e=>e.classList.contains('is-collapsed'))"), 'card open'
    assert await p.evaluate("document.querySelectorAll('.wsd-file.is-open').length") == 0, 'file open'
    assert await p.evaluate(VIS, '.wsd-body') == 0
    shots.append(f'/tmp/wsdfold_f5_{w}_{theme}.png'); await p.screenshot(path=shots[-1], full_page=True)
    # 2) раскрыли карточку → файлы закрыты, «Развернуть» не видно
    card = p.locator('.wsd').first
    await card.locator('.wsd-head').click(); await p.wait_for_timeout(100)
    assert await p.evaluate(VIS, '.wsd-more') == 0
    # 3) открыли длинный файл → кнопка есть; закрыли → кнопки нет
    fh = card.locator('.wsd-file', has_text='test_editor_path.py').locator('.wsd-fhead')
    await fh.click(); await p.wait_for_timeout(150)
    assert await p.evaluate(VIS, '.wsd-more') == 1, 'no more btn'
    shots.append(f'/tmp/wsdfold_open_{w}_{theme}.png'); await card.screenshot(path=shots[-1])
    await fh.click(); await p.wait_for_timeout(100)
    assert await p.evaluate("document.querySelectorAll('.wsd-more').length") == 0, 'more left after close'
    # 4) «Свернуть все» тоже убирает кнопку
    await fh.click(); await p.wait_for_timeout(100)
    await card.locator('[data-act=fold]').click(); await p.wait_for_timeout(100)
    assert await p.evaluate("document.querySelectorAll('.wsd-more').length") == 0, 'more left after fold'
    assert await p.evaluate("document.querySelectorAll('.wsd-file.is-open').length") == 0
    shots.append(f'/tmp/wsdfold_closed_{w}_{theme}.png'); await card.screenshot(path=shots[-1])
    # 5) свернули карточку целиком → ничего ниже шапки
    await card.locator('.wsd-head').click(); await p.wait_for_timeout(100)
    hh = await p.evaluate("(()=>{const c=document.querySelector('.wsd'),h=c.querySelector('.wsd-head');return [c.getBoundingClientRect().height,h.getBoundingClientRect().height]})()")
    assert abs(hh[0] - hh[1]) < 3, hh
    # 6) живой ход: новая карточка не помечена restored, состояние пользователя сохраняется при перерисовке
    await p.evaluate("""()=>{const m=document.createElement('div');m.className='agent-message';m.id='live';
      m.innerHTML='<div class="reasoning-block"><div class="reasoning-header">Размышления</div></div><div class="live-block">пишу…</div>';
      document.querySelector('.chat-messages').append(m);}""")
    ev = dict(HIST[1]); ev['request_id'] = 'live-1'; ev.pop('message_id', None)
    await p.evaluate("(e)=>WsDiff.handle(Object.assign({type:'ws_diff'},e))", ev); await p.wait_for_timeout(100)
    await p.locator('#live .wsd-head').click(); await p.wait_for_timeout(50)
    await p.evaluate("(e)=>WsDiff.handle(Object.assign({type:'ws_diff'},e))", ev); await p.wait_for_timeout(100)
    assert not await p.evaluate("document.querySelector('#live .wsd').classList.contains('is-collapsed')"), 'live state lost'
    # 7) бинарники (и секреты/omitted) по умолчанию свёрнуты, плашки .wsd-note нет; открыты первые 2 текстовых
    st = await p.evaluate("""()=>{const fs=[...document.querySelectorAll('#live .wsd-file')];
      return {bin:fs.filter(f=>f.querySelector('.wsd-tag')).map(f=>f.classList.contains('is-open')),
              txtOpen:fs.filter(f=>!f.querySelector('.wsd-tag')&&f.classList.contains('is-open')).length,
              notes:document.querySelectorAll('#live .wsd-note').length}}""")
    assert st['bin'] and not any(st['bin']) and st['notes'] == 0 and st['txtOpen'] == 2, st
    shots.append(f'/tmp/wsdfold_live_{w}_{theme}.png'); await p.locator('#live .wsd').screenshot(path=shots[-1])
    bf = p.locator('#live .wsd-file:has(.wsd-tag) .wsd-fhead').first
    await bf.click(); await p.wait_for_timeout(50)
    assert await p.evaluate("document.querySelectorAll('#live .wsd-note').length") == 1, 'note not shown on open'
    shots.append(f'/tmp/wsdfold_bin_{w}_{theme}.png'); await p.locator('#live .wsd').screenshot(path=shots[-1])
    await bf.click(); await p.wait_for_timeout(50)
    assert await p.evaluate("document.querySelectorAll('#live .wsd-note').length") == 0, 'note left after close'
    await p.evaluate("(e)=>WsDiff.handle(Object.assign({type:'ws_diff'},e))", ev); await p.wait_for_timeout(100)
    assert await p.evaluate("document.querySelectorAll('#live .wsd-note').length") == 0, 'note back after repaint'
    assert await p.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'hscroll'
    await b.close()

async def main():
    errs, shots = [], []
    async with async_playwright() as pw:
        for w, t in ((390, 'dark'), (360, 'light'), (1440, 'light')):
            await run(pw, w, t, errs, shots)
    print('errors:', errs); print('shots:', shots)
    assert not errs
    print('OK')
asyncio.run(main())
