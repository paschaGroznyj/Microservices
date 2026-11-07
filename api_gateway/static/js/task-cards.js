/* task-cards.js — карточки фоновых задач (submit_task).
 * Два стиля: kind=script — консольное окно, kind=media — светлая карточка в тонах темы.
 * Расположение:
 *   live, пока ответа нет  — внутри ответа агента, сразу после .reasoning-block;
 *   после ответа и после F5 — отдельной группой .task-cards сразу после .agent-message.
 * API (window.TaskCards):
 *   upsert(container, task, {pendingEl})  событие task_update (WS main / опрос sigma)
 *   mount(messageEl, tasks)               история: message.tasks из get_chat_messages
 *   settle(container, botMsg)             финал ответа: live-карточки переезжают под ответ
 */
(function () {
    'use strict';
    const FINAL = new Set(['completed', 'failed', 'cancelled']);
    const STATUS_RU = {
        pending: 'в очереди', running: 'выполняется', cancelling: 'отмена…',
        completed: 'готово', failed: 'ошибка', cancelled: 'отменена',
    };
    const seen = new Map(); // task_id -> {task, at}: снимок и момент получения (для тикающего таймера)
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
        card.className = `task-card task-card--${kind} is-${st}`;
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
            `<div class="tc-head">${head}<span class="tc-title" title="${esc(t.label)}">${esc(t.label || 'Задача')}</span>`
            + `${pctTxt}<span class="tc-status">${STATUS_RU[st] || esc(st)}</span>`
            + `<span class="tc-time">${fmt(elapsed(t, at))}</span></div>`
            + bar
            + (kind === 'media' && !FINAL.has(st) ? '<div class="tc-shimmer"></div>' : '')
            + (stages ? `<ol class="tc-stages">${stages}</ol>` : '')
            + (log ? `<pre class="tc-log">${log}</pre>` : '')
            + foot;
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

    function groupAfter(anchor) {
        const next = anchor.nextElementSibling;
        if (next && next.classList.contains('task-cards')) return next;
        const g = document.createElement('div');
        g.className = 'task-cards';
        anchor.after(g);
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
        let card = findCard(container, task.task_id);
        if (!card) {
            let anchor = null;
            if (task.message_id) {
                const msgEl = container.querySelector(`.agent-message[data-message-id="${sel(task.message_id)}"]`);
                if (msgEl) anchor = msgEl;
            }
            if (!anchor) {
                anchor = (opts.pendingEl && opts.pendingEl.querySelector('.reasoning-block'))
                    || container.querySelector('.agent-message:not([data-id]) .reasoning-block');
            }
            card = document.createElement('div');
            if (anchor) {
                groupAfter(anchor).appendChild(card);
            } else {
                const g = document.createElement('div');
                g.className = 'task-cards';
                container.appendChild(g);
                g.appendChild(card);
            }
        }
        render(card, task, at);
        if (task.status === 'running') ensureTicker();
        return card;
    }

    function mount(messageEl, tasks) {
        if (!messageEl || !Array.isArray(tasks) || !tasks.length) return;
        const root = messageEl.parentElement || document;
        const g = groupAfter(messageEl);
        tasks.forEach((t) => {
            if (!t || !t.task_id) return;
            const prev = seen.get(t.task_id);
            const fresh = !prev || (prev.task.seq || 0) <= (t.seq || 0) || prev.task.seq == null;
            const snap = fresh ? t : prev.task;
            const at = fresh ? remember(t) : prev.at;
            const card = findCard(root, t.task_id) || document.createElement('div');
            render(card, snap, at);
            g.appendChild(card);
            if (snap.status === 'running') ensureTicker();
        });
        dropEmptyGroups(root);
    }

    function settle(container, msg) {
        if (!container) return;
        const mid = msg && msg.message_id;
        const target = (mid && container.querySelector(`.agent-message[data-message-id="${sel(mid)}"]`))
            || [...container.querySelectorAll('.agent-message')].pop();
        if (!target) return;
        const ids = new Set((msg && msg.task_ids) || []);
        target.querySelectorAll('.task-card').forEach((c) => ids.add(c.dataset.taskId));
        if (!ids.size) return;
        const g = groupAfter(target);
        ids.forEach((id) => {
            let card = findCard(container, id);
            const s = seen.get(id);
            if (!card && s) { card = document.createElement('div'); render(card, s.task, s.at); }
            if (card) g.appendChild(card);
        });
        dropEmptyGroups(container);
    }

    window.TaskCards = { upsert, mount, settle, _render: render };
})();
