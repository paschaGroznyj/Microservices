
import asyncio,json,sys
sys.path.insert(0,'tcstand/wsdmarq')
from playwright.async_api import async_playwright
import importlib.util
spec=importlib.util.spec_from_file_location('t','tcstand/wsdmarq/test_marq.py')
src=open('tcstand/wsdmarq/test_marq.py').read().split('async def run(')[0]
__file__='/tmp/exec/workspaces/d976e0e002fd/Microservices/tcstand/wsdmarq/test_marq.py'
exec(src)
async def main():
  async with async_playwright() as pw:
    b=await pw.chromium.launch(args=['--no-sandbox']);p=await b.new_page(viewport={'width':360,'height':800})
    f='/tmp/m.html';open(f,'w').write(html('dark'));await p.goto('file://'+f)
    await p.evaluate("(d)=>{window.fetch=async()=>({ok:true,json:async()=>d});}",{'items':[IT],'running_request_id':None})
    await p.evaluate("WsDiff.init({transport:'ws'}).restore('425826')");await p.wait_for_timeout(400)
    for ph in (0.0,0.72):
      await p.evaluate("""(ph)=>document.querySelectorAll('.wsd-path.is-run,.wsd-path.is-run .wsd-pin').forEach(e=>{const d=parseFloat(getComputedStyle(e.closest('.wsd-path')).getPropertyValue('--wsd-dur'));e.style.animationPlayState='paused';e.style.animationDelay=(-ph*d)+'s'})""",ph)
      await p.wait_for_timeout(80)
      r=await p.evaluate("""()=>{const e=document.querySelector('.wsd-path.is-run'),cs=getComputedStyle(e),pin=e.querySelector('.wsd-pin');
        return {fl:cs.getPropertyValue('--wsd-fl'),fr:cs.getPropertyValue('--wsd-fr'),shift:cs.getPropertyValue('--wsd-shift'),tx:getComputedStyle(pin).transform,
          pinR:pin.getBoundingClientRect().right-e.getBoundingClientRect().right,pinL:pin.getBoundingClientRect().left-e.getBoundingClientRect().left}}""")
      print(ph,r)
    await b.close()
asyncio.run(main())
