/* ══════════════════════════════════════════════
   GRAPH.JS — Knowledge Graph визуализация
   vis-network + тема + поиск + легенда + панель
══════════════════════════════════════════════ */

class KnowledgeGraph {
    constructor() {
        this.network    = null;
        this.allNodes   = null;   // DataSet — кэш на всю сессию
        this.allEdges   = null;   // DataSet — кэш на всю сессию
        this.rawData    = null;   // сырой JSON с бэка
        this.activeType = null;   // фильтр легенды

        // Конфиги групп: цвет зависит от темы, задаётся через _groupColors()
        this.GROUPS = {
            'wiki':            { label: 'Wiki',           shape: 'star'    },
            'agent-inference': { label: 'Агент',          shape: 'dot'     },
            'user':            { label: 'Пользователь',   shape: 'dot'     },
            'web':             { label: 'Веб',            shape: 'dot'     },
            'document':        { label: 'Документ',       shape: 'dot'     },
            'other':           { label: 'Другое',         shape: 'dot'     },
        };
    }

    /* ── Инициализация ─────────────────────────────── */
    async init() {
        const section = document.getElementById('graph-section');
        if (!section) return;

        this._buildLayout(section);
        this._bindSearch();
        this._bindLegend();
        this._bindThemeObserver();

        await this.load();
    }

    /* ── HTML-скелет секции ────────────────────────── */
    _buildLayout(section) {
        section.innerHTML = `
<div class="graph-wrap">

    <!-- Тулбар -->
    <div class="graph-toolbar">
        <input  class="graph-search" id="graph-search"
                type="text" placeholder="Поиск по заголовку или описанию…" autocomplete="off"/>
        <button class="graph-btn" id="graph-fit" title="Уместить граф">⊞</button>
        <button class="graph-btn" id="graph-reload" title="Обновить данные">↺</button>
        <span   class="graph-stats" id="graph-stats"></span>
    </div>

    <!-- Легенда -->
    <div class="graph-legend" id="graph-legend"></div>

    <!-- Канвас -->
    <div class="graph-canvas" id="graph-canvas"></div>

    <!-- Боковая панель -->
    <div class="graph-panel hidden" id="graph-panel">
        <button class="graph-panel-close" id="graph-panel-close">✕</button>
        <div class="graph-panel-body" id="graph-panel-body"></div>
    </div>

</div>`;

        document.getElementById('graph-fit')
            ?.addEventListener('click', () => this.network?.fit({ animation: true }));
        document.getElementById('graph-reload')
            ?.addEventListener('click', () => this.load(true));
        document.getElementById('graph-panel-close')
            ?.addEventListener('click', () => this._closePanel());
    }




    // async load(force = false) {
    //     if (this.allNodes && !force) {
    //         this._render();
    //         return;
    //     }

    //     const canvas = document.getElementById('graph-canvas');
    //     if (canvas) canvas.innerHTML = '<div class="graph-loading">Загрузка графа…</div>';

    //     // Синтетика
    //     const syn = this._syntheticData();
    //     console.log(syn)
    //     this.rawData = syn;
    //     this._prepareDatasets(syn);
    //     this._render();
    //     this._updateStats(syn.stats);
    // }

    /* ── Загрузка данных ───────────────────────────── */
    async load(force = false) {
        if (this.allNodes && !force) {
            // данные уже есть — просто рисуем
            this._render();
            return;
        }

        const canvas = document.getElementById('graph-canvas');
        if (canvas) canvas.innerHTML = '<div class="graph-loading">Загрузка графа…</div>';
        
        try {
            const resp = await fetch('/api/graph');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const json = await resp.json();
            this.rawData = json;
            this._prepareDatasets(json);
            this._render();
            this._updateStats(json.stats);
        } catch (e) {
            if (canvas) canvas.innerHTML = `<div class="graph-error">Ошибка загрузки: ${e.message}</div>`;
        }

    }

    /* ── Синтетика для демо (если бэк вернул пустой граф) ── */
    _syntheticData() {
        return {
            nodes: [
                { id: 1,    label: 'k1',   title: 'Process Mining — общий обзор',    group: 'agent-inference', value: 5, shape: 'dot', type: 'knowledge', text_full: '## Process Mining\nМетодология анализа бизнес-процессов на основе событийных журналов. Позволяет обнаруживать, проверять и улучшать процессы.', importance: 5 },
                { id: 2,    label: 'k2',   title: 'Conformance Checking',             group: 'user',            value: 3, shape: 'dot', type: 'knowledge', text_full: '## Conformance Checking\nПроверка соответствия реального процесса нормативной модели. Выявляет отклонения и нарушения.', importance: 3 },
                { id: 3,    label: 'k3',   title: 'Predictive Monitoring',            group: 'web',             value: 4, shape: 'dot', type: 'knowledge', text_full: '## Predictive Monitoring\nПредсказание будущего поведения процессов с использованием ML-моделей.', importance: 4 },
                { id: 4,    label: 'k4',   title: 'Alpha Algorithm',                  group: 'document',        value: 2, shape: 'dot', type: 'knowledge', text_full: '## Alpha Algorithm\nКлассический алгоритм обнаружения процессов. Строит сеть Петри из событийного журнала.', importance: 2 },
                { id: 'w1', label: 'Wiki: PM Overview', title: 'Wiki: PM Overview',  group: 'wiki',            value: 5, shape: 'star', type: 'wiki', text_full: '# PM Overview\nОбзорная wiki-страница по Process Mining. Охватывает discovery, conformance, enhancement.', source_ids: [1, 2] },
            ],
            edges: [
                { from: 1, to: 2, label: 'related',  width: 2, arrows: 'to', type: 'relation', dashes: false },
                { from: 1, to: 3, label: 'related',  width: 2, arrows: 'to', type: 'relation', dashes: false },
                { from: 2, to: 4, label: 'uses',      width: 1, arrows: 'to', type: 'relation', dashes: false },
                { from: 'w1', to: 1, width: 1, type: 'wiki_link', dashes: true },
                { from: 'w1', to: 2, width: 1, type: 'wiki_link', dashes: true },
            ],
            stats: { knowledge_count: 4, relations_count: 3, wiki_count: 1 }
        };
    }

    /* ── Подготовка DataSet ────────────────────────── */
    _prepareDatasets(json) {
        let nodes = json.nodes || [];
        let edges = json.edges || [];

        // Если бэк вернул пустые — используем синтетику
        if (nodes.length === 0) {
            const syn = this._syntheticData();
            nodes = syn.nodes;
            edges = syn.edges;
        }

        const colors = this._groupColors();

        const visNodes = nodes.map(n => {
            const grp = n.group || 'other';
            const col = colors[grp] || colors['other'];
            return {
                ...n,
                color: {
                    background: col.bg,
                    border:     col.border,
                    highlight:  { background: col.highlight, border: col.border },
                    hover:      { background: col.highlight, border: col.border },
                },
                font:  { color: col.font, size: 11 },
                size:  8 + (n.value || 1) * 2,
            };
        });

        const visEdges = edges.map(e => ({
            ...e,
            color: { color: this._edgeColor(), opacity: 0.5 },
            font:  { color: this._edgeColor(), size: 5, strokeWidth: 0 },
            arrows: e.arrows || (e.type === 'relation' ? 'to' : undefined),
            smooth: { type: 'curvedCW', roundness: 0.1 },
        }));

        this.allNodes = new vis.DataSet(visNodes);
        this.allEdges = new vis.DataSet(visEdges);
    }

    /* ── Рендер vis-network ────────────────────────── */
    _render() {
        const canvas = document.getElementById('graph-canvas');
        if (!canvas) return;
        canvas.innerHTML = '';

        const options = this._visOptions();
        this.network = new vis.Network(
            canvas,
            { nodes: this.allNodes, edges: this.allEdges },
            options
        );

        this.network.on('click', params => {
            if (params.nodes.length > 0) {
                const nodeId = params.nodes[0];
                const node   = this.allNodes.get(nodeId);
                if (node) this._openPanel(node);
            } else {
                this._closePanel();
            }
        });

        this._buildLegend();
    }

    /* ── vis-network опции ─────────────────────────── */
    _visOptions() {
        const isDark = document.documentElement.dataset.theme === 'dark'
                    || document.documentElement.classList.contains('dark');
        return {
            physics: {
                enabled: true,
                solver: 'forceAtlas2Based',
                forceAtlas2Based: {
                    gravitationalConstant: -40,
                    centralGravity:        0.005,
                    springLength:          120,
                    springConstant:        0.08,
                    damping:               0.6,
                },
                stabilization: { iterations: 150, updateInterval: 25 },
            },
            edges: {
                width:        0.5,
                selectionWidth: 0.3,
                arrows:       { to: { enabled: true, scaleFactor: 0.4 } },
                smooth:       { type: 'curvedCW', roundness: 0.1 },
                font:         { size: 4, color: isDark ? '#666' : '#999', strokeWidth: 0 },
                color:        { color: isDark ? '#444' : '#ccc', highlight: '#888', hover: '#888' },
            },
            nodes: {
                borderWidth:          1,
                borderWidthSelected:  2,
                chosen:               true,
            },
            interaction: {
                hover:         true,
                tooltipDelay:  200,
                hideEdgesOnDrag: true,
            },
            layout: { improvedLayout: false },
        };
    }

    /* ── Цвета групп (тема-зависимые) ─────────────── */
    _groupColors() {
        const dark = document.documentElement.dataset.theme === 'dark'
                  || document.documentElement.classList.contains('dark');
        return {
            'wiki':            { bg: dark ? '#3d2b6e' : '#ede9fe', border: '#7c3aed', highlight: dark ? '#4c3580' : '#ddd6fe', font: dark ? '#c4b5fd' : '#5b21b6' },
            'agent-inference': { bg: dark ? '#1e3a5f' : '#dbeafe', border: '#2563eb', highlight: dark ? '#254a75' : '#bfdbfe', font: dark ? '#93c5fd' : '#1e40af' },
            'user':            { bg: dark ? '#1e4d3a' : '#dcfce7', border: '#16a34a', highlight: dark ? '#25604a' : '#bbf7d0', font: dark ? '#86efac' : '#15803d' },
            'web':             { bg: dark ? '#4d3319' : '#ffedd5', border: '#ea580c', highlight: dark ? '#5f4020' : '#fed7aa', font: dark ? '#fdba74' : '#9a3412' },
            'document':        { bg: dark ? '#3d3319' : '#fef9c3', border: '#ca8a04', highlight: dark ? '#4d4020' : '#fef08a', font: dark ? '#fde047' : '#854d0e' },
            'other':           { bg: dark ? '#2d2d2d' : '#f3f4f6', border: '#6b7280', highlight: dark ? '#3d3d3d' : '#e5e7eb', font: dark ? '#9ca3af' : '#374151' },
        };
    }

    _edgeColor() {
        const dark = document.documentElement.dataset.theme === 'dark'
                  || document.documentElement.classList.contains('dark');
        return dark ? '#444' : '#d1d5db';
    }

    /* ── Легенда ───────────────────────────────────── */
    _buildLegend() {
        const legend = document.getElementById('graph-legend');
        if (!legend) return;

        const colors = this._groupColors();
        legend.innerHTML = '';

        Object.entries(this.GROUPS).forEach(([key, cfg]) => {
            const col  = colors[key] || colors['other'];
            const chip = document.createElement('button');
            chip.className   = 'legend-chip' + (this.activeType === key ? ' active' : '');
            chip.dataset.type = key;
            chip.innerHTML   = `<span class="legend-dot" style="background:${col.bg};border-color:${col.border}"></span>${cfg.label}`;
            legend.appendChild(chip);
        });

        // Кнопка «Все»
        const all = document.createElement('button');
        all.className   = 'legend-chip' + (!this.activeType ? ' active' : '');
        all.dataset.type = '';
        all.textContent  = 'Все';
        legend.prepend(all);
    }

    _bindLegend() {
        document.addEventListener('click', e => {
            const chip = e.target.closest('.legend-chip');
            if (!chip) return;

            this.activeType = chip.dataset.type || null;
            this._applyFilter();
            this._buildLegend(); // перестраиваем active-класс
        });
    }

    /* ── Фильтр по типу ────────────────────────────── */
    _applyFilter(searchTerm = '') {
        if (!this.allNodes) return;

        const term  = (searchTerm || document.getElementById('graph-search')?.value || '').toLowerCase().trim();
        const type  = this.activeType;

        const visibleIds = new Set();

        this.allNodes.forEach(n => {
            const matchType   = !type || n.group === type || n.type === type;
            const matchSearch = !term
                || (n.label  || '').toLowerCase().includes(term)
                || (n.title  || '').toLowerCase().includes(term)
                || (n.text_full || '').toLowerCase().includes(term);

            if (matchType && matchSearch) visibleIds.add(n.id);
        });

        // Обновляем hidden через opacity (не удаляем из DataSet — сохраняем физику)
        const colors = this._groupColors();   // ← добавить
        const updates = [];
        this.allNodes.forEach(n => {
            const visible = visibleIds.has(n.id);
            const col = colors[n.group] || colors['other'];   // ← добавить
            updates.push({
                id: n.id,
                opacity: visible ? 1 : 0.08,
                color: visible ? {                            // ← добавить
                    background: col.bg,
                    border:     col.border,
                    highlight:  { background: col.highlight, border: col.border },
                    hover:      { background: col.highlight, border: col.border },
                } : undefined,
            });
        });
        this.allNodes.update(updates);

        // Рёбра — скрываем если оба конца невидимы
        const edgeUpdates = [];
        this.allEdges.forEach(e => {
            const visible = visibleIds.has(e.from) || visibleIds.has(e.to);
            edgeUpdates.push({
                id: e.id,
                opacity: visible ? 1 : 0.03,   // ← было 0.5, теперь 1
                color: { color: this._edgeColor(), opacity: visible ? 0.5 : 0.03 }
            });
        });
        this.allEdges.update(edgeUpdates);
    }

    /* ── Поиск ─────────────────────────────────────── */
    _bindSearch() {
        document.addEventListener('input', e => {
            if (e.target.id !== 'graph-search') return;
            this._applyFilter(e.target.value);
        });
    }

    /* ── Боковая панель ────────────────────────────── */
    _openPanel(node) {
        const panel = document.getElementById('graph-panel');
        const body  = document.getElementById('graph-panel-body');
        if (!panel || !body) return;

        const isWiki  = node.type === 'wiki';
        const typeTag = isWiki ? '⭐ Wiki' : '🔵 Knowledge';
        const colors  = this._groupColors();
        const col     = colors[node.group] || colors['other'];

        // Markdown → HTML (если marked доступен)
        let content = node.text_full || node.title || '—';
        if (typeof marked !== 'undefined') {
            try { content = marked.parse(content); } catch {}
        } else {
            content = `<p>${content.replace(/\n/g, '<br>')}</p>`;
        }

        body.innerHTML = `
<div class="gp-header">
    <span class="gp-type-tag" style="background:${col.bg};color:${col.font || '#555'};border-color:${col.border}">${typeTag}</span>
    <h3 class="gp-title">${this._esc(node.label || node.title)}</h3>
    ${node.importance ? `<div class="gp-meta">Важность: <b>${node.importance}</b></div>` : ''}
    ${node.provenance ? `<div class="gp-meta">Источник: <b>${this._esc(node.provenance)}</b></div>` : ''}
    ${node.access_count != null ? `<div class="gp-meta">Обращений: <b>${node.access_count}</b></div>` : ''}
    ${node.status ? `<div class="gp-meta">Статус: <b>${this._esc(node.status)}</b></div>` : ''}
</div>
<div class="gp-content">${content}</div>
${node.source ? `<div class="gp-source"><a href="${this._esc(node.source)}" target="_blank" rel="noopener">🔗 Источник</a></div>` : ''}`;

        panel.classList.remove('hidden');
    }

    _closePanel() {
        document.getElementById('graph-panel')?.classList.add('hidden');
    }

    /* ── Статистика ────────────────────────────────── */
    _updateStats(stats) {
        const el = document.getElementById('graph-stats');
        if (!el || !stats) return;
        el.textContent = `Knowledge: ${stats.knowledge_count}  ·  Связи: ${stats.relations_count}  ·  Wiki: ${stats.wiki_count}`;
    }

    /* ── Смена темы — перекрасить граф ────────────── */
    _bindThemeObserver() {
        const observer = new MutationObserver(() => {
            if (!this.allNodes) return;
            this._repaintTheme();
        });
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
    }

    _repaintTheme() {
        if (!this.allNodes || !this.network) return;
        const colors = this._groupColors();
        const updates = [];
        this.allNodes.forEach(n => {
            const col = colors[n.group] || colors['other'];
            updates.push({
                id:    n.id,
                color: { background: col.bg, border: col.border, highlight: { background: col.highlight, border: col.border }, hover: { background: col.highlight, border: col.border } },
                font:  { color: col.font, size: 11 },
            });
        });
        this.allNodes.update(updates);

        const edgeColor = this._edgeColor();
        const edgeUpdates = [];
        this.allEdges.forEach(e => {
            edgeUpdates.push({ id: e.id, color: { color: edgeColor } });
        });
        this.allEdges.update(edgeUpdates);

        // Обновляем легенду
        this._buildLegend();
    }

    /* ── Хелпер ────────────────────────────────────── */
    _esc(str) {
        return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
}

/* ── Точка входа ────────────────────────────────── */
const knowledgeGraph = new KnowledgeGraph();
