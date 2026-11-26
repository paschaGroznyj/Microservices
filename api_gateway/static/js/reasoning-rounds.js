/* reasoning-rounds.js
   «Размышления» раундами: каждая итерация агента — раунд с номером, внутри
   мысли (первое предложение заголовком, остальное по клику) и чипы вызовов
   инструментов с таймингом. Данные о вызовах — те же flow-события, что рисует
   FlowMap (fmMiniCv): tool_start / tool_end {call_id, slot, label, ms, ok}.
   После F5 хронология берётся из Redis flow:{chat_id} через /api/chats/{id}/flow:
   i-й think_end ↔ reasoning.steps[i], tool_* с ts — так восстанавливается порядок.

   Мысли по-прежнему добавляет app.js (.reasoning-step прямыми детьми
   .reasoning-body). Модуль перехватывает их MutationObserver'ом и
   перекладывает в раунды; текст шага сохраняется дословно (textContent),
   поэтому дедупликация шагов в app.js продолжает работать.
   Отключить: localStorage.setItem('rr:off','1'). */
(function () {
    'use strict';
    if (window.ReasoningRounds) return;
    var OFF = false;
    try { OFF = localStorage.getItem('rr:off') === '1'; } catch (_) {}

    var BODY = '.reasoning-body';
    var models = new WeakMap();      // body → model
    var pending = {};                // chatId → [flow-события до появления body]
    var NEW_ROUND_GAP = 400;         // мс без текста между тулами = новая итерация

    function newModel() { return { ev: [], user: {}, rid: null }; }
    function model(body) {
        var m = models.get(body);
        if (!m) { m = newModel(); models.set(body, m); }
        return m;
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
        });
    }
    // Первое предложение — заголовок. Возвращаем индекс разреза: head + tail === s.
    function cut(s) {
        var re = /[.!?:](?=\s+[A-ZА-ЯЁ0-9"«(`])/g, m;
        while ((m = re.exec(s))) {
            if (m.index >= 24) return m.index + 1;
        }
        return s.length;
    }
    function sec(ms) {
        var t = Math.max(0, ms) / 1000;
        if (t < 10) return t.toFixed(1).replace('.', ',') + ' с';
        t = Math.round(t);
        return t < 60 ? t + ' с' : Math.floor(t / 60) + ' мин ' + (t % 60) + ' с';
    }
    function plural(n, a, b, c) {
        var m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return a;
        if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
        return c;
    }

    var SVG = function (p, w) {
        return '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="' + (w || 1.5) +
            '" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + p + '</svg>';
    };
    var IC = {
        chev: '<svg class="rr-chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5"/></svg>',
        sandbox: SVG('<path d="M3 4.5 6 7.5 3 10.5M7.5 11.5H13"/>'),
        kb: SVG('<ellipse cx="8" cy="4" rx="5" ry="2"/><path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2"/>'),
        web: SVG('<circle cx="8" cy="8" r="5.5"/><path d="M2.5 8h11M8 2.5c1.6 1.6 2.2 3.5 2.2 5.5S9.6 11.9 8 13.5C6.4 11.9 5.8 10 5.8 8S6.4 4.1 8 2.5"/>'),
        media: SVG('<rect x="2.5" y="3" width="11" height="10" rx="2"/><circle cx="6" cy="6.5" r="1.2"/><path d="m3 12 3.5-3.5 2.5 2.5 1.5-1.5L13 12"/>'),
        mail: SVG('<rect x="2.5" y="3.5" width="11" height="9" rx="1.8"/><path d="m3 5 5 4 5-4"/>'),
        tasks: SVG('<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.5"/>'),
        other: SVG('<path d="M9.5 2.5 4 9h4l-1.5 4.5L12 7H8z"/>'),
        ok: SVG('<path d="M3.5 8.5 6.5 11.5 12.5 5"/>', 1.8),
        err: SVG('<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>', 1.8)
    };

    /* ── Модель: упорядоченная лента событий → раунды ── */
    function addThought(m, text, t) {
        text = String(text || '');
        var key = text.trim();
        if (!key) return false;
        for (var i = 0; i < m.ev.length; i++) {
            if (m.ev[i].k === 'th' && m.ev[i].text.trim() === key) return false;
        }
        m.ev.push({ k: 'th', text: text, t: t });
        return true;
    }
    function findTool(m, id, label) {
        for (var i = m.ev.length - 1; i >= 0; i--) {
            var e = m.ev[i];
            if (e.k !== 'tool' || e.t1 != null) continue;
            if (id ? e.id === id : e.label === label) return e;
        }
        return null;
    }
    function applyFlow(m, f, t) {
        var ev = f.ev;
        var rid = f.rid || f.request_id || null;
        if (ev === 'agent_start' && rid && m.rid && rid !== m.rid) { m.ev = []; m.user = {}; }
        if (rid) m.rid = rid;
        if (ev === 'tool_start') {
            if (f.call_id && findTool(m, f.call_id)) return false;
            m.ev.push({ k: 'tool', id: f.call_id || null, slot: f.slot || 'other', label: f.label || 'Инструмент',
                t: t, gap: f.gap_ms, t1: null, ms: null, ok: null });
            return true;
        }
        if (ev === 'tool_end') {
            var e = findTool(m, f.call_id, f.label);
            if (!e) {
                var ms0 = +f.ms || 0;
                e = { k: 'tool', id: f.call_id || null, slot: f.slot || 'other', label: f.label || 'Инструмент', t: t - ms0 };
                m.ev.push(e);
            }
            // Интервал якорим на момент tool_end: [конец − ms, конец]. ms меряет агент,
            // ts tool_end в Redis ставится тем же моментом, поэтому стрим и F5 совпадают.
            e.ms = Number.isFinite(+f.ms) && f.ms !== null ? +f.ms : t - e.t;
            e.t1 = t;
            e.ts = t - e.ms;
            e.ok = f.ok !== false;
            if (f.label) e.label = f.label;
            return true;
        }
        return false;
    }
    // Длительность тулов раунда: объединение [t, t+ms]. Не зависит от того, когда
    // пришли мысли, поэтому одинакова в стриме и после F5 (ms из события tool_end).
    function toolSpan(tools) {
        var iv = tools.filter(function (x) { return x.ms != null; })
            .map(function (x) { return [x.ts != null ? x.ts : x.t1 - x.ms, x.t1]; })
            .sort(function (a, b) { return a[0] - b[0]; });
        var sum = 0, s = null, e = null;
        iv.forEach(function (p) {
            if (s === null) { s = p[0]; e = p[1]; return; }
            if (p[0] <= e) { e = Math.max(e, p[1]); return; }
            sum += e - s; s = p[0]; e = p[1];
        });
        if (s !== null) sum += e - s;
        return iv.length ? sum : null;
    }
    function rounds(m) {
        var out = [], cur = null, lastEnd = null;
        m.ev.forEach(function (e) {
            var hasTools = cur && cur.tools.length;
            var start = !cur;
            if (cur && e.k === 'th' && hasTools) start = true;
            // Новая итерация без текста: все тулы раунда завершились, и пауза до вызова заметная.
            if (cur && e.k === 'tool' && hasTools && cur.tools.every(function (x) { return x.t1 != null; }) &&
                ((e.gap != null && e.gap > NEW_ROUND_GAP) || (e.gap == null && lastEnd != null && e.t - lastEnd > NEW_ROUND_GAP))) start = true;
            if (start) { cur = { items: [], tools: [], th: [], t0: e.t }; out.push(cur); }
            cur.items.push(e);
            if (e.k === 'tool') { cur.tools.push(e); if (e.t1 != null) lastEnd = Math.max(lastEnd || 0, e.t1); }
            else cur.th.push(e);
        });
        out.forEach(function (r, i) {
            var next = out[i + 1];
            var end = r.t0;
            r.items.forEach(function (e) { end = Math.max(end, e.t1 != null ? e.t1 : e.t); });
            r.t1 = next ? Math.max(end, next.t0) : end;
            r.running = r.tools.some(function (x) { return x.t1 == null; });
            r.toolMs = toolSpan(r.tools);
            r.errs = r.tools.filter(function (x) { return x.ok === false; }).length;
        });
        return out;
    }

    /* ── Рендер ── */
    function thoughtHtml(text, skipHead) {
        var c = cut(text);
        var head = text.slice(0, c), tail = text.slice(c).replace(/^\s+/, '');
        if (skipHead) {
            // Первое предложение уже в шапке раунда: показываем только продолжение.
            // Скрытый head оставляем, чтобы textContent шага совпадал с исходным (дедуп app.js).
            if (!tail.trim()) return '<div class="reasoning-step rr-th rr-th--cont rr-th--empty"><span class="rr-th__hid">' + esc(head) + '</span></div>';
            return '<div class="reasoning-step rr-th rr-th--cont">' +
                '<span class="rr-th__hid">' + esc(head) + '</span><div class="rr-th__b">' + esc(tail) + '</div></div>';
        }
        var more = !!tail.trim();
        return '<div class="reasoning-step rr-th' + (more ? ' has-more' : '') + '"' +
            (more ? ' role="button" tabindex="0" aria-expanded="false"' : '') + '>' +
            '<div class="rr-th__h">' + esc(head) + '</div>' + (more ? IC.chev : '<span class="rr-chev"></span>') +
            (more ? '<div class="rr-th__b">' + esc(tail) + '</div>' : '') + '</div>';
    }
    function toolHtml(x) {
        var st = x.t1 == null ? '<span class="rr-spin" aria-label="выполняется"></span>' : (x.ok ? IC.ok : IC.err);
        return '<div class="rr-tool' + (x.ok === false ? ' is-err' : '') + (x.t1 == null ? ' is-run' : '') + '">' +
            '<span class="rr-tool__ic">' + (IC[x.slot] || IC.other) + '</span>' +
            '<span class="rr-tool__n">' + esc(x.label) + '</span>' +
            '<span class="rr-tool__d">' + (x.t1 == null ? '' : sec(x.ms)) + '</span>' +
            '<span class="rr-tool__st">' + st + '</span></div>';
    }
    function roundTitle(r) {
        if (r.th.length) { var s = r.th[0].text.trim(); return s.slice(0, cut(s)); }
        return r.tools.length ? r.tools[0].label : '';
    }
    function render(body) {
        var m = models.get(body);
        if (!m) return;
        var rs = rounds(m);
        var box = body.querySelector(':scope > .rr-rounds');
        if (!rs.length) { if (box) box.remove(); return; }
        if (!box) {
            box = document.createElement('div');
            box.className = 'rr-rounds';
            body.insertBefore(box, body.querySelector(':scope > .reasoning-collapse-btn'));
        }
        // Открытые мысли переживают перерисовку.
        var openTh = {};
        box.querySelectorAll('.rr-th.is-open').forEach(function (el) { openTh[el.textContent.trim()] = 1; });
        var flat = rs.length === 1 && !rs[0].tools.length;
        box.classList.toggle('rr-flat', flat);
        if (flat) {
            box.innerHTML = '<div class="rr-rd__in">' + rs[0].items.map(function (e) { return thoughtHtml(e.text, false); }).join('') + '</div>';
            restoreOpen(box, openTh);
            return;
        }
        var last = rs.length - 1;
        box.innerHTML = rs.map(function (r, i) {
            var open = m.user[i] != null ? m.user[i] : i === last;
            var meta = [];
            if (r.tools.length) meta.push(r.tools.length + ' инстр.');
            if (r.errs) meta.push('<span class="rr-err">' + r.errs + '\u00a0' + plural(r.errs, 'ошибка', 'ошибки', 'ошибок') + '</span>');
            if (r.toolMs) meta.push(sec(r.toolMs));
            var first = r.th.length ? r.items.indexOf(r.th[0]) : -1;
            var inner = r.items.map(function (e, j) {
                return e.k === 'tool' ? toolHtml(e) : thoughtHtml(e.text, j === first);
            }).join('');
            var title = esc(roundTitle(r));
            // Раунд из одной короткой мысли: раскрывать нечего, шапка без шеврона.
            var empty = !r.tools.length && r.th.length === 1 && !r.th[0].text.trim().slice(cut(r.th[0].text.trim())).trim();
            if (empty) open = false;
            return '<section class="rr-rd' + (open ? ' is-open' : '') + (r.running ? ' is-run' : '') + (empty ? ' is-leaf' : '') + '" data-i="' + i + '">' +
                '<button class="rr-rd__h" type="button"' + (empty ? ' disabled' : ' aria-expanded="' + open + '"') + '>' +
                '<span class="rr-rd__n">' + (i + 1) + '</span>' +
                '<span class="rr-rd__t" title="' + title + '">' + title + '</span>' +
                '<span class="rr-rd__m">' + meta.join(' · ') + '</span>' + IC.chev +
                '</button><div class="rr-rd__b"><div class="rr-rd__in">' + inner + '</div></div></section>';
        }).join('');
        restoreOpen(box, openTh);
    }
    function restoreOpen(box, openTh) {
        box.querySelectorAll('.rr-th.has-more').forEach(function (el) {
            if (openTh[el.textContent.trim()]) { el.classList.add('is-open'); el.setAttribute('aria-expanded', 'true'); }
        });
    }

    /* ── Связь с DOM ── */
    var raf = 0, dirty = new Set();
    function schedule(body) {
        dirty.add(body);
        if (raf) return;
        raf = requestAnimationFrame(function () {
            raf = 0;
            var list = Array.from(dirty); dirty.clear();
            list.forEach(function (b) { if (b.isConnected) render(b); });
        });
    }
    // Сырые шаги app.js (прямые дети body) → мысли модели.
    function absorb(body) {
        if (OFF || !body || !body.matches || !body.matches(BODY)) return;
        body.classList.add('rr-on');
        var m = model(body), changed = false;
        var blk = body.closest('.reasoning-block');
        var chat = blk && blk.getAttribute('data-rr-chat');
        if (chat && pending[chat] && pending[chat].length) {
            pending[chat].forEach(function (p) { if (applyFlow(m, p.f, p.t)) changed = true; });
            pending[chat] = [];
        }
        body.querySelectorAll(':scope > .reasoning-step').forEach(function (step) {
            if (addThought(m, step.textContent, Date.now())) changed = true;
            step.remove();
        });
        if (changed) schedule(body);
    }
    function bodyOf(el) {
        if (!el) return null;
        if (el.matches && el.matches(BODY)) return el;
        return el.querySelector ? el.querySelector(BODY) : null;
    }

    var api = {
        /* Живое flow-событие. getBody → .reasoning-body (или .reasoning-block) текущего ответа либо null. */
        flow: function (chatId, f, getBody) {
            if (OFF || !f || (f.ev !== 'tool_start' && f.ev !== 'tool_end' && f.ev !== 'agent_start')) return;
            var t = Date.now();
            var body = null;
            try { body = bodyOf(getBody && getBody()); } catch (_) {}
            var key = String(chatId);
            if (!body) {
                if (f.ev === 'agent_start') pending[key] = [];
                (pending[key] = pending[key] || []).push({ f: f, t: t });
                return;
            }
            absorb(body);
            if (applyFlow(model(body), f, t)) schedule(body);
        },
        /* Восстановление из Redis (F5 / смена чата): /api/chats/{id}/flow. */
        restore: function (chatId, el) {
            var body = bodyOf(el);
            if (OFF || !body || chatId == null) return Promise.resolve(false);
            absorb(body);
            return fetch('/api/chats/' + encodeURIComponent(chatId) + '/flow', { credentials: 'include', redirect: 'manual' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (j) { return j ? api.replay(body, j) : false; })
                .catch(function () { return false; });
        },
        /* j = {events:[{ev, ts, rid, ...}], reasoning:{request_id, steps:[...]}} */
        replay: function (el, j) {
            var body = bodyOf(el);
            if (!body || !j) return false;
            var steps = (j.reasoning && j.reasoning.steps) || [];
            var rid = j.reasoning && j.reasoning.request_id;
            var evs = (j.events || []).filter(function (e) { return !rid || !e.rid || e.rid === rid; });
            if (!evs.some(function (e) { return e.ev === 'tool_start' || e.ev === 'tool_end'; })) return false;
            var old = models.get(body) || newModel();
            var m = newModel();
            m.user = old.user;
            m.rid = rid || null;
            var si = 0, lastTs = 0;
            evs.forEach(function (e) {
                var ts = +e.ts || lastTs; lastTs = ts;
                if (e.ev === 'think_end') { if (si < steps.length) addThought(m, steps[si++], ts); return; }
                if (e.ev === 'tool_start' || e.ev === 'tool_end') applyFlow(m, e, ts);
            });
            for (; si < steps.length; si++) addThought(m, steps[si], lastTs);
            // Мысли и тулы, пришедшие живьём после снимка, не теряем.
            old.ev.forEach(function (e) {
                if (e.k === 'th') addThought(m, e.text, e.t);
                else if (e.id && !m.ev.some(function (x) { return x.id === e.id; })) m.ev.push(e);
            });
            models.set(body, m);
            render(body);
            return true;
        },
        rounds: function (el) { var b = bodyOf(el); var m = b && models.get(b); return m ? rounds(m) : []; },
        _cut: cut
    };

    function toggle(el, attrEl) {
        var on = el.classList.toggle('is-open');
        (attrEl || el).setAttribute('aria-expanded', String(on));
        return on;
    }
    document.addEventListener('click', function (e) {
        if (OFF || !e.target.closest) return;
        var h = e.target.closest('.rr-rd__h');
        if (h) {
            var rd = h.parentNode, body = h.closest(BODY);
            var on = toggle(rd, h);
            if (body && models.get(body)) models.get(body).user[+rd.getAttribute('data-i')] = on;
            return;
        }
        var th = e.target.closest('.rr-th.has-more');
        if (th) {
            var s = window.getSelection && window.getSelection();
            if (s && String(s).length && th.contains(s.anchorNode)) return;   // выделение текста — не клик
            toggle(th);
        }
    });
    document.addEventListener('keydown', function (e) {
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('.rr-th.has-more')) {
            e.preventDefault(); toggle(e.target);
        }
    });

    function scan(node) {
        if (!node || node.nodeType !== 1) return;
        if (node.matches(BODY)) absorb(node);
        else if (node.matches('.reasoning-step') && node.parentNode && node.parentNode.matches && node.parentNode.matches(BODY)) absorb(node.parentNode);
        if (node.querySelectorAll) node.querySelectorAll(BODY).forEach(absorb);
    }
    function start() {
        if (OFF) return;
        scan(document.body);
        new MutationObserver(function (muts) {
            muts.forEach(function (mu) { mu.addedNodes.forEach(scan); });
        }).observe(document.body, { childList: true, subtree: true });
    }
    window.ReasoningRounds = api;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
