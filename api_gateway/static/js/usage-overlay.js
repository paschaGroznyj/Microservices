/* UsageOverlay — «Использование ресурсов за ход» в .message-actions ответа агента.
 * Данные: tokens_usage из финала хода (WS main / история из БД в main и Sigma).
 * input_tokens включает кеш: некэшированный = input - cache_read - cache_creation.
 * Модель показывается только семейством (claude, gpt, gemini, ...).
 */
(function () {
    'use strict';
    if (window.UsageOverlay) return;

    const FAMILIES = [
        ['claude', 'claude'], ['anthropic', 'claude'],
        ['gpt', 'gpt'], ['openai', 'gpt'], ['o1', 'gpt'], ['o3', 'gpt'], ['o4', 'gpt'],
        ['gemini', 'gemini'], ['google', 'gemini'],
        ['deepseek', 'deepseek'], ['qwen', 'qwen'], ['glm', 'glm'], ['zai', 'glm'],
        ['grok', 'grok'], ['xai', 'grok'], ['llama', 'llama'], ['mistral', 'mistral'],
        ['kimi', 'kimi'], ['moonshot', 'kimi'], ['gigachat', 'gigachat'], ['yandexgpt', 'yandexgpt'],
    ];

    function family(model) {
        if (!model) return null;
        const m = String(model).toLowerCase();
        const tail = m.split('/').pop();
        for (const [k, f] of FAMILIES) if (tail.startsWith(k)) return f;
        for (const [k, f] of FAMILIES) if (m.includes(k)) return f;
        return null;
    }

    const ICON = `<svg class="action-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <ellipse cx="12" cy="5.5" rx="7.5" ry="3" stroke="currentColor" stroke-width="1.8"/>
        <path d="M4.5 5.5v6.5c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3V5.5M4.5 12v6.5c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3V12"
            stroke="currentColor" stroke-width="1.8"/></svg>`;

    const n = (v) => { const x = Number(v); return Number.isFinite(x) && x > 0 ? Math.round(x) : 0; };
    const fmtInt = (v) => n(v).toLocaleString('ru-RU');
    function fmtShort(v) {
        v = n(v);
        if (v < 1000) return String(v);
        if (v < 1e6) return (v / 1000).toFixed(v < 1e5 ? 1 : 0).replace('.', ',').replace(/,0$/, '') + 'K';
        return (v / 1e6).toFixed(1).replace('.', ',').replace(/,0$/, '') + 'M';
    }

    // Нормализация: принимает tokens_usage из WS/БД (+ model / family).
    function normalize(raw) {
        if (!raw || typeof raw !== 'object') return null;
        const input = n(raw.input_tokens), output = n(raw.output_tokens);
        const cr = n(raw.cache_read_tokens), cc = n(raw.cache_creation_tokens);
        const total = n(raw.total_tokens) || input + output;
        if (!total) return null;
        return {
            input, output, total, cacheRead: cr, cacheWrite: cc,
            uncached: Math.max(0, input - cr - cc),
            hit: input ? cr / input : 0,
            family: raw.family || family(raw.model) || null,
        };
    }

    function read(el) {
        const raw = el?.dataset?.usage;
        if (!raw) return null;
        try { return JSON.parse(raw); } catch (_) { return null; }
    }

    let openFor = null, panel = null;

    function close() {
        if (panel) panel.remove();
        if (openFor) openFor.setAttribute('aria-expanded', 'false');
        panel = null; openFor = null;
        document.removeEventListener('pointerdown', onDocDown, true);
        document.removeEventListener('keydown', onKey, true);
        window.removeEventListener('resize', close);
        document.removeEventListener('scroll', onScroll, true);
    }
    function onDocDown(e) {
        if (panel?.contains(e.target) || openFor?.contains(e.target)) return;
        close();
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    function onScroll(e) { if (!panel?.contains(e.target)) close(); }

    function row(label, value) {
        return `<div class="uo-row"><span class="uo-l">${label}</span><span class="uo-v">${value}</span></div>`;
    }

    function place(btn) {
        const vw = document.documentElement.clientWidth, vh = window.innerHeight;
        const r = btn.getBoundingClientRect();
        const m = 8;
        const w = panel.offsetWidth, h = panel.offsetHeight;
        let left = Math.min(Math.max(m, r.left), vw - w - m);
        let top = r.top - h - 8;
        let below = false;
        if (top < m) { top = r.bottom + 8; below = true; }
        if (top + h > vh - m) top = Math.max(m, vh - h - m);
        panel.style.left = `${Math.round(left)}px`;
        panel.style.top = `${Math.round(top)}px`;
        panel.dataset.side = below ? 'below' : 'above';
    }

    function open(btn, u) {
        close();
        panel = document.createElement('div');
        panel.className = 'usage-overlay';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', 'Использование ресурсов за ход');
        const rows = [];
        if (u.family) rows.push(row('Модель', u.family));
        if (u.cacheRead || u.cacheWrite) rows.push(row('Попадание в кэш', `${(u.hit * 100).toFixed(1).replace('.', ',')}%`));
        rows.push(row('Некэшированный ввод', `${fmtInt(u.uncached)} ток`));
        if (u.cacheRead) rows.push(row('Кэшированный ввод', `${fmtInt(u.cacheRead)} ток`));
        if (u.cacheWrite) rows.push(row('Запись в кэш', `${fmtInt(u.cacheWrite)} ток`));
        rows.push(row('Выход', `${fmtInt(u.output)} ток`));
        panel.innerHTML = `
            <div class="uo-head">
                <span class="uo-title">${ICON}<span>Использование ресурсов за ход</span></span>
                <span class="uo-total">${fmtInt(u.total)} ток</span>
            </div>
            <div class="uo-body">${rows.join('')}</div>`;
        document.body.appendChild(panel);
        openFor = btn;
        btn.setAttribute('aria-expanded', 'true');
        place(btn);
        requestAnimationFrame(() => panel && panel.classList.add('is-open'));
        document.addEventListener('pointerdown', onDocDown, true);
        document.addEventListener('keydown', onKey, true);
        window.addEventListener('resize', close);
        document.addEventListener('scroll', onScroll, true);
    }

    function makeChip(u) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'action-button usage-chip';
        btn.title = 'Использование ресурсов за ход';
        btn.setAttribute('aria-haspopup', 'dialog');
        btn.setAttribute('aria-expanded', 'false');
        btn.innerHTML = `${ICON}<span class="usage-chip__val">${fmtShort(u.total)} ток</span>`;
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (openFor === btn) { close(); return; }
            open(btn, u);
        });
        return btn;
    }

    // Встраивает чип в .message-actions (перед временем). Идемпотентно.
    function decorate(actionsEl, raw) {
        if (!actionsEl || !actionsEl.classList?.contains('message-actions')) return false;
        const msgEl = actionsEl.closest('.agent-message');
        if (msgEl?.dataset?.streaming === 'true') return false;
        const u = normalize(raw || read(msgEl));
        if (!u) return false;
        let chip = actionsEl.querySelector(':scope > .usage-chip');
        if (chip) chip.remove();
        chip = makeChip(u);
        const time = actionsEl.querySelector(':scope > .message-time');
        actionsEl.insertBefore(chip, time || null);
        return true;
    }

    // Сохраняет usage на пузыре ответа и вставляет чип, как только появятся actions.
    function mount(msgEl, raw) {
        if (!msgEl || !raw) return;
        const u = normalize(raw);
        if (!u) return;
        const stored = { input_tokens: u.input, output_tokens: u.output, total_tokens: u.total,
            cache_read_tokens: u.cacheRead, cache_creation_tokens: u.cacheWrite, family: u.family };
        msgEl.dataset.usage = JSON.stringify(stored);
        const tryNow = () => {
            const a = msgEl.querySelector(':scope > .message-actions');
            return a ? decorate(a, stored) : false;
        };
        if (tryNow()) return;
        let tries = 0;
        const t = setInterval(() => { if (tryNow() || ++tries > 40) clearInterval(t); }, 150);
    }

    window.UsageOverlay = { family, normalize, decorate, mount, close };
})();
