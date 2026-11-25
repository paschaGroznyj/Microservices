"""Browser regression for the chat menu, using project CSS and real app methods.
Run: python api_gateway/tests/ui/sidebar_dropdown.py
Requires Playwright + Chromium. Screenshots/report: output/sidebar_dropdown/.
The app startup/network are isolated; action handlers are spies, not API calls.
"""
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'output/sidebar_dropdown'
CSS = ROOT / 'api_gateway/static/css/sidebar-chats.css'


def method(build):
    source = (ROOT / f'api_gateway/static/js/{build}').read_text()
    body = source.split('    _toggleChatDropdown(btn) {', 1)[1]
    return body.split('\n    async _renameChat', 1)[0].rsplit('    }', 1)[0]


def styles(original=False):
    template = (ROOT / 'api_gateway/templates/chat.html').read_text()
    sheets = re.findall(r'href="(/static/[^"?]+\.css)(?:\?[^\"]*)?"', template)
    result = []
    for url in sheets:
        path = ROOT / 'api_gateway' / url.lstrip('/')
        if path.exists():
            text = path.read_text()
            if original and path == CSS:
                # Reproduce the former rule without depending on a rewritten commit.
                text = text.replace('.chat-item-menu-btn:not([aria-expanded="true"])', '.chat-item-menu-btn')
            result.append(text)
    return '\n'.join(result)


def fixture(theme):
    rows = []
    for i in range(18):
        rows.append(f'''<div class="chat-item chat-item--c {'active' if i == 1 else ''}" data-chat-id="{i}">
        <div class="chat-av" data-tone="{i % 8}"><span class="chat-av__txt">РД</span></div>
        <div class="chat-line-wrap"><div class="chat-line chat-line--title">{'Работа с дашбордом' if i == 1 else f'Обсуждение проекта {i}'}</div>
        <div class="chat-line chat-line--preview">Сообщение и материалы проекта</div></div>
        <span class="chat-time">14:45</span><button class="chat-item-menu-btn" data-menu-chat-id="{i}" type="button" title="Действия"><svg width="18" height="18" viewBox="0 0 24 24"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg></button></div>''')
    return f'''<!doctype html><html data-theme="{theme}"><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><aside class="sidebar open" style="height:100vh;display:flex;flex-direction:column"><div style="padding:20px;font-size:18px">CatchPM</div><div style="padding:12px">Чаты</div><div id="chats-list" style="overflow:auto;flex:1">{''.join(rows)}</div><div style="padding:18px">Павел Солдаткин</div></aside></body></html>'''


async def setup(browser, build, theme, mobile, original=False):
    page = await browser.new_page(viewport={'width': 390 if mobile else 1440, 'height': 844 if mobile else 900}, is_mobile=mobile, has_touch=mobile, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
    await page.set_content(fixture(theme))
    await page.add_style_tag(content=styles(original))
    await page.add_script_tag(path=str(ROOT / 'api_gateway/static/js/anchored-popup.js'))
    await page.evaluate('''body => {
      window.actions = []; window.toggle = new Function('btn', body);
      window.ctx = {chats: Array.from({length:18},(_,id)=>({id})),
        _renameChat: id=>actions.push(['rename',id]),
        _favoriteChat: (id,value)=>actions.push(['favorite',id,value]),
        _deleteChat: id=>actions.push(['delete',id])};
      document.getElementById('chats-list').addEventListener('click', e=>{
        const btn=e.target.closest('.chat-item-menu-btn');
        if(btn){e.stopPropagation();toggle.call(ctx,btn);}
      });
      document.addEventListener('click', e=>{
        if(!e.target.closest('.chat-item-dropdown')&&!e.target.closest('.chat-item-menu-btn'))
          document.querySelectorAll('.chat-item-dropdown').forEach(el=>el.remove());
      });
    }''', method(build))
    return page, errors


async def open_menu(page, row=1, mobile=False):
    btn = page.locator(f'[data-menu-chat-id="{row}"]')
    assert await btn.evaluate('(el)=>el.closest(".chat-item").dataset.chatId') == str(row)
    if not mobile:
        await page.mouse.move(700, 500)
        await page.locator(f'.chat-item[data-chat-id="{row}"]').hover(position={"x": 12, "y": 12})
    await btn.click(timeout=3000)
    await page.wait_for_timeout(200)
    menu = page.locator('.chat-item-dropdown')
    assert await menu.count() == 1, 'menu did not open'
    return btn, menu


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'baseline_reproduced': False, 'cases': []}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page, errors = await setup(browser, 'app.js', 'dark', False, True)
        btn, menu = await open_menu(page)
        box = await menu.bounding_box()
        await page.mouse.move(box['x']+30, box['y']+20)
        await page.wait_for_timeout(200)
        assert await menu.count() == 0, 'baseline regression not reproduced'
        assert not errors, errors
        report['baseline_reproduced'] = True
        await page.close()
        for build in ['app.js', 'app_sigma.js']:
            for theme in ['light', 'dark']:
                for mobile in [False, True]:
                    name = f"{build[:-3]}_{theme}_{'mobile' if mobile else 'desktop'}"
                    print("CASE",name,flush=True)
                    page, errors = await setup(browser, build, theme, mobile)
                    btn, menu = await open_menu(page, mobile=mobile)
                    box = await menu.bounding_box()
                    if not mobile:
                        await page.mouse.move(box['x']+30, box['y']+20)
                    await page.wait_for_timeout(300)
                    assert await menu.count() == 1, 'selected menu closed on pointer transition'
                    assert await btn.get_attribute('aria-expanded') == 'true'
                    assert await btn.is_visible()
                    if not mobile:
                        assert await page.locator('.active .chat-time').evaluate('(el)=>getComputedStyle(el).visibility') == 'hidden'
                    geometry = await page.evaluate('''()=>{const a=document.querySelector('.active .chat-item-menu-btn').getBoundingClientRect(),m=document.querySelector('.chat-item-dropdown').getBoundingClientRect();return {rightDelta:m.right-a.right,gap:m.top-a.bottom,left:m.left,top:m.top,right:m.right,bottom:m.bottom,w:innerWidth,h:innerHeight}}''')
                    assert abs(geometry['rightDelta']) < 1, geometry
                    assert abs(geometry['gap']-4) < 1, geometry
                    assert geometry['left'] >= 0 and geometry['right'] <= geometry['w'] and geometry['bottom'] <= geometry['h'], geometry
                    await page.screenshot(path=str(OUT / f'{name}.png'), full_page=True)
                    await menu.locator('button').nth(0).click()
                    await page.wait_for_timeout(60)
                    assert await menu.count() == 0
                    assert await btn.get_attribute('aria-expanded') == 'false'
                    assert await page.evaluate('actions[0]') == ['rename','1']
                    # Other actions remain clickable after leaving the selected row.
                    for index, action in [(1,'favorite'), (2,'delete')]:
                        btn, menu = await open_menu(page, mobile=mobile)
                        if not mobile:
                            await menu.locator('button').nth(index).hover()
                            await page.wait_for_timeout(120)
                        await menu.locator('button').nth(index).click()
                        assert (await page.evaluate('actions.at(-1)'))[:2] == [action,'1']
                    btn, menu = await open_menu(page, mobile=mobile)
                    await btn.click()
                    await page.wait_for_timeout(60)
                    assert await menu.count() == 0, 'second click did not close'
                    btn, menu = await open_menu(page, mobile=mobile)
                    await page.locator('.sidebar > div').first.click()
                    await page.wait_for_timeout(60)
                    assert await menu.count() == 0, 'outside click did not close'
                    # A regular (unselected) row uses the same helper.
                    btn, menu = await open_menu(page, row=2, mobile=mobile)
                    if not mobile:
                        await menu.hover()
                        await page.wait_for_timeout(120)
                    assert await menu.count() == 1
                    before = await menu.bounding_box()
                    await page.locator('#chats-list').evaluate('(el)=>el.scrollTop+=20')
                    await page.wait_for_timeout(120)
                    after = await menu.bounding_box()
                    assert after and abs(before['y']-after['y']-20)<1, (before,after)
                    await page.locator('#chats-list').evaluate('(el)=>el.scrollTop=1000')
                    await page.wait_for_timeout(120)
                    assert await menu.count() == 0, 'clipped anchor left an orphan menu'
                    assert not errors, errors
                    report['cases'].append({'name':name,'passed':True,'geometry':geometry,'actions':await page.evaluate('actions'),'console_errors':errors})
                    await page.close()
        await browser.close()
    (OUT/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'baseline_reproduced':report['baseline_reproduced'],'passed':len(report['cases']),'report':str(OUT/'report.json')}))


if __name__ == '__main__':
    asyncio.run(main())
