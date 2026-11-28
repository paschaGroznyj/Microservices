/* Единый тултип UI: светлое на тёмном в светлой теме, тёмное на светлом в тёмной.
   Нативный title стилизовать нельзя, поэтому на hover/focus атрибут title
   переносится в data-ui-tip, а показывается общий #ui-tip (стили в theme.css).
   На тач-устройствах тултипов нет, как и у нативного title. */
(function () {
  'use strict';
  if (window.UiTip) return;
  var SHOW_DELAY = 350, GAP = 8, PAD = 8;
  var tip = null, cur = null, timer = 0, lastOff = 0;

  function el() {
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'ui-tip';
      tip.className = 'ui-tip';
      tip.setAttribute('role', 'tooltip');
      tip.setAttribute('data-export-ignore', '');
      document.body.appendChild(tip);
    }
    return tip;
  }

  // title мог быть выставлен JS-ом заново после прошлого показа: забираем свежий
  function take(t) {
    var v = t.getAttribute('title');
    if (v != null) {
      if (v.trim()) {
        t.setAttribute('data-ui-tip', v);
        if (!t.hasAttribute('aria-label') && !t.textContent.trim()) t.setAttribute('aria-label', v);
      }
      t.removeAttribute('title');
    }
    return t.getAttribute('data-ui-tip') || '';
  }

  function find(n) {
    while (n && n.nodeType === 1) {
      if (n.id === 'ui-tip') return null;
      if (n.hasAttribute('title') || n.hasAttribute('data-ui-tip')) return n;
      n = n.parentElement;
    }
    return null;
  }

  function place(t) {
    var e = el(), r = t.getBoundingClientRect();
    var vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
    e.style.left = '0px'; e.style.top = '0px';
    var w = e.offsetWidth, h = e.offsetHeight;
    var x = Math.max(PAD, Math.min(r.left + r.width / 2 - w / 2, vw - w - PAD));
    var y = r.top - h - GAP;
    if (y < PAD) y = r.bottom + GAP;
    if (y + h > vh - PAD) y = Math.max(PAD, vh - h - PAD);
    e.style.left = Math.round(x) + 'px';
    e.style.top = Math.round(y) + 'px';
  }

  function show(t) {
    var text = take(t);
    if (!text || !t.isConnected) return hide();
    var e = el();
    e.textContent = text;
    place(t);
    e.classList.add('on');
  }

  function hide() {
    clearTimeout(timer); timer = 0; cur = null;
    if (tip && tip.classList.contains('on')) { tip.classList.remove('on'); lastOff = Date.now(); }
  }

  function arm(t, delay) {
    if (t === cur) return;
    hide();
    // переход между соседними кнопками: следующий тултип без задержки
    var warm = Date.now() - lastOff < 300;
    cur = t;
    take(t); // снимаем title сразу, чтобы нативный тултип не успел появиться
    timer = setTimeout(function () { if (cur === t) show(t); }, warm ? 0 : delay);
  }

  document.addEventListener('pointerover', function (ev) {
    if (ev.pointerType === 'touch') return;
    var t = find(ev.target);
    if (!t) { if (cur && !cur.contains(ev.target)) hide(); return; }
    arm(t, SHOW_DELAY);
  }, true);
  document.addEventListener('pointerout', function (ev) {
    if (cur && !(ev.relatedTarget && cur.contains(ev.relatedTarget))) hide();
  }, true);
  document.addEventListener('focusin', function (ev) {
    var t = find(ev.target);
    try { if (t && ev.target.matches(':focus-visible')) arm(t, SHOW_DELAY); } catch (_) {}
  }, true);
  document.addEventListener('focusout', hide, true);
  document.addEventListener('pointerdown', hide, { capture: true, passive: true });
  document.addEventListener('wheel', hide, { capture: true, passive: true });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') hide(); }, true);
  window.addEventListener('scroll', hide, { capture: true, passive: true });
  window.addEventListener('blur', hide);

  window.UiTip = { hide: hide };
})();
