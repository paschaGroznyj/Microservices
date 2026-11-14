"""Стенд: дубль ответа агента после возврата во вкладку (клик по push).
Настоящие chat.html + app.js + все скрипты; API и WebSocket замоканы.
«Скрытая вкладка» = visibilityState hidden + RAF на паузе (как в Chrome).
Сценарии:
  same)  открыт тот же чат, вкладка скрыта, пришли чанки + финал, вкладка видима -> resync
  other) открыт другой чат, вкладка скрыта, в фоне c1 получил финал, клик по push -> openChat(c1)
"""
import asyncio, json, sys, threading, time
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2] / 'api_gateway'
OUT = Path(__file__).resolve().parent
env = Environment(loader=FileSystemLoader(str(ROOT / 'templates')))
PORT = 18931
app = FastAPI()
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')

FINAL_TEXT = 'Официальная учетная цена золота на сегодня составляет **10 222,10 рубля за 1 грамм**.'
HIST = {
    'c1': [
        {'id': 33730, 'message_id': 'u-1', 'message': 'привет', 'is_from_bot': False, 'created_at': '2026-11-14T18:40:00'},
        {'id': 33731, 'message_id': 'msg-old', 'message': 'Привет! Чем помочь?', 'is_from_bot': True, 'created_at': '2026-11-14T18:40:05'},
        {'id': 33732, 'message_id': 'u-2', 'message': 'что по курсу золота сегодня', 'is_from_bot': False, 'created_at': '2026-11-14T18:41:00'},
    ],
    'c2': [
        {'id': 100, 'message_id': 'u-x', 'message': 'другой чат', 'is_from_bot': False, 'created_at': '2026-11-14T18:00:00'},
        {'id': 101, 'message_id': 'msg-x', 'message': 'ответ в другом чате', 'is_from_bot': True, 'created_at': '2026-11-14T18:00:05'},
    ],
}
FINAL = {'id': 33733, 'message_id': 'msg-1794667307321-f6db196b', 'message': FINAL_TEXT, 'is_from_bot': True,
         'created_at': '2026-11-14T18:41:20'}
STATE = {'saved': False, 'delay': 0.0}


@app.get('/', response_class=HTMLResponse)
async def index():
    return env.get_template('chat.html').render(is_sigma=False, user={'user_id': 56, 'username': 'pavel', 'source': ''},
                                                request=None)


@app.get('/api/chats')
async def chats():
    return [{'id': 'c1', 'title': 'Золото', 'last_message': '', 'timestamp': '2026-11-14T18:41:00'},
            {'id': 'c2', 'title': 'Другой', 'last_message': '', 'timestamp': '2026-11-14T18:00:00'}]


@app.get('/api/chats/{cid}/messages')
async def msgs(cid: str):
    if STATE['delay']:
        await asyncio.sleep(STATE['delay'])
    m = list(HIST.get(cid, []))
    if cid == 'c1' and STATE['saved']:
        m.append(FINAL)
    return {'messages': m, 'has_more': False}


@app.post('/__saved')
async def saved():
    STATE['saved'] = True
    STATE['delay'] = STATE.get('next_delay', 0.0)
    return {'ok': True}


@app.post('/__reset')
async def reset(delay: float = 0.0):
    STATE['saved'] = False
    STATE['delay'] = 0.0
    STATE['next_delay'] = delay
    return {'ok': True}


@app.api_route('/api/{rest:path}', methods=['GET', 'POST', 'PUT', 'DELETE'])
async def any_api(rest: str, request: Request):
    if rest.endswith('run_status'):
        return {'running': False, 'loop_active': False}
    if rest.endswith('flow'):
        return {'events': [], 'reasoning': []}
    return JSONResponse({})


def serve():
    uvicorn.run(app, host='127.0.0.1', port=PORT, log_level='error')


INIT = r"""
(() => {
  window.__hidden = false; const q = [];
  Object.defineProperty(document, 'visibilityState', { get: () => window.__hidden ? 'hidden' : 'visible' });
  Object.defineProperty(document, 'hidden', { get: () => window.__hidden });
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => { if (window.__hidden) { q.push(cb); return -q.length; } return raf(cb); };
  const caf = window.cancelAnimationFrame.bind(window);
  window.cancelAnimationFrame = (id) => { if (id < 0) { q[-id - 1] = null; return; } caf(id); };
  window.__setHidden = (h) => {
    window.__hidden = h; document.dispatchEvent(new Event('visibilitychange'));
    if (!h) { const l = q.splice(0); l.forEach(cb => cb && raf(cb)); }
  };
  window.__socks = {};
  class FakeWS {
    constructor(url) { this.url = url; this.readyState = 0; const m = url.match(/\/ws\/chat\/([^/?]+)/);
      this.chat = m ? m[1] : url; window.__socks[this.chat] = this;
      setTimeout(() => { this.readyState = 1; this.onopen && this.onopen({}); }, 10); }
    send() {} close() { this.readyState = 3; }
    addEventListener(t, f) { this['on' + t] = f; }
  }
  FakeWS.OPEN = 1; FakeWS.CONNECTING = 0; FakeWS.CLOSED = 3; FakeWS.CLOSING = 2;
  window.WebSocket = FakeWS;
  window.__wsSend = (chat, obj) => { const s = window.__socks[chat]; if (!s || !s.onmessage) return false;
    s.onmessage({ data: JSON.stringify(obj) }); return true; };
  window.__errs = []; window.addEventListener('error', e => window.__errs.push(String(e.message)));
})();
"""

COUNT = """() => { const c = window.__catchpmApp.getMessagesContainer();
  const els = [...c.querySelectorAll(':scope > .agent-message')].filter(e => (e.textContent||'').includes('10 222,10'));
  const occ = (c.textContent.match(/10 222,10/g) || []).length;
  const all = c.querySelectorAll(':scope > .agent-message').length;
  return {occ, all, els: els.map(e => ({id: e.dataset.id, mid: e.dataset.messageId, actions: e.querySelectorAll('.message-actions').length}))}; }"""


async def stream_final(p, chat):
    for ch in ['Официальная учетная цена ', 'золота на сегодня ', 'составляет **10 222,10 рубля за 1 грамм**.']:
        await p.evaluate("([c,t]) => window.__wsSend(c, {is_from_bot:true, chunk:t})", [chat, ch])
    await p.evaluate("([c]) => window.__wsSend(c, {is_from_bot:true, kind:'final_commit'})", [chat])
    await p.evaluate("() => fetch('/__saved', {method:'POST'})")
    await p.evaluate("([c,f]) => window.__wsSend(c, f)", [chat, FINAL])


async def scenario(br, name, w, other_chat, theme):
    ctx = await br.new_context(viewport={'width': w, 'height': 800})
    p = await ctx.new_page()
    errs = []
    p.on('pageerror', lambda e: errs.append(str(e)))
    await p.add_init_script(INIT)
    await p.add_init_script(f"try{{localStorage.setItem('theme','{theme}')}}catch(_){{}}")
    await p.goto(f'http://127.0.0.1:{PORT}/', wait_until='domcontentloaded')
    await p.evaluate("() => fetch('/__reset', {method:'POST'})")
    await p.wait_for_function("() => window.__catchpmApp && document.querySelector('#chat-list')", timeout=15000)
    await p.wait_for_timeout(800)
    await p.evaluate("() => window.__catchpmApp.openChat('c1')")
    await p.wait_for_timeout(600)
    await p.evaluate("() => window.__wsSend('c1', {is_from_bot:true, chunk:'Думаю над курсом...'})")
    await p.wait_for_timeout(300)
    if other_chat:
        await p.evaluate("() => window.__catchpmApp.openChat('c2')")
        await p.wait_for_timeout(600)
    await p.evaluate("() => window.__setHidden(true)")
    await p.wait_for_timeout(200)
    await stream_final(p, 'c1')
    await p.wait_for_timeout(500)
    await p.evaluate("() => window.__setHidden(false)")
    await p.evaluate("""() => { const a = window.__catchpmApp;
        if (String(a.currentChat) !== 'c1') a.openChat('c1'); }""")
    await p.wait_for_timeout(2500)
    res = await p.evaluate(COUNT)
    errs += await p.evaluate('() => window.__errs')
    await p.screenshot(path=str(OUT / f'out_{name}_{w}_{theme}.png'))
    await ctx.close()
    return res, errs


async def main():
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(1.5)
    ok = True
    async with async_playwright() as pw:
        br = await pw.chromium.launch(args=['--no-sandbox'])
        for w, theme in ((1440, 'light'), (1440, 'dark'), (390, 'dark')):
            for name, other in (('same', False), ('other', True)):
                res, errs = await scenario(br, name, w, other, theme)
                els = res['els']
                good = res['occ'] == 1 and len(els) == 1 and els[0]['mid'] == FINAL['message_id'] and els[0]['actions'] == 1
                ok &= good and not errs
                print(f'{name:5} {w} {theme}: {res} errs={errs[:3]} -> {"OK" if good and not errs else "FAIL"}')
        await br.close()
    print('ALL OK' if ok else 'SOME FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    asyncio.run(main())
