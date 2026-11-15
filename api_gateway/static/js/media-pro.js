/* media-pro.js — Pro-разметка медиа, которое прислал агент.
 * Один модуль для CatchPM (app.js) и Sigma (app_sigma.js). В лайтбоксе
 * _openMediaLightbox появляется кнопка «Pro»; по ней поверх лайтбокса
 * открывается редактор в shadow root (свои стили и id не пересекаются с
 * приложением). Правки уходят обычным сообщением в текущий чат: текст для
 * агента + JSON-разметка. Загрузки своих файлов здесь нет.
 */
(function () {
'use strict';
const IMG_CSS = ":host{position:fixed;inset:0;z-index:10050;display:block}\n:host{--sans:\"anthropic-sans\",system-ui,\"Segoe UI\",Roboto,Arial,sans-serif;--serif:\"anthropic-serif\",Georgia,\"Times New Roman\",serif;--mono:\"anthropic-mono\",ui-monospace,Menlo,Consolas,monospace;\n--bg:#0d0d0d;--panel:#141414;--panel2:#1a1a1a;--panel3:#222;--line:#2a2a2a;--line2:#333;--fg:#e8e4df;--mut:#8a8580;--mut2:#5e5a56;\n--acc:#cc785c;--acc-h:#b8684e;--acc-fg:#0d0d0d;--sel:rgba(204,120,92,.16);--ubg:#e8e4df;--ufg:#0d0d0d;--ibg:#1a1a1a;--ibd:#2e2e2e;--danger:#e05252;\n--op-ai:#7fb8a4;--op-crop:#cc785c;--op-style:#a98bd0;--op-adjust:#8fa68a;--op-beauty:#d987b0;--op-draw:#7aa8cb;--op-text:#d6a347;--op-mosaic:#a39d96;--op-frame:#c9a27e;--op-effect:#b59adf;--op-region:#e08aa8;\n--sh-lg:0 12px 40px rgba(0,0,0,.55);--stbg:#090909;color-scheme:dark}\n:host([data-theme=light]){--bg:#faf9f7;--panel:#fff;--panel2:#f6f4f1;--panel3:#ede9e3;--line:#e6e1da;--line2:#d8d3cb;--fg:#1a1612;--mut:#5c5650;--mut2:#a39d95;\n--acc:#8b4513;--acc-h:#7a3b10;--acc-fg:#fff;--sel:rgba(139,69,19,.1);--ubg:#1a1612;--ufg:#faf9f7;--ibg:#fff;--ibd:#d8d3cb;--danger:#c0392b;\n--op-ai:#3f8a70;--op-crop:#8b4513;--op-style:#7b5aa8;--op-adjust:#4a7c59;--op-beauty:#b0507a;--op-draw:#3d7aa8;--op-text:#a87a1f;--op-mosaic:#7a736b;--op-frame:#9a6b3c;--op-effect:#7d5cc0;--op-region:#b8456e;\n--sh-lg:0 12px 40px rgba(60,40,20,.16);--stbg:#efece7;color-scheme:light}\n*{box-sizing:border-box}\n[hidden]{display:none!important}\n:host{margin:0;height:100%}\n:host{font:14px/1.45 var(--sans);background:var(--bg);color:var(--fg);-webkit-font-smoothing:antialiased;-webkit-tap-highlight-color:transparent}\nbutton,input,select,textarea{font:inherit;color:inherit}\n.i{width:20px;height:20px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}\n.ibtn{width:36px;height:36px;flex:none;display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:10px;background:none;color:var(--mut);cursor:pointer}\n@media (hover:hover){.ibtn:hover{background:var(--panel3);color:var(--fg)}}\n.ibtn:disabled{opacity:.35;cursor:default;background:none;color:var(--mut)}\n.ibtn.on{color:var(--acc);background:var(--sel)}\n.mono{font-family:var(--mono)}\n.pro-btn{flex:none;height:32px;padding:0 12px 0 10px;border-radius:999px;border:1px solid rgba(204,120,92,.5);background:rgba(204,120,92,.14);color:#e8b49f;display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:13px;cursor:pointer}\n.pro-btn:hover{background:rgba(204,120,92,.26)}\n.pro-btn .i{width:18px;height:18px}\n.ed{position:fixed;inset:0;z-index:60;background:var(--bg);display:none;grid-template-columns:minmax(0,1fr) 360px;grid-template-rows:52px minmax(0,1fr);grid-template-areas:\"hd hd\" \"main side\"}\n.ed.on{display:grid}\n.ed__hd{grid-area:hd;display:flex;align-items:center;gap:6px;padding:0 10px;border-bottom:1px solid var(--line);background:var(--panel);min-width:0}\n.ed__ttl{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.2}\n.ed__ttl b{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.ed__ttl span{font-size:11.5px;color:var(--mut);font-family:var(--mono)}\n.badge{position:absolute;top:2px;right:0;min-width:16px;height:16px;padding:0 4px;border-radius:8px;background:var(--acc);color:var(--acc-fg);font-size:10.5px;font-weight:700;display:flex;align-items:center;justify-content:center}\n.ibtn{position:relative}\n.send-btn{height:36px;padding:0 14px 0 12px;border:0;border-radius:999px;background:var(--acc);color:var(--acc-fg);font-weight:600;display:inline-flex;align-items:center;gap:7px;cursor:pointer;flex:none}\n.send-btn:hover{background:var(--acc-h)}\n.send-btn:disabled{opacity:.4;cursor:default}\n.send-btn .i{width:18px;height:18px}\n.ed__main{grid-area:main;display:flex;flex-direction:column;min-width:0;min-height:0}\n.stage{flex:1;min-height:0;position:relative;background:var(--stbg);display:flex;align-items:center;justify-content:center;overflow:hidden;touch-action:none;user-select:none;-webkit-user-select:none}\n.fr{position:relative;transition:padding .2s,background .2s;box-shadow:0 6px 30px rgba(0,0,0,.35)}\n.pic{position:relative;overflow:visible}\n.pic img{display:block;width:100%;height:100%;pointer-events:none;transition:filter .12s}\n.pic .rot{position:absolute;inset:0;transition:transform .2s}\n.pic canvas{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}\n.vig{position:absolute;inset:0;pointer-events:none;opacity:0}\n.grain{position:absolute;inset:0;pointer-events:none;opacity:0;mix-blend-mode:overlay;background-image:repeating-radial-gradient(circle at 17% 32%,rgba(255,255,255,.5) 0 1px,transparent 1px 3px)}\n.ov{position:absolute;inset:0}\n.crop{position:absolute;box-shadow:0 0 0 9999px rgba(0,0,0,.55);outline:1.5px solid #fff;cursor:move}\n.crop::before,.crop::after{content:\"\";position:absolute;inset:0;pointer-events:none;border:solid rgba(255,255,255,.45);border-width:0 1px}\n.crop::before{left:33.33%;right:33.33%}\n.crop::after{top:33.33%;bottom:33.33%;border-width:1px 0}\n.hdl{position:absolute;width:22px;height:22px;margin:-11px 0 0 -11px;touch-action:none}\n.hdl::after{content:\"\";position:absolute;inset:6px;border-radius:50%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.5)}\n.hdl[data-h=nw]{left:0;top:0;cursor:nwse-resize}\n.hdl[data-h=ne]{left:100%;top:0;cursor:nesw-resize}\n.hdl[data-h=sw]{left:0;top:100%;cursor:nesw-resize}\n.hdl[data-h=se]{left:100%;top:100%;cursor:nwse-resize}\n.rg{position:absolute;border:1.5px dashed var(--op-region);background:color-mix(in srgb,var(--op-region) 12%,transparent);cursor:move}\n.rg.cur{border-style:solid;box-shadow:0 0 0 9999px rgba(0,0,0,.35)}\n.rg__lb{position:absolute;left:-1.5px;bottom:100%;margin-bottom:4px;max-width:220px;padding:2px 8px;border-radius:6px;background:var(--op-region);color:#111;font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}\n.rg.top .rg__lb{bottom:auto;top:100%;margin:4px 0 0}\n.tx{position:absolute;transform:translate(-50%,-50%);padding:2px 6px;border:1.5px dashed transparent;border-radius:6px;white-space:pre;cursor:move;font-weight:700;text-shadow:0 1px 3px rgba(0,0,0,.6);line-height:1.15}\n.tx.cur{border-color:#fff}\n.tx.wm{opacity:.55;font-weight:500}\n.pin{position:absolute;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;border:2px solid #fff;box-shadow:0 0 0 4px color-mix(in srgb,var(--op-ai) 55%,transparent),0 2px 8px rgba(0,0,0,.5);pointer-events:none}\n.hint{position:absolute;left:50%;top:14px;transform:translateX(-50%);padding:6px 12px;border-radius:999px;background:rgba(0,0,0,.6);color:#fff;font-size:12.5px;pointer-events:none;white-space:nowrap;backdrop-filter:blur(8px);max-width:calc(100% - 24px);overflow:hidden;text-overflow:ellipsis}\n.hint:empty{display:none}\n.cmp{position:absolute;right:12px;top:12px;height:30px;padding:0 10px;border-radius:999px;border:0;background:rgba(0,0,0,.55);color:#fff;font-size:12px;display:inline-flex;align-items:center;gap:6px;cursor:pointer;backdrop-filter:blur(8px)}\n.cmp .i{width:16px;height:16px}\n.stage.orig .ov,.stage.orig canvas,.stage.orig .vig,.stage.orig .grain{visibility:hidden}\n.stage.orig img{filter:none!important}\n.opts{flex:none;border-top:1px solid var(--line);background:var(--panel);padding:10px 12px 8px;display:flex;flex-direction:column;gap:9px;min-height:132px}\n.row{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent);padding-right:24px}\n.row::-webkit-scrollbar{display:none}\n.chip{flex:none;width:68px;display:flex;flex-direction:column;align-items:center;gap:5px;border:0;background:none;cursor:pointer;color:var(--mut);padding:0;font-size:11.5px;line-height:1.2}\n.chip .ic{width:46px;height:46px;border-radius:14px;background:var(--panel3);display:flex;align-items:center;justify-content:center;color:var(--fg);border:1.5px solid transparent;position:relative}\n.chip .ic .i{width:22px;height:22px}\n.chip>span:not(.ic){max-width:100%;overflow:hidden;white-space:normal;text-align:center;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;line-clamp:2;hyphens:manual;min-height:2.4em;font-size:11px}\n.chip.on{color:var(--fg)}\n.chip.on .ic{border-color:var(--acc);color:var(--acc);background:var(--sel)}\n.chip .dot{position:absolute;right:4px;top:4px;width:6px;height:6px;border-radius:50%;background:var(--acc)}\n.thumb{flex:none;width:72px;border:0;padding:0;background:none;cursor:pointer;color:var(--mut);font-size:11.5px;display:flex;flex-direction:column;gap:5px;align-items:center}\n.thumb img{width:72px;height:72px;object-fit:cover;border-radius:12px;border:2px solid transparent;display:block}\n.thumb.on{color:var(--fg)}\n.thumb.on img{border-color:var(--acc)}\n.thumb span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.seg{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none}\n.seg button{flex:none;height:28px;padding:0 12px;border-radius:999px;border:0;background:none;color:var(--mut);cursor:pointer;font-size:13px}\n.seg button.on{background:var(--panel3);color:var(--fg);font-weight:600}\n.sl{display:flex;align-items:center;gap:10px;font-size:12.5px;color:var(--mut)}\n.sl b{min-width:44px;text-align:right;font-family:var(--mono);color:var(--fg);font-weight:500}\n.sl label{flex:none;min-width:84px}\ninput[type=range]{flex:1;min-width:0;-webkit-appearance:none;appearance:none;height:28px;background:none;cursor:pointer}\ninput[type=range]::-webkit-slider-runnable-track{height:4px;border-radius:2px;background:linear-gradient(90deg,var(--line2) var(--a,0%),var(--acc) var(--a,0%),var(--acc) var(--b,50%),var(--line2) var(--b,50%))}\ninput[type=range]::-moz-range-track{height:4px;border-radius:2px;background:linear-gradient(90deg,var(--line2) var(--a,0%),var(--acc) var(--a,0%),var(--acc) var(--b,50%),var(--line2) var(--b,50%))}\ninput[type=range]::-moz-range-progress{background:transparent}\n.sl .tr{position:relative;flex:1;min-width:0;display:flex}\n.sl .tr input{flex:1}\n.sl .tr.bi::before{content:\"\";position:absolute;left:50%;top:50%;width:2px;height:10px;margin:-5px 0 0 -1px;border-radius:1px;background:var(--mut2);pointer-events:none}\ninput[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;margin-top:-7px;border-radius:50%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.45)}\ninput[type=range]::-moz-range-thumb{width:18px;height:18px;border:0;border-radius:50%;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.45)}\n.pal{display:flex;gap:9px;align-items:center;overflow-x:auto;scrollbar-width:none;padding:2px}\n.sw{flex:none;width:26px;height:26px;border-radius:50%;border:2px solid var(--line2);cursor:pointer;padding:0;position:relative}\n.sw.on{outline:2px solid var(--acc);outline-offset:2px}\n.sw.rb{background:conic-gradient(red,#ff0,lime,cyan,blue,#f0f,red)}\n.sw input{position:absolute;inset:0;opacity:0;cursor:pointer}\n.tinput{flex:1;min-width:0;height:34px;border-radius:10px;border:1px solid var(--ibd);background:var(--ibg);padding:0 10px}\n.tinput:focus,.ta:focus,.sel2:focus{outline:none;border-color:var(--acc)}\n.mini{height:34px;padding:0 12px;border-radius:10px;border:1px solid var(--line2);background:var(--panel2);cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:13px;flex:none}\n.mini:hover{border-color:var(--acc)}\n.mini .i{width:17px;height:17px}\n.flex{display:flex;gap:8px;align-items:center;min-width:0}\n.desc{font-size:12px;color:var(--mut)}\n.tabs{flex:none;display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;background:var(--panel);border-top:1px solid var(--line);padding:6px 8px calc(6px + env(safe-area-inset-bottom))}\n.tabs::-webkit-scrollbar{display:none}\n.tabs,.opts .row{-webkit-mask-image:linear-gradient(90deg,transparent,#000 var(--fl,0px),#000 calc(100% - var(--fr,0px)),transparent);mask-image:linear-gradient(90deg,transparent,#000 var(--fl,0px),#000 calc(100% - var(--fr,0px)),transparent)}\n.tab{flex:1 0 74px;display:flex;flex-direction:column;align-items:center;gap:4px;padding:6px 4px;border:0;border-radius:12px;background:none;color:var(--mut);cursor:pointer;font-size:11.5px;position:relative;white-space:nowrap}\n.tab .i{width:22px;height:22px}\n@media (hover:hover){.tab:hover{color:var(--fg);background:var(--panel2)}}\n.tab.on{color:var(--acc)}\n.tab.on::after{content:\"\";position:absolute;left:30%;right:30%;bottom:0;height:2px;border-radius:2px;background:var(--acc)}\n.tab .dot{position:absolute;top:5px;left:calc(50% + 10px);width:6px;height:6px;border-radius:50%;background:var(--acc)}\n.ed__side{grid-area:side;display:flex;flex-direction:column;min-height:0;border-left:1px solid var(--line);background:var(--panel)}\n.side__hd{flex:none;display:flex;align-items:center;gap:8px;padding:12px 14px 8px;font-weight:600}\n.side__hd .cnt{color:var(--mut);font-weight:400;font-family:var(--mono);font-size:12.5px}\n.side__hd .sp{flex:1}\n.list{flex:1;min-height:0;overflow:auto;padding:4px 12px 12px;display:flex;flex-direction:column;gap:8px;-webkit-mask-image:linear-gradient(#000 calc(100% - 24px),transparent);mask-image:linear-gradient(#000 calc(100% - 24px),transparent)}\n.empty{color:var(--mut);font-size:13px;padding:18px 6px;text-align:center;line-height:1.55}\n.empty .i{width:28px;height:28px;display:block;margin:0 auto 8px;color:var(--mut2)}\n.ec{border:1px solid var(--line);border-radius:14px;background:var(--panel2);padding:10px 10px 10px 12px;display:flex;flex-direction:column;gap:8px;position:relative;overflow:hidden;flex:none}\n.ec::before{content:\"\";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--c)}\n.ec.cur{border-color:var(--c)}\n.ec__h{display:flex;align-items:center;gap:8px;min-width:0}\n.ec__ic{width:28px;height:28px;flex:none;border-radius:8px;display:flex;align-items:center;justify-content:center;background:color-mix(in srgb,var(--c) 18%,transparent);color:var(--c)}\n.ec__ic .i{width:17px;height:17px}\n.ec__t{flex:1;min-width:0;font-weight:600;font-size:13.5px}\n.ec__s{font-size:12.5px;color:var(--mut);line-height:1.45;word-break:break-word}\n.ec .ibtn{width:30px;height:30px}\n.ec .ibtn .i{width:17px;height:17px}\n.sel2{height:32px;border-radius:9px;border:1px solid var(--ibd);background:var(--ibg);padding:0 8px;min-width:0;flex:1}\n.ta{width:100%;min-height:58px;resize:vertical;border-radius:12px;border:1px solid var(--ibd);background:var(--ibg);padding:9px 11px;line-height:1.45;font-size:13.5px}\n.ec .ta{min-height:44px;font-size:12.5px;padding:8px 10px}\n.send{flex:none;border-top:1px solid var(--line);padding:10px 12px 12px;display:flex;flex-direction:column;gap:8px}\n.send .flex{justify-content:space-between}\n.send .desc{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}\n.md{position:fixed;inset:0;z-index:80;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;padding:16px}\n.md.on{display:flex}\n.md__b{width:min(720px,100%);max-height:min(86vh,760px);display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:var(--sh-lg);overflow:hidden}\n.md__h{display:flex;align-items:center;gap:10px;padding:12px 12px 12px 16px;border-bottom:1px solid var(--line)}\n.md__h b{flex:1;font-size:15px}\n.md__seg{display:flex;background:var(--panel3);border-radius:10px;padding:3px}\n.md__seg button{border:0;background:none;padding:5px 12px;border-radius:8px;cursor:pointer;color:var(--mut);font-size:13px}\n.md__seg button.on{background:var(--panel);color:var(--fg);font-weight:600}\n.md__c{flex:1;min-height:0;overflow:auto;padding:14px 16px}\n.md__c pre{margin:0;font:12.5px/1.55 var(--mono);white-space:pre;overflow:auto}\n.md__msg{white-space:pre-wrap;font-size:14px;line-height:1.55}\n.md__f{display:flex;gap:8px;justify-content:flex-end;padding:10px 12px;border-top:1px solid var(--line)}\n.j-k{color:var(--op-draw)}\n.j-s{color:var(--op-adjust)}\n.j-n{color:var(--op-text)}\n.j-b{color:var(--op-style)}\n.toast{position:fixed;left:50%;bottom:28px;transform:translate(-50%,20px);opacity:0;z-index:90;background:var(--fg);color:var(--bg);padding:9px 16px;border-radius:999px;font-size:13px;transition:.2s;pointer-events:none;white-space:nowrap}\n.toast.on{opacity:1;transform:translate(-50%,0)}\n:host(.toast-on) .ed.on .hint{opacity:0!important;transition:opacity .15s}\n.ed.on~.toast{bottom:auto;top:64px;transform:translate(-50%,-12px)}\n.ed.on~.toast.on{transform:translate(-50%,0)}\n.scrim{display:none}\n.only-m{display:none!important}\n@media (max-width:760px){.ed{grid-template-columns:minmax(0,1fr);grid-template-areas:\"hd\" \"main\"}\n.only-m{display:inline-flex!important}\n.only-d{display:none!important}\n.ed__hd{padding:0 4px 0 2px;gap:2px}\n.send-btn{padding:0 12px}\n.send-btn span{display:none}\n.ed__side{position:fixed;left:0;right:0;bottom:0;z-index:70;max-height:78vh;border:1px solid var(--line);border-bottom:0;border-radius:20px 20px 0 0;transform:translateY(105%);transition:transform .25s;box-shadow:var(--sh-lg);padding-bottom:env(safe-area-inset-bottom)}\n.ed.side-on .ed__side{transform:none}\n.ed.side-on .scrim{display:block;position:fixed;inset:0;z-index:65;background:rgba(0,0,0,.45)}\n.side__hd::before{content:\"\";position:absolute;left:50%;top:6px;width:36px;height:4px;margin-left:-18px;border-radius:2px;background:var(--line2)}\n.side__hd{position:relative;padding-top:18px}\n.opts{padding:8px 10px 6px;min-height:118px;gap:7px}\n.tab{flex:0 0 70px}\n.chip{width:66px}\n.sl label{min-width:0}\n.md{padding:0;align-items:flex-end}\n.md__b{border-radius:18px 18px 0 0;max-height:90vh}}\n@media (pointer:coarse){.hdl{width:36px;height:36px;margin:-18px 0 0 -18px}\n.hdl::after{inset:11px}}\n@media (max-width:760px){.cmp{top:auto;bottom:10px;right:10px}\n.hint{top:10px}}\n.crop.ghost{box-shadow:0 0 0 9999px rgba(0,0,0,.28);outline:1.5px dashed rgba(255,255,255,.7);pointer-events:none}\n.crop.ghost::before,.crop.ghost::after,.crop.ghost .hdl{display:none}\n.stage:not([data-tab=text]) .tx{pointer-events:none}\n.stage:not([data-tab=region]) .rg{pointer-events:none;opacity:.55}\n.stage[data-tab=draw],.stage[data-tab=mosaic],.stage.paint{cursor:crosshair}\n.stage[data-tab=cutout],.stage[data-tab=region]{cursor:crosshair}\n.tlt{position:absolute;left:0;right:0;pointer-events:none}\n.rot .hdl{transform:scale(calc(1/var(--k,1)))}\n.rot.flip .tx{transform:translate(-50%,-50%) scaleX(-1)}\n.rot.flip .rg__lb{transform:scaleX(-1)}\n.pic.tilted::after{content:\"\";position:absolute;inset:0;pointer-events:none;background:linear-gradient(rgba(255,255,255,.6),rgba(255,255,255,.6)) 33.33% 0/1px 100% no-repeat,linear-gradient(rgba(255,255,255,.6),rgba(255,255,255,.6)) 66.66% 0/1px 100% no-repeat,linear-gradient(90deg,rgba(255,255,255,.6),rgba(255,255,255,.6)) 0 33.33%/100% 1px no-repeat,linear-gradient(90deg,rgba(255,255,255,.6),rgba(255,255,255,.6)) 0 66.66%/100% 1px no-repeat}\n.pic.tilted::after{filter:drop-shadow(0 0 1px rgba(0,0,0,.7))}\n.stage:not([data-tab=crop]) .pic.tilted::after{display:none}\n.tx.st-outline{text-shadow:none;-webkit-text-stroke:2px rgba(0,0,0,.85);paint-order:stroke fill}\n.tx.st-box{text-shadow:none;background:rgba(0,0,0,.5);border-radius:8px;padding:4px 10px}\n.crop.ghost{box-shadow:0 0 0 9999px rgba(0,0,0,.28);outline:1.5px dashed rgba(255,255,255,.7);pointer-events:none}\n.crop.ghost::before,.crop.ghost::after,.crop.ghost .hdl{display:none}\n.stage:not([data-tab=text]) .tx{pointer-events:none}\n.stage:not([data-tab=region]) .rg{pointer-events:none;opacity:.55}\n.stage[data-tab=draw],.stage[data-tab=mosaic],.stage.paint{cursor:crosshair}\n.stage[data-tab=cutout],.stage[data-tab=region]{cursor:crosshair}\n.tlt{position:absolute;left:0;right:0;pointer-events:none}\n.rot .hdl{transform:scale(calc(1/var(--k,1)))}\n.rot.flip .tx{transform:translate(-50%,-50%) scaleX(-1)}\n.rot.flip .rg__lb{transform:scaleX(-1)}\n.pic.tilted::after{content:\"\";position:absolute;inset:0;pointer-events:none;background:linear-gradient(rgba(255,255,255,.6),rgba(255,255,255,.6)) 33.33% 0/1px 100% no-repeat,linear-gradient(rgba(255,255,255,.6),rgba(255,255,255,.6)) 66.66% 0/1px 100% no-repeat,linear-gradient(90deg,rgba(255,255,255,.6),rgba(255,255,255,.6)) 0 33.33%/100% 1px no-repeat,linear-gradient(90deg,rgba(255,255,255,.6),rgba(255,255,255,.6)) 0 66.66%/100% 1px no-repeat}\n.pic.tilted::after{filter:drop-shadow(0 0 1px rgba(0,0,0,.7))}\n.stage:not([data-tab=crop]) .pic.tilted::after{display:none}\n.tx.st-outline{text-shadow:none;-webkit-text-stroke:2px rgba(0,0,0,.85);paint-order:stroke fill}\n.tx.st-box{text-shadow:none;background:rgba(0,0,0,.5);border-radius:8px;padding:4px 10px}";
const IMG_HTML = "<svg width=\"0\" height=\"0\" style=\"position:absolute\" aria-hidden=\"true\"><defs><symbol id=\"i-back\" viewBox=\"0 0 24 24\"><path d=\"M15 5l-7 7 7 7\"/></symbol><symbol id=\"i-x\" viewBox=\"0 0 24 24\"><path d=\"M6 6l12 12M18 6L6 18\"/></symbol><symbol id=\"i-check\" viewBox=\"0 0 24 24\"><path d=\"M5 12.5l4.5 4.5L19 7.5\"/></symbol><symbol id=\"i-undo\" viewBox=\"0 0 24 24\"><path d=\"M9 14L4 9l5-5\"/><path d=\"M4 9h10.5a5.5 5.5 0 010 11H11\"/></symbol><symbol id=\"i-redo\" viewBox=\"0 0 24 24\"><path d=\"M15 14l5-5-5-5\"/><path d=\"M20 9H9.5a5.5 5.5 0 000 11H13\"/></symbol><symbol id=\"i-cmp\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"5\" width=\"17\" height=\"14\" rx=\"2.5\"/><path d=\"M12 3v18\"/><path d=\"M12 5h6a2.5 2.5 0 012.5 2.5v9A2.5 2.5 0 0118 19h-6z\" fill=\"currentColor\" stroke=\"none\" opacity=\".35\"/></symbol><symbol id=\"i-list\" viewBox=\"0 0 24 24\"><path d=\"M9 6.5h11M9 12h11M9 17.5h11\"/><circle cx=\"4.5\" cy=\"6.5\" r=\"1\"/><circle cx=\"4.5\" cy=\"12\" r=\"1\"/><circle cx=\"4.5\" cy=\"17.5\" r=\"1\"/></symbol><symbol id=\"i-send\" viewBox=\"0 0 24 24\"><path d=\"M12 19V5M6 11l6-6 6 6\"/></symbol><symbol id=\"i-pro\" viewBox=\"0 0 24 24\"><path d=\"M4 20l4-1 10.5-10.5a2.1 2.1 0 00-3-3L5 16z\"/><path d=\"M14 7l3 3\"/><path d=\"M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z\"/></symbol><symbol id=\"i-imgadd\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"4.5\" width=\"17\" height=\"15\" rx=\"3\"/><circle cx=\"9\" cy=\"9.5\" r=\"1.6\"/><path d=\"M4 17.5l4.6-4.6 3.4 3.4 2.6-2.6 5.4 5.4\"/></symbol><symbol id=\"i-dl\" viewBox=\"0 0 24 24\"><path d=\"M12 4v11M7 10.5l5 5 5-5M5 19.5h14\"/></symbol><symbol id=\"i-trash\" viewBox=\"0 0 24 24\"><path d=\"M4.5 7h15M10 4h4M6.5 7l.8 12a1.8 1.8 0 001.8 1.6h5.8a1.8 1.8 0 001.8-1.6l.8-12M10 11v6M14 11v6\"/></symbol><symbol id=\"i-copy\" viewBox=\"0 0 24 24\"><rect x=\"8.5\" y=\"8.5\" width=\"11\" height=\"11\" rx=\"2.5\"/><path d=\"M15.5 8.5V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7.5a2 2 0 002 2h2.5\"/></symbol><symbol id=\"i-sun2\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4\"/></symbol><symbol id=\"i-moon\" viewBox=\"0 0 24 24\"><path d=\"M19.5 14.5A8 8 0 019.5 4.5a8 8 0 1010 10z\"/></symbol><!-- вкладки --><symbol id=\"t-ai\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"5.5\" width=\"14\" height=\"14\" rx=\"3\"/><path d=\"M4 17l4-4.5 3 3 2-2 4 4\"/><path d=\"M19 2.5l.9 2.1 2.1.9-2.1.9L19 8.5l-.9-2.1-2.1-.9 2.1-.9z\"/></symbol><symbol id=\"t-crop\" viewBox=\"0 0 24 24\"><path d=\"M7 3v14h14\"/><path d=\"M3 7h14v14\"/></symbol><symbol id=\"t-style\" viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2.5\"/><path d=\"M3 8.5h18M3 15.5h18M7 5v3.5M11 5v3.5M15 5v3.5M7 15.5V19M11 15.5V19M15 15.5V19\"/></symbol><symbol id=\"t-adjust\" viewBox=\"0 0 24 24\"><path d=\"M4 8h16M4 16h16\"/><circle cx=\"9\" cy=\"8\" r=\"2.4\" fill=\"var(--panel)\"/><circle cx=\"15\" cy=\"16\" r=\"2.4\" fill=\"var(--panel)\"/></symbol><symbol id=\"t-beauty\" viewBox=\"0 0 24 24\"><path d=\"M9 3.5c-3 1-4.5 3.8-4.5 7 0 2 .8 3 .8 4.5L4 17.5h2.5c0 2 1.3 3 3.5 3\"/><path d=\"M9.5 9.5c.8.6 1.6.6 2.4 0\"/><path d=\"M17.5 7l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z\"/></symbol><symbol id=\"t-draw\" viewBox=\"0 0 24 24\"><path d=\"M3.5 20.5c2-.2 3.4-1 4-2.6\"/><path d=\"M7.5 17.9l-1.4-1.4L16.8 5.8a2 2 0 012.8 2.8z\"/><path d=\"M15 7.6l1.4 1.4\"/></symbol><symbol id=\"t-text\" viewBox=\"0 0 24 24\"><path d=\"M5 7V5h14v2M12 5v14M9 19h6\"/></symbol><symbol id=\"t-mosaic\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2.5\"/><path d=\"M4 9.3h16M4 14.6h16M9.3 4v16M14.6 4v16\"/><path d=\"M4 4h5.3v5.3H4zM14.6 9.3h5.4v5.3h-5.4zM9.3 14.6h5.3V20H9.3z\" fill=\"currentColor\" stroke=\"none\" opacity=\".45\"/></symbol><symbol id=\"t-frame\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"2.5\"/><rect x=\"8\" y=\"8\" width=\"8\" height=\"8\" rx=\"1\" fill=\"currentColor\" stroke=\"none\" opacity=\".45\"/></symbol><symbol id=\"t-effect\" viewBox=\"0 0 24 24\"><path d=\"M10 3.5l1.9 4.6 5 .4-3.8 3.2 1.2 4.8L10 13.9l-4.3 2.6 1.2-4.8L3 8.5l5-.4z\"/><path d=\"M18.5 15l.6 1.5 1.5.6-1.5.6-.6 1.5-.6-1.5-1.5-.6 1.5-.6zM17 3.5v2M16 4.5h2\"/></symbol><symbol id=\"t-cutout\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\" stroke-dasharray=\"3 2.6\"/><path d=\"M8.5 15.5c.8-2.5 2-4 3.5-4s2.7 1.5 3.5 4\"/><circle cx=\"12\" cy=\"8.5\" r=\"1.8\"/></symbol><symbol id=\"t-region\" viewBox=\"0 0 24 24\"><path d=\"M4 8V5.5A1.5 1.5 0 015.5 4H8M16 4h2.5A1.5 1.5 0 0120 5.5V8M20 16v2.5a1.5 1.5 0 01-1.5 1.5H16M8 20H5.5A1.5 1.5 0 014 18.5V16\"/><path d=\"M8.5 10h7M8.5 14h4.5\"/></symbol><!-- инструменты --><symbol id=\"s-rot\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"9\" width=\"11\" height=\"11\" rx=\"2\"/><path d=\"M9 4.5h4.5a6 6 0 016 6V13\"/><path d=\"M17 11l2.5 2.5L22 11\"/></symbol><symbol id=\"s-flip\" viewBox=\"0 0 24 24\"><path d=\"M12 3v18\" stroke-dasharray=\"2 2.4\"/><path d=\"M9 7L3.5 17H9zM15 7l5.5 10H15z\"/></symbol><symbol id=\"s-free\" viewBox=\"0 0 24 24\"><path d=\"M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4\"/></symbol><symbol id=\"s-reset\" viewBox=\"0 0 24 24\"><path d=\"M4.5 12a7.5 7.5 0 102.2-5.3L4.5 9\"/><path d=\"M4.5 4.5V9H9\"/></symbol><symbol id=\"s-bright\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"3.8\"/><path d=\"M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7\"/></symbol><symbol id=\"s-contrast\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 010 17z\" fill=\"currentColor\" stroke=\"none\"/></symbol><symbol id=\"s-sat\" viewBox=\"0 0 24 24\"><path d=\"M12 3.5s6 6.4 6 10.5a6 6 0 01-12 0c0-4.1 6-10.5 6-10.5z\"/><path d=\"M9 14.5a3 3 0 003 3\"/></symbol><symbol id=\"s-temp\" viewBox=\"0 0 24 24\"><path d=\"M10 14.5V5a2 2 0 014 0v9.5a4 4 0 11-4 0z\"/><path d=\"M12 9v7\"/></symbol><symbol id=\"s-tint\" viewBox=\"0 0 24 24\"><path d=\"M12 3.5s6 6.4 6 10.5a6 6 0 01-12 0c0-4.1 6-10.5 6-10.5z\"/><path d=\"M6.3 15.5A6 6 0 0012 20a6 6 0 004-1.5z\" fill=\"currentColor\" stroke=\"none\" opacity=\".5\"/></symbol><symbol id=\"s-hl\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5v17M15 4.2v15.6M18 6.2v11.6\"/></symbol><symbol id=\"s-shadow\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 000 17z\" fill=\"currentColor\" stroke=\"none\" opacity=\".75\"/><path d=\"M15 4.2v15.6M18 6.2v11.6\"/></symbol><symbol id=\"s-enh\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 7v10M7.7 9.5l8.6 5M7.7 14.5l8.6-5\"/></symbol><symbol id=\"s-sharp\" viewBox=\"0 0 24 24\"><path d=\"M12 4l8.5 15h-17z\"/></symbol><symbol id=\"s-fade\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2.5\"/><path d=\"M8 4v16M12 4v16M16 4v16\" opacity=\".55\"/></symbol><symbol id=\"s-vig\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"3.5\" fill=\"currentColor\" stroke=\"none\" opacity=\".8\"/><circle cx=\"12\" cy=\"12\" r=\"4.5\" fill=\"var(--panel)\" stroke=\"none\"/></symbol><symbol id=\"s-wand\" viewBox=\"0 0 24 24\"><path d=\"M4 20L15 9M13.5 7.5l3 3\"/><path d=\"M18 3l.7 1.6 1.6.7-1.6.7L18 7.6l-.7-1.6-1.6-.7 1.6-.7zM8 3.5l.5 1.2 1.2.5-1.2.5L8 6.9l-.5-1.2-1.2-.5 1.2-.5zM19.5 13l.5 1.2 1.2.5-1.2.5-.5 1.2-.5-1.2-1.2-.5 1.2-.5z\"/></symbol><symbol id=\"s-up\" viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"4.5\" width=\"13\" height=\"13\" rx=\"2.5\"/><path d=\"M3.5 15l3.5-4 3 3\"/><circle cx=\"17.5\" cy=\"17\" r=\"4\"/><path d=\"M17.5 19v-4M15.8 16.5l1.7-1.7 1.7 1.7\"/></symbol><symbol id=\"s-erase\" viewBox=\"0 0 24 24\"><path d=\"M9 20h11\"/><path d=\"M5.5 16.5l9.8-9.8a2 2 0 012.8 0l1.2 1.2a2 2 0 010 2.8L11.5 18.5H7.5z\"/><path d=\"M10 12l4.5 4.5\"/></symbol><symbol id=\"s-glare\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"4.5\" width=\"17\" height=\"15\" rx=\"2.5\"/><path d=\"M7 15l6-6M10 16.5l6.5-6.5\" opacity=\".7\"/></symbol><symbol id=\"s-people\" viewBox=\"0 0 24 24\"><circle cx=\"9\" cy=\"8\" r=\"3\"/><path d=\"M3.5 19c.6-3.3 2.7-5 5.5-5s4.9 1.7 5.5 5\"/><path d=\"M16 5l5 5M21 5l-5 5\"/></symbol><symbol id=\"s-expand\" viewBox=\"0 0 24 24\"><rect x=\"8\" y=\"8\" width=\"8\" height=\"8\" rx=\"1.5\"/><path d=\"M3.5 8V3.5H8M16 3.5h4.5V8M20.5 16v4.5H16M8 20.5H3.5V16\"/></symbol><symbol id=\"s-face\" viewBox=\"0 0 24 24\"><ellipse cx=\"12\" cy=\"12\" rx=\"7\" ry=\"8.5\"/><path d=\"M9 10.5q1 1 2 0M13 10.5q1 1 2 0M9.5 15q2.5 2 5 0\"/></symbol><symbol id=\"s-slim\" viewBox=\"0 0 24 24\"><path d=\"M7 5c-1.5 2-2 4.5-1.5 7.5.6 3.5 3.5 7 6.5 7s5.9-3.5 6.5-7c.5-3-0-5.5-1.5-7.5\"/><path d=\"M3 12h3M18 12h3M4.5 10.5L6 12l-1.5 1.5M19.5 10.5L18 12l1.5 1.5\"/></symbol><symbol id=\"s-eye\" viewBox=\"0 0 24 24\"><path d=\"M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/></symbol><symbol id=\"s-lip\" viewBox=\"0 0 24 24\"><path d=\"M6 21v-8h5v8zM6.5 13V9.5L10.5 7v6\"/><path d=\"M15 21l1.5-7h3l1.5 7zM16 14c0-3 .5-6 2-8.5 1.5 2.5 2 5.5 2 8.5\"/></symbol><symbol id=\"s-brush\" viewBox=\"0 0 24 24\"><path d=\"M4 20c4-1 4-6 8.5-6M20 4l-7.5 10\"/><path d=\"M4 20c2.5-5 5.5-8 8.5-6\" opacity=\".4\"/></symbol><symbol id=\"s-marker\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-width=\"4.5\" opacity=\".45\"/></symbol><symbol id=\"s-neon\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-width=\"5\" opacity=\".25\"/><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\"/></symbol><symbol id=\"s-crayon\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-width=\"3\" stroke-dasharray=\"1 1.2\"/></symbol><symbol id=\"s-dash\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-dasharray=\"3.5 3\"/></symbol><symbol id=\"s-pixel\" viewBox=\"0 0 24 24\"><path d=\"M4 4h5v5H4zM9 9h5v5H9zM14 4h6v5h-6zM4 14h5v6H4zM14 14h6v6h-6z\" fill=\"currentColor\" stroke=\"none\" opacity=\".55\"/><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"1.5\"/></symbol><symbol id=\"s-blur\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\" stroke-dasharray=\"1.5 2.5\"/><circle cx=\"12\" cy=\"12\" r=\"4.5\" opacity=\".6\"/><circle cx=\"12\" cy=\"12\" r=\"1.2\" fill=\"currentColor\"/></symbol><symbol id=\"s-hatch\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2.5\"/><path d=\"M4 10l6-6M4 16L16 4M8 20L20 8M14 20l6-6\"/></symbol><symbol id=\"s-bokeh\" viewBox=\"0 0 24 24\"><circle cx=\"8\" cy=\"9\" r=\"4\"/><circle cx=\"16.5\" cy=\"7\" r=\"2.5\" opacity=\".6\"/><circle cx=\"15\" cy=\"16\" r=\"4.5\" opacity=\".8\"/></symbol><symbol id=\"s-grain\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"3\"/><path d=\"M8 8h.01M12 7h.01M16 9h.01M7 12h.01M11 12h.01M15 13h.01M9 16h.01M13 16.5h.01M17 16h.01\" stroke-width=\"2.4\"/></symbol><symbol id=\"s-tilt\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"3\"/><path d=\"M3.5 9.5h17M3.5 14.5h17\"/><path d=\"M3.5 3.5h17v6h-17zM3.5 14.5h17v6h-17z\" fill=\"currentColor\" stroke=\"none\" opacity=\".25\"/></symbol><symbol id=\"s-color\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 018.5 8.5H12z\" fill=\"currentColor\" stroke=\"none\" opacity=\".5\"/></symbol><symbol id=\"s-tap\" viewBox=\"0 0 24 24\"><path d=\"M10 11V5.5a1.5 1.5 0 013 0V11l4 .8a2 2 0 011.6 2.3l-.9 4.4A2.5 2.5 0 0115.2 20.5H11a2.5 2.5 0 01-2-1l-3.2-4.2a1.4 1.4 0 012.1-1.8L10 15\"/><path d=\"M7 4.5a5 5 0 019 0\"/></symbol></defs></svg><section class=\"ed\" id=\"ed\" aria-label=\"Pro-разметка изображения\"><div class=\"ed__hd\"><button class=\"ibtn\" id=\"edBack\" type=\"button\" title=\"Назад (Esc)\"><svg class=\"i\"><use href=\"#i-back\"/></svg></button><div class=\"ed__ttl\"><b id=\"edName\"></b><span id=\"edDim\"></span></div><button class=\"ibtn\" id=\"undoBtn\" type=\"button\" title=\"Отменить (Ctrl+Z)\" disabled><svg class=\"i\"><use href=\"#i-undo\"/></svg></button><button class=\"ibtn\" id=\"redoBtn\" type=\"button\" title=\"Повторить (Ctrl+Shift+Z)\" disabled><svg class=\"i\"><use href=\"#i-redo\"/></svg></button><button class=\"ibtn only-m\" id=\"listBtn\" type=\"button\" title=\"Список правок\"><svg class=\"i\"><use href=\"#i-list\"/></svg><span class=\"badge\" id=\"listBadge\" hidden>0</span></button><button class=\"send-btn only-m\" id=\"sendM\" type=\"button\" disabled><svg class=\"i\"><use href=\"#i-send\"/></svg><span>Отправить</span></button></div><div class=\"ed__main\"><div class=\"stage\" id=\"stage\"><div class=\"fr\" id=\"fr\"><div class=\"pic\" id=\"pic\"><div class=\"rot\" id=\"rot\"><img id=\"img\" src=\"\" alt=\"\"><canvas id=\"ink\"></canvas><div class=\"vig\" id=\"vig\"></div><div class=\"grain\" id=\"grain\"></div><div class=\"ov\" id=\"ov\"></div></div></div></div><div class=\"hint\" id=\"hint\"></div><button class=\"cmp\" id=\"cmpBtn\" type=\"button\" title=\"Удерживайте, чтобы увидеть оригинал\"><svg class=\"i\"><use href=\"#i-cmp\"/></svg>Оригинал</button></div><div class=\"opts\" id=\"opts\"></div><nav class=\"tabs\" id=\"tabs\"></nav></div><div class=\"scrim\" id=\"scrim\"></div><aside class=\"ed__side\" id=\"side\"><div class=\"side__hd\">Правки <span class=\"cnt\" id=\"cnt\">0</span><span class=\"sp\"></span><button class=\"ibtn\" id=\"clearBtn\" type=\"button\" title=\"Очистить всё\" disabled><svg class=\"i\"><use href=\"#i-trash\"/></svg></button><button class=\"ibtn only-m\" id=\"sideClose\" type=\"button\" title=\"Свернуть\"><svg class=\"i\"><use href=\"#i-x\"/></svg></button></div><div class=\"list\" id=\"list\"></div><div class=\"send\"><textarea class=\"ta\" id=\"prompt\" placeholder=\"Общий комментарий для агента: стиль, формат, что важно сохранить…\"></textarea><div class=\"flex\"><span class=\"desc\" id=\"sendDesc\">Добавьте правку или комментарий</span><button class=\"send-btn\" id=\"sendBtn\" type=\"button\" disabled><svg class=\"i\"><use href=\"#i-send\"/></svg><span>Отправить агенту</span></button></div></div></aside></section><div class=\"md\" id=\"md\"><div class=\"md__b\"><div class=\"md__h\"><b>Проверьте перед отправкой</b><div class=\"md__seg\" id=\"mdSeg\"><button class=\"on\" data-v=\"msg\" type=\"button\">Сообщение</button><button data-v=\"json\" type=\"button\">JSON</button></div><button class=\"ibtn\" id=\"mdClose\" type=\"button\" title=\"Закрыть\"><svg class=\"i\"><use href=\"#i-x\"/></svg></button></div><div class=\"md__c\"><div class=\"md__msg\" id=\"mdMsg\"></div><pre id=\"mdJson\" hidden></pre></div><div class=\"md__f\"><button class=\"mini\" id=\"mdCopy\" type=\"button\"><svg class=\"i\"><use href=\"#i-copy\"/></svg>Копировать JSON</button><button class=\"send-btn\" id=\"mdOk\" type=\"button\"><svg class=\"i\"><use href=\"#i-check\"/></svg><span>Отправить в чат</span></button></div></div></div><div class=\"toast\" id=\"toast\"></div>";

/* ── редактор изображения (перенос стенда image_pro.html) ── */
function mountImage(R, host, MP, __on) {


const $=s=>R.querySelector(s), $$=s=>[...R.querySelectorAll(s)];
const P=new URLSearchParams('');
const TOUCH=matchMedia('(pointer:coarse)').matches;
const FILE=Object.assign({},MP.file);
const ic=(id,cls='i')=>`<svg class="${cls}"><use href="#${id}"/></svg>`;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const r3=v=>Math.round(v*1000)/1000;
const sgn=v=>v>0?'+'+v:v<0?'−'+(-v):'0';
const pct=v=>Math.round(v*100)+'%';

const TABS=[['ai','ИИ-ретушь','t-ai'],['crop','Кадрировать','t-crop'],['style','Стили','t-style'],['adjust','Коррекция','t-adjust'],['beauty','Красота','t-beauty'],['draw','Наброски','t-draw'],['text','Текст','t-text'],['mosaic','Мозаика','t-mosaic'],['frame','Рамки','t-frame'],['effect','Эффекты','t-effect'],['cutout','Вырезать','t-cutout'],['region','Область','t-region']];
const TAB=Object.fromEntries(TABS.map(t=>[t[0],{label:t[1],icon:t[2]}]));
const COL={ai:'--op-ai',crop:'--op-crop',style:'--op-style',adjust:'--op-adjust',beauty:'--op-beauty',draw:'--op-draw',text:'--op-text',mosaic:'--op-mosaic',frame:'--op-frame',effect:'--op-effect',cutout:'--op-ai',region:'--op-region'};
const AI=[['auto','Авто','s-wand'],['enhance','Улучшить','s-up'],['erase','Стереть объект','s-erase'],['glare','Убрать блики','s-glare'],['people','Убрать людей','s-people'],['expand','Расширить кадр','s-expand']];
const ASPECT=[['orig','Исходное',null],['free','Свободно',null],['1:1','1:1',1],['3:4','3:4',3/4],['4:3','4:3',4/3],['9:16','9:16',9/16],['16:9','16:9',16/9]];
const ADJ=[['bright','Яркость','s-bright',-100],['contrast','Контраст','s-contrast',-100],['sat','Насыщенность','s-sat',-100],['temp','Температура','s-temp',-100],['tint','Оттенок','s-tint',-100],['hl','Светлые участки','s-hl',-100],['shadow','Тени','s-shadow',-100],['enh','Усиление','s-enh',0],['sharp','Резкость','s-sharp',0],['fade','Затухание','s-fade',0],['vig','Виньетка','s-vig',0]];
const BEAUTY=[['smooth','Сглаживание','s-face'],['whiten','Осветление','s-bright'],['slim','Тонкое лицо','s-slim'],['eyes','Глаза','s-eye'],['makeup','Макияж','s-lip']];
const STYLES={'Простые':[['sunny','Солнечный','sepia(.25) saturate(1.3) brightness(1.06)'],['vivid','Насыщенный','saturate(1.6) contrast(1.05)'],['porcelain','Фарфор','brightness(1.12) saturate(.75) contrast(.92)'],['negative','Негатив','invert(1)'],['positive','Позитив','contrast(1.2) saturate(1.2)'],['mono','Ч/Б','grayscale(1) contrast(1.1)']],
'Портрет':[['soft','Нежный','brightness(1.08) saturate(.85) contrast(.9)'],['warm','Тёплый','sepia(.3) saturate(1.2)'],['cine','Кино','contrast(1.15) saturate(.8) hue-rotate(-8deg)'],['noir','Нуар','grayscale(1) contrast(1.4) brightness(.9)']],
'Пейзаж':[['sunset','Закат','sepia(.35) saturate(1.5) hue-rotate(-12deg)'],['forest','Лес','saturate(1.3) hue-rotate(12deg)'],['sea','Море','hue-rotate(-20deg) saturate(1.25)'],['fog','Туман','contrast(.8) brightness(1.1) saturate(.7)']],
'Съемка моментов':[['film','Плёнка','sepia(.2) contrast(1.1) saturate(.9)'],['retro','Ретро','sepia(.55) contrast(.95)'],['day','Яркий день','brightness(1.1) saturate(1.35)'],['cold','Холод','hue-rotate(-25deg) saturate(.9) brightness(1.05)']]};
const STYLE_BY=Object.fromEntries(Object.entries(STYLES).flatMap(([c,a])=>a.map(s=>[s[0],{cat:c,name:s[1],css:s[2]}])));
const BRUSH=[['eraser','Ластик','s-erase'],['pen','Кисть','s-brush'],['marker','Маркер','s-marker'],['neon','Неон','s-neon'],['crayon','Мелок','s-crayon'],['dash','Пунктир','s-dash']];
const COLORS=['#111111','#ffffff','#e5484d','#ff8a3d','#f5c518','#3fb950','#3b82f6','#a371f7'];
const MOS=[['eraser','Ластик','s-erase'],['pixel','Пиксели','s-pixel'],['blur','Размытие','s-blur'],['hatch','Штрихи','s-hatch']];
const TKIND=[['plain','Обычные'],['wm','Водяные знаки'],['date','Дата']];
const TSTYLE=[['shadow','Тень'],['outline','Обводка'],['box','Плашка']];
const FKIND=[['none','Без рамки'],['color','Сплошной цвет'],['poster','Постер']];
const FFORMAT=[['orig','Исходное',null],['full','Полный',null],['1:1','1:1',1],['4:3','4:3',4/3],['3:4','3:4',3/4],['9:16','9:16',9/16]];
const FCOLORS=['#111111','#ffffff','#f4c7c3','#f6dcc4','#cfe3c8','#c7dcef','#d9cdea'];
const EFF=[['bokeh','Боке','s-bokeh'],['grain','Зерно','s-grain'],['glow','Свечение','i-sun2'],['tilt','Тилт-шифт','s-tilt']];
const CUTBG=[['transparent','Прозрачный'],['white','Белый'],['blur','Размытый'],['color','Свой цвет']];
const RACT=[['comment','По комментарию'],['remove','Удалить объект'],['replace','Заменить'],['blur','Размыть'],['enhance','Улучшить'],['crop','Кадрировать сюда']];
const ACT_BY=Object.fromEntries(RACT);

const blank=()=>({
  crop:{aspect:'orig',rect:null,rot:0,flip:false,angle:0},
  style:{preset:null,strength:100},
  adjust:Object.fromEntries(ADJ.map(a=>[a[0],0])),
  beauty:Object.fromEntries(BEAUTY.map(a=>[a[0],0])),
  ai:{tasks:[],erase:[]},
  draw:{strokes:[]}, mosaic:{strokes:[]},
  text:[], region:[],
  frame:{kind:'none',format:'orig',color:'#ffffff'},
  effect:Object.fromEntries(EFF.map(a=>[a[0],0])),
  cutout:{point:null,bg:'transparent',color:'#ffffff'}
});
let S=blank();
const U={tab:'crop',adj:'bright',beauty:'smooth',eff:'bokeh',styleCat:'Простые',brush:'pen',color:'#ff8a3d',bw:6,mos:'pixel',mw:28,tkind:'plain',tcolor:'#ffffff',tstyle:'shadow',cur:null,aiErase:false,nw:0,nh:0,seq:1};
const nid=p=>p+(U.seq++);

/* история */
const H={u:[],r:[],t:0,open:false};
function mark(){
  if(!H.open){H.u.push(JSON.stringify(S));if(H.u.length>120)H.u.shift();H.r=[];H.open=true}
  clearTimeout(H.t);H.t=setTimeout(()=>H.open=false,450);
}
function undo(){if(!H.u.length)return;H.r.push(JSON.stringify(S));S=JSON.parse(H.u.pop());H.open=false;U.cur=null;all()}
function redo(){if(!H.r.length)return;H.u.push(JSON.stringify(S));S=JSON.parse(H.r.pop());H.open=false;U.cur=null;all()}

/* элементы */
const ed=$('#ed'),stage=$('#stage'),fr=$('#fr'),pic=$('#pic'),rot=$('#rot'),img=$('#img'),ink=$('#ink'),ov=$('#ov'),opts=$('#opts'),tabs=$('#tabs'),list=$('#list'),hint=$('#hint');
rot.appendChild(ov);
let BOX={w:0,h:0};

function totalDeg(){return S.crop.rot*90+S.crop.angle}
function fit(){
  if(!U.nw||!ed.classList.contains('on'))return;
  const sw=stage.clientWidth-32,sh=stage.clientHeight-32;
  const odd=S.crop.rot%2===1;
  const da=odd?U.nh/U.nw:U.nw/U.nh;
  const f=S.frame, on=f.kind!=='none';
  const fo=FFORMAT.find(x=>x[0]===f.format);
  const fa=on&&fo&&fo[2]?fo[2]:da;
  let fw=sw,fh=sw/fa; if(fh>sh){fh=sh;fw=sh*fa}
  const pad=on&&f.format!=='full'?Math.round(Math.min(fw,fh)*.06):0;
  let pw=fw-2*pad,ph=pw/da; if(ph>fh-2*pad){ph=fh-2*pad;pw=ph*da}
  if(f.format==='full'&&on){/* заполнить рамку */ if(pw<fw){pw=fw;ph=pw/da} if(ph<fh){ph=fh;pw=ph*da}}
  Object.assign(fr.style,{width:fw+'px',height:fh+'px',display:'flex',alignItems:'center',justifyContent:'center',overflow:'hidden',
    background:on?(f.kind==='poster'?`linear-gradient(160deg,${f.color},color-mix(in srgb,${f.color} 45%,#000))`:f.color):'transparent',boxShadow:on?'':'none'});
  pic.style.width=pw+'px';pic.style.height=ph+'px';
  const w=odd?ph:pw,h=odd?pw:ph;
  /* наклон: увеличиваем снимок, чтобы повёрнутый кадр закрыл рамку без пустых углов (как «Выровнять» в галерее) */
  const ph_=totalDeg()*Math.PI/180,ac=Math.abs(Math.cos(ph_)),as=Math.abs(Math.sin(ph_));
  const k=S.crop.angle?Math.max((pw*ac+ph*as)/w,(pw*as+ph*ac)/h):1;
  BOX={w,h,k};
  rot.classList.toggle('flip',!!S.crop.flip);rot.style.setProperty('--k',k);
  pic.classList.toggle('tilted',!!S.crop.angle);
  Object.assign(rot.style,{width:w+'px',height:h+'px',left:(pw-w)/2+'px',top:(ph-h)/2+'px',right:'auto',bottom:'auto',
    transform:`rotate(${totalDeg()}deg) scale(${k}) scaleX(${S.crop.flip?-1:1})`});
  const dpr=Math.min(2,devicePixelRatio||1);
  ink.width=Math.round(w*dpr);ink.height=Math.round(h*dpr);
  drawInk();overlay();
}
/* экран → доли исходника */
function toLocal(e){
  const b=pic.getBoundingClientRect();
  let dx=e.clientX-(b.left+b.width/2),dy=e.clientY-(b.top+b.height/2);
  const a=-totalDeg()*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
  let x=dx*c-dy*s,y=dx*s+dy*c;
  if(S.crop.flip)x=-x;
  const k=BOX.k||1;
  return [x/(BOX.w*k)+.5,y/(BOX.h*k)+.5];
}
/* сила стиля: каждую функцию фильтра тянем от нейтрального значения к пресету */
function scaleCss(css,k){return css.replace(/([a-z-]+)\(([-\d.]+)(deg)?\)/g,(m,f,v,u)=>{const id=/^(brightness|contrast|saturate)$/.test(f)?1:0;return `${f}(${r3(id+(+v-id)*k)}${u||''})`})}
function filterCss(){
  const a=S.adjust,st=S.style.preset?scaleCss(STYLE_BY[S.style.preset].css,S.style.strength/100):'';
  const f=[];
  if(a.bright)f.push(`brightness(${1+a.bright/200})`);
  if(a.contrast)f.push(`contrast(${1+a.contrast/200})`);
  if(a.sat)f.push(`saturate(${1+a.sat/100})`);
  if(a.temp)f.push(`sepia(${Math.max(0,a.temp)/250})`,`hue-rotate(${-a.temp/8}deg)`);
  if(a.tint)f.push(`hue-rotate(${a.tint/4}deg)`);
  if(a.hl)f.push(`brightness(${1+a.hl/400})`);
  if(a.shadow)f.push(`contrast(${1-a.shadow/400})`);
  if(a.enh)f.push(`contrast(${1+a.enh/300})`,`saturate(${1+a.enh/250})`);
  if(a.fade)f.push(`contrast(${1-a.fade/250})`,`brightness(${1+a.fade/500})`);
  if(S.effect.glow)f.push(`brightness(${1+S.effect.glow/400})`,`saturate(${1+S.effect.glow/300})`);
  if(S.beauty.whiten)f.push(`brightness(${1+S.beauty.whiten/600})`);
  return (st+' '+f.join(' ')).trim()||'none';
}

const PAT={};
function pattern(ctx,type,w,h){
  const k=type+w+'x'+h; if(PAT[k])return ctx.createPattern(PAT[k],'no-repeat');
  const c=document.createElement('canvas');c.width=Math.max(1,Math.round(w));c.height=Math.max(1,Math.round(h));
  const x=c.getContext('2d');
  if(type==='pixel'){
    const t=document.createElement('canvas'),q=Math.max(2,Math.round(w/22));t.width=Math.ceil(w/q);t.height=Math.ceil(h/q);
    t.getContext('2d').drawImage(img,0,0,t.width,t.height);x.imageSmoothingEnabled=false;x.drawImage(t,0,0,c.width,c.height);
  }else if(type==='blur'){x.filter=`blur(${Math.max(4,w/60)}px)`;x.drawImage(img,-10,-10,c.width+20,c.height+20)}
  else{x.fillStyle='rgba(30,30,30,.55)';x.fillRect(0,0,c.width,c.height);x.strokeStyle='rgba(255,255,255,.55)';x.lineWidth=2;
    for(let i=-c.height;i<c.width;i+=9){x.beginPath();x.moveTo(i,c.height);x.lineTo(i+c.height,0);x.stroke()}}
  PAT[k]=c;return ctx.createPattern(c,'no-repeat');
}
function line(ctx,pts,w,h){
  ctx.beginPath();
  pts.forEach((p,i)=>i?ctx.lineTo(p[0]*w,p[1]*h):ctx.moveTo(p[0]*w,p[1]*h));
  if(pts.length===1)ctx.lineTo(pts[0][0]*w+.1,pts[0][1]*h+.1);
}
function drawInk(){
  const ctx=ink.getContext('2d'),w=BOX.w,h=BOX.h,d=ink.width/(w||1);
  ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,ink.width,ink.height);ctx.setTransform(d,0,0,d,0,0);
  ctx.lineCap='round';ctx.lineJoin='round';
  for(const s of S.mosaic.strokes){ctx.save();ctx.strokeStyle=pattern(ctx,s.type,w,h);ctx.lineWidth=s.w*w;line(ctx,s.pts,w,h);ctx.stroke();ctx.restore()}
  for(const s of S.ai.erase){ctx.save();ctx.strokeStyle='rgba(127,184,164,.55)';ctx.lineWidth=s.w*w;line(ctx,s.pts,w,h);ctx.stroke();ctx.restore()}
  for(const s of S.draw.strokes){
    const lw=s.w*w;ctx.save();ctx.strokeStyle=s.color;ctx.lineWidth=lw;
    if(s.brush==='marker'){ctx.globalAlpha=.45;ctx.lineWidth=lw*1.8;ctx.lineCap='square'}
    if(s.brush==='crayon'){ctx.globalAlpha=.85;ctx.setLineDash([lw*.35,lw*.3])}
    if(s.brush==='dash'){ctx.setLineDash([lw*2.2,lw*1.8])}
    if(s.brush==='neon'){ctx.shadowColor=s.color;ctx.shadowBlur=lw*2.2;line(ctx,s.pts,w,h);ctx.stroke();ctx.strokeStyle='rgba(255,255,255,.9)';ctx.lineWidth=lw*.45}
    line(ctx,s.pts,w,h);ctx.stroke();ctx.restore();
  }
}
const HANDLES='<i class="hdl" data-h="nw"></i><i class="hdl" data-h="ne"></i><i class="hdl" data-h="sw"></i><i class="hdl" data-h="se"></i>';
const box=r=>`left:${r.x*100}%;top:${r.y*100}%;width:${r.w*100}%;height:${r.h*100}%`;
function overlay(){
  let h='';const t=U.tab;
  if(S.crop.rect)h+=`<div class="crop${t==='crop'?'':' ghost'}" data-k="crop" style="${box(S.crop.rect)}">${t==='crop'?HANDLES:''}</div>`;
  if(t==='region'||S.region.length){
    S.region.forEach((r,i)=>{if(!r.rect)return;const cur=U.cur&&U.cur.id===r.id;
      const lb=`${i+1}. ${ACT_BY[r.action]}${r.comment?': '+r.comment:''}`;
      h+=`<div class="rg${cur?' cur':''}${r.rect.y<.09?' top':''}" data-k="rg" data-id="${r.id}" style="${box(r.rect)}"><span class="rg__lb">${esc(lb)}</span>${cur&&t==='region'?HANDLES:''}</div>`});
  }
  for(const x of S.text){
    const cur=U.cur&&U.cur.id===x.id&&t==='text';
    h+=`<div class="tx st-${x.style}${x.kind==='wm'?' wm':''}${cur?' cur':''}" data-k="tx" data-id="${x.id}" style="left:${x.x*100}%;top:${x.y*100}%;font-size:${x.size*BOX.h}px;color:${x.color}">${esc(x.text||' ')}</div>`;
  }
  if(S.cutout.point)h+=`<div class="pin" style="left:${S.cutout.point[0]*100}%;top:${S.cutout.point[1]*100}%"></div>`;
  const tl=S.effect.tilt;
  if(tl){const b=(tl/100*6).toFixed(1);
    h+=`<div class="tlt" style="top:0;height:32%;backdrop-filter:blur(${b}px);-webkit-backdrop-filter:blur(${b}px);mask-image:linear-gradient(#000 40%,transparent)"></div><div class="tlt" style="bottom:0;height:32%;backdrop-filter:blur(${b}px);-webkit-backdrop-filter:blur(${b}px);mask-image:linear-gradient(transparent,#000 60%)"></div>`}
  ov.innerHTML=h;
}
function preview(){
  img.style.filter=filterCss();
  const v=$('#vig');v.style.opacity=S.adjust.vig/100;v.style.background='radial-gradient(ellipse at center,transparent 42%,rgba(0,0,0,.88) 100%)';
  $('#grain').style.opacity=(S.effect.grain/100*.7).toFixed(2);
}
/* жесты на кадре */
let G=null;
const cp=p=>[clamp(p[0],0,1),clamp(p[1],0,1)];
function ratioN(aspect){const a=ASPECT.find(x=>x[0]===aspect);return a&&a[2]?a[2]*U.nh/U.nw:null}
function fitAspect(ra){let w=.9,h=w/ra;if(h>.9){h=.9;w=h*ra}return{x:(1-w)/2,y:(1-h)/2,w,h}}
function spanRect(o,p,ra){
  p=cp(p);let w=Math.abs(p[0]-o[0]),h=Math.abs(p[1]-o[1]);
  if(ra){if(w/h>ra)w=h*ra;else h=w/ra}
  return{x:p[0]<o[0]?o[0]-w:o[0],y:p[1]<o[1]?o[1]-h:o[1],w,h};
}
function opposite(r,hd){return[hd.includes('w')?r.x+r.w:r.x,hd.includes('n')?r.y+r.h:r.y]}
function hit(s,p){const R=Math.max(14,s.w*BOX.w/2+8);return s.pts.some(q=>Math.hypot((q[0]-p[0])*BOX.w,(q[1]-p[1])*BOX.h)<R)}
function eraseAt(arr,p){const n=arr.length;for(let i=arr.length-1;i>=0;i--)if(hit(arr[i],p))arr.splice(i,1);return n!==arr.length}
function strokeArr(){return U.tab==='draw'?S.draw.strokes:U.tab==='mosaic'?S.mosaic.strokes:S.ai.erase}

stage.addEventListener('pointerdown',e=>{
  if(e.button>0||e.target.closest('.cmp')||!U.nw)return;
  const p=toLocal(e),t=U.tab,hd=e.target.closest('.hdl'),el=e.target.closest('[data-k]');
  G={p0:p,x0:e.clientX,y0:e.clientY,moved:false,marked:false};
  if(t==='crop'){
    const r=S.crop.rect;
    if(r&&hd){G.m='resize';G.o=opposite(r,hd.dataset.h);G.ra=ratioN(S.crop.aspect);G.tgt='crop'}
    else if(r&&el&&el.dataset.k==='crop'){G.m='move';G.r0={...r};G.tgt='crop'}
    else if(!ratioN(S.crop.aspect)){G.m='new';G.o=cp(p);G.tgt='crop'}
    else{G=null;return}
  }else if(t==='region'){
    const id=el&&el.dataset.id,r=id&&S.region.find(x=>x.id===id);
    if(r){U.cur={op:'region',id};G.tgt=r;if(hd){G.m='resize';G.o=opposite(r.rect,hd.dataset.h)}else{G.m='move';G.r0={...r.rect}}overlay();renderList();renderOpts()}
    else{G.m='new';G.o=cp(p);G.tgt=null}
  }else if(t==='text'){
    const id=el&&el.dataset.id,x=id&&S.text.find(q=>q.id===id);
    if(x){U.cur={op:'text',id};G.m='tmove';G.tgt=x;G.r0={x:x.x,y:x.y};overlay();renderList();renderOpts()}
    else{if(U.cur&&U.cur.op==='text'){U.cur=null;overlay();renderList();renderOpts()}G=null;return}
  }else if(t==='draw'||t==='mosaic'||(t==='ai'&&U.aiErase)){
    const er=(t==='draw'&&U.brush==='eraser')||(t==='mosaic'&&U.mos==='eraser');
    mark();G.marked=true;
    if(er){G.m='erase';if(eraseAt(strokeArr(),p))drawInk()}
    else{
      const s=t==='draw'?{brush:U.brush,color:U.color,w:U.bw/BOX.w,pts:[cp(p)]}:t==='mosaic'?{type:U.mos,w:U.mw/BOX.w,pts:[cp(p)]}:{w:U.mw/BOX.w,pts:[cp(p)]};
      if(t==='ai'&&!S.ai.tasks.includes('erase'))S.ai.tasks.push('erase');
      strokeArr().push(s);G.m='stroke';G.s=s;drawInk();
    }
  }else if(t==='cutout'){mark();S.cutout.point=cp(p).map(r3);overlay();renderList();renderTabs();G=null;return}
  else{G=null;return}
  stage.setPointerCapture(e.pointerId);e.preventDefault();
});
stage.addEventListener('pointermove',e=>{
  if(!G)return;
  const p=toLocal(e);
  if(!G.moved&&Math.hypot(e.clientX-G.x0,e.clientY-G.y0)<5&&G.m!=='stroke'&&G.m!=='erase')return;
  G.moved=true;
  if(!G.marked&&G.m!=='stroke'&&G.m!=='erase'){mark();G.marked=true}
  const dx=p[0]-G.p0[0],dy=p[1]-G.p0[1];
  if(G.m==='stroke'){const l=G.s.pts[G.s.pts.length-1];if(Math.hypot((p[0]-l[0])*BOX.w,(p[1]-l[1])*BOX.h)>2){G.s.pts.push(cp(p));drawInk()}return}
  if(G.m==='erase'){if(eraseAt(strokeArr(),p))drawInk();return}
  if(G.m==='tmove'){G.tgt.x=r3(clamp(G.r0.x+dx,0,1));G.tgt.y=r3(clamp(G.r0.y+dy,0,1));overlay();return}
  let r;
  if(G.m==='move'){r={...G.r0,x:clamp(G.r0.x+dx,0,1-G.r0.w),y:clamp(G.r0.y+dy,0,1-G.r0.h)}}
  else r=spanRect(G.o,p,G.m==='resize'?G.ra:null);
  if(G.tgt==='crop'){if(G.m==='new'&&S.crop.aspect==='orig')S.crop.aspect='free';S.crop.rect=r}
  else if(G.m==='new'&&!G.tgt){const reg={id:nid('r'),rect:r,action:'comment',comment:''};S.region.push(reg);G.tgt=reg;U.cur={op:'region',id:reg.id}}
  else G.tgt.rect=r;
  overlay();
});
function endG(){
  if(!G)return;const g=G;G=null;
  const tiny=r=>r&&(r.w<.02||r.h<.02);
  if(g.tgt==='crop'&&tiny(S.crop.rect)){S.crop.rect=null;if(S.crop.aspect==='free')S.crop.aspect='orig'}
  if(g.tgt&&g.tgt.rect&&tiny(g.tgt.rect)){S.region=S.region.filter(x=>x!==g.tgt);U.cur=null}
  if(U.tab==='region'&&g.m==='new'&&!g.moved&&U.cur){U.cur=null}
  if(g.tgt&&g.tgt.rect)for(const k in g.tgt.rect)g.tgt.rect[k]=r3(g.tgt.rect[k]);
  if(S.crop.rect)for(const k in S.crop.rect)S.crop.rect[k]=r3(S.crop.rect[k]);
  if(g.s)g.s.pts=g.s.pts.map(q=>q.map(r3));
  overlay();renderList();renderTabs();renderOpts();
  if(U.cur&&U.cur.op==='region'&&g.m==='new'&&g.moved&&!TOUCH){const ta=$(`.ec[data-id="${U.cur.id}"] textarea`);ta&&ta.focus()}
}
stage.addEventListener('pointerup',endG);stage.addEventListener('pointercancel',endG);
const cmp=$('#cmpBtn');
const orig=on=>stage.classList.toggle('orig',on);
cmp.addEventListener('pointerdown',e=>{e.stopPropagation();orig(true)});
['pointerup','pointerleave','pointercancel'].forEach(n=>cmp.addEventListener(n,()=>orig(false)));
/* вкладки и панель опций */
/* мягкие переносы для подписей плиток: гласная|согласная+гласная и согласная|согласная, не ближе 3 букв к краям */
const VW='аеёиоуыэюяaeiouy';
const hy=t=>t.split(' ').map(w=>{if(w.length<9)return w;let o='';for(let i=0;i<w.length;i++){const a=w[i-1]||'',b=w[i],c=w[i+1]||'',l=a.toLowerCase(),m=b.toLowerCase(),n=c.toLowerCase();
  if(i>=3&&w.length-i>=3&&/[а-яё]/i.test(m)&&!/[ьъй]/.test(m)&&((VW.includes(l)&&!VW.includes(m)&&VW.includes(n))||(!VW.includes(l)&&!/[ьъй]/.test(l)&&!VW.includes(m)&&VW.includes(n)&&VW.includes((w[i-2]||'').toLowerCase()))))o+='\u00AD';o+=b}return o}).join(' ');
const chip=(a,v,label,icon,on,dot)=>`<button class="chip${on?' on':''}" type="button" data-a="${a}" data-v="${v}" title="${esc(label)}"><span class="ic">${icon}${dot?'<i class="dot"></i>':''}</span><span lang="ru">${esc(hy(label))}</span></button>`;
const fillVars=(v,min,max)=>{const p=(v-min)/(max-min)*100,z=min<0?-min/(max-min)*100:0;return [Math.min(p,z)+'%',Math.max(p,z)+'%']};
const slider=(label,key,val,min,max,fmt)=>{const [a,b]=fillVars(val,min,max);return `<div class="sl"><label>${label}</label><span class="tr${min<0?' bi':''}"><input type="range" data-s="${key}" min="${min}" max="${max}" value="${val}" style="--a:${a};--b:${b}" aria-label="${esc(label)}"></span><b>${fmt?fmt(val):val}</b></div>`};
const seg=(a,items,cur)=>`<div class="seg">${items.map(i=>`<button type="button" data-a="${a}" data-v="${i[0]}" class="${i[0]===cur?'on':''}">${i[1]}</button>`).join('')}</div>`;
const pal=(a,colors,cur,custom)=>`<div class="pal">${colors.map(c=>`<button class="sw${c===cur?' on':''}" type="button" data-a="${a}" data-v="${c}" style="background:${c}" title="${c}"></button>`).join('')}${custom?`<label class="sw rb${colors.includes(cur)?'':' on'}" title="Свой цвет"><input type="color" data-c="${a}" value="${cur}"></label>`:''}</div>`;
const rectIc=r=>{if(!r)return ic('s-free');let w=16,h=w/r;if(h>16){h=16;w=h*r}return `<svg class="i" viewBox="0 0 24 24"><rect x="${12-w/2}" y="${12-h/2}" width="${w}" height="${h}" rx="2"/></svg>`};
const isOn={
  ai:()=>S.ai.tasks.length>0,
  crop:()=>!!(S.crop.rect||S.crop.rot||S.crop.flip||S.crop.angle),
  style:()=>!!S.style.preset,
  adjust:()=>ADJ.some(a=>S.adjust[a[0]]),
  beauty:()=>BEAUTY.some(a=>S.beauty[a[0]]),
  draw:()=>S.draw.strokes.length>0, text:()=>S.text.length>0, mosaic:()=>S.mosaic.strokes.length>0,
  frame:()=>S.frame.kind!=='none', effect:()=>EFF.some(a=>S.effect[a[0]]),
  cutout:()=>!!S.cutout.point, region:()=>S.region.length>0
};
function renderTabs(){
  tabs.innerHTML=TABS.map(([k,l,i])=>`<button class="tab${U.tab===k?' on':''}" type="button" data-tab="${k}">${ic(i)}<span>${l}</span>${isOn[k]()?'<i class="dot"></i>':''}</button>`).join('');
}
const HINTS={crop:()=>S.crop.rect?'Тяните рамку или её углы':ratioN(S.crop.aspect)?'':'Проведите по фото, чтобы выделить кадр',
  region:()=>'Проведите по фото, чтобы выделить область',draw:()=>U.brush==='eraser'?'Проведите по штриху, чтобы стереть':'Рисуйте пальцем или мышью',
  text:()=>S.text.length?'Перетаскивайте надпись по фото':'',mosaic:()=>U.mos==='eraser'?'Проведите по мазку, чтобы стереть':'Закрасьте то, что нужно скрыть',
  cutout:()=>S.cutout.point?'':'Коснитесь объекта, который нужно вырезать',ai:()=>U.aiErase?'Закрасьте объект, который нужно убрать':''};
function curItem(op){return U.cur&&U.cur.op===op?S[op].find(x=>x.id===U.cur.id):null}
function renderOpts(){
  const t=U.tab;let h='';
  stage.dataset.tab=t;stage.classList.toggle('paint',t==='ai'&&U.aiErase);
  hint.textContent=HINTS[t]?HINTS[t]():'';
  if(t==='ai'){
    h+=`<div class="row">${AI.map(a=>chip('ai',a[0],a[1],ic(a[2]),a[0]==='erase'?(U.aiErase||S.ai.tasks.includes('erase')):S.ai.tasks.includes(a[0]))).join('')}</div>`;
    h+=U.aiErase?slider('Кисть','mw',U.mw,8,90,v=>v+' px'):`<div class="desc">Выберите, что сделать ИИ, можно несколько. «Стереть объект» включает кисть.</div>`;
  }else if(t==='crop'){
    h+=`<div class="flex"><button class="mini" type="button" data-a="rot">${ic('s-rot')}<span class="only-d">Повернуть</span></button><button class="mini" type="button" data-a="flip">${ic('s-flip')}<span class="only-d">Отразить</span></button>${slider('Наклон','angle',S.crop.angle,-45,45,FMT.angle).replace('<div class="sl">','<div class="sl" style="flex:1">')}</div>`;
    h+=`<div class="row">${ASPECT.map(a=>chip('aspect',a[0],a[1],a[0]==='orig'?ic('s-reset'):rectIc(a[2]),S.crop.aspect===a[0])).join('')}</div>`;
  }else if(t==='style'){
    h+=seg('scat',Object.keys(STYLES).map(c=>[c,c]),U.styleCat);
    h+=`<div class="row"><button class="thumb${S.style.preset?'':' on'}" type="button" data-a="style" data-v=""><img src="${img.src}" alt=""><span>Исходный</span></button>${STYLES[U.styleCat].map(s=>`<button class="thumb${S.style.preset===s[0]?' on':''}" type="button" data-a="style" data-v="${s[0]}"><img src="${img.src}" alt="" style="filter:${s[2]}"><span>${s[1]}</span></button>`).join('')}</div>`;
    if(S.style.preset)h+=slider('Сила','strength',S.style.strength,0,100,v=>v+'%');
  }else if(t==='adjust'){
    const a=ADJ.find(x=>x[0]===U.adj);
    h+=slider(a[1],'adj',S.adjust[a[0]],a[3],100,FMT.adj);
    h+=`<div class="row">${ADJ.map(x=>chip('adj',x[0],x[1],ic(x[2]),U.adj===x[0],S.adjust[x[0]])).join('')}</div>`;
  }else if(t==='beauty'){
    const a=BEAUTY.find(x=>x[0]===U.beauty);
    h+=slider(a[1],'beauty',S.beauty[a[0]],0,100,v=>v||'Откл.');
    h+=`<div class="row">${BEAUTY.map(x=>chip('beauty',x[0],x[1],ic(x[2]),U.beauty===x[0],S.beauty[x[0]])).join('')}</div>`;
  }else if(t==='draw'){
    h+=pal('color',COLORS,U.color,true)+slider('Толщина','bw',U.bw,2,40,v=>v+' px');
    h+=`<div class="row">${BRUSH.map(b=>chip('brush',b[0],b[1],ic(b[2]),U.brush===b[0])).join('')}</div>`;
  }else if(t==='text'){
    const x=curItem('text');
    h+=seg('tkind',TKIND,x?x.kind:U.tkind);
    h+=`<div class="flex"><input class="tinput" id="tIn" maxlength="80" placeholder="Текст надписи" value="${x?esc(x.text):''}"><button class="mini" type="button" data-a="tadd">${ic('t-text')}Добавить</button>${x?`<button class="ibtn" type="button" data-a="tdel" title="Удалить надпись">${ic('i-trash')}</button>`:''}</div>`;
    h+=`<div class="flex" style="flex-wrap:wrap">${pal('tcolor',COLORS,x?x.color:U.tcolor,true)}${seg('tstyle',TSTYLE,x?x.style:U.tstyle)}</div>`;
    if(x)h+=slider('Размер','tsize',Math.round(x.size*100),3,25,v=>v+'%');
  }else if(t==='mosaic'){
    h+=slider('Толщина кисти','mw',U.mw,8,90,v=>v+' px');
    h+=`<div class="row">${MOS.map(m=>chip('mos',m[0],m[1],ic(m[2]),U.mos===m[0])).join('')}</div>`;
  }else if(t==='frame'){
    h+=seg('fkind',FKIND,S.frame.kind);
    if(S.frame.kind!=='none'){
      h+=`<div class="row">${FFORMAT.map(f=>chip('fformat',f[0],f[1],f[0]==='orig'?ic('t-frame'):f[0]==='full'?ic('s-free'):rectIc(f[2]),S.frame.format===f[0])).join('')}</div>`;
      h+=pal('fcolor',FCOLORS,S.frame.color,true);
    }else h+=`<div class="desc">Рамка добавляет поля вокруг фото и меняет формат кадра.</div>`;
  }else if(t==='effect'){
    const a=EFF.find(x=>x[0]===U.eff);
    h+=slider(a[1],'eff',S.effect[a[0]],0,100,v=>v||'Откл.');
    h+=`<div class="row">${EFF.map(x=>chip('eff',x[0],x[1],ic(x[2]),U.eff===x[0],S.effect[x[0]])).join('')}</div>`;
  }else if(t==='cutout'){
    h+=`<div class="flex">${seg('cutbg',CUTBG,S.cutout.bg)}${S.cutout.point?`<button class="ibtn" type="button" data-a="cutreset" title="Сбросить">${ic('i-trash')}</button>`:''}</div>`;
    h+=S.cutout.bg==='color'?pal('cutcolor',FCOLORS,S.cutout.color,true):`<div class="desc">Агент выделит объект под точкой и заменит фон.</div>`;
  }else if(t==='region'){
    const r=curItem('region');
    h+=`<div class="flex"><button class="mini" type="button" data-a="radd">${ic('t-region')}Новая область</button><span class="desc">${S.region.length?'Областей: '+S.region.length:'Или проведите по фото'}</span></div>`;
    if(r)h+=`<div class="flex"><select class="sel2" data-c="ract" style="flex:0 1 180px">${RACT.map(a=>`<option value="${a[0]}"${a[0]===r.action?' selected':''}>${a[1]}</option>`).join('')}</select><input class="tinput" id="rIn" placeholder="Комментарий к области" value="${esc(r.comment)}"><button class="ibtn" type="button" data-a="rdel" title="Удалить область">${ic('i-trash')}</button></div>`;
  }
  opts.innerHTML=h;
}
/* список правок */
const plural=(n,f)=>{const a=n%10,b=n%100;return n+' '+(a===1&&b!==11?f[0]:a>=2&&a<=4&&(b<10||b>=20)?f[1]:f[2])};
const PX=r=>({x:Math.round(r.x*U.nw),y:Math.round(r.y*U.nh),w:Math.round(r.w*U.nw),h:Math.round(r.h*U.nh)});
const rtxt=r=>{const p=PX(r);return `${p.w}×${p.h} px от точки ${p.x},${p.y}`};
const lab=(arr,k)=>(arr.find(a=>a[0]===k)||[k,k])[1];
const NOTES=()=>S.notes||(S.notes={});
const today=()=>new Date().toLocaleDateString('ru-RU');
const lc=s=>s.charAt(0).toLowerCase()+s.slice(1);
function edits(){
  const E=[],N=NOTES();
  const add=(tab,id,title,sum,data)=>{const d={id,op:tab,...data};if(!d.comment)delete d.comment;const n=(N[id]||'').trim();if(n&&!d.comment)d.comment=n;E.push({tab,id,title,sum,data:d})};
  if(isOn.ai()){const t=S.ai.tasks,d={tasks:t.slice()};let s=t.map(k=>lab(AI,k)).join(', ');
    if(S.ai.erase.length){d.erase_mask={strokes:S.ai.erase.map(q=>({width_px:Math.round(q.w*U.nw),points:q.pts}))};s+=` · кисть: ${plural(S.ai.erase.length,['мазок','мазка','мазков'])}`}
    add('ai','ai','ИИ-ретушь',s,d)}
  if(isOn.crop()){const c=S.crop,p=[],d={};
    if(c.rect){p.push(`${ratioN(c.aspect)?'кадр '+c.aspect:'свободный кадр'}: ${rtxt(c.rect)}`);d.aspect=c.aspect;d.rect=c.rect;d.rect_px=PX(c.rect)}
    if(c.rot){p.push(`поворот на ${c.rot*90}° по часовой`);d.rotate_deg=c.rot*90}
    if(c.flip){p.push('отразить по горизонтали');d.flip='horizontal'}
    if(c.angle){p.push(`наклон ${sgn(c.angle)}° с подрезкой краёв без пустых углов`);d.straighten_deg=c.angle;d.straighten_fill='crop_to_fit'}
    add('crop','crop','Кадрирование',p.join(', '),d)}
  if(isOn.style()){const st=STYLE_BY[S.style.preset];
    add('style','style','Стиль',`${st.cat} · ${st.name}, сила ${S.style.strength}%`,{preset:S.style.preset,name:st.name,category:st.cat,strength:S.style.strength/100})}
  if(isOn.adjust()){const p=ADJ.filter(a=>S.adjust[a[0]]);
    add('adjust','adjust','Коррекция',p.map(a=>`${a[1]} ${sgn(S.adjust[a[0]])}`).join(', '),{params:Object.fromEntries(p.map(a=>[a[0],S.adjust[a[0]]])),scale:'-100..100, 0 — без изменений'})}
  if(isOn.beauty()){const p=BEAUTY.filter(a=>S.beauty[a[0]]);
    add('beauty','beauty','Красота',p.map(a=>`${a[1]} ${S.beauty[a[0]]}`).join(', '),{params:Object.fromEntries(p.map(a=>[a[0],S.beauty[a[0]]])),scale:'0..100'})}
  if(isOn.draw()){const s=S.draw.strokes,br=[...new Set(s.map(q=>lc(lab(BRUSH,q.brush))))],co=[...new Set(s.map(q=>q.color))];
    add('draw','draw','Наброски',`${plural(s.length,['штрих','штриха','штрихов'])}: ${br.join(', ')} · ${co.join(' ')}`,{strokes:s.map(q=>({brush:q.brush,color:q.color,width_px:Math.round(q.w*U.nw),points:q.pts}))})}
  for(const x of S.text)
    add('text',x.id,'Текст',`«${x.text}» · ${lab(TKIND,x.kind)}, ${lc(lab(TSTYLE,x.style))}, ${Math.round(x.size*100)}% высоты · центр ${Math.round(x.x*100)}%, ${Math.round(x.y*100)}%`,
      {kind:x.kind,text:x.text,center:{x:x.x,y:x.y},center_px:{x:Math.round(x.x*U.nw),y:Math.round(x.y*U.nh)},size:x.size,size_px:Math.round(x.size*U.nh),color:x.color,style:x.style});
  if(isOn.mosaic()){const s=S.mosaic.strokes,ty=[...new Set(s.map(q=>lc(lab(MOS,q.type))))];
    add('mosaic','mosaic','Мозаика',`${plural(s.length,['мазок','мазка','мазков'])}: ${ty.join(', ')}`,{strokes:s.map(q=>({type:q.type,width_px:Math.round(q.w*U.nw),points:q.pts}))})}
  if(isOn.frame()){const f=S.frame;
    add('frame','frame','Рамка',`${lab(FKIND,f.kind)} · формат ${lc(lab(FFORMAT,f.format))} · ${f.color}`,{kind:f.kind,format:f.format,color:f.color})}
  if(isOn.effect()){const p=EFF.filter(a=>S.effect[a[0]]);
    add('effect','effect','Эффекты',p.map(a=>`${a[1]} ${S.effect[a[0]]}`).join(', '),{params:Object.fromEntries(p.map(a=>[a[0],S.effect[a[0]]])),scale:'0..100'})}
  if(isOn.cutout()){const c=S.cutout,p=c.point;
    add('cutout','cutout','Вырезать объект',`объект в точке ${Math.round(p[0]*U.nw)},${Math.round(p[1]*U.nh)} px · фон: ${lc(lab(CUTBG,c.bg))}${c.bg==='color'?' '+c.color:''}`,
      {point:{x:p[0],y:p[1]},point_px:{x:Math.round(p[0]*U.nw),y:Math.round(p[1]*U.nh)},background:c.bg,...(c.bg==='color'?{color:c.color}:{})})}
  S.region.forEach((r,i)=>{if(!r.rect)return;
    add('region',r.id,`Область ${i+1}`,`${ACT_BY[r.action]} · ${rtxt(r.rect)}`,{rect:r.rect,rect_px:PX(r.rect),action:r.action,comment:r.comment.trim()})});
  return E;
}
function renderList(){
  const E=edits();
  list.innerHTML=E.length?E.map(e=>{
    const t=TAB[e.tab],cur=U.cur&&U.cur.id===e.id;let f='';
    if(e.tab==='region'){const r=S.region.find(x=>x.id===e.id);
      f=`<select class="sel2" data-rs="${e.id}">${RACT.map(a=>`<option value="${a[0]}"${a[0]===r.action?' selected':''}>${a[1]}</option>`).join('')}</select><textarea class="ta" data-rc="${e.id}" placeholder="Что сделать с этой областью">${esc(r.comment)}</textarea>`}
    else if(e.tab==='text'){const x=S.text.find(q=>q.id===e.id);f=`<input class="tinput" data-tt="${e.id}" maxlength="80" placeholder="Текст надписи" value="${esc(x.text)}">`}
    else f=`<input class="tinput" data-nt="${e.id}" placeholder="Комментарий агенту (необязательно)" value="${esc(NOTES()[e.id]||'')}">`;
    return `<div class="ec${cur?' cur':''}" data-id="${e.id}" data-tab="${e.tab}" style="--c:var(${COL[e.tab]})"><div class="ec__h"><span class="ec__ic">${ic(t.icon)}</span><span class="ec__t">${esc(e.title)}</span><button class="ibtn" type="button" data-del="${e.id}" data-dt="${e.tab}" title="Удалить правку">${ic('i-trash')}</button></div><div class="ec__s">${esc(e.sum)}</div>${f}</div>`;
  }).join(''):`<div class="empty">${ic('i-pro')}Правок пока нет. Выберите инструмент внизу и отметьте на фото, что поменять. Всё делается кликом или пальцем, клавиатура не нужна.</div>`;
  updBtns(E.length);
}
function updBtns(n){
  $('#undoBtn').disabled=!H.u.length;$('#redoBtn').disabled=!H.r.length;$('#clearBtn').disabled=!n;
  const can=n>0||!!$('#prompt').value.trim();$('#sendBtn').disabled=$('#sendM').disabled=!can;
  $('#cnt').textContent=n;const b=$('#listBadge');b.hidden=!n;b.textContent=n;
  $('#sendDesc').textContent=n?plural(n,['правка','правки','правок'])+' · новым файлом':'Добавьте правку или комментарий';
}
function all(){preview();fit();overlay();renderTabs();renderOpts();renderList()}
function delEdit(tab,id){
  mark();
  if(tab==='text')S.text=S.text.filter(x=>x.id!==id);
  else if(tab==='region')S.region=S.region.filter(x=>x.id!==id);
  else{S[tab]=blank()[tab];if(tab==='ai')U.aiErase=false}
  delete NOTES()[id];if(U.cur&&U.cur.id===id)U.cur=null;all();
}
function setTab(k){
  if(U.tab!==k){U.tab=k;if(U.cur&&U.cur.op!==k)U.cur=null}
  overlay();renderTabs();renderOpts();renderList();
  const b=tabs.querySelector('.tab.on');if(b){const l=b.offsetLeft-(tabs.clientWidth-b.offsetWidth)/2;tabs.scrollTo({left:l,behavior:'smooth'})}
}

/* действия панели */
function act(a,v,soft){
  const x=curItem('text');
  switch(a){
  case 'ai':
    if(v==='erase'){U.aiErase=!U.aiErase;if(!U.aiErase&&!S.ai.erase.length&&S.ai.tasks.includes('erase')){mark();S.ai.tasks=S.ai.tasks.filter(t=>t!=='erase')}}
    else{mark();const t=S.ai.tasks;S.ai.tasks=t.includes(v)?t.filter(q=>q!==v):[...t,v]}
    break;
  case 'rot':mark();S.crop.rot=(S.crop.rot+1)%4;break;
  case 'flip':mark();S.crop.flip=!S.crop.flip;break;
  case 'aspect':{mark();S.crop.aspect=v;const ra=ratioN(v);
    if(v==='orig')S.crop.rect=null;
    else if(ra){const r=fitAspect(ra);for(const k in r)r[k]=r3(r[k]);S.crop.rect=r}
    else if(!S.crop.rect)S.crop.rect={x:.05,y:.05,w:.9,h:.9};
    break}
  case 'scat':U.styleCat=v;break;
  case 'style':mark();S.style.preset=v||null;S.style.strength=100;break;
  case 'adj':U.adj=v;break;
  case 'beauty':U.beauty=v;break;
  case 'eff':U.eff=v;break;
  case 'color':U.color=v;break;
  case 'brush':U.brush=v;break;
  case 'mos':U.mos=v;break;
  case 'tkind':if(x){mark();x.kind=v;if(v==='date')x.text=today();else if(v==='wm'&&!x.text.trim())x.text='© AuGent'}U.tkind=v;break;
  case 'tadd':{mark();const inp=$('#tIn'),k=U.tkind,n=S.text.length;
    const t=(!x&&inp?inp.value.trim():'')||(k==='date'?today():k==='wm'?'© AuGent':'Текст');
    const nx={id:nid('t'),kind:k,text:t,x:k==='wm'?.8:.5,y:k==='wm'?.92:[.5,.66,.34][n%3],size:k==='wm'?.05:.08,color:U.tcolor,style:U.tstyle};
    S.text.push(nx);U.cur={op:'text',id:nx.id};break}
  case 'tdel':if(x){mark();S.text=S.text.filter(q=>q!==x);U.cur=null}break;
  case 'tcolor':if(x){mark();x.color=v}U.tcolor=v;break;
  case 'tstyle':if(x){mark();x.style=v}U.tstyle=v;break;
  case 'fkind':mark();S.frame.kind=v;break;
  case 'fformat':mark();S.frame.format=v;break;
  case 'fcolor':mark();S.frame.color=v;break;
  case 'cutbg':mark();S.cutout.bg=v;break;
  case 'cutcolor':mark();S.cutout.color=v;break;
  case 'cutreset':mark();S.cutout.point=null;break;
  case 'radd':{mark();const o=(S.region.length%4)*.05,r={id:nid('r'),rect:{x:r3(.3+o),y:r3(.3+o),w:.4,h:.3},action:'comment',comment:''};
    S.region.push(r);U.cur={op:'region',id:r.id};break}
  case 'rdel':{const r=curItem('region');if(r){mark();S.region=S.region.filter(q=>q!==r);U.cur=null}break}
  }
  if(soft){preview();fit();overlay();renderTabs();renderList()}else all();
}
const FMT={angle:v=>(v?sgn(v):0)+'°',strength:v=>v+'%',adj:v=>v?sgn(v):'0',beauty:v=>v||'Откл.',bw:v=>v+' px',tsize:v=>v+'%',mw:v=>v+' px',eff:v=>v||'Откл.'};
opts.addEventListener('click',e=>{const b=e.target.closest('[data-a]');if(b&&!b.matches('label'))act(b.dataset.a,b.dataset.v)});
opts.addEventListener('input',e=>{
  const t=e.target,s=t.closest('[data-s]');
  if(s){const k=s.dataset.s,v=+s.value;
    {const [a,b]=fillVars(v,+s.min,+s.max);s.style.setProperty('--a',a);s.style.setProperty('--b',b)}s.closest('.sl').querySelector('b').textContent=FMT[k](v);
    const ui=k==='bw'||k==='mw';if(!ui)mark();
    if(k==='angle'){S.crop.angle=v;fit()}
    else if(k==='strength')S.style.strength=v;
    else if(k==='adj')S.adjust[U.adj]=v;
    else if(k==='beauty')S.beauty[U.beauty]=v;
    else if(k==='eff'){S.effect[U.eff]=v;overlay()}
    else if(k==='tsize'){const x=curItem('text');if(x){x.size=v/100;overlay()}}
    else if(k==='bw')U.bw=v;else if(k==='mw')U.mw=v;
    if(!ui){preview();renderTabs();renderList()}
    return}
  if(t.type==='color'&&t.dataset.c){act(t.dataset.c,t.value,true);return}
  if(t.id==='tIn'){const x=curItem('text');if(x){mark();x.text=t.value;overlay();renderList()}}
  if(t.id==='rIn'){const r=curItem('region');if(r){mark();r.comment=t.value;overlay();renderList()}}
});
opts.addEventListener('change',e=>{const t=e.target;
  if(t.dataset.s&&['adj','beauty','eff'].includes(t.dataset.s))renderOpts();
  else if(t.type==='color')renderOpts();
  else if(t.dataset.c==='ract'){const r=curItem('region');if(r){mark();r.action=t.value;overlay();renderList()}}
});
opts.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.id==='tIn'){e.preventDefault();if(!curItem('text'))act('tadd');else e.target.blur()}});
tabs.addEventListener('click',e=>{const b=e.target.closest('[data-tab]');b&&setTab(b.dataset.tab)});
list.addEventListener('click',e=>{
  const d=e.target.closest('[data-del]');if(d){delEdit(d.dataset.dt,d.dataset.del);return}
  if(e.target.closest('input,textarea,select'))return;
  const c=e.target.closest('.ec');if(!c)return;
  const t=c.dataset.tab;U.cur=(t==='text'||t==='region')?{op:t,id:c.dataset.id}:null;
  if(innerWidth<=760)ed.classList.remove('side-on');
  U.tab=t;setTab(t);
});
list.addEventListener('input',e=>{const t=e.target,id=t.dataset.tt||t.dataset.rc||t.dataset.nt;if(!id)return;mark();
  if(t.dataset.tt){const x=S.text.find(q=>q.id===id);if(x)x.text=t.value}
  else if(t.dataset.rc){const r=S.region.find(q=>q.id===id);if(r)r.comment=t.value}
  else NOTES()[id]=t.value;
  overlay();
  const E=edits().find(q=>q.id===id),s=t.closest('.ec').querySelector('.ec__s');if(E&&s)s.textContent=E.sum;
  if(!opts.contains(R.activeElement))renderOpts();
});
list.addEventListener('change',e=>{const t=e.target;if(!t.dataset.rs)return;const r=S.region.find(q=>q.id===t.dataset.rs);
  if(r){mark();r.action=t.value;overlay();renderOpts();renderList()}});
$('#prompt').addEventListener('input',()=>updBtns(edits().length));

/* payload */
const APPLY=['crop','ai','cutout','adjust','style','beauty','effect','mosaic','region','draw','text','frame'];
function payload(E){return{kind:'image_edit_request',version:1,
  context:Object.assign({},MP.context),
  file:{...FILE,width:U.nw,height:U.nh},
  coord_base:'source',
  coord_note:'x, y, w, h в долях 0..1 от левого верхнего угла исходника, до поворота и кадрирования; *_px в пикселях исходника',
  apply_order:APPLY.filter(o=>E.some(e=>e.tab===o)),
  edits:E.map(e=>e.data),
  prompt:$('#prompt').value.trim(),
  output:{keep_original:true,format:'jpg'},
  client:{ui:'lightbox-pro-image/0.1',created_at:new Date().toISOString()}}}
function message(p,E){
  const L=[`Правки к изображению «${FILE.name}» (${U.nw}×${U.nh}), правок: ${E.length}.`,''];
  if(FILE.source==='local')L.splice(1,0,'Фото загружено с устройства и приложено к сообщению.');
  E.forEach((e,i)=>L.push(`${i+1}. ${e.title}: ${e.sum}${e.data.comment?' — '+e.data.comment:''}`));
  L.push('','Координаты считаются от левого верхнего угла исходного файла, до поворота и кадрирования. Пиксели указаны для исходного размера.');
  if(p.apply_order.length)L.push('Порядок применения: '+p.apply_order.map(o=>TAB[o].label).join(' → ')+'.');
  if(p.prompt)L.push('','Комментарий: '+p.prompt);
  L.push('','Исходник не трогать, результат новым файлом.');
  L.push('Разметка в JSON ниже (image_edit_request).');
  return L.join('\n');
}
function hlJson(s){return esc(s).replace(/(&quot;(?:\\.|(?!&quot;)[^\\])*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
  (m,str,colon,b)=>str?`<span class="${colon?'j-k':'j-s'}">${str}</span>${colon||''}`:b?`<span class="j-b">${m}</span>`:`<span class="j-n">${m}</span>`)}
let LAST=null;
const md=$('#md');
function setMd(v){$$('#mdSeg button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));$('#mdMsg').hidden=v!=='msg';$('#mdJson').hidden=v!=='json'}
function send(){
  const E=edits(),p=payload(E);
  if(!E.length&&!p.prompt){toast('Сначала добавьте правку');return}
  const m=message(p,E);console.log('[image_edit_request]',p);
  LAST={p,m,n:E.length};
  $('#mdMsg').textContent=m;$('#mdJson').innerHTML=hlJson(JSON.stringify(p,null,2));setMd('msg');md.classList.add('on');
}
$('#sendBtn').onclick=send;$('#sendM').onclick=send;
$('#mdSeg').addEventListener('click',e=>{const b=e.target.closest('button');b&&setMd(b.dataset.v)});
$('#mdClose').onclick=()=>md.classList.remove('on');
md.addEventListener('click',e=>{if(e.target===md)md.classList.remove('on')});
$('#mdCopy').onclick=()=>{if(!LAST)return;const t=JSON.stringify(LAST.p,null,2);
  (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast('JSON скопирован'),()=>toast('Не удалось скопировать'))};
$('#mdOk').onclick=async()=>{
  if(!LAST)return;
  const ok=MP.onSend?await MP.onSend({kind:'image',text:LAST.m,payload:LAST.p,count:LAST.n}):true;
  if(ok===false)return;
  md.classList.remove('on');closeEd(true);
};

/* служебное */
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');host.classList.add('toast-on');clearTimeout(toast.h);toast.h=setTimeout(()=>{t.classList.remove('on');host.classList.remove('toast-on')},1900)}
const lb={classList:{contains:()=>false,add(){},remove(){}}};
function dims(){$('#edDim').textContent=U.nw?`${U.nw}×${U.nh}`:''}
function onImg(){U.nw=img.naturalWidth;U.nh=img.naturalHeight;dims();all()}
img.addEventListener('load',onImg);
if(img.complete&&img.naturalWidth)onImg();
img.addEventListener('error',()=>{if(!img.src.startsWith('blob:'))toast('Не удалось загрузить '+FILE.name)});

/* лайтбокс и редактор */
function lockScroll(){}
function openLb(){lb.classList.add('on');lockScroll()}
function closeLb(){lb.classList.remove('on');lockScroll()}
function openEd(){
  lb.classList.add('on');ed.classList.add('on');lockScroll();
  requestAnimationFrame(()=>{fit();setTab(U.tab)});
}
function closeEd(toChat){if(!host.isConnected)return;
  ed.classList.remove('on','side-on');$('#md').classList.remove('on');
  lockScroll();MP.onClose&&MP.onClose(!!toChat);
}




$('#edBack').onclick=()=>closeEd(false);
$('#undoBtn').onclick=undo;
$('#redoBtn').onclick=redo;
$('#clearBtn').onclick=()=>{if(!edits().length)return;mark();S=blank();U.cur=null;U.aiErase=false;H.open=false;all();toast('Правки очищены. Ctrl+Z или ↶ вернёт их')};
$('#listBtn').onclick=()=>ed.classList.toggle('side-on');
$('#sideClose').onclick=()=>ed.classList.remove('side-on');
$('#scrim').onclick=()=>ed.classList.remove('side-on');


/* клавиатура: всё дублируется кнопками, это только ускорители */
__on(window,'keydown',e=>{const __t=e.composedPath&&e.composedPath()[0]||e.target;
  const md=$('#md');
  if(md.classList.contains('on')){if(e.key==='Escape')md.classList.remove('on');return}
  const inF=/^(INPUT|TEXTAREA|SELECT)$/.test(__t.tagName),mod=e.ctrlKey||e.metaKey;
  if(ed.classList.contains('on')){
    if(mod&&e.key==='Enter'){e.preventDefault();send();return}
    if(inF){if(e.key==='Escape')__t.blur();return}
    if(mod&&e.code==='KeyZ'){e.preventDefault();e.shiftKey?redo():undo();return}
    if(mod&&e.code==='KeyY'){e.preventDefault();redo();return}
    if(mod||e.altKey)return;
    if(e.key==='Escape'){
      if(ed.classList.contains('side-on'))ed.classList.remove('side-on');
      else if(U.cur){U.cur=null;overlay();renderOpts();renderList()}
      else closeEd(false);
      return}
    if((e.key==='Delete'||e.key==='Backspace')&&U.cur){e.preventDefault();delEdit(U.cur.op,U.cur.id);return}
    return}
  if(lb.classList.contains('on')&&!inF){
    if(e.key==='Escape')closeLb();
    else if(e.code==='KeyP'&&!mod&&!e.altKey){e.preventDefault();openEd()}
  }
});

/* размеры сцены: окно, поворот телефона, смена высоты панели опций */
let fitRaf=0;
/* затухание по краям прокручиваемых рядов: видно, что дальше есть ещё */
const edges=el=>{const l=el.scrollLeft>2,r=el.scrollLeft+el.clientWidth<el.scrollWidth-2;el.style.setProperty('--fl',l?'28px':'0px');el.style.setProperty('--fr',r?'28px':'0px')};
const allEdges=()=>{edges(tabs);opts.querySelectorAll('.row').forEach(edges)};
tabs.addEventListener('scroll',()=>edges(tabs),{passive:true});
opts.addEventListener('scroll',e=>{if(e.target.classList&&e.target.classList.contains('row'))edges(e.target)},{capture:true,passive:true});
if(window.MutationObserver){const mo=new MutationObserver(()=>requestAnimationFrame(allEdges));mo.observe(tabs,{childList:true});mo.observe(opts,{childList:true})}
const refit=()=>{cancelAnimationFrame(fitRaf);fitRaf=requestAnimationFrame(()=>{fit();allEdges()})};
__on(window,'resize',refit);
if(window.ResizeObserver)new ResizeObserver(refit).observe(stage);

/* старт */
if(TAB[P.get('tab')])U.tab=P.get('tab');
renderTabs();renderOpts();renderList();

host.__ipe={get S(){return S},U,edits,payload,message,act,setTab,undo,redo,send};

openEd();
}

/* ── общая часть ── */
const HAS_VIDEO = false;
let CUR = null;   // { host, off[], mo, kind }

function gatewayName() {
    return (document.body && document.body.dataset.gateway) === 'sigma' ? 'Sigma' : 'CatchPM';
}

/* message_id и имя файла из url ответа агента:
 * /api/chats/{chat}/image/{mid}/{name}?image_key=..  или  /api/chats/{chat}/files/{mid}/{name} */
function parseUrl(url) {
    const m = String(url || '').match(/\/api\/chats\/([^/]+)\/(?:image|files)\/([^/?#]+)\/([^/?#]+)/);
    if (!m) return null;
    const dec = (s) => { try { return decodeURIComponent(s); } catch (_) { return s; } };
    return { chat: dec(m[1]), message_id: dec(m[2]), name: dec(m[3]) };
}

function canEdit(item) {
    if (!item || !item.url || item.url === '#') return false;
    if (!parseUrl(item.url)) return false;     // только файлы из сообщений агента
    return item.isVideo ? HAS_VIDEO : true;
}

function close() {
    if (!CUR) return;
    const c = CUR; CUR = null;
    c.off.forEach((f) => { try { f(); } catch (_) {} });
    if (c.mo) c.mo.disconnect();
    try { c.host.__vpe && c.host.__vpe.destroy && c.host.__vpe.destroy(); } catch (_) {}
    c.host.remove();
}

function open(kind, item, ctx) {
    close();
    const host = document.createElement('div');
    host.className = 'media-pro-host';
    host.setAttribute('data-kind', kind);
    const syncTheme = () => {
        host.dataset.theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    };
    syncTheme();
    const mo = new MutationObserver(syncTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const R = host.attachShadow({ mode: 'open' });
    const st = document.createElement('style');
    st.textContent = kind === 'video' ? VID_CSS : IMG_CSS;
    R.appendChild(st);
    const wrap = document.createElement('template');
    wrap.innerHTML = kind === 'video' ? VID_HTML : IMG_HTML;
    R.appendChild(wrap.content);
    document.body.appendChild(host);
    const off = [];
    const __on = (target, type, fn, o) => { target.addEventListener(type, fn, o); off.push(() => target.removeEventListener(type, fn, o)); };
    CUR = { host, off, mo, kind };

    const meta = parseUrl(item.url) || {};
    const app = ctx && ctx.app;
    const file = {
        name: item.name || meta.name || (kind === 'video' ? 'video.mp4' : 'image'),
        message_id: meta.message_id || null,
        url: item.url,
    };
    if (item.imageKey) file.image_key = item.imageKey;
    const opts = {
        file,
        src: (ctx && ctx.src) || item.url,
        context: { source: gatewayName(), chat_id: (app && app.currentChat) || meta.chat || null },
        onClose: (toChat) => {
            close();
            if (toChat && ctx && typeof ctx.closeLightbox === 'function') ctx.closeLightbox();
        },
        onSend: async ({ text, payload }) => {
            if (!app || typeof app.sendMessage !== 'function') return false;
            if (app._runState && app._runState !== 'idle') {
                app.showToast?.('Агент ещё отвечает. Дождитесь ответа и отправьте правки.');
                return false;
            }
            if (!app.currentChat) { app.showToast?.('Откройте чат, чтобы отправить правки'); return false; }
            const body = text + '\n\n```json\n' + JSON.stringify(payload, null, 2) + '\n```';
            try { await app.sendMessage(body); } catch (e) { console.error('[media-pro] send', e); return false; }
            return true;
        },
    };
    try {
        if (kind === 'video') mountVideo(R, host, opts, __on);
        else {
            mountImage(R, host, opts, __on);
            const img = R.getElementById('img');
            const nm = R.getElementById('edName');
            if (nm) { nm.textContent = file.name; nm.title = file.name; }
            if (img) img.src = opts.src;
        }
    } catch (e) {
        console.error('[media-pro] mount', e);
        close();
        app?.showToast?.('Не удалось открыть Pro-редактор');
    }
}

/* Подключение к лайтбоксу приложения.
 * bindLightbox({ bar, before, app, getItem, getSrc, closeLightbox }) → refresh()
 * refresh() вызывается при каждом показе слайда: кнопка видна только для
 * файлов агента, которые умеем размечать. */
function bindLightbox(cfg) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'image-lightbox__pro';
    btn.title = 'Разметить правки для агента (P)';
    btn.setAttribute('aria-label', 'Pro-разметка');
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20l4-1 10.5-10.5a2.1 2.1 0 00-3-3L5 16z"/><path d="M14 7l3 3"/><path d="M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg><span>Pro</span>';
    btn.hidden = true;
    if (cfg.before && cfg.before.parentNode === cfg.bar) cfg.bar.insertBefore(btn, cfg.before);
    else cfg.bar.appendChild(btn);
    const go = (e) => {
        if (e) e.stopPropagation();
        const it = cfg.getItem();
        if (!canEdit(it)) return;
        const vid = cfg.bar.closest('.image-lightbox')?.querySelector('video');
        if (vid && !vid.paused) vid.pause();
        open(it.isVideo ? 'video' : 'image', it, {
            app: cfg.app,
            src: cfg.getSrc ? cfg.getSrc() : it.url,
            closeLightbox: cfg.closeLightbox,
        });
    };
    btn.addEventListener('click', go);
    const refresh = () => { btn.hidden = !canEdit(cfg.getItem()); };
    refresh();
    return { refresh, open: () => { if (!btn.hidden) go(); } };
}

window.MediaPro = {
    bindLightbox,
    canEdit,
    isOpen: () => !!CUR,
    close,
    _open: open,
};
})();
