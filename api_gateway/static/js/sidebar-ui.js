/* sidebar-ui.js
   1) Ширина левого сайдбара: тянется за правый край, от 250px до 500px
      (не больше чем в 2 раза), запоминается в localStorage, двойной клик
      по краю сбрасывает. Только десктоп (>768px), в /editor не работает.
   2) Заголовки чатов (.chat-line--title) остаются однострочными (высота не
      меняется), а при переполнении затухают к правому краю маской, как
      .reasoning-step--clampable, даже без наведения. */
(function () {
    'use strict';
    if (window.__sidebarUiInit) return;
    window.__sidebarUiInit = true;

    var MIN = 250, MAX = 500, KEY = 'catchpm:sidebar-width';

    function init() {
        var layout = document.querySelector('.chat-layout');
        var sidebar = layout && layout.querySelector(':scope > .sidebar');
        if (!layout || !sidebar || layout.classList.contains('ed-layout')) return;
        initResize(layout, sidebar);
        initTitles(sidebar);
    }

    function clamp(w) {
        // На узком окне не даём сайдбару съесть больше 45% ширины.
        var cap = Math.min(MAX, Math.max(MIN, Math.round(window.innerWidth * 0.45)));
        return Math.round(Math.min(cap, Math.max(MIN, w)));
    }

    function initResize(layout, sidebar) {
        var handle = document.createElement('div');
        handle.className = 'sidebar-resizer';
        handle.setAttribute('role', 'separator');
        handle.setAttribute('aria-orientation', 'vertical');
        handle.setAttribute('aria-label', 'Ширина панели');
        handle.setAttribute('aria-valuemin', String(MIN));
        handle.setAttribute('aria-valuemax', String(MAX));
        handle.tabIndex = 0;
        handle.title = 'Потяните, чтобы изменить ширину. Двойной клик — сброс';
        sidebar.appendChild(handle);

        var current = MIN;
        function apply(w, save) {
            current = clamp(w);
            if (current === MIN) sidebar.style.removeProperty('--sidebar-w');
            else sidebar.style.setProperty('--sidebar-w', current + 'px');
            handle.setAttribute('aria-valuenow', String(current));
            if (save) { try { localStorage.setItem(KEY, String(current)); } catch (e) {} }
        }

        var saved = MIN;
        try { saved = parseInt(localStorage.getItem(KEY), 10) || MIN; } catch (e) {}
        if (window.innerWidth > 768) apply(saved, false);

        var startX = 0, startW = 0, dragging = false;
        handle.addEventListener('pointerdown', function (e) {
            if (e.button !== 0 || window.innerWidth <= 768) return;
            e.preventDefault();
            dragging = true;
            startX = e.clientX;
            startW = sidebar.getBoundingClientRect().width;
            handle.setPointerCapture(e.pointerId);
            layout.classList.add('sidebar-resizing');
        });
        handle.addEventListener('pointermove', function (e) {
            if (dragging) apply(startW + (e.clientX - startX), false);
        });
        function stop(e) {
            if (!dragging) return;
            dragging = false;
            layout.classList.remove('sidebar-resizing');
            try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
            apply(current, true);
        }
        handle.addEventListener('pointerup', stop);
        handle.addEventListener('pointercancel', stop);
        handle.addEventListener('dblclick', function () { apply(MIN, true); });
        handle.addEventListener('keydown', function (e) {
            var step = e.shiftKey ? 50 : 10;
            if (e.key === 'ArrowRight') apply(current + step, true);
            else if (e.key === 'ArrowLeft') apply(current - step, true);
            else if (e.key === 'Home') apply(MIN, true);
            else if (e.key === 'End') apply(MAX, true);
            else return;
            e.preventDefault();
        });
        window.addEventListener('resize', function () {
            if (window.innerWidth <= 768) return;
            var w = MIN;
            try { w = parseInt(localStorage.getItem(KEY), 10) || MIN; } catch (e) {}
            apply(w, false);
        });
    }

    function initTitles(sidebar) {
        var raf = 0;
        function run() {
            raf = 0;
            sidebar.querySelectorAll('.chat-line--title, .chat-line--preview').forEach(function (el) {
                var over = el.scrollWidth > el.clientWidth + 1;
                el.classList.toggle('chat-line--clamped', over);
                var full = (el.textContent || '').trim();
                if (over) { if (el.title !== full) el.title = full; }
                else if (el.hasAttribute('title')) el.removeAttribute('title');
            });
        }
        function schedule() { if (!raf) raf = requestAnimationFrame(run); }
        new MutationObserver(function (list) {
            for (var i = 0; i < list.length; i++) {
                var m = list[i];
                // Свой же класс --clamped и title не должны запускать пересчёт по кругу.
                if (m.type === 'attributes' && m.target.classList &&
                    m.target.classList.contains('chat-line')) continue;
                schedule(); return;
            }
        }).observe(sidebar, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
        if (typeof ResizeObserver !== 'undefined') new ResizeObserver(schedule).observe(sidebar);
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
        schedule();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
