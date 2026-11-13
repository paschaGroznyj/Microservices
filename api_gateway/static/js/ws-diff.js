/* ws-diff.js — карточка «Изменённые файлы» под ответом агента (diff теневых git-снимков workspace).
 * Один файл для main gateway (WebSocket) и sigma (SSE): отличается только transport в WsDiff.init. */
(function(){
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const I={
 diff:'<svg class="wsd-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M12 9.5v5M9.5 12h5"/><path d="M9.5 17.5h5"/></svg>',
 caret:'<svg class="wsd-caret" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>',
 ext:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8"/></svg>',
 copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
 fold:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4l4 4 4-4M8 20l4-4 4 4M4 12h16"/></svg>',
 exp:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 9l4-4 4 4M8 15l4 4 4-4"/></svg>',
 wrap:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16M4 12h13a3 3 0 0 1 0 6h-4M4 18h5"/><path d="M15 16l-2 2 2 2"/></svg>',
 lock:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
 bin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 15l5-5 4 4 3-3 6 6"/></svg>'
};
const plural=(n,a,b,c)=>{const m=n%10,h=n%100;return n+' '+(m===1&&h!==11?a:(m>=2&&m<=4&&(h<12||h>14)?b:c));};
const fmtN=n=>String(n).replace(/\B(?=(\d{3})+(?!\d))/g,'\u202f');
const kb=b=>b==null?'—':(b/1024).toFixed(b<10240?1:0).replace('.',',')+' КБ';
function bar(a,d){const t=a+d;let na=t?Math.round(5*a/t):0,nd=t?Math.min(5-na,Math.round(5*d/t)):0;if(t&&na+nd===0)(a>=d?na=1:nd=1);
 let h='';for(let i=0;i<5;i++)h+='<i class="'+(i<na?'a':i<na+nd?'d':'')+'"></i>';return '<span class="wsd-bar" aria-hidden="true">'+h+'</span>';}
const stat=(a,d)=>'<span class="wsd-stat"><span class="'+(a?'a':'z')+'">+'+fmtN(a)+'</span><span class="'+(d?'d':'z')+'">\u2212'+fmtN(d)+'</span></span>';
const lang=p=>/\.py$/.test(p)?'python':null;
function hl(code,lg){if(lg&&window.hljs){try{return hljs.highlight(code,{language:lg,ignoreIllegals:true}).value}catch(e){}}return esc(code);}
function mark(el,s,e,cls){if(s>=e)return;const w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT),ns=[];while(w.nextNode())ns.push(w.currentNode);
 let pos=0;for(const n of ns){const len=n.data.length,a=Math.max(s,pos),b=Math.min(e,pos+len);
  if(a<b){const r=document.createRange();r.setStart(n,a-pos);r.setEnd(n,b-pos);const m=document.createElement('mark');m.className=cls;r.surroundContents(m);}pos+=len;}}
function wdiff(a,b){const m=Math.min(a.length,b.length);let p=0;while(p<m&&a[p]===b[p])p++;let s=0;while(s<m-p&&a[a.length-1-s]===b[b.length-1-s])s++;
 const ca=a.length-p-s,cb=b.length-p-s;if(p+s<4||ca>a.length*.65||cb>b.length*.65)return null;const lw=x=>x.length-x.trimStart().length;return [[Math.max(p,lw(a)),a.length-s],[Math.max(p,lw(b)),b.length-s]];}
function cell(code,lg,rng,cls){const td=document.createElement('td');td.className='cd';td.innerHTML=hl(code,lg)||' ';if(rng)mark(td,rng[0],rng[1],cls);return td;}
/* разбивка ханка на ряды: контекст / блок (dels, adds) */
function blocks(lines){const out=[];let i=0;while(i<lines.length){if(lines[i][0]===' '){out.push({c:lines[i][1]});i++;continue;}
 const d=[],a=[];while(i<lines.length&&lines[i][0]==='-')d.push(lines[i++][1]);while(i<lines.length&&lines[i][0]==='+')a.push(lines[i++][1]);out.push({d,a});}return out;}
function hunkRow(h,prevEnd,cols){const gap=h.o-prevEnd;const tr=document.createElement('tr');tr.className='h';
 tr.innerHTML='<td class="ln o"'+(cols>4?'':' colspan="1"')+'>'+(gap>0?'<button class="hx" title="Показать '+plural(gap,'строку','строки','строк')+'">'+I.exp+'</button>':'')+'</td>'
  +(cols===4?'<td class="ln n"></td><td class="cd" colspan="2">':'<td class="cd" colspan="'+(cols-1)+'">')
  +esc(h.head)+(h.ctx?' <span>'+esc(h.ctx)+'</span>':'')+(gap>0?'<span class="gap">\u22ef '+plural(gap,'строка скрыта','строки скрыто','строк скрыто')+'</span>':'')+'</td>';return tr;}
function unified(f){const t=document.createElement('table');t.className='wsd-t';const tb=document.createElement('tbody');t.appendChild(tb);const lg=lang(f.path);let prev=1;
 for(const h of f.hunks){tb.appendChild(hunkRow(h,prev,4));let o=h.o,n=h.n;
  for(const b of blocks(h.lines)){
   if('c' in b){const tr=document.createElement('tr');tr.innerHTML='<td class="ln o">'+o+'</td><td class="ln n">'+n+'</td><td class="sg"> </td>';tr.appendChild(cell(b.c,lg));tb.appendChild(tr);o++;n++;continue;}
   const pr=b.d.map((x,i)=>b.d.length===b.a.length?wdiff(x,b.a[i]):null);
   b.d.forEach((x,i)=>{const tr=document.createElement('tr');tr.className='d';tr.innerHTML='<td class="ln o">'+o+'</td><td class="ln n"><span class="mo">'+(o++)+'</span></td><td class="sg">\u2212</td>';tr.appendChild(cell(x,lg,pr[i]&&pr[i][0],'wd'));tb.appendChild(tr);});
   b.a.forEach((x,i)=>{const tr=document.createElement('tr');tr.className='a';tr.innerHTML='<td class="ln o"></td><td class="ln n">'+(n++)+'</td><td class="sg">+</td>';tr.appendChild(cell(x,lg,pr[i]&&pr[i][1],'wa'));tb.appendChild(tr);});
  }prev=o;}
 return t;}
function split(f){const t=document.createElement('table');t.className='wsd-t split';
 t.innerHTML='<colgroup><col style="width:42px"><col style="width:22px"><col><col style="width:42px"><col style="width:22px"><col></colgroup>';
 const tb=document.createElement('tbody');t.appendChild(tb);const lg=lang(f.path);let prev=1;
 for(const h of f.hunks){tb.appendChild(hunkRow(h,prev,6));let o=h.o,n=h.n;
  for(const b of blocks(h.lines)){const tr=()=>{const r=document.createElement('tr');tb.appendChild(r);return r;};
   if('c' in b){const r=tr();r.innerHTML='<td class="ln">'+o+'</td><td class="sg"></td>';const c1=cell(b.c,lg);c1.classList.add('l');r.appendChild(c1);
    r.insertAdjacentHTML('beforeend','<td class="ln">'+n+'</td><td class="sg"></td>');r.appendChild(cell(b.c,lg));o++;n++;continue;}
   const k=Math.max(b.d.length,b.a.length);
   for(let i=0;i<k;i++){const r=tr(),x=b.d[i],y=b.a[i],w=(x!=null&&y!=null&&b.d.length===b.a.length)?wdiff(x,y):null;
    if(x!=null){r.insertAdjacentHTML('beforeend','<td class="ln dl">'+(o++)+'</td><td class="sg dl">\u2212</td>');const c=cell(x,lg,w&&w[0],'wd');c.classList.add('dl','l');r.appendChild(c);}
    else r.insertAdjacentHTML('beforeend','<td class="ln em"></td><td class="sg em"></td><td class="cd em l"></td>');
    if(y!=null){r.insertAdjacentHTML('beforeend','<td class="ln ad">'+(n++)+'</td><td class="sg ad">+</td>');const c=cell(y,lg,w&&w[1],'wa');c.classList.add('ad');r.appendChild(c);}
    else r.insertAdjacentHTML('beforeend','<td class="ln em"></td><td class="sg em"></td><td class="cd em"></td>');}
  }prev=o;}
 return t;}
function note(f){if(f.omitted)return '<div class="wsd-note">'+I.diff+'<span>Patch не передан: превышен лимит размера diff</span></div>';if(f.secret)return '<div class="wsd-note">'+I.lock+'<span>Содержимое скрыто: файл попал под маску секретов (.env, *.pem, *token*)</span></div>';
 if(f.binary)return '<div class="wsd-note">'+I.bin+'<span>Бинарный файл · '+(f.size[0]==null?'новый, '+kb(f.size[1]):kb(f.size[0])+' \u2192 '+kb(f.size[1]))+'</span></div>';if(!f.hunks.length&&f.status==='D')return '<div class="wsd-note"><span class="d">\u2212</span><span>Файл удалён целиком · '+plural(f.del,'строка','строки','строк')+'</span></div>';return '';}
function patchText(L){let s='';for(const f of L){if(f.secret||f.binary)continue;s+='diff --git a/'+f.path+' b/'+f.path+'\n--- '+(f.status==='A'?'/dev/null':'a/'+f.path)+'\n+++ '+(f.status==='D'?'/dev/null':'b/'+f.path)+'\n';
 for(const h of f.hunks){s+=h.head+(h.ctx?' '+h.ctx:'')+'\n';for(const l of h.lines)s+=l[0]+l[1]+'\n';}}return s;}
function toast(t){let el=document.querySelector('.wsd-toast');if(!el){el=document.createElement('div');el.className='wsd-toast';document.body.appendChild(el);}
 el.textContent=t;el.classList.add('on');clearTimeout(el._t);el._t=setTimeout(()=>el.classList.remove('on'),1600);}
function render(root,files,opt){opt=opt||{};let mode='unified';
 const A=files.reduce((s,f)=>s+f.add,0),Dd=files.reduce((s,f)=>s+f.del,0);
 root.className='wsd'+(opt.collapsed?' is-collapsed':'');root.dataset.cds='TurnChanges';root.dataset.state=opt.live?'live':'done';
 const head='<div class="wsd-head" role="button" tabindex="0" aria-expanded="'+!opt.collapsed+'">'+I.diff+'<span class="wsd-title">Изменённые файлы</span>'
  +'<span class="wsd-count">'+plural(files.length,'файл','файла','файлов')+(opt.live?'<span class="wsd-hide-m"> · в работе</span>':'')+'</span>'+(opt.live?'<span class="wsd-live" title="'+esc(opt.live)+'"></span>':'')
  +'<span class="wsd-sp"></span>'+stat(A,Dd)+bar(A,Dd)+I.caret+'</div>';
 const m=opt.meta||{},tools='<div class="wsd-tools"><span class="wsd-seg" role="tablist"><button class="on" data-m="unified">Unified</button><button data-m="split">Split</button></span>'
  +'<button class="wsd-btn wsd-tg" data-act="wrap" title="Перенос строк">'+I.wrap+'<span class="wsd-hide-m">Перенос</span></button><span class="wsd-sp"></span>'
  +'<button class="wsd-btn" data-act="fold" title="Свернуть все">'+I.fold+'<span class="wsd-hide-m">Свернуть</span></button>'
  +'<button class="wsd-btn" data-act="patch" title="Скопировать patch">'+I.copy+'<span>Patch</span></button>'
  +'<button class="wsd-btn wsd-hide-m" data-act="editor">'+I.ext+'<span>В редакторе</span></button></div>';
 root.innerHTML=head+'<div class="wsd-body">'+tools+'<div class="wsd-files"></div><div class="wsd-foot">workspace <b>'+esc(m.ws||'')+'</b> · request <b>'+esc(m.rid||'')+'</b> · baseline '+(m.base||'—')+(opt.live?'':' \u2192 '+(m.end||'—'))+'</div></div>';
 const body=root.querySelector('.wsd-body'),list=root.querySelector('.wsd-files');
 files.forEach((f,i)=>{const p=f.path.lastIndexOf('/'),dir=p>=0?f.path.slice(0,p+1):'',name=f.path.slice(p+1);
  const el=document.createElement('div');el.className='wsd-file is-'+f.status+((opt.open?opt.open.has(f.path):(f.open!==false&&!f.collapsed&&(i<2||f.secret||f.binary)))?' is-open':'');el.dataset.path=f.path;if(f.ws)el.dataset.ws=f.ws;if(f.rel)el.dataset.rel=f.rel;
  el.innerHTML='<div class="wsd-fhead">'+I.caret+'<span class="wsd-badge '+f.status+'" title="'+({M:'изменён',A:'добавлен',D:'удалён'})[f.status]+'">'+f.status+'</span>'
   +'<span class="wsd-path" title="'+esc(f.path)+'"><span class="wsd-dir"><span>'+esc(dir)+'</span></span><span class="wsd-name">'+esc(name)+'</span></span>'
   +(f.secret?'<span class="wsd-tag">скрыт</span>':f.binary?'<span class="wsd-tag">бинарный</span>':'')
   +(f.binary?'':stat(f.add,f.del)+'<span class="wsd-hide-m">'+bar(f.add,f.del)+'</span>')
   +(f.status!=='D'?'<button class="wsd-open" title="Открыть в редакторе">'+I.ext+'</button>':'<span class="wsd-open"></span>')+'</div><div class="wsd-code"></div>';
  list.appendChild(el);});
 let paint=()=>body.querySelectorAll('.wsd-file').forEach((el,i)=>{const f=files[i],c=el.querySelector('.wsd-code');
   if(!el.classList.contains('is-open')){c.innerHTML='';c._m=null;return;}if(c._m===mode)return;c._m=mode;
   const n=note(f);if(n){c.innerHTML=n;return;}c.innerHTML='';c.appendChild(mode==='split'?split(f):unified(f));clip(el,f);});
 list.querySelectorAll('.wsd-code').forEach(c=>{const e=document.createElement('div');e.className='wsd-edge';const fb=document.createElement('div');fb.className='wsd-fade';c.parentElement.append(e,fb);c.addEventListener('scroll',()=>edge(c),{passive:true});});
 
 const edges=()=>requestAnimationFrame(()=>list.querySelectorAll('.wsd-file.is-open .wsd-code').forEach(edge));
 root.querySelector('.wsd-head').addEventListener('click',()=>{root.classList.toggle('is-collapsed');root.querySelector('.wsd-head').setAttribute('aria-expanded',!root.classList.contains('is-collapsed'));paint();});
 body.addEventListener('click',e=>{const ob=e.target.closest('.wsd-open');if(ob){e.stopPropagation();if(ob.tagName==='BUTTON'){const fe=ob.closest('.wsd-file');openEditor(fe.dataset.ws||m.ws,fe.dataset.rel||fe.dataset.path);}return;}
  const fh=e.target.closest('.wsd-fhead');if(fh){fh.parentElement.classList.toggle('is-open');paint();return;}
  const sb=e.target.closest('.wsd-seg button');if(sb){mode=sb.dataset.m;root.querySelectorAll('.wsd-seg button').forEach(b=>b.classList.toggle('on',b===sb));paint();return;}
  const ab=e.target.closest('[data-act]');if(!ab)return;
  if(ab.dataset.act==='wrap'){root.classList.toggle('is-wrap');ab.classList.toggle('on',root.classList.contains('is-wrap'));list.querySelectorAll('.wsd-file').forEach(x=>{const c=x.querySelector('.wsd-code');if(c._m)clip(x,files[[...list.children].indexOf(x)]);});edges();}
  if(ab.dataset.act==='fold'){const any=[...body.querySelectorAll('.wsd-file')].some(x=>x.classList.contains('is-open'));body.querySelectorAll('.wsd-file').forEach(x=>x.classList.toggle('is-open',!any));paint();}
  if(ab.dataset.act==='patch'){const t=patchText(files);(navigator.clipboard?.writeText(t)||Promise.reject()).then(()=>toast('patch скопирован · '+t.split('\n').length+' строк'),()=>toast('patch · '+t.split('\n').length+' строк'));}
  if(ab.dataset.act==='editor')openEditor(m.ws);});
 const mq=matchMedia('(max-width:640px)');const fit=()=>{if(mq.matches&&mode==='split'){mode='unified';root.querySelectorAll('.wsd-seg button').forEach(b=>b.classList.toggle('on',b.dataset.m==='unified'));paint();}};
 mq.addEventListener('change',fit);root._setMode=m2=>{mode=m2;root.querySelectorAll('.wsd-seg button').forEach(b=>b.classList.toggle('on',b.dataset.m===m2));paint();};
 const _p=paint;paint=()=>{_p();edges();};if(mq.matches){root.classList.add('is-wrap');root.querySelector('[data-act=wrap]').classList.add('on');}paint();return root;}
function clip(el,f){const c=el.querySelector('.wsd-code');el.querySelector('.wsd-more')?.remove();c.classList.toggle('is-full',!!el._full);
 const lim=parseFloat(getComputedStyle(c).getPropertyValue('--wsd-maxh'))||420;if(c.scrollHeight<=lim+2){edge(c);return;}
 const n=f.hunks.reduce((s,h)=>s+h.lines.length,0),b=document.createElement('button');b.className='wsd-more';
 b.innerHTML=el._full?I.fold+'<span>Свернуть до '+lim+' px</span>':I.exp+'<span>Развернуть · '+plural(n,'строка','строки','строк')+'</span>';
 b.onclick=e=>{e.stopPropagation();el._full=!el._full;if(!el._full)c.scrollTop=0;clip(el,f);
  if(!el._full){const r=el.getBoundingClientRect();if(r.top<0)el.scrollIntoView({block:'start'});}};c.after(b);edge(c);}
function edge(c){const el=c.parentElement,f=el.querySelector('.wsd-edge'),fb=el.querySelector('.wsd-fade');if(!f)return;
 f.classList.toggle('on',c.scrollLeft+c.clientWidth<c.scrollWidth-2);
 el.classList.toggle('is-scrolled',c.scrollTop>1);
 f.style.top=c.offsetTop+'px';f.style.height=c.clientHeight+'px';
 if(fb){fb.style.top=(c.offsetTop+c.clientHeight-40)+'px';fb.classList.toggle('on',c.scrollTop+c.clientHeight<c.scrollHeight-2);}}

/* ── Встройка в чат: один файл для main (WebSocket) и sigma (SSE) ───────────── */
const ROOT='.chat-main-inner:not(.gc-inner) .chat-messages';
const AG='.agent-message';
const cards=new Map(); // request_id -> {items:Map(ws->diff), message_id, live, el}
let transport='ws';
const sel=s=>(window.CSS&&CSS.escape?CSS.escape(s):String(s).replace(/"/g,'\\"'));
const hhmm=t=>{if(!t)return '';const d=new Date(t*1000);return d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});};
function openEditor(ws,path){if(!ws)return;window.open('/editor?ws='+encodeURIComponent(ws)+(path?'&path='+encodeURIComponent(path):''),'_blank','noopener');}
function parsePatch(p){const hs=[];let h=null;
 for(const ln of String(p||'').split('\n')){
  if(ln.startsWith('@@')){const m=/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/.exec(ln);if(!m)continue;
   h={head:ln.slice(0,ln.indexOf('@@',2)+2),ctx:m[3]||'',o:+m[1],n:+m[2],lines:[]};hs.push(h);continue;}
  if(!h||ln.startsWith('\\'))continue;const c=ln[0];if(c===' '||c==='+'||c==='-')h.lines.push([c,ln.slice(1)]);}
 const last=hs[hs.length-1];if(last){const t=last.lines[last.lines.length-1];if(t&&t[0]===' '&&t[1]==='')last.lines.pop();}
 return hs;}
function normFile(f,ws){const st=String(f.status||'M')[0];
 return {path:f.path,rel:f.path,ws,status:st==='A'||st==='D'?st:'M',add:+f.add||0,del:+f.del||0,binary:!!f.binary,secret:!!f.secret,
  omitted:!!f.patch_omitted,size:[f.size_before??null,f.size_after??null],hunks:(f.binary||f.secret)?[]:parsePatch(f.patch)};}
function host(c){const root=document.querySelector(ROOT);if(!root)return null;
 if(c.el&&c.el.isConnected&&c.el.parentElement&&c.el.parentElement.matches(AG))return c.el.parentElement;
 if(c.message_id){const s=sel(String(c.message_id));const el=root.querySelector(AG+'[data-message-id="'+s+'"],'+AG+'[data-id="'+s+'"]');if(el)return el;}
 if(!c.live)return null;const list=root.querySelectorAll(':scope > '+AG);const last=list[list.length-1];
 return last&&!last.querySelector(':scope > .message-actions')?last:null;}
function place(h,el){const acts=h.querySelector(':scope > .message-actions');
 const after=h.querySelector(':scope > .task-cards')||h.querySelector(':scope > .reasoning-block');
 if(after){if(after.nextSibling!==el)after.after(el);}else if(acts){if(acts.previousSibling!==el)acts.before(el);}
 else if(h.firstChild!==el)h.prepend(el);}
function paintCard(rid){const c=cards.get(rid);if(!c)return;const h=host(c);if(!h)return;
 if(!c.message_id&&h.dataset.messageId)c.message_id=h.dataset.messageId;
 if(c.live&&h.querySelector(':scope > .message-actions'))c.live=false;
 const files=[];let ws='',base=null,end=null;
 for(const [w,d] of c.items){ws=ws||w;(d.files||[]).forEach(f=>files.push(normFile(f,w)));
  if(d.base_at&&(base==null||d.base_at<base))base=d.base_at;if(d.updated&&(end==null||d.updated>end))end=d.updated;}
 if(!files.length)return;
 let el=c.el;const prev=el&&el.isConnected?el:null;
 const open=prev?new Set([...prev.querySelectorAll('.wsd-file.is-open')].map(x=>x.dataset.path)):null;
 const collapsed=prev?prev.classList.contains('is-collapsed'):!c.live?false:true;
 if(!el){el=document.createElement('div');c.el=el;}
 el.dataset.requestId=rid;
 render(el,files,{collapsed,open,live:c.live?'агент работает':null,meta:{ws:c.items.size>1?ws+' +'+(c.items.size-1):ws,rid:String(rid).slice(0,8),base:hhmm(base),end:hhmm(end)}});
 if(c.items.size>1)el.querySelector('.wsd-foot b').textContent=[...c.items.keys()].join(', ');
 place(h,el);watch(h,rid);}
const watched=new WeakSet();
function watch(h,rid){if(watched.has(h))return;watched.add(h);
 new MutationObserver(()=>{const c=cards.get(rid);if(!c||!c.el)return;
  if(c.live&&h.querySelector(':scope > .message-actions')){c.live=false;paintCard(rid);return;}
  if(c.el.isConnected&&c.el.parentElement===h)place(h,c.el);else if(!c.el.isConnected)paintCard(rid);}).observe(h,{childList:true});}
function apply(ev,opts){if(!ev||!ev.request_id)return;const rid=String(ev.request_id);opts=opts||{};
 let c=cards.get(rid);if(!c){c={items:new Map(),message_id:null,live:!opts.done,el:null};cards.set(rid,c);}
 if(ev.message_id)c.message_id=String(ev.message_id);if(opts.done)c.live=false;
 c.items.set(ev.workspace_id||'',ev);paintCard(rid);}
function reset(){cards.forEach(c=>c.el&&c.el.remove());cards.clear();}
async function restore(chatId){reset();if(chatId==null)return;
 let r;try{r=await fetch('/api/chats/'+encodeURIComponent(chatId)+'/ws_diff',{credentials:'include'});}catch(_){return;}
 if(!r.ok)return;let data;try{data=await r.json();}catch(_){return;}
 const running=data.running_request_id||null;
 (data.items||[]).forEach(d=>apply(d,{done:String(d.request_id)!==String(running)}));}
/* transport: 'ws' — app.js отдаёт разобранные сообщения в WsDiff.handle(msg);
   'sse' — app_sigma.js отдаёт EventSource в WsDiff.listen(es), событие ws_diff. */
function init(o){transport=(o&&o.transport)||'ws';return api;}
function handle(msg){if(msg&&msg.type==='ws_diff'){apply(msg);return true;}return false;}
function listen(es){if(!es||es._wsdBound)return;es._wsdBound=true;
 es.addEventListener('ws_diff',e=>{let d;try{d=JSON.parse(e.data);}catch(_){return;}apply(d);});}
let _rz=0;addEventListener('resize',()=>{cancelAnimationFrame(_rz);_rz=requestAnimationFrame(()=>document.querySelectorAll('.wsd-file.is-open .wsd-code').forEach(edge));},{passive:true});
const api={init,handle,listen,apply,restore,reset,render,parsePatch,get transport(){return transport;}};
window.WsDiff=api;
})();
