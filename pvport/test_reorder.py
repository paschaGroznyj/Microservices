"""Standalone real-module regression. Run: python pvport/test_reorder.py"""
import asyncio
import functools
import http.server
import json
from pathlib import Path
import threading
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/gc_fixes_reorder'
OUT.mkdir(parents=True, exist_ok=True)

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass

async def run():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for width in (1440, 390):
            for theme in ('light', 'dark'):
                page = await browser.new_page(viewport={'width': width, 'height': 900}, has_touch=True)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.goto(f'http://127.0.0.1:{server.server_port}/pvport/reorder.html')
                await page.evaluate('(theme) => document.documentElement.dataset.theme=theme', theme)
                await page.wait_for_timeout(450)
                checks = []

                def tile(name, composer='group'):
                    return page.locator(f'#{composer} .fa-chip').filter(has=page.locator('.fa-chip__name', has_text=name))

                async def order(expected):
                    actual = await page.evaluate('FileAttach.getFiles().map(f=>f.name)')
                    dom = await page.locator('#' + await page.evaluate('activeComposer') + ' .fa-chip:not(.input-chip)').evaluate_all('(els)=>els.filter(el=>el._faFile).map(el=>el._faFile.name)')
                    assert actual == expected, (actual, expected)
                    assert dom == expected, ('DOM', dom, expected)

                track = page.locator('#group .fa-tiles__track')

                async def show_start():
                    await track.evaluate('''async t => {
                        t.scrollTo({left: 0, behavior: 'instant'});
                        await new Promise(requestAnimationFrame);
                        await new Promise(requestAnimationFrame);
                    }''')
                    assert await track.evaluate('t => t.scrollLeft') == 0

                async def visible_point(locator, side=None):
                    # Do not use drag_to: its per-locator scrollIntoView can snap
                    # the mobile strip after it has calculated the source point.
                    # Pick an actual visible hit-tested point, not a clipped edge.
                    box = await locator.bounding_box()
                    clip = await track.bounding_box()
                    assert box and clip
                    left = max(box['x'], clip['x'], 0)
                    right = min(box['x'] + box['width'], clip['x'] + clip['width'], width)
                    middle = box['x'] + box['width'] / 2
                    if side == 'before':
                        right = min(right, middle - 8)
                    elif side == 'after':
                        left = max(left, middle + 8)
                    top = max(box['y'], clip['y'], 0)
                    bottom = min(box['y'] + box['height'], clip['y'] + clip['height'], 900)
                    assert right - left > 8 and bottom - top > 8, (box, clip, side)
                    point = {'x': (left + right) / 2, 'y': (top + bottom) / 2}
                    assert await locator.evaluate('''(el, p) => {
                        const hit = document.elementFromPoint(p.x, p.y);
                        return hit === el || el.contains(hit);
                    }''', point), (box, clip, point)
                    return point

                async def native_drag(source, target, side):
                    await show_start()
                    start = await visible_point(source)
                    end = await visible_point(target, side)
                    await page.mouse.move(**start)
                    await page.mouse.down()
                    await page.mouse.move(**end, steps=12)
                    await page.mouse.up()

                await order(['report.txt','notes.md','data.csv'])
                # Real native mouse DnD in both directions, without locator auto-scroll.
                await native_drag(tile('report.txt'), tile('notes.md'), 'after')
                await order(['notes.md','report.txt','data.csv'])
                await native_drag(tile('report.txt'), tile('notes.md'), 'before')
                await order(['report.txt','notes.md','data.csv'])
                assert not await page.locator('.fa-dragover').count()
                checks.append('native mouse DnD both directions, no upload overlay')
                # Keyboard on tile and dedicated focusable handle. No preview side effects.
                await tile('report.txt').focus()
                await page.keyboard.press('Alt+ArrowRight')
                await order(['notes.md','report.txt','data.csv'])
                handle = tile('report.txt').locator('.fa-reorder-handle')
                await handle.focus(); await page.keyboard.press('End')
                await order(['notes.md','data.csv','report.txt'])
                await page.keyboard.press('Home')
                await order(['report.txt','notes.md','data.csv'])
                assert await handle.evaluate('(el)=>el===document.activeElement')
                assert await page.locator('.fa-reorder-status').first.inner_text() == 'report.txt: 1 из 3'
                assert not await page.locator('#text-preview-panel.open').count()
                checks.append('keyboard Alt+arrows and handle Home/End; focus + live announcement')
                # Actual touch stream via Chrome DevTools (not synthetic DOM pointer events).
                await show_start()
                start = await visible_point(handle)
                target = await visible_point(tile('notes.md'), 'after')
                cdp = await page.context.new_cdp_session(page)
                x, y = start['x'], start['y']
                await cdp.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
                await cdp.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':target['x'],'y':y}]})
                await cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
                await order(['notes.md','report.txt','data.csv'])
                assert not await page.locator('.fa-reordering').count()
                checks.append('real touch/pointer captured handle reorder')
                # Clicks and remove work; no accidental preview on handle activation.
                await handle.click()
                assert not await page.locator('#text-preview-panel.open').count()
                await tile('report.txt').click(position={'x':65,'y':70})
                await page.wait_for_function('document.querySelector("#text-preview-panel").textContent.includes("Content of report.txt")')
                await page.locator('#text-preview-close').click()
                await tile('report.txt').focus()
                await tile('report.txt').locator('.fa-chip__remove').click()
                # Array is updated before the 200ms exit animation finishes.
                assert await page.evaluate('FileAttach.getFiles().map(f=>f.name)') == ['notes.md','data.csv']
                await page.wait_for_timeout(260)
                await order(['notes.md','data.csv'])
                checks.append('real file preview, handle click isolation, immediate removal')
                await page.evaluate('add(["later.txt", "last.txt"]); add(["later.txt"])')
                await order(['notes.md','data.csv','later.txt','last.txt'])
                # Real external DataTransfer files: must flow to normal upload/drop path.
                await page.locator('#group .fa-tiles__track').evaluate('''track=>{
                    const dt=new DataTransfer(); dt.items.add(new File(['external'],'external.txt',{type:'text/plain'}));
                    for(const type of ['dragenter','dragover','drop']) track.dispatchEvent(new DragEvent(type,{bubbles:true,cancelable:true,dataTransfer:dt}));
                }''')
                await order(['notes.md','data.csv','later.txt','last.txt','external.txt'])
                assert not await page.locator('.fa-dragover').count()
                checks.append('multiple additions + dedup + external file drop')
                # Multipart is built from the reordered send snapshot; concurrent work stays separate.
                await tile('external.txt').locator('.fa-reorder-handle').focus()
                await page.keyboard.press('Home')
                snapshot = ['external.txt','notes.md','data.csv','later.txt','last.txt']
                await order(snapshot)
                await page.evaluate('''()=>{
                    window.sent=[];
                    window.UploadProgress={send:async(url,fd)=>{
                        sent.push(fd.getAll('files').map(f=>f.name));
                        await new Promise(resolve=>window.finishUpload=resolve);
                        return {ok:true};
                    }};
                    window.sendSnapshot=FileAttach.getFiles();
                    window.uploadDone=FileAttach.upload('group','message',{files:sendSnapshot});
                    FileAttach.clear(); add(['new-message.txt']);
                }''')
                await tile('new-message.txt').locator('.fa-reorder-handle').focus()
                await page.evaluate('finishUpload()'); await page.evaluate('uploadDone')
                assert await page.evaluate('sent') == [snapshot]
                await order(['new-message.txt'])
                checks.append('reordered multipart snapshot preserved during async upload + clear/add')
                # Context banking across both actual selectors, and stale removal timeout.
                await page.evaluate('useComposer("agent"); add(["agent-a.txt","agent-b.txt"])')
                await tile('agent-b.txt','agent').locator('.fa-reorder-handle').focus()
                await page.keyboard.press('Home')
                await order(['agent-b.txt','agent-a.txt'])
                await page.evaluate('useComposer("group")')
                await order(['new-message.txt'])
                await tile('new-message.txt').focus(); await tile('new-message.txt').locator('.fa-chip__remove').click()
                await page.evaluate('useComposer("agent")')
                await page.wait_for_timeout(260)
                await order(['agent-b.txt','agent-a.txt'])
                await page.evaluate('useComposer("group")')
                await order([])
                await page.evaluate('add(["report.txt","notes.md","data.csv"])')
                assert not await page.locator('#history [draggable="true"], #history .fa-reorder-handle').count()
                assert await page.locator('#history .fa-chip__name').inner_text() == 'sent-report.txt'
                checks.append('group/agent contexts keep order; removal cannot mutate another context; history untouched')
                # Overflow auto-scroll while holding native drag (deterministic DragEvents).
                await page.evaluate('add(["extra1.txt","extra2.txt","extra3.txt","extra4.txt","extra5.txt","extra6.txt"])')
                track=page.locator('#group .fa-tiles__track')
                await page.wait_for_timeout(400)
                await track.evaluate('(t)=>t.scrollLeft=0')
                await tile('report.txt').dispatch_event('pointerdown', {'pointerType':'mouse','button':0})
                await track.evaluate('''t=>{
                    window.dt=new DataTransfer(); const tile=t.querySelector('.fa-chip');
                    tile.dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:dt}));
                    t.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,clientX:t.getBoundingClientRect().right-2,dataTransfer:dt}));
                }''')
                await page.wait_for_timeout(250)
                assert await track.evaluate('(t)=>t.scrollLeft') > 0
                await page.keyboard.press('Escape')
                assert not await page.locator('.fa-reordering').count()
                checks.append('overflow edge auto-scroll + Escape cleanup')
                await page.evaluate('FileAttach.clear(); add(["report.txt","notes.md","data.csv","later.txt"])')
                await page.wait_for_timeout(400)
                await track.evaluate('(t)=>t.scrollLeft=0')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert not errors, errors
                await page.screenshot(path=str(OUT/f'reorder-{width}-{theme}.png'),full_page=True)
                results.append({'width':width,'theme':theme,'checks':checks,'pageerrors':errors,'status':'PASS'})
                print(f'PASS {width} {theme}: {len(checks)} checks; pageerrors={errors}',flush=True)
                await page.close()
        await browser.close()
    server.shutdown()
    (OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(f'PASS: {len(results)} viewport/theme cases, {sum(len(r["checks"]) for r in results)} checks')

if __name__ == '__main__': asyncio.run(run())
