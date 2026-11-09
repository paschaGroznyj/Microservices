/* task-hint.js — ручной триггер задачника.
 * Автотриггер выключен: chats_service шлёт task_update с awaiting_user=true. Когда агент
 * свободен, чат открыт и поле ввода пустое, в #message-input печатается подсказка
 * «Задача выполнена, посмотри результат», кнопка отправки загорается (событие input).
 * При отправке consume() отдаёт task_ids, если текст подсказки остался в сообщении:
 * chats_service забирает результаты (claim_for_user) и прикладывает их агенту.
 * Состояние только в памяти: после F5 подсказка не появляется. Задачи, завершившиеся,
 * пока чат был закрыт, подсказываются при его открытии (main — WS по всем чатам,
 * sigma — SSE /api/chats/{id}/tasks/stream с курсором).
 * API (window.TaskHint): note(app, chatId, task), consume(chatId, text), watchSigma(app)
 */
(function () {
    'use strict';
    const pending = new Map();  // chat_id -> Map(task_id -> status)
    const offered = new Map();  // chat_id -> {text, ids, st}
    const done = new Set();     // task_id, которые уже подсказаны или отправлены
    let appRef = null, timer = null, typing = null, lastNote = 0;
    const DEBOUNCE_MS = 1200, CHAR_MS = 25;

    function hintText(statuses) {
        const n = statuses.length, bad = statuses.filter((s) => s === 'failed').length;
        if (n === 1) return bad ? 'Задача завершилась с ошибкой, посмотри результат' : 'Задача выполнена, посмотри результат';
        return `Задачи выполнены (${n})${bad ? ', есть ошибки' : ''}, посмотри результаты`;
    }
    function field() {
        const c = appRef?.getInputContainer?.();
        return c ? c.querySelector('#message-input') : null;
    }
    const isText = (f) => f.tagName === 'TEXTAREA' || f.tagName === 'INPUT';
    function readField(f) { return isText(f) ? f.value : f.textContent; }
    function setField(f, v) { if (isText(f)) f.value = v; else f.textContent = v; }
    function writeField(f, v) { setField(f, v); f.dispatchEvent(new Event('input', { bubbles: true })); }
    function caretEnd(f) {
        if (document.activeElement !== f || isText(f)) return;
        const r = document.createRange(); r.selectNodeContents(f); r.collapse(false);
        const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    }
    function stopTyping(finish) {
        if (!typing) return;
        clearInterval(typing.iv);
        typing.f.removeEventListener('beforeinput', typing.onUser);
        const t = typing; typing = null;
        if (finish && t.f.isConnected) { writeField(t.f, t.text); caretEnd(t.f); }
    }

    function note(app, chatId, task) {
        if (app) appRef = app;
        if (!task || !task.task_id || chatId == null) return;
        const id = String(task.task_id);
        if (!task.awaiting_user || !['completed', 'failed'].includes(task.status) || done.has(id)) return;
        const key = String(chatId);
        if (!pending.has(key)) pending.set(key, new Map());
        pending.get(key).set(id, task.status);
        lastNote = Date.now();
        if (!timer) timer = setInterval(tryShow, 500);
    }

    function tryShow() {
        if (!pending.size) { clearInterval(timer); timer = null; return; }
        if (!appRef || typing || Date.now() - lastNote < DEBOUNCE_MS) return;
        const key = String(appRef.currentChat ?? '');
        const p = pending.get(key);
        if (!p || !p.size) return;
        if (appRef._runState && appRef._runState !== 'idle') return;  // агент работает — после ответа
        const f = field();
        if (!f) return;
        const cur = readField(f).trim();
        const off = offered.get(key);
        // Поле занято текстом пользователя — ждём. Наша подсказка без правок — дополняем её.
        if (cur && !(off && cur === off.text)) return;
        const merged = new Map(off && cur ? off.ids.map((i) => [i, off.st[i]]) : []);
        p.forEach((st, id) => merged.set(id, st));
        pending.delete(key);
        const ids = [...merged.keys()], st = Object.fromEntries(merged);
        const text = hintText(ids.map((i) => st[i]));
        ids.forEach((i) => done.add(i));
        offered.set(key, { text, ids, st });
        typeInto(f, text);
    }

    function typeInto(f, text) {
        let i = 0;
        writeField(f, '');
        const onUser = () => stopTyping(true);  // пользователь начал печатать — дописываем мгновенно
        f.addEventListener('beforeinput', onUser);
        typing = { f, text, onUser, iv: setInterval(() => {
            if (!f.isConnected) { stopTyping(false); return; }
            i += 1;
            writeField(f, text.slice(0, i));
            caretEnd(f);
            if (i >= text.length) stopTyping(false);
        }, CHAR_MS) };
    }

    // Отправка: task_ids, если подсказка осталась в тексте. Стёрли или заменили — задачи не
    // уходят и повторно не подсказываются (в БД ждут; при включении автотриггера их подберёт sweep).
    function consume(chatId, text) {
        stopTyping(true);
        const key = String(chatId ?? '');
        const off = offered.get(key);
        if (!off) return undefined;
        offered.delete(key);
        return String(text || '').includes(off.text) ? off.ids : undefined;
    }

    // sigma: у гейтвея нет WS — SSE задач текущего чата. Курсор по чатам: при открытии чата
    // приходят задачи, завершившиеся после загрузки страницы, пока чат был закрыт.
    function watchSigma(app) {
        if (!app || !window.EventSource) return;
        appRef = app;
        let es = null, esChat = null, base = null;
        const cursors = new Map();
        function open(chatId) {
            if (es) { es.close(); es = null; }
            esChat = chatId;
            if (chatId == null || chatId === '') return;
            const key = String(chatId);
            const after = cursors.has(key) ? cursors.get(key) : base;
            const url = `/api/chats/${encodeURIComponent(key)}/tasks/stream` + (after != null ? `?after=${after}` : '');
            es = new EventSource(url, { withCredentials: true });
            es.addEventListener('tasks', (e) => {
                let d; try { d = JSON.parse(e.data); } catch (_) { return; }
                if (d.hello) { if (base == null) base = d.cursor; cursors.set(key, d.cursor); return; }
                cursors.set(key, d.cursor);
                const box = app.getMessagesContainer?.();
                (d.tasks || []).forEach((t) => {
                    // карточку обновляем, только если она уже в ленте (иначе её покажет история)
                    if (box && window.TaskCards && String(app.currentChat) === key
                        && box.querySelector(`.task-card[data-task-id="${CSS.escape(String(t.task_id))}"]`)) {
                        window.TaskCards.upsert(box, t, {});
                    }
                    note(app, key, t);
                });
            });
        }
        setInterval(() => {
            const c = app.currentChat ?? null;
            if (String(c) !== String(esChat)) open(c);
        }, 1000);
    }

    window.TaskHint = { note, consume, watchSigma, _hintText: hintText };
})();
