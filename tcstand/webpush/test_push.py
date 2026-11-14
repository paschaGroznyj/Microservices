"""Стенд Web Push: настоящие chat.html (профиль), theme/modals/push.css, push.js, sw.js,
push_gw.py и run_status.push_notify_setting (дебаунс 1 с). Redis и chats_service — в памяти.
Запуск: ../../.venvt/bin/python tcstand/webpush/test_push.py [1440|390] [light|dark]
"""
import asyncio, json, os, re, sys, threading, time
R = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, R + '/api_gateway'); sys.path.insert(0, R + '/chats_service')
D = os.path.dirname(os.path.abspath(__file__))
import uvicorn, jinja2
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import push_gw, run_status
from app import push_sender as ps
from py_vapid import Vapid

PORT = 18631


class FakeRedis:
    def __init__(self): self.kv, self.ttl, self.lists = {}, {}, {}
    async def get(self, k): return self.kv.get(k)
    async def set(self, k, v, ex=None, nx=False): self.kv[k] = v; self.ttl[k] = ex; return True
    async def delete(self, *ks): [self.kv.pop(k, None) for k in ks]; return 1
    async def rpush(self, k, v): self.lists.setdefault(k, []).append(v); return 1
    async def exists(self, k): return int(k in self.kv)


REDIS = FakeRedis()
DB = {'notify_enabled': False, 'subs': {}, 'settings_writes': []}
VAPID = Vapid(); VAPID.generate_keys()


class FakeResp:
    def __init__(self, b, code=200): self._b, self.status_code = b, code
    def json(self): return self._b


class FakeChats:
    async def get(self, url, timeout=None, **kw):
        return FakeResp({'public_key': ps.public_key_b64(VAPID)})
    async def post(self, url, headers=None, json=None, timeout=None):
        if url.endswith('/push/subscribe'): DB['subs'][json['endpoint']] = json
        if url.endswith('/push/unsubscribe'): DB['subs'].pop(json['endpoint'], None)
        return FakeResp({'ok': True})


async def get_user(request): return {'user_id': 56, 'username': 'pavel'}

run_status._redis = REDIS
push_gw.bind(http_client=FakeChats(), chats_url='http://chats', get_current_user=get_user,
             get_redis=lambda: REDIS, static_dir=R + '/api_gateway/static', gw='main')
app = FastAPI()
app.include_router(push_gw.router)
app.mount('/static', StaticFiles(directory=R + '/api_gateway/static'), name='static')


@app.get('/api/user/settings')
async def gs(): return {'user_id': 56, 'notify_enabled': DB['notify_enabled']}


@app.post('/api/user/settings')
async def ps_(request: Request):
    data = await request.json()
    await run_status.push_notify_setting(56, bool(data['notify_enabled']))
    return {'ok': True}


async def chats_worker():  # как _flush_pending_user_settings_from_redis
    while True:
        q = REDIS.lists.get(run_status.USER_SETTINGS_QUEUE_KEY) or []
        while q:
            ev = json.loads(q.pop(0)); DB['notify_enabled'] = ev['notify_enabled']; DB['settings_writes'].append(ev)
        await asyncio.sleep(0.05)


@app.on_event('startup')
async def _start(): asyncio.create_task(chats_worker())


def profile_html(theme):
    t = open(R + '/api_gateway/templates/chat.html').read()
    a = t.index('<!-- PROFILE OVERLAY -->'); b = t.index('<!-- ══', a)
    snippet = jinja2.Template(t[a:b]).render(is_sigma=False)
    css = ''.join(f'<link rel="stylesheet" href="/static/css/{c}.css">' for c in ('theme', 'layout', 'modals', 'palette', 'tg-chats', 'push'))
    return f"""<!doctype html><html lang="ru" data-theme="{theme}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{css}</head><body>{snippet}
<script>window.__catchpmApp={{currentChat:'c1',opened:[],openChat(id){{this.opened.push(id);this.currentChat=id;}}}};
document.getElementById('profile-overlay').classList.add('active');</script>
<script src="/static/js/push.js"></script></body></html>"""


THEME = {'v': 'light'}
@app.get('/', response_class=HTMLResponse)
async def index(): return profile_html(THEME['v'])


def serve():
    uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='warning')).run()


async def main(w, theme):
    from playwright.async_api import async_playwright
    THEME['v'] = theme
    threading.Thread(target=serve, daemon=True).start(); time.sleep(1.2)
    errs, origin = [], f'http://127.0.0.1:{PORT}'
    async with async_playwright() as pw:
        import tempfile
        # persistent context: в инкогнито Chrome Push API не поддерживает
        ctx = await pw.chromium.launch_persistent_context(tempfile.mkdtemp(), executable_path='/usr/bin/chromium',
                                                          args=['--no-sandbox'], viewport={'width': w, 'height': 900})
        b = ctx
        p = ctx.pages[0] if ctx.pages else await ctx.new_page()
        p.on('pageerror', lambda e: errs.append(str(e)))
        p.on('console', lambda m: m.type == 'error' and 'Failed to load resource' not in m.text and errs.append(m.text))
        p.on('response', lambda r: r.status >= 400 and errs.append(f'{r.status} {r.url}'))
        LOG = []
        p.on('console', lambda m: m.text.startswith('DBG') and LOG.append(m.text))
        await ctx.add_init_script("document.addEventListener('change',e=>console.log('DBG change',e.target.checked,e.isTrusted,performance.now()|0),true);document.addEventListener('click',e=>console.log('DBG click',e.target.tagName,e.target.className,performance.now()|0),true)")
        await p.goto(origin + '/?push_chat=c77', wait_until='domcontentloaded'); await p.wait_for_timeout(500)
        # 0) ?push_chat -> sessionStorage для app.js, параметр убран из адреса
        assert await p.evaluate("sessionStorage.getItem('catchpm:restore_chat_id')") == 'c77'
        assert p.url == origin + '/', p.url
        # 1) старт: выключено, подсказки нет
        sw = p.locator('#pn-switch')
        assert not await sw.is_checked() and not await sw.is_disabled()
        assert await p.evaluate("document.getElementById('pn-hint').textContent") == ''
        await p.locator('#notify-section').screenshot(path=f'{D}/out_off_{w}_{theme}.png')
        # 2) отказ в разрешении -> откат, подсказка
        await p.evaluate("Notification.requestPermission=async()=> 'default'")
        await p.locator('.pn-row').click(); await p.wait_for_timeout(300)
        assert not await sw.is_checked()
        assert 'не выдано' in await p.evaluate("document.getElementById('pn-hint').textContent")
        # 3) разрешение есть -> реальная подписка через sw.js
        await ctx.grant_permissions(['notifications'], origin=origin)
        await p.reload(wait_until='domcontentloaded'); await p.wait_for_timeout(400)
        sub_err = None
        await p.locator('.pn-row').click()
        # настоящая регистрация в FCM из headless иногда идёт 20-35 с — ждём, пока push.js закончит
        t_sub = time.time()
        busy_click = await p.evaluate("window.WebPush.state.busy")
        if busy_click:  # клик во время подписки игнорируется, тумблер остаётся включённым
            pre = (await sw.is_checked(), await sw.is_disabled())
            await p.locator('.pn-row').click(force=True); await p.wait_for_timeout(100)
            assert await sw.is_checked(), ('тумблер откатился во время подписки', pre, LOG[-4:], await p.evaluate("JSON.stringify(window.WebPush.state)"))
        await p.wait_for_function("!window.WebPush.state.busy", timeout=90000)
        t_sub = round(time.time() - t_sub, 1)
        hint = await p.evaluate("document.getElementById('pn-hint').textContent")
        real = bool(DB['subs'])
        print('subscribe:', real, repr(hint), await sw.is_checked(), await p.evaluate("JSON.stringify(window.WebPush.state)"))
        if not real:
            sub_err = hint
            # headless без FCM: подменяем pushManager.subscribe, остальное — настоящее
            await p.evaluate("""()=>{PushManager.prototype.subscribe=async function(o){return {options:o,
              toJSON:()=>({endpoint:'https://fcm.googleapis.com/fcm/send/stand',keys:{p256dh:'BPstand',auth:'Astand'}}),unsubscribe:async()=>true};};
              PushManager.prototype.getSubscription=async()=>null;}""")
            await p.locator('.pn-row').click(); await p.wait_for_timeout(800)
        assert DB['subs'], ('нет подписки', hint)
        try:
            await p.wait_for_function("window.WebPush.state.enabled && !window.WebPush.state.busy", timeout=30000)
        except Exception:
            print('LOG', LOG, DB['settings_writes']); print('STUCK', await p.evaluate("JSON.stringify(window.WebPush.state)"), await p.evaluate("document.getElementById('pn-hint').textContent"), list(DB['subs'])[:1])
            raise
        sub = list(DB['subs'].values())[0]
        assert sub['gw'] == 'main' and sub['keys']['auth'], sub
        assert await sw.is_checked()
        reg = await p.evaluate("navigator.serviceWorker.getRegistration('/').then(r=>r&&r.active&&r.active.scriptURL)")
        assert reg == origin + '/sw.js', reg
        # 4) дебаунс: настройка в «БД» не сразу, а через ~1 с
        assert DB['notify_enabled'] is False
        await p.wait_for_timeout(1300)
        assert DB['notify_enabled'] is True and len(DB['settings_writes']) == 1, DB
        # 5) вкладка на экране: viewing:56:c1, TTL 35
        assert REDIS.kv.get('viewing:56:c1') == '1' and REDIS.ttl['viewing:56:c1'] == 35, REDIS.kv
        # быстрые 3 клика -> одна запись с последним значением
        await p.locator('.pn-row').click(); await p.wait_for_timeout(150)
        await p.locator('.pn-row').click(); await p.wait_for_timeout(150)
        await p.locator('.pn-row').click(); await p.wait_for_timeout(1500)
        assert DB['notify_enabled'] is False and len(DB['settings_writes']) == 2, DB['settings_writes']
        await p.locator('.pn-row').click(); await p.wait_for_timeout(1500)
        assert DB['notify_enabled'] is True and len(DB['settings_writes']) == 3
        # 6) смена чата -> c1 снят, c2 поставлен
        await p.evaluate("window.__catchpmApp.currentChat='c2'"); await p.wait_for_timeout(1300)
        assert 'viewing:56:c1' not in REDIS.kv and REDIS.kv.get('viewing:56:c2') == '1', REDIS.kv
        # 7) вкладка скрыта -> ключ снят; снова видима -> поставлен
        await p.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});document.dispatchEvent(new Event('visibilitychange'))")
        await p.wait_for_timeout(500)
        assert 'viewing:56:c2' not in REDIS.kv, REDIS.kv
        await p.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});document.dispatchEvent(new Event('visibilitychange'))")
        await p.wait_for_timeout(500)
        assert REDIS.kv.get('viewing:56:c2') == '1'
        # 8) push в service worker (CDP) -> уведомление с нашим заголовком
        cdp = await ctx.new_cdp_session(p)
        await cdp.send('ServiceWorker.enable')
        regs = []
        cdp.on('ServiceWorker.workerRegistrationUpdated', lambda e: regs.extend(e['registrations']))
        await cdp.send('ServiceWorker.disable'); await cdp.send('ServiceWorker.enable'); await p.wait_for_timeout(400)
        rid = [r for r in regs if r['scopeURL'] == origin + '/'][0]['registrationId']
        data = json.dumps(ps.build_payload('c9', 'Пуши', '## Готово\nИзменил **два** файла'), ensure_ascii=False)
        await cdp.send('ServiceWorker.deliverPushMessage', {'origin': origin, 'registrationId': rid, 'data': data})
        notes = []
        for _ in range(30):
            await p.wait_for_timeout(100)
            notes = await p.evaluate("navigator.serviceWorker.ready.then(r=>r.getNotifications()).then(l=>l.map(n=>({t:n.title,b:n.body,tag:n.tag,d:n.data})))")
            if notes: break
        assert notes and notes[0]['t'] == 'AuGent ответил: Пуши' and notes[0]['b'] == 'Готово Изменил два файла' and notes[0]['tag'] == 'chat-c9', notes
        assert notes[0]['d']['chat_id'] == 'c9'
        # 8b) настоящий путь: pywebpush (PushSender) -> push-сервис браузера -> sw.js
        live = None
        if real:
            sub = list(DB['subs'].values())[0]
            sender = ps.PushSender()
            t0 = time.time()
            sender.enqueue([{'endpoint': sub['endpoint'], 'p256dh': sub['keys']['p256dh'], 'auth': sub['keys']['auth']}],
                           ps.build_payload('c5', 'Живой push', 'Ответ пришёл через **FCM**'), VAPID)
            await asyncio.to_thread(sender.join, 20)
            got = []
            for _ in range(250):
                await p.wait_for_timeout(100)
                got = await p.evaluate("navigator.serviceWorker.ready.then(r=>r.getNotifications({tag:'chat-c5'})).then(l=>l.map(n=>n.title+' | '+n.body))")
                if got: break
            live = {'sent': sender.sent, 'failed': sender.failed, 'host': sub['endpoint'].split('/')[2],
                    'notes': got, 'sec': round(time.time() - t0, 1)}
            print('live push:', live)
            assert sender.sent == 1 and got == ['AuGent ответил: Живой push | Ответ пришёл через FCM'], live
        # 9) сообщение от sw «открыть чат» -> app.openChat
        await p.evaluate("navigator.serviceWorker.controller ? 0 : 0")
        await p.evaluate("navigator.serviceWorker.dispatchEvent(new MessageEvent('message',{data:{type:'push:open-chat',chat_id:'c9'}}))")
        assert await p.evaluate("window.__catchpmApp.opened") == ['c9']
        # 10) геометрия: строка в панели, без горизонтального скролла
        geo = await p.evaluate("""()=>{const r=document.querySelector('.pn-row').getBoundingClientRect(),
            s=document.querySelector('#pn-switch').getBoundingClientRect(),t=document.querySelector('.pn-txt').getBoundingClientRect(),
            pb=document.querySelector('.profile-body');return {rw:r.width,sr:s.right,rr:r.right,tr:t.right,sl:s.left,
            sw:s.width,sh:s.height,ov:pb.scrollWidth-pb.clientWidth,
            tl:t.left,ol:(()=>{const o=document.querySelector('#maxc-panel-toggle > span');return o?o.getBoundingClientRect().left:null})()}}""")
        assert geo['sr'] <= geo['rr'] - 10 and geo['tr'] <= geo['sl'] and geo['ov'] <= 0 and geo['sw'] == 38, geo
        assert geo['ol'] is None or abs(geo['tl'] - geo['ol']) < 1, ('текст не на одной линии с соседней карточкой', geo)
        await p.mouse.move(1, 1); await p.wait_for_timeout(200)
        await p.locator('#notify-section').screenshot(path=f'{D}/out_on_{w}_{theme}.png')
        await p.screenshot(path=f'{D}/out_panel_{w}_{theme}.png')
        # 11) запрет в браузере -> disabled + подсказка
        p2 = await ctx.new_page()
        await p2.add_init_script("Object.defineProperty(Notification,'permission',{get:()=> 'denied'})")
        await p2.goto(origin + '/', wait_until='domcontentloaded'); await p2.wait_for_timeout(400)
        assert await p2.locator('#pn-switch').is_disabled()
        assert 'запрещены' in await p2.evaluate("document.getElementById('pn-hint').textContent")
        hg = await p2.evaluate("""()=>{const h=document.getElementById('pn-hint'),r=document.createRange();r.selectNodeContents(h);
            const rs=[...r.getClientRects()];return {hl:rs[0].left,lines:new Set(rs.map(x=>Math.round(x.top))).size,
            tl:document.querySelector('.pn-txt').getBoundingClientRect().left,gap:h.getBoundingClientRect().top-document.querySelector('.pn-row').getBoundingClientRect().bottom}}""")
        print('hint geo', hg)
        assert abs(hg['hl'] - hg['tl']) < 1 and hg['gap'] >= 6, hg
        await p2.locator('#notify-section').screenshot(path=f'{D}/out_denied_{w}_{theme}.png')
        await b.close()
    errs = [e for e in errs if 'favicon' not in e]
    assert not errs, errs
    print(f'OK {w} {theme}: real_subscribe={real} subscribe_sec={t_sub} busy_click={busy_click} sub_err={sub_err!r} writes={len(DB["settings_writes"])} geo={geo}')


if __name__ == '__main__':
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
    th = sys.argv[2] if len(sys.argv) > 2 else 'light'
    asyncio.run(main(w, th))
