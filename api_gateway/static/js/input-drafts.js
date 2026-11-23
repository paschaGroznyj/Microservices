/* InputDrafts — черновики поля ввода агента по чатам (app.js и app_sigma.js).
   Ключ — app._inputDraftKey(chatId): id чата или '__start__' для стартового экрана.
   Обычный текст хранится строкой. Если в поле есть чипы вставленного текста,
   храним разметку поля (с якорями .input-chip-anchor) и тексты чипов: при
   возврате в чат чипы собираются заново, в другом чате их не видно.
   Файлы (FileAttach) разводятся по чатам отдельно, ключом ctx в FileAttach.init. */
(() => {
    const fieldOf = (app) => app.getInputContainer()?.querySelector('#message-input') || null;

    // Чипы принадлежат уходящему чату: снимаем их из состояния и из полосы превью.
    function drop(app) {
        if (app._activePreviewChipId) {
            app._activePreviewChipId = null;
            try { app.closeTextPreview(); } catch (_) {}
        }
        app._pendingChips?.clear();
        app._fileChips?.clear();
        const box = app.getInputContainer();
        if (!box) return;
        box.querySelectorAll('.fa-preview-bar .input-chip').forEach((el) => el.remove());
        box.querySelectorAll('.fa-preview-bar').forEach((bar) => { try { app._syncInputPreviewBar(bar); } catch (_) {} });
    }

    function save(app, chatId) {
        if (!app._inputDrafts) app._inputDrafts = {};
        const key = app._inputDraftKey(chatId);
        const field = fieldOf(app);
        if (!field) return;
        const chips = app._pendingChips && app._pendingChips.size ? [...app._pendingChips.entries()] : [];
        if (chips.length && field.tagName !== 'TEXTAREA') {
            app._inputDrafts[key] = { html: field.innerHTML, chips, asFile: [...(app._fileChips || [])] };
        } else {
            const text = app.getInputText();
            if (text) app._inputDrafts[key] = text;
            else delete app._inputDrafts[key];
        }
        drop(app);
    }

    function caretToEnd(field) {
        const sel = window.getSelection();
        if (!sel) return;
        const r = document.createRange();
        r.selectNodeContents(field);
        r.collapse(false);
        sel.removeAllRanges();
        sel.addRange(r);
    }

    function restore(app, chatId) {
        const key = app._inputDraftKey(chatId);
        const d = app._inputDrafts?.[key];
        if (!d) return;
        const field = fieldOf(app);
        if (!field) return;
        if (typeof d === 'string') {
            app.setInputText(field, d);
            return;
        }
        drop(app);
        field.innerHTML = d.html;
        const asFile = new Set(d.asFile || []);
        d.chips.forEach(([id, text]) => app._insertInputChip(field, text, { restoreId: id, asFile: asFile.has(id) }));
        caretToEnd(field);
        try { field.dispatchEvent(new Event('input')); } catch (_) {}
    }

    window.InputDrafts = { save, restore, drop };
})();
