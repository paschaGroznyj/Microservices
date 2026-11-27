/* reasoning-rounds.js — «Размышления» графом узлов: мысли на стволе,
   параллельные вызовы инструментов на цветных дорожках с живыми таймингами. Данные о вызовах — те же flow-события, что рисует
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

    function newModel() { return { ev: [], user: {}, rid: null, chat: null, fo: {}, fp: {} }; }
    /* Файлы шага: tool_end.files = {n, add, del, rid, approx, list:[{p,s,a,d,b,ws}]}.
       Patch шага — по клику: /api/chats/{chat}/ws_diff/step (кеш по rid|call_id). */
    var stepCache = {};
    var FP_SEP = '\u0000';
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
            if (f.files && typeof f.files === 'object' && +f.files.n > 0) e.files = f.files;
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
    /* ── Рендер: граф узлов в стиле mm-tg (панель памяти) ──
       Ствол слева — ход ответа; мысль = полый узел на стволе; вызов = закрашенный узел.
       Вызовы, которые шли одновременно, расходятся на свои дорожки (тона t1/t2/t4/t3)
       и сливаются в ствол, когда закончились. Номера шагов не показываем. */
    var TONES = [1, 2, 4, 3], MAXL = 4;
    function seq(m) {
        var out = [], g = null;
        m.ev.forEach(function (e) {
            if (e.k === 'th') { g = null; out.push({ k: 'th', e: e }); return; }
            // Параллельно: вызов стартовал, пока хотя бы один из группы ещё шёл.
            if (g && g.calls.some(function (x) { return x.t1 == null || x.t1 > e.t + 1; })) { g.calls.push(e); return; }
            g = { k: 'grp', calls: [e] }; out.push(g);
        });
        return out;
    }
    function wall(calls) {
        if (calls.some(function (x) { return x.t1 == null; })) return null;
        var s = Math.min.apply(null, calls.map(function (x) { return x.ts != null ? x.ts : x.t; }));
        return Math.max.apply(null, calls.map(function (x) { return x.t1; })) - s;
    }
    function toneOf(j) { return j ? TONES[(Math.min(j, MAXL - 1) - 1) % TONES.length] : 0; }
    function statHtml(a, d) {
        return '<span class="rg-fst"><b class="' + (a ? 'a' : 'z') + '">+' + a + '</b><b class="' + (d ? 'd' : 'z') + '">\u2212' + d + '</b></span>';
    }
    function filesSum(fs) {
        var n = +fs.n || 0;
        return '<span class="rg-f"' + (fs.approx ? ' title="Вызовы шли параллельно в одной области: в шаг могли попасть изменения соседнего вызова"' : '') + '>' +
            (fs.approx ? '<i class="rg-fa">\u2248</i>' : '') +
            '<span class="rg-fn">' + n + '<span class="rg-fw"> ' + plural(n, 'файл', 'файла', 'файлов') + '</span></span>' +
            statHtml(+fs.add || 0, +fs.del || 0) + '</span>';
    }
    function filesPanel(m, x) {
        var fs = x.files, list = fs.list || [], out = '';
        list.forEach(function (it) {
            var p = String(it.p || ''), k = x.id + FP_SEP + p, open = !!m.fp[k], cut = p.lastIndexOf('/');
            var st = String(it.s || 'M')[0], badge = st === 'A' || st === 'D' ? st : 'M';
            out += '<div class="rg-fp is-' + badge + (open ? ' is-open' : '') + '" role="button" tabindex="0" aria-expanded="' + open + '"' +
                ' data-p="' + esc(p) + '" title="' + esc(p) + '">' + IC.chev +
                '<span class="rg-fb">' + badge + '</span>' +
                '<span class="rg-fpn"><span class="rg-fdir"><bdi>' + esc(cut >= 0 ? p.slice(0, cut + 1) : '') + '</bdi></span>' +
                '<span class="rg-fname">' + esc(p.slice(cut + 1)) + '</span></span>' +
                (it.b ? '<span class="rg-fbin">бинарный</span>' : statHtml(+it.a || 0, +it.d || 0)) + '</div>' +
                (open ? '<div class="rg-fx" data-p="' + esc(p) + '"></div>' : '');
        });
        var more = (+fs.n || 0) - list.length;
        if (more > 0) out += '<div class="rg-fm" title="Полный список — в «Изменённых файлах»">и ещё ' + more + ' ' + plural(more, 'файл', 'файла', 'файлов') + '</div>';
        return '<div class="rg-fl">' + out + '</div>';
    }
    function callHtml(x, j, g, m) {
        var run = x.t1 == null, err = x.ok === false, n = g.calls.length, side = '';
        var hasF = !run && x.files && x.id, fopen = hasF && m && !!m.fo[x.id];
        if (j === 0 && n > 1) {
            side = '<span class="rg-s" data-n="×' + n + '">' + n + ' параллельно</span>';
        }
        var gc = n > 1 ? (j === 0 ? ' is-g0' : '') + (j === n - 1 ? ' is-gN' : '') : '';
        return '<div class="rg-r is-call rg-t' + toneOf(j) + gc + (run ? ' is-run' : '') + (err ? ' is-err' : '') +
            (hasF ? ' has-files' + (fopen ? ' is-open' : '') : '') + '"' +
            (run ? ' data-t="' + x.t + '"' : '') + (hasF ? ' data-cid="' + esc(x.id) + '"' : '') + ' title="' + esc(x.label) + '">' +
            '<span class="rg-ic">' + (IC[x.slot] || IC.other) + '</span>' +
            '<span class="rg-n">' + esc(x.label) + '</span>' +
            (err ? '<span class="rg-e">ошибка</span>' : '') +
            (hasF ? '<span class="rg-fh" role="button" tabindex="0" aria-expanded="' + fopen + '">' + filesSum(x.files) + IC.chev + '</span>' : '') +
            '<span class="rg-d">' + sec(run ? Date.now() - x.t : x.ms) + '</span>' + side +
            (fopen ? filesPanel(m, x) : '') + '</div>';
    }
    function render(body) {
        var m = models.get(body);
        if (!m) return;
        var sq = seq(m);
        var box = body.querySelector(':scope > .rr-rounds');
        if (!sq.length) { if (box) box.remove(); return; }
        if (!box) {
            box = document.createElement('div');
            box.className = 'rr-rounds rg';
            body.insertBefore(box, body.querySelector(':scope > .reasoning-collapse-btn'));
        }
        var openTh = {};
        box.querySelectorAll('.rr-th.is-open').forEach(function (el) { openTh[el.textContent.trim()] = 1; });
        var lanes = 1, rows = [];
        sq.forEach(function (it) {
            if (it.k === 'th') { rows.push('<div class="rg-r is-th">' + thoughtHtml(it.e.text, false) + '</div>'); return; }
            lanes = Math.max(lanes, Math.min(it.calls.length, MAXL));
            it.calls.forEach(function (x, j) { rows.push(callHtml(x, j, it, m)); });
        });
        box.style.setProperty('--rg-n', lanes);
        box.innerHTML = '<svg class="rg-svg" aria-hidden="true"></svg><div class="rg-rows">' + rows.join('') + '</div>';
        box._sq = sq;
        if (m.chat != null) box.setAttribute('data-chat', String(m.chat));
        paintPatches(box, m);
        restoreOpen(box, openTh);
        draw(box);
        if (gro) gro.observe(box.querySelector('.rg-rows'));
        tick();
    }
    function chatOf(box, m) {
        if (m && m.chat != null) return m.chat;
        var c = box && box.closest('[data-rr-chat]');
        return c ? c.getAttribute('data-rr-chat') : (box && box.getAttribute('data-chat'));
    }
    function findCall(m, cid) {
        for (var i = 0; i < m.ev.length; i++) if (m.ev[i].k === 'tool' && m.ev[i].id === cid) return m.ev[i];
        return null;
    }
    function loadStep(body, m, x) {
        var fs = x.files || {}, rid = fs.rid || m.rid, key = rid + '|' + x.id;
        if (stepCache[key]) return stepCache[key];
        var chat = chatOf(body.querySelector('.rr-rounds'), m);
        var wss = [];
        (fs.list || []).forEach(function (it) { if (it.ws && wss.indexOf(it.ws) < 0) wss.push(it.ws); });
        var c = stepCache[key] = { st: 'load', files: [] };
        if (!rid || chat == null || !wss.length) { c.st = 'err'; return c; }
        Promise.all(wss.map(function (ws) {
            var q = '?workspace_id=' + encodeURIComponent(ws) + '&request_id=' + encodeURIComponent(rid) + '&call_id=' + encodeURIComponent(x.id);
            return fetch('/api/chats/' + encodeURIComponent(chat) + '/ws_diff/step' + q, { credentials: 'include' })
                .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
                .then(function (j) { (j.files || []).forEach(function (f) { f._ws = ws; c.files.push(f); }); });
        })).then(function () { c.st = 'ok'; }, function () { c.st = c.files.length ? 'ok' : 'err'; })
            .then(function () { if (body.isConnected) schedule(body); });
        return c;
    }
    function paintPatches(box, m) {
        box.querySelectorAll('.rg-fx').forEach(function (el) {
            var row = el.closest('.rg-r'), x = row && findCall(m, row.getAttribute('data-cid'));
            if (!x) return;
            var c = stepCache[((x.files && x.files.rid) || m.rid) + '|' + x.id], p = el.getAttribute('data-p');
            if (!c || c.st === 'load') { el.innerHTML = '<div class="rg-fxm"><span class="rr-spin"></span>Загружаю diff шага…</div>'; return; }
            var f = null;
            c.files.forEach(function (it) { if (!f && it.path === p) f = it; });
            if (c.st === 'err' || !f) { el.innerHTML = '<div class="rg-fxm">Diff шага недоступен: workspace удалён или запрос старше 40 последних</div>'; return; }
            if (window.WsDiff && WsDiff.renderFile) WsDiff.renderFile(el, f, f._ws);
            else el.innerHTML = '<div class="rg-fxm">Нет ws-diff.js</div>';
        });
    }
    function draw(box) {
        var sq = box && box._sq, rowsEl = box && box.querySelector('.rg-rows'), svg = box && box.querySelector('.rg-svg');
        if (!sq || !rowsEl || !svg || !rowsEl.children.length) return;
        var rs = rowsEl.children, cs = getComputedStyle(box);
        var x0 = parseFloat(cs.getPropertyValue('--rg-x0')) || 7, lane = parseFloat(cs.getPropertyValue('--rg-lane')) || 16;
        var X = function (j) { return x0 + Math.min(j, MAXL - 1) * lane; };
        var Y = function (i) { return rs[i].offsetTop + 12; };
        var C = function (xa, ya, xb, yb) { var k = (yb - ya) * 0.6; return ' C' + xa + ',' + (ya + k) + ' ' + xb + ',' + (yb - k) + ' ' + xb + ',' + yb; };
        var D = 22, paths = [], nodes = [], i = 0, top = Y(0), bottom = Y(rs.length - 1), cutAt = null;
        function node(x, cx, cy, j) {
            var run = x.t1 == null;
            nodes.push('<g class="rg-o rg-t' + toneOf(j) + (run ? ' is-run' : '') + (x.ok === false ? ' is-err' : '') + '">' +
                (run ? '<circle class="rg-pl" cx="' + cx + '" cy="' + cy + '" r="4"/>' : '') +
                '<circle class="rg-c" cx="' + cx + '" cy="' + cy + '" r="4"/></g>');
        }
        sq.forEach(function (it) {
            if (it.k === 'th') { nodes.push('<circle class="rg-q" cx="' + X(0) + '" cy="' + Y(i) + '" r="3.5"/>'); i++; return; }
            var n = it.calls.length, first = i, last = i + n - 1;
            var run = it.calls.some(function (x) { return x.t1 == null; });
            var fy = n > 1 ? Y(first) - 6 - D : Y(first), open = Y(last) + 10;
            if (run) {
                // Группа ещё идёт: ствол обрывается на развилке, дорожки открыты книзу.
                cutAt = fy; bottom = Math.max(bottom, open);
                paths.push('<path class="rg-p rg-t0' + (it.calls[0].t1 == null ? ' is-run' : '') + '" d="M' + X(0) + ',' + fy + ' L' + X(0) + ',' + open + '"/>');
            }
            it.calls.forEach(function (x, j) {
                if (j) {
                    var xx = X(j), d = 'M' + X(0) + ',' + fy + C(X(0), fy, xx, Y(first) - 6);
                    if (run) d += ' L' + xx + ',' + open;
                    else { var jy = Y(last) + 6 + D; d += ' L' + xx + ',' + (Y(last) + 6) + C(xx, Y(last) + 6, X(0), jy); bottom = Math.max(bottom, jy); }
                    paths.push('<path class="rg-p rg-t' + toneOf(j) + (x.t1 == null ? ' is-run' : '') + '" d="' + d + '"/>');
                }
                node(x, X(j), Y(first + j), j);
            });
            top = Math.min(top, fy);
            i = last + 1;
        });
        var tEnd = cutAt != null ? cutAt : bottom;
        var trunk = tEnd > top ? '<path class="rg-p rg-t0" d="M' + X(0) + ',' + top + ' L' + X(0) + ',' + tEnd + '"/>' : '';
        svg.setAttribute('width', X(MAXL) + 4);
        svg.setAttribute('height', Math.ceil(bottom + 8));
        svg.innerHTML = trunk + paths.join('') + nodes.join('');
    }
    var gro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(function (es) {
        es.forEach(function (e) { draw(e.target.closest('.rg')); });
    }) : null;
    var tickId = 0;
    function tick() {
        if (tickId) return;
        tickId = setInterval(function () {
            var live = document.querySelectorAll('.rg-r.is-run[data-t]');
            if (!live.length) { clearInterval(tickId); tickId = 0; return; }
            var now = Date.now();
            live.forEach(function (r) { var d = r.querySelector('.rg-d'); if (d) d.textContent = sec(now - +r.getAttribute('data-t')); });
        }, 100);
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
            model(body).chat = chatId;
            if (applyFlow(model(body), f, t)) schedule(body);
        },
        /* Восстановление из Redis (F5 / смена чата): /api/chats/{id}/flow. */
        restore: function (chatId, el) {
            var body = bodyOf(el);
            if (OFF || !body || chatId == null) return Promise.resolve(false);
            absorb(body);
            return fetch('/api/chats/' + encodeURIComponent(chatId) + '/flow', { credentials: 'include', redirect: 'manual' })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (j) {
                    model(body).chat = chatId;
                    var ok = j ? api.replay(body, j) : false;
                    if (models.get(body)) models.get(body).chat = chatId;
                    return ok;
                })
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
            m.fo = old.fo || {}; m.fp = old.fp || {}; m.chat = old.chat;
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
    function onFiles(t) {
        var fp = t.closest('.rg-fp');
        var fh = !fp && t.closest('.rg-fh');
        if (!fp && !fh) return false;
        var row = t.closest('.rg-r.has-files'), body = row && row.closest(BODY), m = body && models.get(body);
        if (!m) return true;
        var cid = row.getAttribute('data-cid');
        if (fh) { if (m.fo[cid]) delete m.fo[cid]; else m.fo[cid] = 1; }
        else {
            var k = cid + FP_SEP + fp.getAttribute('data-p');
            if (m.fp[k]) delete m.fp[k]; else { m.fp[k] = 1; var x = findCall(m, cid); if (x) loadStep(body, m, x); }
        }
        render(body);
        var again = body.querySelector('.rg-r[data-cid="' + (window.CSS && CSS.escape ? CSS.escape(cid) : cid) + '"] ' +
            (fh ? '.rg-fh' : '.rg-fp[data-p="' + (window.CSS && CSS.escape ? CSS.escape(fp.getAttribute('data-p')) : fp.getAttribute('data-p')) + '"]'));
        if (again && again.focus) again.focus({ preventScroll: true });
        return true;
    }
    document.addEventListener('click', function (e) {
        if (OFF || !e.target.closest) return;
        if (e.target.closest('.rg-fx')) return;   // клики внутри patch (выделение, кнопки) — не сворачивают
        if (onFiles(e.target)) return;
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
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('.rg-fh, .rg-fp')) {
            e.preventDefault(); onFiles(e.target);
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
