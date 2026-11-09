/* task-cards.js — карточки фоновых задач (submit_task).
 * Два стиля: kind=script — консольное окно, kind=media — светлая карточка в тонах темы.
 * Расположение: внутри .agent-message своего хода.
 *   Пока ответ стримится (нет .message-actions) — сразу после .reasoning-block (если его нет — первой).
 *   Ответ дописан (.message-actions есть) — под последним блоком ответа, прямо над .message-actions.
 *   После F5 так же: у ответа из истории .message-actions уже есть.
 *   Если ответа в ленте ещё нет — временная группа .task-cards--orphan в колонке ленты,
 *   при следующем событии или финале ответа карточки переезжают в ответ.
 * API (window.TaskCards):
 *   upsert(container, task, {pendingEl})  событие task_update (WS main / опрос sigma)
 *   mount(messageEl, tasks)               история: message.tasks из get_chat_messages
 *   settle(container, botMsg)             финал ответа: собрать карточки хода в ответ
 *   place(messageEl)                      вернуть группу на место после вставки .reasoning-block
 * Сворачивание: клик или Enter/Space по .tc-head. Из истории (mount) карточки приходят свёрнутыми,
 *   живые (upsert) — развёрнутыми; выбор пользователя хранится по task_id и переживает перерисовку.
 */
(function () {
    'use strict';
    const FINAL = new Set(['completed', 'failed', 'cancelled']);
    const STATUS_RU = {
        pending: 'в очереди', running: 'выполняется', cancelling: 'отмена…',
        completed: 'готово', failed: 'ошибка', cancelled: 'отменена',
    };
    const seen = new Map(); // task_id -> {task, at}: снимок и момент получения (для тикающего таймера)
    const collapsed = new Map(); // task_id -> true/false: свёрнута ли карточка
    let ticker = null;

    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const sel = (id) => (window.CSS && CSS.escape ? CSS.escape(id) : String(id).replace(/"/g, '\\"'));
    const lastLines = (s, n) => String(s || '').replace(/\s+$/, '').split('\n').slice(-n).join('\n');

    function fmt(ms) {
        const s = Math.max(0, Math.floor((ms || 0) / 1000));
        const m = Math.floor(s / 60);
        return m ? `${m}:${String(s % 60).padStart(2, '0')}` : `${s} с`;
    }
    function elapsed(t, at) {
        let ms = t.elapsed_ms || 0;
        if (t.status === 'running' && at) ms += Date.now() - at;
        return ms;
    }
    function stageCls(st) {
        if (st === 'done' || st === 'completed') return 'done';
        return ['running', 'failed', 'skipped'].includes(st) ? st : 'pending';
    }
    const STAGE_ICON = { done: '✓', running: '›', failed: '✕', skipped: '–', pending: '·' };

    function fileName(f) {
        if (!f) return '';
        if (typeof f === 'string') return f.split('/').pop();
        return f.filename || f.name || String(f.path || '').split('/').pop();
    }

    function render(card, t, at) {
        const kind = t.kind === 'media' ? 'media' : 'script';
        const st = t.status || 'pending';
        const isCol = collapsed.get(t.task_id) === true;
        card.className = `task-card task-card--${kind} is-${st}${isCol ? ' is-collapsed' : ''}`;
        card.dataset.taskId = t.task_id;
        if (t.message_id) card.dataset.messageId = t.message_id;

        const p = t.progress || {};
        const pct = typeof p.percent === 'number' ? Math.max(0, Math.min(100, p.percent))
            : (FINAL.has(st) ? 100 : null);
        const bar = pct == null
            ? '<div class="tc-bar is-indeterminate"><span></span></div>'
            : `<div class="tc-bar"><span style="width:${pct}%"></span></div>`;
        const stages = (t.stages || []).map((s) => {
            let c = stageCls(s.status);
            // Задача уже закончилась, а этап остался «в работе»: на нём и упали / остановились.
            if (c === 'running' && st === 'failed') c = 'failed';
            else if ((c === 'running' || c === 'pending') && st === 'cancelled') c = 'skipped';
            return `<li class="is-${c}"><b>${STAGE_ICON[c]}</b><span>${esc(s.title)}</span></li>`;
        }).join('');

        let log = '';
        if (st === 'failed') {
            const err = lastLines(t.stderr_tail || '', 8) || t.error || '';
            const head = t.error && t.stderr_tail ? `${esc(t.error)}\n` : '';
            log = `<span class="tc-err">${head}${esc(err)}</span>`;
        } else if (FINAL.has(st)) {
            log = esc(lastLines(t.stdout_tail, 6));
        } else if (p.text) {
            log = `<span class="tc-prompt">›</span> ${esc(p.text)}`;
        }

        const files = (Array.isArray(t.files) ? t.files : []).map(fileName).filter(Boolean);
        const pctTxt = pct != null && !FINAL.has(st) ? `<span class="tc-pct">${pct}%</span>` : '';
        const head = kind === 'script'
            ? '<span class="tc-dots" aria-hidden="true"><i></i><i></i><i></i></span>'
            : '<span class="tc-ico" aria-hidden="true">✦</span>';
        let foot = '';
        if (kind === 'script') {
            const parts = [];
            if (t.exit_code != null) parts.push(`exit ${esc(t.exit_code)}`);
            if (files.length) parts.push(`файлы: ${esc(files.slice(0, 4).join(', '))}${files.length > 4 ? ` +${files.length - 4}` : ''}`);
            if (t.workspace_id) parts.push(`ws ${esc(t.workspace_id)}`);
            foot = parts.length ? `<div class="tc-foot">${parts.join(' · ')}</div>` : '';
        } else if (st === 'failed' && t.exit_code != null) {
            foot = `<div class="tc-foot">exit ${esc(t.exit_code)}</div>`;
        } else if (files.length) {
            foot = `<div class="tc-files">${files.slice(0, 8).map((f) => `<span>${esc(f)}</span>`).join('')}</div>`;
        }

        card.innerHTML =
            `<div class="tc-head" role="button" tabindex="0" aria-expanded="${isCol ? 'false' : 'true'}" title="Свернуть / развернуть">`
            + `${head}<span class="tc-title" title="${esc(t.label)}">${esc(t.label || 'Задача')}</span>`
            + `${pctTxt}<span class="tc-status">${STATUS_RU[st] || esc(st)}</span>`
            + `<span class="tc-time">${fmt(elapsed(t, at))}</span>`
            + '<span class="tc-chev" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none"><path d="M3.5 6L8 10.5L12.5 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></span></div>'
            + bar
            + '<div class="tc-body">'
            + (stages ? `<ol class="tc-stages">${stages}</ol>` : '')
            + (log ? `<pre class="tc-log">${log}</pre>` : '')
            + foot
            + '</div>';
    }

    function tick() {
        const running = document.querySelectorAll('.task-card.is-running .tc-time');
        if (!running.length) { clearInterval(ticker); ticker = null; return; }
        running.forEach((el) => {
            const s = seen.get(el.closest('.task-card').dataset.taskId);
            if (s) el.textContent = fmt(elapsed(s.task, s.at));
        });
    }
    function ensureTicker() { if (!ticker) ticker = setInterval(tick, 1000); }

    const AGENT = '.agent-message';
    function byMessageId(root, mid) {
        if (!root || !mid) return null;
        const s = sel(String(mid));
        return root.querySelector(`${AGENT}[data-message-id="${s}"], ${AGENT}[data-id="${s}"]`);
    }
    // Ответ, который ещё стримится: последний .agent-message ленты без панели действий.
    function pendingMessage(root) {
        if (!root) return null;
        const list = root.querySelectorAll(`:scope > ${AGENT}`);
        const last = list[list.length - 1];
        return last && !last.querySelector(':scope > .message-actions') ? last : null;
    }
    // Группа карточек внутри ответа: ответ дописан — над .message-actions;
    // стримится — сразу после .reasoning-block, если он есть, иначе первой.
    function place(msgEl) {
        if (!msgEl) return null;
        const g = msgEl.querySelector(':scope > .task-cards');
        if (!g) return null;
        const acts = msgEl.querySelector(':scope > .message-actions');
        if (acts) {
            if (acts.previousElementSibling !== g) {
                // живой ход (upsert): плавный «стеклянный» переезд под ответ; история/F5 — без анимации
                const from = g.dataset.live === '1' && g.offsetHeight ? g.getBoundingClientRect() : null;
                acts.before(g);
                if (from) glide(g, from);
            }
            return g;
        }
        const rb = msgEl.querySelector(':scope > .reasoning-block');
        if (rb) { if (rb.nextElementSibling !== g) rb.after(g); }
        else if (msgEl.firstElementChild !== g) msgEl.prepend(g);
        return g;
    }
    // FLIP: старт из прежней позиции, полупрозрачно и размыто, за 0.5 с сверху вниз на новое место.
    function glide(g, from) {
        if (!g.animate || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
        const to = g.getBoundingClientRect();
        const dy = from.top - to.top;
        if (Math.abs(dy) < 2) return;
        g.getAnimations?.().forEach((a) => a.cancel());
        g.classList.add('is-gliding');
        const a = g.animate([
            { transform: `translateY(${dy}px)`, opacity: 0.55, filter: 'blur(0.6px)' },
            { transform: `translateY(${dy * 0.35}px)`, opacity: 0.6, filter: 'blur(0.4px)', offset: 0.45 },
            { transform: 'translateY(0)', opacity: 1, filter: 'blur(0)' },
        ], { duration: 500, easing: 'cubic-bezier(.22,.7,.2,1)' });
        const done = () => g.classList.remove('is-gliding');
        a.onfinish = done; a.oncancel = done;
    }
    function slotIn(msgEl) {
        let g = msgEl.querySelector(':scope > .task-cards');
        if (!g) {
            g = document.createElement('div');
            g.className = 'task-cards';
            msgEl.prepend(g);
        }
        place(msgEl);
        return g;
    }
    // Ответа в ленте нет (событие пришло между запусками) — временная группа в колонке ленты.
    function orphanSlot(container) {
        let g = container.querySelector(':scope > .task-cards--orphan');
        if (!g) {
            g = document.createElement('div');
            g.className = 'task-cards task-cards--orphan';
            container.appendChild(g);
        }
        return g;
    }
    function findCard(root, id) { return root.querySelector(`.task-card[data-task-id="${sel(id)}"]`); }
    function dropEmptyGroups(root) {
        root.querySelectorAll('.task-cards').forEach((g) => { if (!g.children.length) g.remove(); });
    }
    function remember(t) {
        const at = Date.now();
        seen.set(t.task_id, { task: t, at });
        return at;
    }

    function upsert(container, task, opts = {}) {
        if (!container || !task || !task.task_id) return null;
        const at = remember(task);
        if (!collapsed.has(task.task_id)) collapsed.set(task.task_id, false);
        let card = findCard(container, task.task_id);
        const host = card && card.closest(AGENT);
        if (host) {
            place(host);
        } else {
            const target = byMessageId(container, task.message_id)
                || (opts.pendingEl && opts.pendingEl.isConnected ? opts.pendingEl : null)
                || pendingMessage(container);
            if (!card) card = document.createElement('div');
            if (target) { const lg = slotIn(target); lg.dataset.live = '1'; lg.appendChild(card); }
            else if (!card.parentElement) orphanSlot(container).appendChild(card);
            dropEmptyGroups(container);
        }
        render(card, task, at);
        if (task.status === 'running') ensureTicker();
        return card;
    }

    function mount(messageEl, tasks) {
        if (!messageEl || !Array.isArray(tasks) || !tasks.length) return;
        const root = messageEl.parentElement || document;
        const g = slotIn(messageEl);
        tasks.forEach((t) => {
            if (!t || !t.task_id) return;
            if (!collapsed.has(t.task_id)) collapsed.set(t.task_id, true);
            const prev = seen.get(t.task_id);
            const fresh = !prev || prev.task.seq == null || (prev.task.seq || 0) <= (t.seq || 0);
            const snap = fresh ? t : prev.task;
            const at = fresh ? remember(t) : prev.at;
            const card = findCard(root, t.task_id) || document.createElement('div');
            render(card, snap, at);
            g.appendChild(card);
            if (snap.status === 'running') ensureTicker();
        });
        dropEmptyGroups(root);
    }

    // Финал ответа: карточки этого хода собираются в ответ (под текстом, над .message-actions).
    function settle(container, msg) {
        if (!container) return;
        const target = byMessageId(container, msg && msg.message_id)
            || [...container.querySelectorAll(`:scope > ${AGENT}`)].pop();
        if (!target) return;
        const ids = new Set((msg && msg.task_ids) || []);
        target.querySelectorAll('.task-card').forEach((c) => ids.add(c.dataset.taskId));
        container.querySelectorAll(':scope > .task-cards--orphan .task-card').forEach((c) => ids.add(c.dataset.taskId));
        if (!ids.size) return;
        const g = slotIn(target);
        ids.forEach((id) => {
            let card = findCard(container, id);
            const s = seen.get(id);
            if (!card && s) { card = document.createElement('div'); render(card, s.task, s.at); }
            if (card) g.appendChild(card);
        });
        dropEmptyGroups(container);
    }

    function toggle(card) {
        const id = card.dataset.taskId;
        const isCol = !card.classList.contains('is-collapsed');
        collapsed.set(id, isCol);
        card.classList.toggle('is-collapsed', isCol);
        const h = card.querySelector(':scope > .tc-head');
        if (h) h.setAttribute('aria-expanded', isCol ? 'false' : 'true');
    }
    document.addEventListener('click', (e) => {
        const h = e.target.closest && e.target.closest('.task-card > .tc-head');
        if (!h) return;
        const sel = window.getSelection && window.getSelection();
        if (sel && !sel.isCollapsed && h.contains(sel.anchorNode)) return; // выделяли текст, не клик
        toggle(h.parentElement);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const h = e.target.closest && e.target.closest('.task-card > .tc-head');
        if (!h || e.target !== h) return;
        e.preventDefault();
        toggle(h.parentElement);
    });

    window.TaskCards = { upsert, mount, settle, place, toggle, _render: render };
})();
