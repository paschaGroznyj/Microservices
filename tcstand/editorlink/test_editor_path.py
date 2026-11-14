"""Стенд: кнопки карточки ws-diff открывают /editor с нужной областью и файлом.
Реальные editor.html, editor.js, ws-diff.js; API /api/editor/* замоканы через page.route.
"""
import asyncio, json, os, sys, mimetypes
from urllib.parse import urlparse, parse_qs
import jinja2
from playwright.async_api import async_playwright

ROOT = sys.argv[1] if len(sys.argv) > 1 else "."
GW = os.path.join(ROOT, "api_gateway")
WS_A, WS_B = "6573fa4250df", "d976e0e002fd"
PATH = "Microservices/tcstand/diffcard/scroll.py"
TREE = [{"name": "Microservices", "path": "Microservices", "type": "dir", "children": [
          {"name": "tcstand", "path": "Microservices/tcstand", "type": "dir", "children": [
            {"name": "diffcard", "path": "Microservices/tcstand/diffcard", "type": "dir", "children": [
              {"name": "scroll.py", "path": PATH, "type": "file", "size": 10},
              {"name": "shot.py", "path": "Microservices/tcstand/diffcard/shot.py", "type": "file", "size": 10}]}]}]},
        {"name": "wsd_live", "path": "wsd_live", "type": "dir", "children": [
          {"name": "hello.py", "path": "wsd_live/hello.py", "type": "file", "size": 10}]}]

def editor_html(qs):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(GW, "templates")))
    q = parse_qs(qs)
    return env.get_template("editor.html").render(
        request=None, chat_id="", workspace_id=q.get("workspace_id", [""])[0],
        readonly=False, chat_transport="ws")

async def route(r):
    u = urlparse(r.request.url); p = u.path; q = parse_qs(u.query)
    if p == "/editor":
        return await r.fulfill(body=editor_html(u.query), content_type="text/html")
    if p == "/api/editor/workspaces":
        return await r.fulfill(json={"workspaces": [{"workspace_id": WS_A, "title": "other"},
                                                    {"workspace_id": WS_B, "title": "Microservices"}]})
    if p == "/api/editor/tree":
        return await r.fulfill(json={"tree": TREE if q["workspace_id"][0] == WS_B else []})
    if p == "/api/editor/file":
        return await r.fulfill(json={"content": f"# {q['workspace_id'][0]}:{q['path'][0]}\n", "is_binary": False})
    if p.startswith("/api/"):
        return await r.fulfill(json={})
    if p.startswith("/static/"):
        fp = os.path.join(GW, p.lstrip("/"))
        if os.path.isfile(fp):
            return await r.fulfill(path=fp, content_type=mimetypes.guess_type(fp)[0] or "application/octet-stream")
    if p == "/card":
        return await r.fulfill(content_type="text/html", body=CARD)
    return await r.fulfill(status=404, body="")

CARD = """<!doctype html><html><head><link rel=stylesheet href=/static/css/ws-diff.css></head><body>
<div class="chat-main-inner"><div class="chat-messages"><div class="agent-message" data-message-id="m1">
<div class="reasoning-block">r</div><div class="message-actions"></div></div></div></div>
<script src="/static/js/ws-diff.js"></script></body></html>"""

async def main():
    errs, ok = [], True
    async with async_playwright() as pw:
        b = await pw.chromium.launch(args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1440, "height": 900})
        await ctx.route("http://stand.local/**", route)
        # 1. карточка: какие URL открывают кнопки
        pg = await ctx.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto("http://stand.local/card")
        await pg.evaluate("""() => { window._opened=[]; window.open=(u)=>{window._opened.push(u);return null;};
          WsDiff.apply({request_id:'r1', message_id:'m1', workspace_id:'%s', files:[
            {path:'%s', status:'M', add:1, del:0, patch:'@@ -1,1 +1,2 @@\\n a\\n+b\\n'}]}, {done:true}); }""" % (WS_B, PATH))
        await pg.click(".wsd-btn[data-act=editor]")
        await pg.click(".wsd-file .wsd-open")
        opened = await pg.evaluate("window._opened")
        print("opened:", opened)
        want = [f"/editor?workspace_id={WS_B}", f"/editor?workspace_id={WS_B}&path=Microservices%2Ftcstand%2Fdiffcard%2Fscroll.py"]
        ok &= opened == want
        # 2. редактор по ссылке файла
        ed = await ctx.new_page(); ed.on("pageerror", lambda e: errs.append(str(e)))
        await ed.goto("http://stand.local" + want[1])
        await ed.wait_for_function("document.querySelector('.ed-node.file.sel')", timeout=20000)
        await ed.wait_for_timeout(300)
        st = await ed.evaluate("""() => ({ws: document.getElementById('ed-ws-select').value,
          sel: document.querySelector('.ed-node.file.sel')?.dataset.path,
          name: document.getElementById('ed-filename').textContent,
          url: location.search,
          code: (window.monaco && monaco.editor.getModels().map(m=>m.getValue()).pop()) || ''})""")
        print("editor:", json.dumps(st, ensure_ascii=False))
        ok &= st["ws"] == WS_B and st["sel"] == PATH and f"{WS_B}:{PATH}" in st["code"]
        await ed.screenshot(path="output/editor_path.png")
        # 3. клик по другому файлу -> path в адресе меняется
        await ed.click(".ed-node.file[data-path='Microservices/tcstand/diffcard/shot.py']")
        await ed.wait_for_timeout(400)
        u2 = await ed.evaluate("location.search"); print("after click:", u2)
        ok &= "shot.py" in u2
        # 4. смена области -> path из адреса убран
        await ed.select_option("#ed-ws-select", WS_A); await ed.wait_for_timeout(400)
        u3 = await ed.evaluate("location.search"); print("after ws switch:", u3)
        ok &= "path=" not in u3 and WS_A in u3
        # 5. чужая/неизвестная область -> первая область, path игнорируется
        e2 = await ctx.new_page(); e2.on("pageerror", lambda e: errs.append(str(e)))
        await e2.goto(f"http://stand.local/editor?workspace_id=nope&path={PATH}")
        await e2.wait_for_timeout(2500)
        u4 = await e2.evaluate("[document.getElementById('ed-ws-select').value, location.search]"); print("foreign ws:", u4)
        ok &= u4[0] == WS_A and "path=" not in u4[1]
        await b.close()
    print("page errors:", errs)
    print("RESULT:", "OK" if ok and not errs else "FAIL")

os.makedirs("output", exist_ok=True)
asyncio.run(main())
