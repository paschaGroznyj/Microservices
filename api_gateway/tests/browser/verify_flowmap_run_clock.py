import asyncio,json,subprocess,os
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'output/flowmap_run_clock';OUT.mkdir(exist_ok=True)
HTML='''<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="http://local/static/css/flowmap.css"><style>
:root{--surface:#fff;--surface2:#f4f3f0;--text-primary:#333;--text-secondary:#777;--text-muted:#999;--border:#ddd;--accent:#b77847;--accent-muted:#b7784780;--radius-md:12px;--bg-color:#f8f7f4;--surface-color:#fff;--text-color:#333;--secondary-text-color:#777;--muted-text-color:#999;--border-color:#ddd;--accent-color:#b77847;--font-mono:monospace;}body{margin:0;font-family:Arial;background:var(--bg-color);color:var(--text-color)}
html[data-theme=dark]{--surface:#292929;--surface2:#333;--text-primary:#ddd;--text-secondary:#aaa;--text-muted:#888;--border:#555;--bg-color:#222;--surface-color:#292929;--text-color:#ddd;--secondary-text-color:#aaa;--muted-text-color:#888;--border-color:#555}#view-agent{position:relative;height:100vh}.chat-input-area{position:absolute;bottom:20px;left:5%;width:90%;}.chat-input-box{height:55px;background:var(--surface-color);border:1px solid var(--border-color);border-radius:14px;display:flex;align-items:center;justify-content:space-between;padding:8px}.chat-send-btn{border:1px solid #888;border-radius:50%;width:36px;height:36px;color:#777}.chat-send-btn.active.stop-mode{background:#b77847;color:white}.message-input-container{position:static}h3{padding:20px;font-weight:400}</style></head><body><main id="view-agent"><h3>FlowMap · проверка индикатора работы</h3><div class="message-input-container"><div class="chat-input-area"><div class="chat-input-box"><span>Сообщение агенту</span><button id="send-btn" class="chat-send-btn active stop-mode">■</button></div></div></div></main><script src="http://local/static/js/flowmap.js"></script></body></html>'''
# Instrument only observation: capture real canvas text and exact argument of timer formatter.
PROBE='''window.__fmText=[];window.__fmMs=[];const ft=CanvasRenderingContext2D.prototype.fillText;CanvasRenderingContext2D.prototype.fillText=function(t,...args){if(this.canvas.id==='fmMiniCv')window.__fmText.push(String(t));return ft.call(this,t,...args)};'''
async def main():
 results=[]
 async with async_playwright() as pw:
  b=await pw.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  for theme in ['light','dark']:
   for width in [390,1440]:
    for reduced in ['no-preference','reduce']:
     page=await b.new_page(viewport={'width':width,'height':844},reduced_motion=reduced)
     errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
     async def route(r):
      path=ROOT/'api_gateway'/r.request.url.split('http://local/')[-1]
      if path.is_file():
       text=subprocess.check_output(['git','show','HEAD:api_gateway/static/js/flowmap.js'],cwd=ROOT,text=True) if path.name=='flowmap.js' and os.getenv('FLOW_BASELINE') else path.read_text()
       if path.name=='flowmap.js':text=text.replace('const fmtS = ms => {','const fmtS = ms => { window.__fmMs.push(ms);')
       await r.fulfill(body=text,content_type='text/css' if path.suffix=='.css' else 'application/javascript')
      else:await r.fulfill(body=HTML,content_type='text/html')
     await page.route('http://local/**',route);await page.add_init_script(PROBE)
     await page.goto('http://local/');await page.evaluate('(t)=>document.documentElement.dataset.theme=t',theme)
     await page.evaluate("FlowViz.emit('send',{chat:'one'});FlowViz.expand();FlowViz.emit('agent_start');FlowViz.emit('tool_start',{id:'t1',slot:'sandbox',label:'Запускаю код'});")
     await page.wait_for_timeout(650)
     await page.evaluate("document.querySelector('#send-btn').className='chat-send-btn';")
     await page.wait_for_timeout(120)
     snap="({renders:FlowViz.stats.renders,d:FlowViz.state.d,t:window.__fmMs.at(-1),text:window.__fmText.at(-1)})"
     a=await page.evaluate(snap);await page.wait_for_timeout(700);c=await page.evaluate(snap)
     assert a==c,(theme,width,reduced,'not frozen',a,c)
     if width==390 and reduced=='no-preference':
      box=await page.locator('#fmMiniCv').bounding_box()
      assert box and box['y']>0 and box['y']+box['height']<844,box
      assert await page.locator('#fmMini').evaluate("e=>getComputedStyle(e).opacity==='1'")
      await page.screenshot(path=str(OUT/f'{theme}_paused.png'))
     # active for typing is NOT an active request.
     await page.evaluate("document.querySelector('#send-btn').className='chat-send-btn active';")
     await page.wait_for_timeout(140);a=await page.evaluate(snap);await page.wait_for_timeout(550);c=await page.evaluate(snap)
     assert a==c,('typing restarts clock',a,c)
     # Replaced input button must be observed as well.
     await page.evaluate("document.querySelector('#send-btn').outerHTML='<button id=send-btn class=\"chat-send-btn active stop-mode\">■</button>'")
     await page.wait_for_timeout(650);d=await page.evaluate(snap)
     assert d['renders']>c['renders'],('did not resume',c,d)
     assert d['t']-c['t']<950,('pause jumped into timer',c,d)
     if reduced=='no-preference':assert d['t']-c['t']>300,(c,d)
     await page.evaluate("FlowViz.emit('tool_end',{id:'t1',ok:true,ms:900});document.querySelector('#send-btn').className='chat-send-btn';")
     await page.wait_for_timeout(120)
     # Completion delivered AFTER stop indicator is off still settles state.
     await page.evaluate("FlowViz.emit('done',{chat:'one'});")
     await page.wait_for_timeout(700)
     assert await page.evaluate("FlowViz.state.phase==='done' && FlowViz.state.calls.size===0")
     await page.evaluate("document.querySelector('#send-btn').className='chat-send-btn active stop-mode';FlowViz.emit('send',{chat:'two'});FlowViz.emit('agent_start');FlowViz.emit('tool_start',{id:'t2',slot:'sandbox',label:'Код'});")
     await page.wait_for_timeout(250)
     assert await page.evaluate("FlowViz.state.chat==='two' && FlowViz.state.tEnd===0 && window.__fmMs.at(-1)<450")
     assert not errors,errors
     results.append({'theme':theme,'width':width,'reduced':reduced,'paused':True,'resumed':True,'late_done':True,'console_errors':errors})
     await page.close()
  await b.close()
 (OUT/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
 print('PASS',len(results),'cases; frozen animation/timer, active-only, replace/resume, no time jump, late done, console clean')
asyncio.run(main())
