import asyncio, functools, http.server, threading, json, io
from reportlab.pdfgen import canvas
from pathlib import Path
from bs4 import BeautifulSoup
from jinja2 import Environment
from playwright.async_api import async_playwright
D=Path(__file__).resolve().parent; MS=D.parent/'api_gateway'; O=D.parent/'output/gc_fixes'; O.mkdir(parents=True,exist_ok=True)
html=Environment().from_string((MS/'templates/chat.html').read_text()).render(user={'username':'preview-test','display_name':'UI test','user_id':56,'role':'admin'},is_sigma=False)
s=BeautifulSoup(html,'html.parser')
# Real DOM, styles and modules, without app boot requiring authentication/backends.
for e in list(s.find_all('script')): e.decompose()
for e in list(s.find_all(['link','img'])):
 if e.get('href','').startswith('http') or e.get('src','').startswith('http'): e.decompose()
for name in ['vendor/marked.min.js','vendor/purify.min.js','text-preview-view.js','file-tiles.js','groupChat.js','groupInteraction.js']:
 e=s.new_tag('script',src='/static/js/'+name); s.body.append(e)
(D/'gc_fixes.html').write_text(str(s))
class H(http.server.SimpleHTTPRequestHandler):
 def log_message(self,*a): pass
 def translate_path(self,path):
  if path.startswith('/fixture'): return str(D/'gc_fixes.html')
  return super().translate_path(path)
srv=http.server.ThreadingHTTPServer(('127.0.0.1',18130),functools.partial(H,directory=str(MS))); threading.Thread(target=srv.serve_forever,daemon=True).start()
pdfbuf=io.BytesIO(); pdf=canvas.Canvas(pdfbuf); pdf.drawString(72,720,'Group PDF regression'); pdf.save(); pdfbytes=pdfbuf.getvalue()
async def main():
 async with async_playwright() as p:
  b=await p.chromium.launch(); results=[]
  for w in [1440,390]:
   for theme in ['light','dark']:
    page=await b.new_page(viewport={'width':w,'height':1000}); errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
    await page.route('**/api/group/file/*',lambda r:r.fulfill(status=200,content_type='text/plain',body='Preview remains in group'))
    await page.goto('http://127.0.0.1:18130/fixture')
    await page.evaluate(r'''theme=>{
      document.documentElement.dataset.theme=theme;
      document.querySelectorAll('.view').forEach(e=>e.classList.add('hidden'));
      document.querySelector('#view-group').classList.remove('hidden');
      document.querySelectorAll('.nav-item').forEach(e=>e.classList.toggle('active',e.dataset.tab==='group'));
      GroupChat.userId=56; GroupChat.isAdmin=true;
      const img='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><rect width="600" height="400" fill="#508b80"/><circle cx="300" cy="200" r="95" fill="#d9e6b3"/></svg>');
      const files=[{id:77,file_name:'report.txt',mime_type:'text/plain',size_bytes:88000}, {id:78,file_name:'notes.pdf',mime_type:'application/pdf',size_bytes:120000}, {id:79,file_name:'photo1.png',is_image:true,width:600,height:400,_localUrl:img}, {id:80,file_name:'photo2.png',is_image:true,width:600,height:400,_localUrl:img}];
      window.msg={id:901,user_id:56,username:'Павел',message_text:'Файлы и фотографии — отдельными баблами',files,created_at:new Date().toISOString(),reactions:[{emoji:'🔥',count:1,user_ids:[99]}]};
      GroupChat.messages=[msg];
      document.querySelector('#gc-messages').innerHTML=GroupChat._msgHtml(msg);
      GroupInteraction.bindChips(document.querySelector('#gc-messages'));
      GroupChat._bindFileChips(document.querySelector('#gc-messages'));GroupChat._bindCollages(document.querySelector('#gc-messages'));GroupChat._initCollapsibles(document.querySelector('#gc-messages'));GroupChat._observeLazyMedia(document.querySelector('#gc-messages'));
      for(const e of ['❤️','🔥','👍']) GroupInteraction.toggle(901,e);
      clearTimeout(GroupInteraction._timer);
      window.toast=[];window.FileAttach={showToast:s=>toast.push(s)};
      GroupInteraction.toggle(901,'😁');
    }''',theme)
    st=await page.evaluate('''()=>({mine:GroupInteraction.myEmojis(msg),toast, chips:document.querySelectorAll('.gc-react--mine').length, bubbles:document.querySelectorAll('.gc-row > .gc-msg').length, mixed:[...document.querySelectorAll('.gc-msg')].some(b=>b.querySelector('.gc-files')&&b.querySelector('.gc-collage')), queue:GroupInteraction._take()})''')
    assert st['mine']==['🔥','❤️','👍'] and len(st['toast'])==1 and st['chips']==3,st
    assert st['bubbles']==1 and not st['mixed'],st
    assert set(st['queue'][0]['emojis'])=={'🔥','❤️','👍'},st
    await page.locator('.gc-react[data-emoji="🔥"]').click()
    assert set(await page.evaluate('GroupInteraction.myEmojis(msg)'))=={'❤️','👍'}
    await page.evaluate("GroupInteraction.toggle(901,'😁');clearTimeout(GroupInteraction._timer)")
    await page.evaluate("GroupInteraction.onEvent({items:[{message_id:901,reactions:[{emoji:'🔥',count:1,user_ids:[99]}]}]})")
    assert set(await page.evaluate('GroupInteraction.myEmojis(msg)'))=={'❤️','👍','😁'}
    await page.evaluate('''()=>{GroupInteraction.openMenu({clientX:200,clientY:200},{id:901,message:msg,canEdit:true,onClose:()=>{}})}''')
    assert await page.locator('#gc-ctx-menu .is-mine').count()>=1
    await page.evaluate("document.querySelector('#gc-ctx-menu')?.remove()")
    await page.evaluate("document.querySelector('#gc-messages').insertAdjacentHTML('beforeend',GroupChat._msgHtml({...msg,id:902,user_id:99,username:'Коллега',message_text:''}));GroupChat._bindFileChips(document.querySelector('#gc-messages'))")
    geometry=await page.evaluate('''()=>[...document.querySelectorAll('.gc-row')].map(r=>{const bs=[...r.querySelectorAll(':scope > .gc-msg, :scope > .gc-file-block')];return {metas:bs.map(b=>b.querySelectorAll('.gc-meta').length),overlap:bs[0].getBoundingClientRect().bottom>bs[1].getBoundingClientRect().top,overflow:bs.some(b=>b.getBoundingClientRect().right>innerWidth+1)}})''')
    assert all(x['metas']==[1,1] and not x['overlap'] and not x['overflow'] for x in geometry),geometry
    standalone = await page.evaluate('''()=>{
      const box=document.querySelector('#gc-messages'); const out=[];
      for(const uid of [56,99]) for(const text of ['', 'Документы к сообщению']) {
        const m={...msg,id:903,user_id:uid,message_text:text,files:msg.files.slice(0,2)};
        box.insertAdjacentHTML('beforeend',GroupChat._msgHtml(m));
        const row=box.lastElementChild, block=row.querySelector('.gc-file-block');
        GroupChat._bindMsgActions(box);GroupChat._flashBubble(row);
        out.push({cards:block.querySelectorAll('.fa-chip').length, nested:!!block.closest('.gc-msg'),
          context:typeof block.oncontextmenu==='function', flash:!!row.querySelector('.gc-msg--flash'),
          bubbles:row.querySelectorAll('.gc-msg').length, metas:row.querySelectorAll('.gc-meta').length,
          reactions:row.querySelectorAll('.gc-reacts').length,
          bg:getComputedStyle(block).backgroundColor, border:getComputedStyle(block).borderTopWidth,
          padding:getComputedStyle(block).paddingTop,
          overflow:block.getBoundingClientRect().right>innerWidth+1});
        row.remove();
      } return out;
    }''')
    assert all(x['cards']==2 and x['context'] and x['flash'] and not x['nested'] and x['bg']=='rgba(0, 0, 0, 0)' and x['border']=='0px' and x['padding']=='0px' and not x['overflow'] and x['reactions']==1 for x in standalone),standalone
    assert [x['bubbles'] for x in standalone]==[0,1,0,1],standalone
    assert [x['metas'] for x in standalone]==[1,2,1,2],standalone
    await page.evaluate("document.querySelector('.gc-row[data-id=\"902\"]').remove()")
    await page.wait_for_timeout(600)
    assert await page.locator('.gc-bubble-text').first.evaluate('(e)=>getComputedStyle(e).color===getComputedStyle(e.closest(\".gc-msg\")).color')
    await page.screenshot(path=str(O/f'{w}_{theme}.png'))
    print(w,theme,await page.locator('.gc-msg').first.evaluate('(b)=>({bg:getComputedStyle(b).backgroundColor,color:getComputedStyle(b).color})'))
    await page.locator('.gc-row[data-id="901"] .fa-chip').first.click();await page.wait_for_timeout(500)
    assert await page.evaluate("document.querySelector('#text-preview-panel').parentElement.id==='view-group' && document.querySelector('#text-preview-panel').classList.contains('open')")
    await page.evaluate('''()=>{const snapshot=JSON.parse(JSON.stringify(msg.reactions));GroupInteraction.onEvent({items:[{message_id:901,reactions:snapshot}]})}''')
    assert not await page.evaluate('GroupInteraction._override.has(901)')
    # Actual POST transport + failed request retry preserve the full list.
    payload = await page.evaluate("""async()=>{const old=window.fetch;window.fetch=async(url,opts)=>{window.posted=JSON.parse(opts.body);return {status:200}};await GroupInteraction.flush();window.fetch=old;return posted;}""")
    assert set(payload['items'][0]['emojis'])=={'❤️','👍','😁'},payload
    retry=await page.evaluate("""async()=>{const old=window.fetch;window.fetch=async()=>{throw Error('offline')};GroupInteraction._queue.set(901,['❤️','👍','😁']);await GroupInteraction.flush();window.fetch=old;return GroupInteraction._take();}""")
    assert retry[0]['emojis']==['❤️','👍','😁'],retry
    await page.evaluate("document.querySelector('#text-preview-panel').classList.remove('open')")
    # Real menu selection on own/other text bubbles and standalone file blocks.
    for uid, files_only, string_id in [(56,False,False),(56,False,True),(99,False,True),(56,True,True),(99,True,True)]:
      await page.evaluate("""({uid,files_only,string_id})=>{
        GroupChat._closeCtxMenu(); GroupInteraction._queue.clear();GroupInteraction._override.clear();
        window.msg={id:string_id?'911':911,user_id:uid,username:'Тест',
          message_text:files_only?'':'Проверка реакций своего сообщения',
          files:files_only?[{id:77,file_name:'report.txt',mime_type:'text/plain',size_bytes:100}]:[],
          created_at:new Date().toISOString(),reactions:[]};
        GroupChat.messages=[msg];const box=document.querySelector('#gc-messages');
        box.innerHTML=GroupChat._msgHtml(msg);GroupChat._bindMsgActions(box);
        window.fetch=async(url,opts)=>{window.lastReactionPost=JSON.parse(opts.body);return {status:200}};
      }""",dict(uid=uid,files_only=files_only,string_id=string_id))
      target=page.locator('.gc-file-block' if files_only else '.gc-msg')
      if not files_only:
        assert await target.locator('.gc-bubble-text').evaluate('(e)=>getComputedStyle(e).color===getComputedStyle(e.closest(".gc-msg")).color')

      for emoji in ['✅','1️⃣','🤷‍♂️']:
        await target.click(button='right')
        await page.locator('.gc-rbar__more').click()
        assert await page.locator('.gc-rgrid__btn').count()==80
        await page.locator('.gc-rgrid__btn').filter(has_text=emoji).click()
      assert await page.locator('.gc-react--mine').count()==3,(uid,files_only,string_id)
      await target.click(button='right');await page.locator('.gc-rbar__more').click()
      await page.locator('.gc-rgrid__btn').filter(has_text='🫂').click()
      assert await page.locator('.gc-react--mine').count()==3
      await page.evaluate('GroupInteraction.flush()')
      assert await page.evaluate("lastReactionPost.items[0].message_id===911 && lastReactionPost.items[0].emojis.join('|')==='✅|1️⃣|🤷‍♂️'")
      # Simulated server echo, then independent removal via a rendered chip.
      await page.evaluate("GroupInteraction.onEvent({items:[{message_id:911,reactions:msg.reactions}]})")
      assert not await page.evaluate('GroupInteraction._override.has(911)')
      await page.locator('.gc-react[data-emoji="1️⃣"]').click()
      assert await page.locator('.gc-react--mine').count()==2
      await page.evaluate('GroupInteraction.flush()')
      assert await page.evaluate("lastReactionPost.items[0].emojis.join('|')==='✅|🤷‍♂️'")
      if uid==56 and not string_id:
        await page.screenshot(path=str(O/f'reactions_{w}_{theme}.png'))
    assert not errors,errors
    results.append({'width':w,'theme':theme,'state':st,'geometry':geometry,'pageerrors':errors})
    await page.close()
  await b.close();(O/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)); print('PASS 4 viewport/theme cases: 20 real-menu scenarios own/other text/files, numeric/string IDs, 80 emoji, max3, removal/echo/POST; existing standalone-files/preview regressions PASS; pageerrors=[]')
asyncio.run(main())
