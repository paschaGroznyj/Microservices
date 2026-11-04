/* reasoning-clamp.js
   Каждый .reasoning-step в «Размышлениях» обрезается до 2 строк. Клик по шагу
   раскрывает или сворачивает его (выделение текста клик не считает).
   Если .reasoning-body стал выше экрана, внизу карточки появляется
   кнопка «Свернуть размышления», она закреплена у нижнего края чата.
   Работает и для живого стрима, и для истории: новые узлы ловит
   MutationObserver, высоту отслеживает ResizeObserver. */
(function () {
    'use strict';
    if (window.__reasoningClampInit) return;
    window.__reasoningClampInit = true;

    var BODY = '.reasoning-body';
    var STEP = '.reasoning-step';
    var seen = new WeakSet();
    var pending = new Set();
    var raf = 0;

    var ro = typeof ResizeObserver !== 'undefined'
        ? new ResizeObserver(function (entries) {
            entries.forEach(function (e) { schedule(e.target); });
        })
        : null;

    function schedule(body) {
        if (!body) return;
        pending.add(body);
        if (raf) return;
        raf = requestAnimationFrame(function () {
            raf = 0;
            var list = Array.from(pending);
            pending.clear();
            list.forEach(refresh);
        });
    }

    function ensureButton(body) {
        var btn = body.querySelector(':scope > .reasoning-collapse-btn');
        if (btn) return btn;
        btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'reasoning-collapse-btn';
        btn.title = 'Свернуть размышления';
        btn.setAttribute('aria-label', 'Свернуть размышления');
        btn.innerHTML = '<svg viewBox="0 0 14 14" fill="none" aria-hidden="true">'
            + '<path d="M3.5 8.5 7 5l3.5 3.5" stroke="currentColor" stroke-width="1.5" '
            + 'stroke-linecap="round" stroke-linejoin="round"/></svg>'
            + '<span>Свернуть</span>';
        body.appendChild(btn);
        return btn;
    }

    function refresh(body) {
        if (!body.isConnected) return;
        // Свёрнутый блок имеет display:none, мерить нечего.
        if (!body.offsetParent && getComputedStyle(body).display === 'none') return;
        body.querySelectorAll(STEP).forEach(function (step) {
            if (step.classList.contains('reasoning-step--open')) {
                step.classList.add('reasoning-step--clampable');
                return;
            }
            // +2px запаса на субпиксели.
            var over = step.scrollHeight > step.clientHeight + 2;
            step.classList.toggle('reasoning-step--clampable', over);
            if (over) {
                step.setAttribute('role', 'button');
                step.setAttribute('tabindex', '0');
                step.setAttribute('aria-expanded', 'false');
            } else {
                step.removeAttribute('role');
                step.removeAttribute('tabindex');
                step.removeAttribute('aria-expanded');
            }
        });
        var btn = ensureButton(body);
        // Кнопку не считаем в высоту, иначе она бы включалась и выключалась по кругу.
        var h = body.getBoundingClientRect().height - (btn.offsetHeight || 0);
        var tall = h > window.innerHeight;
        body.classList.toggle('reasoning-body--tall', tall);
    }

    function attach(body) {
        if (seen.has(body)) { schedule(body); return; }
        seen.add(body);
        if (ro) ro.observe(body);
        schedule(body);
    }

    function scan(node) {
        if (!node || node.nodeType !== 1) return;
        if (node.matches(BODY)) attach(node);
        else if (node.matches(STEP)) attach(node.closest(BODY));
        if (node.querySelectorAll) node.querySelectorAll(BODY).forEach(attach);
        var host = node.closest && node.closest(BODY);
        if (host) attach(host);
    }

    function toggleStep(step) {
        var open = step.classList.toggle('reasoning-step--open');
        step.setAttribute('aria-expanded', open ? 'true' : 'false');
        schedule(step.closest(BODY));
    }

    function collapseBlock(btn) {
        var block = btn.closest('.reasoning-block');
        if (!block) return;
        var header = block.querySelector('.reasoning-header');
        var before = header ? header.getBoundingClientRect().top : 0;
        block.classList.add('reasoning-collapsed');
        // Блок над экраном: после сворачивания возвращаем заголовок в зону видимости.
        if (header && before < 0) header.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.reasoning-collapse-btn');
        if (btn) { e.preventDefault(); e.stopPropagation(); collapseBlock(btn); return; }
        var step = e.target.closest(STEP + '.reasoning-step--clampable');
        if (!step) return;
        if (e.target.closest('a, button, pre, code, input, textarea')) return;
        var sel = window.getSelection && window.getSelection();
        if (sel && !sel.isCollapsed && step.contains(sel.anchorNode)) return;
        toggleStep(step);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        var step = e.target.closest && e.target.closest(STEP + '.reasoning-step--clampable');
        if (!step || e.target !== step) return;
        e.preventDefault();
        toggleStep(step);
    });

    // Раскрытие свёртка из заголовка: тело становится видимым, его надо перемерить.
    document.addEventListener('click', function (e) {
        var header = e.target.closest('.reasoning-header');
        if (!header) return;
        var body = header.parentElement && header.parentElement.querySelector(BODY);
        if (body) setTimeout(function () { attach(body); }, 0);
    });

    window.addEventListener('resize', function () {
        document.querySelectorAll(BODY).forEach(schedule);
    });

    function start() {
        document.querySelectorAll(BODY).forEach(attach);
        new MutationObserver(function (muts) {
            muts.forEach(function (m) {
                if (m.type === 'attributes') {
                    if (m.target.matches && m.target.matches('.reasoning-block')) {
                        var b = m.target.querySelector(BODY);
                        if (b) attach(b);
                    }
                    return;
                }
                m.addedNodes.forEach(scan);
                if (m.target && m.target.nodeType === 1 && m.target.closest) {
                    var host = m.target.closest(BODY);
                    if (host) schedule(host);
                }
            });
        }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();

    window.ReasoningClamp = { refresh: function (el) { scan(el || document.body); } };
})();
