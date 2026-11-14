import asyncio, threading, time, sys
import test_dup as T
from playwright.async_api import async_playwright
DUMP = """() => [...window.__catchpmApp.getMessagesContainer().querySelectorAll(':scope > .agent-message')].map(e => 'id='+(e.dataset.id||'')+'|mid='+(e.dataset.messageId||'')+'|'+((e.textContent||'').match(/10 222,10/g)||[]).length)"""
async def run(br, delay, other, long):
    p = await (await br.new_context(viewport={'width':1440,'height':800})).new_page()
    await p.add_init_script(T.INIT)
    await p.goto(f'http://127.0.0.1:{T.PORT}/', wait_until='domcontentloaded')
    await p.evaluate(f"() => fetch('/__reset?delay={delay}', {{method:'POST'}})")
    await p.wait_for_function("() => window.__catchpmApp", timeout=15000); await p.wait_for_timeout(800)
    await p.evaluate("() => window.__catchpmApp.openChat('c1')"); await p.wait_for_timeout(600)
    await p.evaluate("() => window.__wsSend('c1', {is_from_bot:true, chunk:'Думаю над курсом...', id:'u-2'})")
    await p.wait_for_timeout(300)
    if other:
        await p.evaluate("() => window.__catchpmApp.openChat('c2')"); await p.wait_for_timeout(600)
    await p.evaluate("() => window.__setHidden(true)")
    txt = T.FINAL_TEXT + (' Подробности. ' * 150 if long else '')
    await p.evaluate("([c]) => window.__wsSend(c, {is_from_bot:true, kind:'final_commit', id:'u-2'})", ['c1'])
    for i in range(0, len(txt), 40):
        await p.evaluate("([c,t]) => window.__wsSend(c, {is_from_bot:true, chunk:t, id:'u-2'})", ['c1', txt[i:i+40]])
    await p.evaluate("() => fetch('/__saved', {method:'POST'})")
    f = dict(T.FINAL, message=txt, chat_id='c1', request_id='r1')
    await p.evaluate("([c,f]) => window.__wsSend(c, f)", ['c1', f])
    await p.wait_for_timeout(400)
    await p.evaluate("() => window.__setHidden(false)")
    await p.evaluate("() => { const a = window.__catchpmApp; if (String(a.currentChat) !== 'c1') a.openChat('c1'); }")
    await p.wait_for_timeout(4000)
    r = await p.evaluate(DUMP); await p.context.close(); return r
async def main():
    threading.Thread(target=T.serve, daemon=True).start(); time.sleep(1.5)
    async with async_playwright() as pw:
        br = await pw.chromium.launch(args=['--no-sandbox'])
        for other in (False, True):
            for long in (False, True):
                for delay in (0, 0.4, 1.5):
                    print('other' if other else 'same ', 'long' if long else 'short', delay, await run(br, delay, other, long), flush=True)
asyncio.run(main())
