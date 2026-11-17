/* ipynb-render.js — рендер Jupyter Notebook (.ipynb, nbformat 4) в панели превью.
 *
 * file-tiles.js → buildRendered('json', text, 'x.ipynb') вызывает NotebookRender.render(text, name)
 * и получает { cls, html, after, note, err } в том же формате, что и остальные рендеры.
 * Markdown-ячейки: marked + DOMPurify. Код: highlight.js по языку ядра.
 * Выводы: stream, execute_result, display_data, error. Из data берётся один MIME
 * по приоритету Jupyter: html → svg → png → jpeg → markdown → text/plain.
 * HTML и SVG вставляются только после DOMPurify, без него показываются текстом.
 */
(function (window) {
    'use strict';

    const HL_MAX = 200 * 1024;
    const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g;
    const PURIFY_OPTS = { FORBID_TAGS: ['style', 'script', 'iframe', 'form', 'input', 'link', 'meta'] };

    const esc = (t) => String(t ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const join = (v) => (Array.isArray(v) ? v.join('') : String(v ?? ''));
    const hl = (text, lang) => {
        const h = window.hljs;
        if (h && lang && text.length <= HL_MAX && h.getLanguage && h.getLanguage(lang)) {
            try { return h.highlight(text, { language: lang, ignoreIllegals: true }).value; } catch (_) {}
        }
        return esc(text);
    };
    const purify = (html) => (window.DOMPurify ? window.DOMPurify.sanitize(html, PURIFY_OPTS) : null);
    const md = (src) => {
        if (!window.marked) return `<pre>${esc(src)}</pre>`;
        try {
            const html = purify(window.marked.parse(src));
            return html == null ? `<pre>${esc(src)}</pre>` : html;
        } catch (_) { return `<pre>${esc(src)}</pre>`; }
    };
    const plural = (n, one, few, many) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
        return many;
    };

    function renderData(data) {
        const html = data['text/html'];
        if (html != null) {
            const clean = purify(join(html));
            if (clean != null) return `<div class="nb-out nb-out--html">${clean}</div>`;
        }
        const svg = data['image/svg+xml'];
        if (svg != null) {
            const clean = window.DOMPurify
                ? window.DOMPurify.sanitize(join(svg), { USE_PROFILES: { svg: true, svgFilters: true } })
                : null;
            if (clean != null) return `<div class="nb-out nb-out--img">${clean}</div>`;
        }
        for (const mime of ['image/png', 'image/jpeg', 'image/gif']) {
            if (data[mime] != null) {
                const b64 = join(data[mime]).replace(/\s/g, '');
                if (/^[A-Za-z0-9+/=]+$/.test(b64)) {
                    return `<div class="nb-out nb-out--img"><img src="data:${mime};base64,${b64}" alt="" loading="lazy"></div>`;
                }
            }
        }
        if (data['text/markdown'] != null) return `<div class="nb-out nb-out--md">${md(join(data['text/markdown']))}</div>`;
        if (data['text/plain'] != null) return `<pre class="nb-out nb-out--text">${esc(join(data['text/plain']).replace(ANSI, ''))}</pre>`;
        const other = Object.keys(data)[0];
        return other ? `<div class="nb-out nb-out--note">Вывод ${esc(other)} не отображается</div>` : '';
    }

    function renderOutput(out) {
        const ot = out && out.output_type;
        if (ot === 'stream') {
            const err = out.name === 'stderr';
            return `<pre class="nb-out nb-out--stream${err ? ' nb-out--stderr' : ''}">${esc(join(out.text).replace(ANSI, ''))}</pre>`;
        }
        if (ot === 'execute_result' || ot === 'display_data') return renderData(out.data || {});
        if (ot === 'error') {
            const tb = Array.isArray(out.traceback) && out.traceback.length
                ? out.traceback.join('\n')
                : `${out.ename || 'Error'}: ${out.evalue || ''}`;
            return `<pre class="nb-out nb-out--error">${esc(tb.replace(ANSI, ''))}</pre>`;
        }
        return '';
    }

    function renderCell(cell, lang) {
        const ct = cell && cell.cell_type;
        const src = join(cell && cell.source);
        if (ct === 'markdown') {
            if (!src.trim()) return '';
            return `<section class="nb-cell nb-cell--md">${md(src)}</section>`;
        }
        if (ct === 'raw') {
            return `<section class="nb-cell nb-cell--raw"><pre class="nb-raw">${esc(src)}</pre></section>`;
        }
        if (ct !== 'code') return '';
        const n = cell.execution_count;
        const label = n != null ? `[${esc(n)}]` : '[&nbsp;]';
        const outs = (cell.outputs || []).map(renderOutput).filter(Boolean).join('');
        return `<section class="nb-cell nb-cell--code">`
            + `<div class="nb-in"><span class="nb-prompt" aria-hidden="true">${label}</span>`
            + `<pre class="nb-src"><code class="hljs language-${esc(lang)}">${hl(src, lang)}</code></pre></div>`
            + (outs ? `<div class="nb-outs">${outs}</div>` : '')
            + `</section>`;
    }

    function renderNotebook(text, name) {
        let nb;
        try {
            nb = typeof text === 'string' ? JSON.parse(text) : text;
            if (!nb || !Array.isArray(nb.cells)) throw new Error('нет массива cells (нужен nbformat 4)');
        } catch (err) {
            return {
                cls: 'code', err: true,
                html: `<pre class="tp-code"><code class="hljs">${esc(typeof text === 'string' ? text : '')}</code></pre>`,
                note: `Ноутбук не разобран: ${err && err.message ? err.message : err}`,
            };
        }
        const meta = nb.metadata || {};
        const li = meta.language_info || {};
        const lang = String(li.name || meta.kernelspec?.language || 'python').toLowerCase();
        const kernel = meta.kernelspec?.display_name || li.name || '';
        const cells = nb.cells;
        const nCode = cells.filter((c) => c && c.cell_type === 'code').length;
        const info = [
            kernel && esc(kernel),
            `${cells.length} ${plural(cells.length, 'ячейка', 'ячейки', 'ячеек')}`,
            nCode && `${nCode} с кодом`,
        ].filter(Boolean).join(' · ');
        const body = cells.map((c) => renderCell(c, lang)).join('');
        return {
            cls: 'ipynb',
            html: `<div class="nb-head">${info}</div><div class="nb-cells">${body || '<p class="nb-empty">Ноутбук пуст.</p>'}</div>`,
            after: (root) => {
                root.querySelectorAll('.nb-cell--md a[href], .nb-out--md a[href], .nb-out--html a[href]').forEach((a) => {
                    a.target = '_blank'; a.rel = 'noopener noreferrer';
                });
                if (window.hljs) {
                    root.querySelectorAll('.nb-cell--md pre code, .nb-out--md pre code').forEach((c) => {
                        if (c.textContent.length <= HL_MAX) { try { window.hljs.highlightElement(c); } catch (_) {} }
                    });
                }
            },
        };
    }

    window.NotebookRender = { render: renderNotebook };
})(window);
