// CatchPM — Chat SPA
class ChatApp {
    constructor() {
        this.currentUser   = null;
        this.currentChat   = null;
        this.isUIReady     = false;
        this.ws            = null;
        this.chats         = [];
        this.currentTab    = 'agent'; // 'agent' | 'graph'
        this._runState = 'idle';      // idle | running | stopping
        this._activeRequestId = null;
        this._statusPollTimer = null;  // фолбэк-таймер, если SSE недоступен
        this.INPUT_MAX_CHARS = 10000;  // жёсткий предел длины поля ввода

        /* ══════════════════════════════════════════
           ВАРИАНТ 2: МУЛЬТИПЛЕКС СТРИМИНГА ПО ЧАТАМ (SSE)
           Раньше весь стрим-стейт был глобальным (this._pendingBotEl,
           this._streamBuffer, this._statusSse, this._streamRaf ...) — один
           на инстанс. При переключении чата EventSource закрывался и стрим
           обрывался.

           Теперь на КАЖДЫЙ чат — свой объект стрима (this._streams[chatId])
           и свой SSE-источник (this._sources[chatId]). Уходя из стримящего
           чата, мы НЕ закрываем его EventSource: он продолжает копить чанки
           в свой (открепленный от DOM) узел в фоне. При возврате — просто
           re-attach готового узла.

           Совместимость: старые методы стрима обращаются к глобальным
           this._pendingBotEl / this._streamBuffer и т.д. Мы делаем эти поля
           ПРОКСИ-геттерами на активный стрим (this._streams[currentChat]),
           а фоновые источники пишут напрямую в свой стрим по chatId из
           замыкания (см. _startStatusPolling).
        ══════════════════════════════════════════ */
        this._streams = {};    // chatId -> объект стрима (см. _getStream)
        this._sources = {};    // chatId -> EventSource

        const STREAM_FIELDS = [
            'pendingBotEl', 'pendingBotText', 'liveBlockEl', 'reasoningWrapEl',
            'streamBuffer', 'streamShown', 'streamDone', 'streamIsFinal',
            'streamFinalData', 'streamingActive', 'streamRaf', 'lastStreamRender',
            'botAnswered', 'runStartedAt', 'reasoningDurationSec', 'reasoningTimer',
        ];
        for (const f of STREAM_FIELDS) {
            const prop = '_' + f;
            Object.defineProperty(this, prop, {
                get() {
                    const s = this._streams[this.currentChat];
                    return s ? s[f] : undefined;
                },
                set(v) {
                    const s = this._ensureStream(this.currentChat);
                    if (s) s[f] = v;
                },
                configurable: false,
                enumerable: false,
            });
        }
    }

    /* Фабрика чистого объекта стрима для одного чата. */
    _newStreamState() {
        return {
            pendingBotEl: null,
            pendingBotText: '',
            liveBlockEl: null,
            reasoningWrapEl: null,
            streamBuffer: '',
            streamShown: '',
            streamDone: false,
            streamIsFinal: false,
            streamFinalData: null,
            streamingActive: false,
            streamRaf: null,
            lastStreamRender: 0,
            botAnswered: false,
            runStartedAt: 0,
            reasoningDurationSec: 0,
            reasoningTimer: null,
        };
    }

    _ensureStream(chatId) {
        if (chatId == null) return null;
        const key = String(chatId);
        if (!this._streams[key]) this._streams[key] = this._newStreamState();
        return this._streams[key];
    }

    _getStream(chatId) {
        const key = String(chatId);
        return this._streams[key] || null;
    }

    _resolveStream(chatId) {
        if (chatId != null) return this._ensureStream(chatId);
        return this._ensureStream(this.currentChat);
    }

    _isActiveChat(chatId) {
        return String(chatId) === String(this.currentChat);
    }

    /* Прокси на активный SSE-источник — совместимость со старым this._statusSse. */
    get _statusSse() {
        return this._sources[this.currentChat] || null;
    }
    set _statusSse(v) {
        if (this.currentChat == null) return;
        const key = String(this.currentChat);
        if (v == null) delete this._sources[key];
        else this._sources[key] = v;
    }

    /* Обрезает содержимое поля ввода до INPUT_MAX_CHARS символов.
       Учитывает полный текст с раскрытыми чипами (getInputText). Если
       перебор — усекаем хвостовой текстовый узел, сохраняя чипы. */
    _enforceInputLimit(field) {
        if (!field) return;
        const max = this.INPUT_MAX_CHARS;
        if (this.getInputText().length <= max) return;

        // Пока превышаем лимит — режем по одному символу с конца последнего
        // текстового узла. Чипы (свёрнутый текст) не трогаем как атомарные.
        let guard = 0;
        while (this.getInputText().length > max && guard < 100000) {
            guard++;
            let lastText = null;
            const findLast = (node) => {
                node.childNodes.forEach((n) => {
                    if (n.nodeType === Node.TEXT_NODE && n.textContent.length > 0) {
                        lastText = n;
                    } else if (n.nodeType === Node.ELEMENT_NODE &&
                               !(n.getAttribute && n.getAttribute('data-chip-id'))) {
                        findLast(n);
                    }
                });
            };
            findLast(field);
            if (!lastText) break;
            lastText.textContent = lastText.textContent.slice(0, -1);
        }
        // Ставим каретку в конец
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(field);
        range.collapse(false);
        sel.removeAllRanges();
        sel.addRange(range);
        this.showToast('Достигнут предел ' + max + ' символов');
    }

    /* ══════════════════════════════════════════
       INIT
    ══════════════════════════════════════════ */
    async init() {
        await this.checkAuth();
        if (!this.currentUser) {
            window.location.href = '/login-page';
            return;
        }

        // Передаём username в ToolsPanel для проверки прав и подтягиваем чаты + user_settings
        await Promise.all([
            window.ToolsPanel?.setUser(this.currentUser.username),
            this.loadChats(),
        ]);
        const userSettings = await window.ToolsPanel?.loadUserSettings?.();

        initMobileMenu();
        this.initTabSwitcher();
        this.initSidebarCollapse();
        this.initHelp();
        this.initChatsSectionToggle();
        this.initVisibilityReconnect();
        this.initTextPreview();
        window.SkillsPanel?.init(this);
        window.GroupChat?.init(this);
        const restored = await this._restoreSessionAfterAuthReload(userSettings);
        if (!restored) {
            await this.renderMessageInput();
        }
    }

    async _restoreSessionAfterAuthReload(userSettings = null) {
        try {
            const pendingRaw = sessionStorage.getItem('catchpm:pending_send_after_auth');
            const restoreChatId = sessionStorage.getItem('catchpm:restore_chat_id');
            sessionStorage.removeItem('catchpm:pending_send_after_auth');
            sessionStorage.removeItem('catchpm:restore_chat_id');

            if (pendingRaw) {
                const pending = JSON.parse(pendingRaw);
                if (pending && pending.text && (Date.now() - Number(pending.ts || 0) < 180_000)) {
                    const cid = pending.chatId || restoreChatId;
                    if (cid) {
                        if (!this.chats.some(c => String(c.id) === String(cid))) {
                            this.chats.unshift({ id: cid, last_message: pending.text, timestamp: new Date().toISOString() });
                            this.renderChats(this.chats);
                        }
                        await this.openChat(cid);
                    } else {
                        await this.renderMessageInput();
                    }
                    await this.sendMessage(pending.text, pending.pollAnswer || null);
                    return true;
                }
            }

            if (restoreChatId && this.chats.some(c => !c.is_deleted && String(c.id) === String(restoreChatId))) {
                await this.openChat(restoreChatId);
                return true;
            }

            const savedChatId = userSettings?.active_chat_id ? String(userSettings.active_chat_id).trim() : '';
            if (savedChatId) {
                const existsInDb = this.chats.some(c => !c.is_deleted && String(c.id) === savedChatId);
                if (existsInDb) {
                    await this.openChat(savedChatId, { skipSaveSettings: true });
                    return true;
                }
                window.ToolsPanel?.saveUserSettings?.({ active_chat_id: null });
            }
        } catch (e) {
            console.warn('_restoreSessionAfterAuthReload error:', e);
        }
        return false;
    }

    /* ══════════════════════════════════════════
       TEXT PREVIEW — правый сайдбар просмотра
       длинного текста. Регистрирует глобальную ссылку
       (для inline-onclick чипов) и обработчик крестика.
    ══════════════════════════════════════════ */
    initTextPreview() {
        window.__catchpmApp = this;
        this._activePreviewChipEl = null;
        this._activePreviewChipId = null;
        document.getElementById('text-preview-close')
            ?.addEventListener('click', () => this.closeTextPreview());

        const body = document.getElementById('text-preview-body');
        if (body && !body.dataset.previewBound) {
            body.dataset.previewBound = '1';
            body.addEventListener('input', () => {
                this._syncActivePreviewChipFromBody();
            });
            body.addEventListener('paste', (e) => {
                const cd = e.clipboardData || window.clipboardData;
                if (!cd) return;
                const pasted = cd.getData('text/plain') || cd.getData('text') || '';
                e.preventDefault();
                if (pasted) {
                    document.execCommand('insertText', false, pasted);
                }
                this._syncActivePreviewChipFromBody();
            });
        }
    }


    /* ══════════════════════════════════════════
       TAB SWITCHER (agent ↔ graph)
    ══════════════════════════════════════════ */
    initTabSwitcher() {
    document.querySelectorAll('.pill[data-tab], .nav-item[data-tab], .sidebar-skills-btn[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            this.switchTab(tab);
        });
    });
}

    /* ════════════════════════════════
       SIDEBAR COLLAPSE (desktop) — сворачивание
       левой панели. Кнопка в шапке сайдбара
       сворачивает, кнопка в topbar разворачивает.
    ════════════════════════════════ */
    initSidebarCollapse() {
        const layout = document.querySelector('.chat-layout');
        const collapseBtn = document.getElementById('sidebar-collapse-btn');
        const expandBtn = document.getElementById('sidebar-expand-btn');
        if (!layout) return;

        const KEY = 'catchpm:sidebar-collapsed';
        const apply = (collapsed) => {
            layout.classList.toggle('sidebar-collapsed', collapsed);
            if (expandBtn) expandBtn.hidden = !collapsed;
            if (collapseBtn) collapseBtn.setAttribute('aria-expanded', String(!collapsed));
            try { localStorage.setItem(KEY, collapsed ? '1' : '0'); } catch (e) {}
        };

        // На десктопе восстанавливаем состояние; на мобилке — отдельный механизм (mobile.js).
        if (window.innerWidth > 768) {
            let saved = '0';
            try { saved = localStorage.getItem(KEY) || '0'; } catch (e) {}
            apply(saved === '1');
        }

        collapseBtn?.addEventListener('click', () => apply(true));
        expandBtn?.addEventListener('click', () => apply(false));

        // Гамбургер в топбаре универсален: на десктопе он тоглит collapse,
        // на мобилке (<=768) открытие оверлея рулит mobile.js — его не трогаем.
        const topbarToggle = document.getElementById('mobile-menu-toggle');
        topbarToggle?.addEventListener('click', () => {
            if (window.innerWidth <= 768) return; // мобилка — отработает mobile.js
            apply(!layout.classList.contains('sidebar-collapsed'));
        });
    }

    switchTab(tab) {
        if (this.currentTab === tab) return;
        this.currentTab = tab;

        // Если уходим со стартового экрана в "Граф" без выбранного чата,
        // снимаем стартовый режим у .chat-main, иначе шапка остаётся
        // абсолютной/прозрачной и перекрывает контент графа.
        const chatMain = document.querySelector('.chat-main');
        const inputContainer = this.getInputContainer();
        if (tab === 'graph') {
            chatMain?.classList.remove('start-screen-mode');
            inputContainer?.classList.remove('start-screen-host');
        }

        // Переключаем views
        document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
        document.getElementById(`view-${tab}`)?.classList.remove('hidden');

        // Активный pill / nav-item
        document.querySelectorAll('.pill[data-tab], .nav-item[data-tab], .sidebar-skills-btn[data-tab]').forEach(n => {
            n.classList.toggle('active', n.dataset.tab === tab);
        });

        // Заголовок шапки по активной вкладке
        const titleEl = document.getElementById("main-title");
        if (titleEl) {
            if (tab === 'skills') titleEl.textContent = 'Магазин скиллов';
            else if (tab === 'agent') titleEl.textContent = 'Агент';
            else if (tab === 'graph') titleEl.textContent = 'Граф';
            else if (tab === 'group') titleEl.textContent = 'Общий чат';
            else titleEl.textContent = 'AuGent';
        }

        // Таб-специфичная логика
        if (tab === 'agent') {
            this.renderMessageInput();
        }
        if (tab === 'graph') {
            console.log('INIT GRAPH')
            knowledgeGraph.init();
        }
        if (tab === 'skills') {
            chatMain?.classList.remove('start-screen-mode');
            window.SkillsPanel?.open(this);
        }
        if (tab === 'group') {
            chatMain?.classList.remove('start-screen-mode');
            inputContainer?.classList.remove('start-screen-host');
            window.GroupChat?.open(this);
        }
    }


    /* ══════════════════════════════════════════
       AUTH
    ══════════════════════════════════════════ */
    async checkAuth() {
        try {
            const res = await fetch('/api/auth/me', { credentials: 'include' });
            if (!res.ok) return false;
            const data = await res.json();
            this.currentUser = data;

            const el = document.getElementById('username');
            if (el && data.username) el.textContent = data.username;
            return true;
        } catch (e) {
            console.warn('Auth check error:', e);
            return false;
        }
    }

    /* ══════════════════════════════════════════
       CHATS LIST
    ══════════════════════════════════════════ */
    async loadChats() {
        try {
            const res = await fetch('/api/chats', { credentials: 'include' });
            if (res.ok) {
                this.chats = await res.json();
                this.renderChats(this.chats);
            }
        } catch (e) {
            console.error('loadChats error:', e);
        }
    }

    renderChats(chats) {
        const list = document.getElementById('chat-list');
        const favList = document.getElementById('chat-favorites-list');
        if (!list) return;

        const escapeHtml = (str) => {
            return (str || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        };

        const stripHtml = (html) => {
            if (!html) return '';
            const doc = new DOMParser().parseFromString(html, 'text/html');
            return doc.body.textContent || '';
        };

        // Если есть renamed_title — показываем его как единственную строку (без preview).
        // Если нет — показываем последнее сообщение как единственную строку.
        const getDisplayLine = (chat) => {
            const renamed = (chat.renamed_title || '').trim();
            if (renamed) return { title: escapeHtml(renamed), preview: null, kind: 'renamed' };

            // Автогенерированный заголовок (по первому обмену) — приоритетнее last_message.
            const auto = (chat.auto_title || '').trim();
            if (auto) return { title: escapeHtml(auto), preview: null, kind: 'auto' };

            const raw = (chat.last_message || '').trim();
            if (!raw || raw === 'Чат создан') return { title: 'Новый чат', preview: null, kind: 'auto' };
            const plainText = stripHtml(raw).trim();
            const truncated = plainText.length > 60 ? plainText.slice(0, 60) + '...' : plainText;
            return { title: null, preview: escapeHtml(truncated) };
        };

        const sorted = (Array.isArray(chats) ? chats : [])
            .filter(chat => chat && !chat.is_deleted)
            .slice()
            .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

        const buildList = (items) => {
            if (!items.length) {
                return '<div class="chat-item-skeleton">Нет чатов</div>';
            }
            return items.map(chat => {
                const { title, preview, kind } = getDisplayLine(chat);
                const titleCls = kind === 'renamed' ? 'chat-line chat-line--title chat-line--renamed' : 'chat-line chat-line--title';
                const lineHtml = title
                    ? `<div class="${titleCls}">${title}</div>`
                    : `<div class="chat-line chat-line--preview">${preview}</div>`;
                return `
                <div class="chat-item ${chat.id === this.currentChat ? 'active' : ''}"
                    data-chat-id="${escapeHtml(String(chat.id))}">
                    <div class="chat-line-wrap">${lineHtml}</div>
                    <button class="chat-item-menu-btn" data-menu-chat-id="${escapeHtml(String(chat.id))}" title="Действия" type="button">
                        <svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14">
                            <circle cx="10" cy="4" r="1.5"/><circle cx="10" cy="10" r="1.5"/><circle cx="10" cy="16" r="1.5"/>
                        </svg>
                    </button>
                </div>`;
            }).join('');
        };

        const favorites = sorted.filter(c => c.is_favorite);
        const regular   = sorted.filter(c => !c.is_favorite);

        // Секция избранного — показываем/скрываем целиком
        const favSection = document.getElementById('favorites-section');
        if (favList) {
            if (favorites.length) {
                favList.innerHTML = buildList(favorites);
                if (favSection) favSection.style.display = 'flex';
            } else {
                favList.innerHTML = '';
                if (favSection) favSection.style.display = 'none';
            }
        }

        list.innerHTML = buildList(regular);
    }

    /* ══════════════════════════════════════════
       UI SETUP (вешается один раз после рендера инпута)
    ══════════════════════════════════════════ */
    setupUI() {
        if (this.isUIReady) return;
        this.isUIReady = true;

        const handleChatListClick = (e) => {
            const menuBtn = e.target.closest('.chat-item-menu-btn');
            if (menuBtn) {
                e.stopPropagation();
                this._toggleChatDropdown(menuBtn);
                return;
            }

            const item = e.target.closest('.chat-item');
            if (!item) return;
            this.openChat(item.dataset.chatId);
        };

        document.getElementById('chat-list')?.addEventListener('click', handleChatListClick);
        document.getElementById('chat-favorites-list')?.addEventListener('click', handleChatListClick);

        document.addEventListener('click', (e) => {
            if (!e.target.closest('.chat-item-dropdown') && !e.target.closest('.chat-item-menu-btn')) {
                document.querySelectorAll('.chat-item-dropdown').forEach(d => d.remove());
            }
        });

        document.getElementById('new-chat-btn')?.addEventListener('click', () => this.startNewChat());
        document.getElementById('profile-btn')?.addEventListener('click', () => this.attachProfileHandlers());
    }

    /* ══════════════════════════════════════════
       OPEN / CREATE CHAT
    ══════════════════════════════════════════ */
    async openChat(chatId, opts = {}) {
        // Сохраняем черновик поля ввода уходящего чата (в т.ч. со стартового
        // экрана, где currentChat === null) до сноса DOM инпута.
        if (String(this.currentChat) !== String(chatId)) {
            this._saveInputDraft(this.currentChat);
        }
        // Сохраняем статус уходящего чата перед сносом DOM
        if (this.currentChat && this.currentChat !== chatId) {
            if (!this._chatStatuses) this._chatStatuses = {};
            // Главный статус PMыча
            const mainStatusEl = document.getElementById('agent-status-indicator');
            // Признак что уходящий чат ещё в работе — чтобы при возврате
            // мгновенно (без ожидания сети) поднять стоп-кнопку/плейсхолдер.
            const runningMeta = {
                running: this._runState === 'running',
                activeRequestId: this._activeRequestId || null,
            };
            // ВАРИАНТ 2 (мультиплекс SSE): EventSource уходящего чата НЕ
            // закрываем, если он ещё стримит — пусть достримит в фоне. Стрим-
            // стейт этого чата живёт изолированно в this._streams[oldChatId],
            // а RAF-цикл продолжает тикать по его liveBlockEl. Узел ответа мы
            // ОТКРЕПЛЯЕМ из ленты (её DOM сейчас очистится под новый чат), но
            // ссылки в стриме сохраняем — фоновый RAF пишет туда. При возврате
            // re-attach через _reattachBackgroundStream.
            const oldKey = String(this.currentChat);
            const oldStream = this._getStream(oldKey);
            let streamDetached = false;
            if (oldStream && oldStream.pendingBotEl) {
                const el = oldStream.pendingBotEl;
                if (el.parentNode) el.parentNode.removeChild(el);
                streamDetached = true;
            }
            if (mainStatusEl || (this.subagentStates && Object.keys(this.subagentStates).length)
                || runningMeta.running || streamDetached) {
                this._chatStatuses[this.currentChat] = {
                    main: mainStatusEl?.querySelector('.status-text')?.textContent || '',
                    subagents: this.subagentStates ? { ...this.subagentStates } : {},
                    ...runningMeta
                };
            }
        }

        if (String(this._noToolsWarnChatId ?? '') !== String(chatId)) {
            this._resetNoToolsWarn(chatId);
        }
        this.currentChat = chatId;
        this.subagentStates = {};
        if (!opts?.skipSaveSettings && chatId != null) {
            window.ToolsPanel?.saveUserSettings?.({ active_chat_id: String(chatId) });
        }

        // Сбрасываем run-UI прошлого чата, чтобы стоп-кнопка/плейсхолдер не
        // «протекли» в новый чат. Ниже _restoreRunningUI поднимет их заново,
        // если новый чат действительно в работе (по Redis-флагу).
        this._runState = 'idle';
        this._activeRequestId = null;
        this._botAnswered = false;
        this._setSendButtonMode('send');

        const msgContainer = this.getMessagesContainer();
        if (msgContainer) {
            msgContainer.classList.remove('hidden');
            msgContainer.innerHTML = '';
        }

        document.querySelector('.chat-main-inner')?.classList.add('chat-active');

        document.querySelectorAll('.chat-item').forEach(item => {
            item.classList.toggle('active', String(item.dataset.chatId) === String(chatId));
        });

        // Переключаемся на вкладку агента если были не на ней
        if (this.currentTab !== 'agent') {
            this.switchTab('agent');

            const titleEl = document.getElementById("main-title");
            titleEl.textContent = 'Агент';
        }

        const targetMsgId = opts?.targetMessageId || opts?.targetDbId || null;
        await Promise.all([
            targetMsgId
                ? this.loadMessagesAround(chatId, targetMsgId, opts?.radius || 10, { dbId: opts?.targetDbId })
                : this.loadMessages(chatId),
            this.renderMessageInput()
        ]);

        // Восстанавливаем сохранённый черновик поля ввода этого чата (если был).
        this._restoreInputDraft(chatId);

        // ВАЖНО: раньше здесь был ранний return по наличию любого
        // .agent-message в ленте («ответ уже есть → агент свободен»). Это
        // ломало восстановление в многоходовых чатах: старые ответы бота
        // присутствуют всегда, поэтому ранний выход срабатывал даже когда
        // агент реально в работе над НОВЫМ сообщением, и _restoreRunningUI
        // (а с ним запрос /run_status) не вызывался. Теперь источник истины —
        // Redis-флаг: всегда доходим до _restoreRunningUI.

        // Восстанавливаем сохранённый статус если есть (чат ещё в процессе)
        const savedState = this._chatStatuses?.[chatId];
        if (savedState) {
            if (savedState.main) this._updateMainStatus(savedState.main);
            if (savedState.subagents && Object.keys(savedState.subagents).length) {
                this.subagentStates = savedState.subagents;
                this._restoreSubagentStatuses(savedState.subagents);
            }
        }

        // ВАРИАНТ 2: если этот чат стримил в фоне (SSE был открыт, RAF тикал,
        // узел откреплён) — возвращаем его узел ответа обратно в ленту. Стрим
        // продолжается без разрывов: буфер копился всё время в this._streams.
        this._reattachBackgroundStream(chatId);

        // Восстанавливаем UI «агент в работе» (стоп-кнопка, плейсхолдер).
        // Источник истины — Redis-флаг на гейтвее: переживает и переключение
        // чата, и F5. savedState даёт мгновенное восстановление без сети,
        // run_status подтверждает/синхронизирует по серверу.
        await this._restoreRunningUI(chatId, savedState);
    }

    /**
     * Восстанавливает индикацию активной генерации по чату:
     *  - стоп-кнопку (_setSendButtonMode('stop')) и _runState='running'
     *  - _activeRequestId (чтобы Stop работал даже после F5)
     *  - плейсхолдер-волну, если ответ ещё не начал приходить
     * savedState (из _chatStatuses) — быстрый оптимистичный путь при
     * переключении вкладок; run_status — авторитетная проверка по серверу.
     */
    async _restoreRunningUI(chatId, savedState = null) {
        let running = false;
        let requestId = savedState?.activeRequestId || null;
        let serverStatus = null;

        if (savedState?.running) {
            running = true;
        }

        if (window.__authSessionExpired) return;
        try {
            const r = await fetch(`/api/chats/${chatId}/run_status`, {
                credentials: 'include',
                redirect: 'manual'
            });
            if (window.__isAuthRedirectOrUnauthorized?.(r)) {
                window.__onAuthSessionExpired?.();
                return;
            }
            if (r.ok) {
                const j = await r.json();
                serverStatus = j;
                if (j?.running) {
                    running = true;
                    requestId = j.request_id || requestId;
                } else {
                    running = false;
                }
            }
        } catch (_) { /* сеть недоступна — доверяем savedState */ }

        if (String(this.currentChat) !== String(chatId)) return;

        const container = this.getMessagesContainer();
        const st = this._ensureStream(chatId);

        if (running && serverStatus && container) {
            const uMsgText = serverStatus.user_message || serverStatus.reasoning?.user_message;
            const uMsgId = serverStatus.user_message_id || serverStatus.reasoning?.user_message_id;
            if (uMsgText) {
                const hasById = uMsgId && container.querySelector(
                    `.user-message-wrapper[data-message-id="${uMsgId}"], .user-message-wrapper[data-id="${uMsgId}"]`
                );
                const normTarget = String(uMsgText).replace(/\s+/g, ' ').trim();
                const hasByText = [...container.querySelectorAll('.user-message .message-text, .user-message')].some(el => {
                    const normEl = (el.textContent || '').replace(/\s+/g, ' ').trim();
                    return normEl && normTarget && (normEl.includes(normTarget) || normTarget.includes(normEl));
                });
                const lastChild = container.lastElementChild;
                const botAlreadyBelow = lastChild && lastChild.classList?.contains('agent-message') && /^\d+$/.test(String(lastChild.dataset.id || ''));
                if (!hasById && !hasByText && !botAlreadyBelow) {
                    this.addMessage({
                        id: uMsgId || `msg-${Date.now()}`,
                        message_id: uMsgId || '',
                        message: uMsgText,
                        is_from_bot: false,
                        created_at: new Date().toISOString(),
                    }, false);
                }
            }
        }

        if (serverStatus?.active_requests !== undefined) {
            this._updateActiveRequestsUI(chatId, serverStatus.active_requests);
        }

        if (serverStatus?.elapsed_sec && st) {
            const elapsedSec = Math.max(1, Number(serverStatus.elapsed_sec) || 1);
            st.runStartedAt = Date.now() - (elapsedSec * 1000);
            st.reasoningDurationSec = elapsedSec;
        } else if (serverStatus?.started_at && st) {
            st.runStartedAt = Number(serverStatus.started_at) * 1000;
        }

        if (serverStatus?.reasoning) {
            this._restoreReasoningFromServer(chatId, serverStatus.reasoning, running);
        }

        if (!running) {
            const hasUnfinishedDom = !!(container && (
                container.querySelector('#agent-response-placeholder') ||
                container.querySelector('#agent-status-indicator') ||
                container.querySelector('.agent-message:not([data-id])')
            ));
            const wasRunningBeforeCheck = (
                this._runState === 'running' ||
                this._runState === 'stopping' ||
                !!(st && (st.pendingBotEl || st.reasoningWrapEl || st.streamingActive)) ||
                hasUnfinishedDom
            );
            const savedReasoningWrap = st?.reasoningWrapEl
                || st?.pendingBotEl?.querySelector('.reasoning-block')
                || container?.querySelector('.agent-message:not([data-id]) .reasoning-block')
                || null;

            if (st) {
                this._stopReasoningTimer?.(chatId, true);
                st.pendingBotEl = null;
                st.liveBlockEl = null;
                st.reasoningWrapEl = null;
                st.streamingActive = false;
                st.streamBuffer = '';
            }
            if (this._chatStatuses) delete this._chatStatuses[chatId];
            this._removeBotPlaceholder?.();
            this._clearAllStatuses?.();
            this._onRunFinished();

            if (serverStatus !== null && wasRunningBeforeCheck && !this._loadingMessagesChat) {
                await this.loadMessages(chatId);
                if (savedReasoningWrap && String(this.currentChat) === String(chatId)) {
                    savedReasoningWrap.classList.add('reasoning-collapsed', 'reasoning-done');
                    const liveEl = savedReasoningWrap.querySelector('.reasoning-live');
                    if (liveEl) liveEl.remove();
                    const freshContainer = this.getMessagesContainer();
                    const userWrappers = freshContainer?.querySelectorAll('.user-message-wrapper') || [];
                    const lastUser = userWrappers.length ? userWrappers[userWrappers.length - 1] : null;
                    let lastBot = null;
                    if (lastUser) {
                        let cur = lastUser.nextElementSibling;
                        while (cur) {
                            if (cur.classList?.contains('.agent-message') || cur.classList?.contains('agent-message')) {
                                lastBot = cur;
                                break;
                            }
                            cur = cur.nextElementSibling;
                        }
                    }
                    if (lastBot && !lastBot.querySelector('.reasoning-block')) {
                        lastBot.insertBefore(savedReasoningWrap, lastBot.firstElementChild);
                    }
                }
            }
            return;
        }

        if (container && st?.pendingBotEl) {
            this._dedupeTurnReasoningAndActions(container, st);
            this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        }

        this._runState = 'running';
        this._activeRequestId = requestId;
        this._setSendButtonMode('stop');

        if (!this._sources[String(chatId)]) {
            this._startStatusPolling(chatId);
        }

        const alreadyHasFinal = container?.querySelector('.live-block');
        const hasPlaceholder = container?.querySelector('#agent-response-placeholder');
        if (container && !alreadyHasFinal && !hasPlaceholder) {
            this._createBotPlaceholder(container);
        } else if (container && hasPlaceholder && st?.pendingBotEl) {
            this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        }
    }

    /* ══════════════════════════════════════════
       ВАРИАНТ 2: мультиплекс стриминга по чатам (SSE).
       Каждый чат стримит в СВОЙ this._streams[chatId] через СВОЙ EventSource
       this._sources[chatId]. При уходе из стримящего чата источник НЕ
       закрывается, RAF продолжает тикать в фоне (узел ответа откреплён от
       ленты, но жив по ссылке в стриме). При возврате — re-attach узла:
       размышления и уже напечатанный текст на месте, стрим без разрывов.
    ══════════════════════════════════════════ */

    /**
     * Возвращает откреплённый фоновый стрим чата обратно в ленту.
     * Вызывается из openChat после loadMessages. Если чат не стримил в
     * фоне — тихо выходит.
     */
    _reattachBackgroundStream(chatId) {
        const st = this._getStream(chatId);
        if (!st || !st.pendingBotEl) return;
        const container = this.getMessagesContainer();
        if (!container) return;
        if (st.pendingBotEl.parentNode === container) return;

        if (st.streamDone || st.streamIsFinal) {
            this._removeBotPlaceholder();
        }

        this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        if (!st.streamDone && !st.streamIsFinal && !container.querySelector('#agent-response-placeholder')) {
            this._createBotPlaceholder(container);
        }
        this._dedupeTurnReasoningAndActions(container, st);
        this._botAnswered = true;

        // Показываем накопленное и (пере)запускаем RAF, если он был снят.
        if (st.liveBlockEl) this._renderStreamPartial(chatId);
        if ((st.streamBuffer && st.streamBuffer.length) || st.streamDone) {
            this._startStreamLoop(chatId);
        }

        container.scrollTop = container.scrollHeight;
    }

    async createNewChat() {
        const res = await fetch('/api/chats/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include'
        });
        if (!res.ok) throw new Error('Ошибка создания чата');

        const data = await res.json();
        const chatId = data.chat_id;

        this.chats.push({ id: chatId, last_message: 'Новый чат', timestamp: new Date().toISOString() });
        this.renderChats(this.chats);
        await this.openChat(chatId);
        return chatId;
    }

    startNewChat() {
        // Перед уходом на стартовый экран снимаем черновик инпута текущего
        // чата и сохраняем статус стримящего чата (как в openChat), иначе
        // фоновый стрим/стоп-кнопка «протекут» на start page.
        this._saveInputDraft(this.currentChat);
        if (this.currentChat) {
            if (!this._chatStatuses) this._chatStatuses = {};
            const mainStatusEl = document.getElementById('agent-status-indicator');
            const runningMeta = {
                running: this._runState === 'running',
                activeRequestId: this._activeRequestId || null,
            };
            // ВАРИАНТ 2 (мультиплекс SSE): EventSource стримящего чата НЕ
            // закрываем — открепляем его узел из ленты, фоновый RAF достримит.
            const oldKey = String(this.currentChat);
            const oldStream = this._getStream(oldKey);
            let streamDetached = false;
            if (oldStream && oldStream.pendingBotEl) {
                const el = oldStream.pendingBotEl;
                if (el.parentNode) el.parentNode.removeChild(el);
                streamDetached = true;
            }
            if (mainStatusEl || (this.subagentStates && Object.keys(this.subagentStates).length)
                || runningMeta.running || streamDetached) {
                this._chatStatuses[this.currentChat] = {
                    main: mainStatusEl?.querySelector('.status-text')?.textContent || '',
                    subagents: this.subagentStates ? { ...this.subagentStates } : {},
                    ...runningMeta
                };
            }
        }

        this._resetNoToolsWarn(null);
        this.currentChat = null;
        window.ToolsPanel?.saveUserSettings?.({ active_chat_id: null });
        this.subagentStates = {};

        // Сбрасываем run-UI: на стартовом экране стоп-кнопки быть не должно,
        // даже если предыдущий (теперь фоновый) чат ещё стримит. Остановить
        // его можно только вернувшись в него.
        this._runState = 'idle';
        this._activeRequestId = null;
        this._botAnswered = false;

        const msgContainer = this.getMessagesContainer();
        if (msgContainer) {
            msgContainer.innerHTML = '';
            msgContainer.classList.add('hidden');
        }

        document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
        document.querySelector('.chat-main-inner')?.classList.remove('chat-active');

        // Переключаемся на агента если были не на ней
        if (this.currentTab !== 'agent') {
            this.switchTab('agent');
        }

        this.renderMessageInput();
        this._setSendButtonMode('send');
        this._restoreInputDraft(null);
    }

    /* ══════════════════════════════════════════
       MESSAGES
    ══════════════════════════════════════════ */
    async loadMessages(chatId) {
        const PAGE_SIZE = 50;
        const key = String(chatId);
        this._loadingMessagesChat = key;
        let res;
        try {
            res = await fetch(`/api/chats/${chatId}/messages?limit=${PAGE_SIZE}&offset=0`, {
                credentials: 'include',
                redirect: 'manual'
            });
        } catch (_) {
            this._messagesLoadFailedChat = key;
            return false;
        } finally {
            if (this._loadingMessagesChat === key) {
                this._loadingMessagesChat = null;
            }
        }
        if (window.__isAuthRedirectOrUnauthorized?.(res)) {
            this._messagesLoadFailedChat = key;
            window.__onAuthSessionExpired?.();
            return false;
        }
        if (!res || !res.ok) {
            this._messagesLoadFailedChat = key;
            return false;
        }
        const data = await res.json();
        if (String(chatId) !== String(this.currentChat)) return false; // гонка

        if (this._messagesLoadFailedChat === key) {
            this._messagesLoadFailedChat = null;
        }

        const msgs = data.messages || [];
        // Состояние пагинации по конкретному чату
        this._pagination = {
            chatId,
            pageSize: PAGE_SIZE,
            loaded: msgs.length,
            hasMore: !!data.has_more,
            hasNewer: false,
            oldestId: msgs.length ? msgs[0].id : null,
            newestId: msgs.length ? msgs[msgs.length - 1].id : null,
            loading: false,
            loadingNewer: false,
        };

        this.renderMessages(msgs);
        this._initHistoryScrollLoader();
        return true;
    }

    async loadMessagesAround(chatId, targetId, radius = 10, opts = {}) {
        const rad = Math.max(1, Number(radius) || 15);
        const key = String(chatId);
        this._loadingMessagesChat = key;
        let res;
        try {
            res = await fetch(
                `/api/chats/${encodeURIComponent(chatId)}/messages?around_id=${encodeURIComponent(String(targetId))}&radius=${rad}`,
                {
                    credentials: 'include',
                    redirect: 'manual'
                }
            );
        } catch (_) {
            this._messagesLoadFailedChat = key;
            return false;
        } finally {
            if (this._loadingMessagesChat === key) {
                this._loadingMessagesChat = null;
            }
        }
        if (window.__isAuthRedirectOrUnauthorized?.(res)) {
            this._messagesLoadFailedChat = key;
            window.__onAuthSessionExpired?.();
            return false;
        }
        if (!res || !res.ok) {
            this._messagesLoadFailedChat = key;
            return false;
        }
        const data = await res.json();
        if (String(chatId) !== String(this.currentChat)) return false;

        if (this._messagesLoadFailedChat === key) {
            this._messagesLoadFailedChat = null;
        }

        const msgs = data.messages || [];
        this._pagination = {
            chatId,
            pageSize: rad,
            loaded: msgs.length,
            hasMore: !!data.has_more,
            hasNewer: !!data.has_newer,
            oldestId: msgs.length ? msgs[0].id : null,
            newestId: msgs.length ? msgs[msgs.length - 1].id : null,
            loading: false,
            loadingNewer: false,
        };

        this.renderMessages(msgs, { scrollToBottom: false });
        this._initHistoryScrollLoader();

        const container = this.getMessagesContainer();
        if (container) {
            const resolvedMsgId = data.target_message_id || targetId;
            const resolvedDbId = opts?.dbId || data.target_id || targetId;
            const targetEl = this._findMessageElement(container, resolvedMsgId, resolvedDbId);
            if (targetEl) {
                this._scrollToAndHighlightMessage(targetEl, { immediate: true });
            } else {
                container.scrollTop = container.scrollHeight;
            }
        }
        return true;
    }

    _findMessageElement(container, messageId, dbId) {
        if (!container) return null;
        const candidates = [];
        if (messageId != null && String(messageId).trim() !== '') {
            candidates.push(String(messageId).trim());
        }
        if (dbId != null && String(dbId).trim() !== '' && !candidates.includes(String(dbId).trim())) {
            candidates.push(String(dbId).trim());
        }
        for (const idVal of candidates) {
            const safe = (typeof CSS !== 'undefined' && CSS.escape)
                ? CSS.escape(idVal)
                : idVal.replace(/["\\]/g, '\\$&');
            const found = container.querySelector(
                `.user-message-wrapper[data-message-id="${safe}"], .user-message-wrapper[data-id="${safe}"], .agent-message[data-message-id="${safe}"], .agent-message[data-id="${safe}"]`
            );
            if (found) return found;
        }
        return null;
    }

    _scrollToAndHighlightMessage(el, opts = {}) {
        if (!el) return;
        const container = this.getMessagesContainer();
        const highlightNode = el.classList.contains('user-message-wrapper')
            ? (el.querySelector('.user-message') || el)
            : el;

        const doScroll = (behavior) => {
            try {
                el.scrollIntoView({ behavior, block: 'center' });
            } catch (_) {
                if (container) {
                    const top = el.offsetTop - Math.max(40, (container.clientHeight - el.offsetHeight) / 2);
                    container.scrollTop = Math.max(0, top);
                }
            }
        };

        if (opts?.immediate) {
            doScroll('auto');
            requestAnimationFrame(() => {
                doScroll('auto');
            });
        } else {
            doScroll('smooth');
        }

        if (highlightNode) {
            highlightNode.classList.remove('search-jump-highlight');
            void highlightNode.offsetWidth;
            highlightNode.classList.add('search-jump-highlight');
            if (highlightNode._searchHighlightTimer) {
                clearTimeout(highlightNode._searchHighlightTimer);
            }
            highlightNode._searchHighlightTimer = setTimeout(() => {
                highlightNode.classList.remove('search-jump-highlight');
            }, 2400);
        }
    }

    async navigateToSearchMessage(chatId, { messageId = null, dbId = null } = {}) {
        if (!chatId) return;
        const targetChatId = String(chatId).trim();
        if (!targetChatId) return;

        const isSameChat = String(this.currentChat ?? '') === targetChatId;
        if (isSameChat) {
            if (this.currentTab !== 'agent') {
                this.switchTab('agent');
                const titleEl = document.getElementById('main-title');
                if (titleEl) titleEl.textContent = 'Агент';
            }
            const container = this.getMessagesContainer();
            const existingEl = this._findMessageElement(container, messageId, dbId);
            if (existingEl) {
                this._scrollToAndHighlightMessage(existingEl, { immediate: false });
                return;
            }
            const targetKey = messageId || dbId;
            if (targetKey) {
                await this.loadMessagesAround(targetChatId, targetKey, 15, { dbId });
                return;
            }
        }

        await this.openChat(targetChatId, {
            targetMessageId: messageId || dbId || null,
            targetDbId: dbId || null,
            radius: 15,
        });
    }

    async _syncMissedMessages(chatId) {
        if (!chatId || window.__authSessionExpired) return;
        const key = String(chatId);
        if (String(this.currentChat) !== key || this._loadingMessagesChat === key) return;
        if (this._pagination && String(this._pagination.chatId) === key && this._pagination.hasNewer) return;

        const container = this.getMessagesContainer();
        if (!container) return;

        const existingNodes = container.querySelectorAll('.user-message-wrapper, .agent-message[data-id]');
        if (this._messagesLoadFailedChat === key || existingNodes.length === 0) {
            await this.loadMessages(chatId);
            return;
        }

        let res;
        try {
            res = await fetch(`/api/chats/${chatId}/messages?limit=50&offset=0`, {
                credentials: 'include',
                redirect: 'manual'
            });
        } catch (_) {
            return;
        }
        if (window.__isAuthRedirectOrUnauthorized?.(res)) {
            window.__onAuthSessionExpired?.();
            return;
        }
        if (!res || !res.ok) return;

        const data = await res.json();
        if (String(this.currentChat) !== key) return;
        const serverMsgs = Array.isArray(data?.messages) ? data.messages : [];
        if (!serverMsgs.length) return;

        const hasMessageInDom = (msg) => {
            if (msg.id != null) {
                const byId = container.querySelector(
                    `[data-id="${msg.id}"], [data-message-id="${msg.id}"]`
                );
                if (byId) return true;
            }
            if (msg.message_id) {
                const byUuid = container.querySelector(
                    `[data-id="${msg.message_id}"], [data-message-id="${msg.message_id}"]`
                );
                if (byUuid) return true;
            }
            if (!msg.is_from_bot && msg.message) {
                const normTarget = String(msg.message).replace(/\s+/g, ' ').trim();
                const wrappers = container.querySelectorAll('.user-message-wrapper');
                for (const w of wrappers) {
                    const txtEl = w.querySelector('.user-message .message-text, .user-message');
                    const normEl = (txtEl?.textContent || '').replace(/\s+/g, ' ').trim();
                    if (normEl && normTarget && normEl === normTarget) {
                        if (msg.id != null) w.dataset.messageId = String(msg.id);
                        if (msg.message_id) w.dataset.id = String(msg.message_id);
                        return true;
                    }
                }
            }
            return false;
        };

        const missing = serverMsgs.filter(m => !hasMessageInDom(m));
        if (!missing.length) return;

        missing.forEach(msg => this.addMessage(msg, false));
        const st = this._getStream(key);
        if (st && st.pendingBotEl && st.pendingBotEl.parentNode === container) {
            this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        }
        if (this._pagination && String(this._pagination.chatId) === key) {
            this._pagination.loaded = Math.max(this._pagination.loaded || 0, serverMsgs.length);
        }
        container.scrollTop = container.scrollHeight;
    }

    renderMessages(messages, opts = {}) {
        const scrollToBottom = opts?.scrollToBottom !== false;
        const container = this.getMessagesContainer();
        if (!container) return;
        const noToolsHint = container.querySelector('#no-tools-warn');
        container.innerHTML = '';
        if (noToolsHint) container.appendChild(noToolsHint);
        const prevSuppress = this._suppressHistoryAutoScroll;
        this._suppressHistoryAutoScroll = !scrollToBottom;
        try {
            messages.forEach(msg => this.addMessage(msg, false));
        } finally {
            this._suppressHistoryAutoScroll = prevSuppress;
        }
        if (scrollToBottom) {
            container.scrollTop = container.scrollHeight;
        }
        this._initMessagesFade(container);
        container._syncFade && container._syncFade();
    }

    /**
     * Дозагрузка более старых сообщений (head) при скролле вверх.
     */
    async loadOlderMessages() {
        const p = this._pagination;
        if (!p || p.loading || !p.hasMore) return;
        if (String(p.chatId) !== String(this.currentChat)) return;

        const container = this.getMessagesContainer();
        if (!container) return;

        p.loading = true;

        const prevScrollHeight = container.scrollHeight;
        const prevScrollTop = container.scrollTop;

        try {
            const url = p.oldestId != null
                ? `/api/chats/${encodeURIComponent(p.chatId)}/messages?limit=${p.pageSize}&before_id=${encodeURIComponent(p.oldestId)}`
                : `/api/chats/${encodeURIComponent(p.chatId)}/messages?limit=${p.pageSize}&offset=${p.loaded}`;
            const res = await fetch(url, { credentials: 'include' });
            if (!res.ok) { p.loading = false; return; }
            const data = await res.json();
            if (String(p.chatId) !== String(this.currentChat)) return;

            const older = data.messages || [];
            if (!older.length) {
                p.hasMore = false;
                p.loading = false;
                return;
            }

            const buffer = document.createDocumentFragment();
            const staging = document.createElement('div');
            older.forEach(msg => this._renderMessageInto(staging, msg));
            while (staging.firstChild) buffer.appendChild(staging.firstChild);
            container.insertBefore(buffer, container.firstChild);

            p.loaded += older.length;
            if (older[0]?.id != null) {
                p.oldestId = older[0].id;
            }
            p.hasMore = !!data.has_more;

            const newScrollHeight = container.scrollHeight;
            container.scrollTop = prevScrollTop + (newScrollHeight - prevScrollHeight);
        } catch (e) {
            console.error('[pagination] loadOlderMessages error', e);
        } finally {
            p.loading = false;
        }
    }

    /**
     * Дозагрузка более новых сообщений (tail) при скролле вниз после прыжка из поиска.
     */
    async loadNewerMessages() {
        const p = this._pagination;
        if (!p || p.loadingNewer || !p.hasNewer || p.newestId == null) return;
        if (String(p.chatId) !== String(this.currentChat)) return;

        const container = this.getMessagesContainer();
        if (!container) return;

        p.loadingNewer = true;

        try {
            const url = `/api/chats/${encodeURIComponent(p.chatId)}/messages?limit=${p.pageSize}&after_id=${encodeURIComponent(p.newestId)}`;
            const res = await fetch(url, { credentials: 'include' });
            if (!res.ok) { p.loadingNewer = false; return; }
            const data = await res.json();
            if (String(p.chatId) !== String(this.currentChat)) return;

            const newer = data.messages || [];
            if (!newer.length) {
                p.hasNewer = false;
                p.loadingNewer = false;
                return;
            }

            const prevSuppress = this._suppressHistoryAutoScroll;
            this._suppressHistoryAutoScroll = true;
            try {
                newer.forEach(msg => this.addMessage(msg, false));
            } finally {
                this._suppressHistoryAutoScroll = prevSuppress;
            }

            p.loaded += newer.length;
            const lastMsg = newer[newer.length - 1];
            if (lastMsg?.id != null) {
                p.newestId = lastMsg.id;
            }
            p.hasNewer = !!data.has_newer;
            container._syncFade && container._syncFade();
        } catch (e) {
            console.error('[pagination] loadNewerMessages error', e);
        } finally {
            p.loadingNewer = false;
        }
    }

    _renderMessageInto(targetContainer, message) {
        const origGetter = this.getMessagesContainer;
        this.getMessagesContainer = () => targetContainer;
        try {
            this.addMessage(message, false);
        } finally {
            this.getMessagesContainer = origGetter;
        }
    }

    _initHistoryScrollLoader() {
        const container = this.getMessagesContainer();
        if (!container) return;
        if (container._historyLoaderBound) return;
        container._historyLoaderBound = true;

        let ticking = false;
        container.addEventListener('scroll', () => {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(() => {
                ticking = false;
                if (container.scrollTop < 120) {
                    this.loadOlderMessages();
                }
                const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
                if (distFromBottom < 140) {
                    this.loadNewerMessages();
                }
            });
        }, { passive: true });
    }

    /* ══════════════════════════════════════════
       STRUCTURED UI (InteractivePoll / AskUserQuestion)
    ══════════════════════════════════════════ */
    _tryParseComponentJson(raw) {
        if (!raw || typeof raw !== 'string') return null;
        const trimmed = raw.trim();
        if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return null;
        try {
            const obj = JSON.parse(trimmed);
            if (!obj || typeof obj !== 'object') return null;
            const comp = String(obj.component || obj.type || '').trim().toLowerCase();
            const opts = Array.isArray(obj.options) ? obj.options : (Array.isArray(obj.choices) ? obj.choices : null);
            if ((comp === 'interactivepoll' || comp === 'askuserquestion' || comp === 'poll') && opts && opts.length > 0) {
                return { component: 'InteractivePoll', data: { ...obj, options: opts } };
            }
        } catch (_) {}
        return null;
    }

    _extractBalancedJsonObjects(text) {
        const results = [];
        let i = 0;
        const n = text.length;
        while (i < n) {
            const start = text.indexOf('{', i);
            if (start === -1) break;
            let depth = 0, inStr = false, esc = false, end = -1;
            for (let j = start; j < n; j++) {
                const ch = text[j];
                if (inStr) {
                    if (esc) { esc = false; }
                    else if (ch === '\\') { esc = true; }
                    else if (ch === '"') { inStr = false; }
                } else {
                    if (ch === '"') inStr = true;
                    else if (ch === '{') depth++;
                    else if (ch === '}') {
                        depth--;
                        if (depth === 0) { end = j; break; }
                    }
                }
            }
            if (end !== -1) {
                const candidate = text.slice(start, end + 1);
                const parsed = this._tryParseComponentJson(candidate);
                if (parsed) {
                    results.push({ start, end: end + 1, parsed });
                    i = end + 1;
                    continue;
                }
            }
            i = start + 1;
        }
        return results;
    }

    _extractStructuredSegments(text) {
        const src = String(text || '');
        const segments = [];
        const fenceRe = /```(?:json)?\s*([\s\S]*?)```/gi;
        let lastIdx = 0;
        let m;

        const pushPlainOrInlineJson = (chunk) => {
            if (!chunk) return;
            const inlineMatches = this._extractBalancedJsonObjects(chunk);
            if (!inlineMatches.length) {
                if (chunk.trim()) segments.push({ kind: 'md', text: chunk });
                return;
            }
            let cur = 0;
            for (const hit of inlineMatches) {
                const before = chunk.slice(cur, hit.start);
                if (before.trim()) segments.push({ kind: 'md', text: before });
                segments.push({ kind: 'component', component: hit.parsed.component, data: hit.parsed.data });
                cur = hit.end;
            }
            const after = chunk.slice(cur);
            if (after.trim()) segments.push({ kind: 'md', text: after });
        };

        while ((m = fenceRe.exec(src)) !== null) {
            const parsed = this._tryParseComponentJson(m[1]);
            if (parsed) {
                pushPlainOrInlineJson(src.slice(lastIdx, m.index));
                segments.push({ kind: 'component', component: parsed.component, data: parsed.data });
                lastIdx = m.index + m[0].length;
            }
        }
        pushPlainOrInlineJson(src.slice(lastIdx));
        return segments;
    }

    _parseMarkdown(mdText) {
        const raw = String(mdText || '');
        if (typeof marked !== 'undefined' && marked && typeof marked.parse === 'function') {
            try { return marked.parse(raw); } catch (_) {}
        }
        return raw
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\n/g, '<br>');
    }

    _renderSafeMarkdownHtml(mdText) {
        let html = this._parseMarkdown(mdText);
        if (typeof DOMPurify !== 'undefined') {
            html = DOMPurify.sanitize(html, {
                ADD_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td'],
                ADD_ATTR: ['colspan', 'rowspan', 'align']
            });
        }
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        tmp.querySelectorAll('a[href]').forEach(a => {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer');
        });
        return tmp.innerHTML;
    }

    _createInteractivePollEl(data, meta = {}) {
        this._pollSeq = (this._pollSeq || 0) + 1;
        const uid = `_chat_poll_${this._pollSeq}_`;
        const botMessageId = String(meta.botMessageId || '').trim();
        const pollIndex = Number(meta.pollIndex ?? 0);
        const savedAnswer = meta.savedAnswer || null;
        const titleText = String(data.title || data.question || data.prompt || 'Выберите вариант').trim();
        const rawOpts = Array.isArray(data.options) ? data.options : [];
        const options = rawOpts.map((opt) => {
            if (typeof opt === 'string') return { label: opt, value: opt, description: '' };
            if (opt && typeof opt === 'object') {
                const lbl = String(opt.label || opt.title || opt.text || opt.value || '').trim();
                const val = String(opt.value || lbl).trim();
                const desc = opt.description ? String(opt.description).trim() : '';
                return { label: lbl, value: val, description: desc };
            }
            return { label: String(opt), value: String(opt), description: '' };
        }).filter(o => o.label);

        const allowCustom = data.allowCustom !== false;
        const placeholder = String(data.placeholder || 'Something else');
        const skipLabel = String(data.skipLabel || 'Skip');
        const sendLabel = String(data.sendLabel || 'Send');

        const root = document.createElement('div');
        root.setAttribute('data-cds', 'AskUserQuestion');
        root.setAttribute('role', 'region');
        root.setAttribute('aria-labelledby', `${uid}label`);
        root.tabIndex = -1;
        root.className = 'ed-poll';
        if (botMessageId) root.dataset.botMessageId = botMessageId;
        root.dataset.pollIndex = String(pollIndex);

        const header = document.createElement('div');
        header.className = 'ed-poll-header';

        const h2 = document.createElement('h2');
        h2.id = `${uid}label`;
        h2.tabIndex = -1;
        h2.className = 'ed-poll-title';
        h2.textContent = titleText;

        const minBtn = document.createElement('button');
        minBtn.type = 'button';
        minBtn.className = 'ed-poll-min-btn';
        minBtn.setAttribute('aria-label', 'Minimize');
        minBtn.title = 'Свернуть / развернуть';
        minBtn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.5 6L8 10.5L12.5 6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        minBtn.addEventListener('click', () => {
            root.classList.toggle('is-minimized');
        });

        header.append(h2, minBtn);

        const bodyWrap = document.createElement('div');
        bodyWrap.className = 'ed-poll-body';
        const bodyInner = document.createElement('div');
        bodyInner.className = 'ed-poll-body-inner';
        const scrollBox = document.createElement('div');
        scrollBox.className = 'ed-poll-scroll';

        const listEl = document.createElement('div');
        listEl.setAttribute('role', 'listbox');
        listEl.setAttribute('aria-labelledby', `${uid}label`);
        listEl.tabIndex = -1;
        listEl.className = 'ed-poll-list';

        let answered = false;
        const optionButtons = [];
        let otherInput = null;
        let actionBtn = null;
        let actionBtnText = null;

        const markLockedUI = (selectedBtn) => {
            answered = true;
            root.classList.add('is-answered');
            optionButtons.forEach((b) => {
                b.disabled = true;
                if (b === selectedBtn) {
                    b.classList.add('is-selected');
                    b.setAttribute('aria-selected', 'true');
                    const trail = b.querySelector('.ed-poll-opt-trail');
                    if (trail) {
                        trail.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.5 8.5L6.5 11.5L12.5 4.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
                    }
                }
            });
            if (otherInput) otherInput.disabled = true;
            if (actionBtn) actionBtn.disabled = true;
        };

        const lockAndSubmit = (selectedBtn, valueToSend, extra = {}) => {
            if (answered) return;
            if (this._runState && this._runState !== 'idle') return;
            markLockedUI(selectedBtn);
            const currentBotMsgId = root.dataset.botMessageId
                || root.closest('.agent-message')?.dataset?.messageId
                || root.closest('.agent-message')?.dataset?.id
                || botMessageId
                || '';
            const pollAnswerMeta = currentBotMsgId ? {
                bot_message_id: String(currentBotMsgId),
                poll_index: pollIndex,
                selected_index: extra.selectedIndex ?? null,
                selected_value: valueToSend,
                is_custom: !!extra.isCustom,
                is_skipped: !!extra.isSkipped,
                title: titleText,
            } : null;
            this.sendMessage(valueToSend, pollAnswerMeta);
        };

        options.forEach((opt, idx) => {
            const item = document.createElement('div');
            item.className = 'ed-poll-item';

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.id = `${uid}item-${idx}`;
            btn.setAttribute('role', 'option');
            btn.setAttribute('aria-selected', 'false');
            btn.tabIndex = idx === 0 ? 0 : -1;
            btn.className = 'ed-poll-opt';
            btn.dataset.optLabel = opt.label;
            btn.dataset.optValue = opt.value;

            const badge = document.createElement('span');
            badge.className = 'ed-poll-badge';
            badge.setAttribute('aria-hidden', 'true');
            badge.textContent = String(idx + 1);

            const content = document.createElement('span');
            content.className = 'ed-poll-opt-content';
            const labelSpan = document.createElement('span');
            labelSpan.className = 'ed-poll-opt-label';
            labelSpan.textContent = opt.label;
            content.appendChild(labelSpan);

            if (opt.description) {
                const descSpan = document.createElement('span');
                descSpan.className = 'ed-poll-opt-desc';
                descSpan.textContent = opt.description;
                content.appendChild(descSpan);
            }

            const trail = document.createElement('span');
            trail.className = 'ed-poll-opt-trail';
            trail.setAttribute('aria-hidden', 'true');
            trail.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

            btn.append(badge, content, trail);

            btn.addEventListener('click', () => {
                lockAndSubmit(btn, opt.value, { selectedIndex: idx, isCustom: false, isSkipped: false });
            });

            btn.addEventListener('keydown', (e) => {
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    const dir = e.key === 'ArrowDown' ? 1 : -1;
                    const nextIdx = (idx + dir + optionButtons.length) % optionButtons.length;
                    optionButtons.forEach((b, i) => { b.tabIndex = i === nextIdx ? 0 : -1; });
                    optionButtons[nextIdx]?.focus();
                }
            });

            const divider = document.createElement('div');
            divider.className = 'ed-poll-divider';
            divider.setAttribute('aria-hidden', 'true');

            item.append(btn, divider);
            listEl.appendChild(item);
            optionButtons.push(btn);
        });

        scrollBox.appendChild(listEl);

        if (allowCustom) {
            const otherWrap = document.createElement('div');
            otherWrap.className = 'ed-poll-other';

            const otherDivider = document.createElement('div');
            otherDivider.className = 'ed-poll-divider';
            otherDivider.setAttribute('aria-hidden', 'true');

            const form = document.createElement('form');
            form.className = 'ed-poll-other-form';

            const label = document.createElement('label');
            label.className = 'ed-poll-other-label';

            const otherBadge = document.createElement('span');
            otherBadge.className = 'ed-poll-badge ed-poll-other-badge';
            otherBadge.setAttribute('aria-hidden', 'true');
            otherBadge.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M11.2 2.5a1.4 1.4 0 0 1 2 2L5.5 12.2l-2.8.7.7-2.8L11.2 2.5Z" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

            otherInput = document.createElement('input');
            otherInput.type = 'text';
            otherInput.className = 'ed-poll-other-input';
            otherInput.placeholder = placeholder;
            otherInput.setAttribute('aria-label', placeholder);
            otherInput.value = '';

            actionBtn = document.createElement('button');
            actionBtn.type = 'button';
            actionBtn.setAttribute('data-cds', 'Button');
            actionBtn.setAttribute('data-size', 'sm');
            actionBtn.className = 'ed-poll-action-btn';
            actionBtnText = document.createElement('span');
            actionBtnText.textContent = skipLabel;
            actionBtn.appendChild(actionBtnText);

            const updateActionBtn = () => {
                const hasVal = otherInput.value.trim().length > 0;
                actionBtnText.textContent = hasVal ? sendLabel : skipLabel;
                actionBtn.classList.toggle('is-primary', hasVal);
            };

            otherInput.addEventListener('input', updateActionBtn);

            const submitOtherOrSkip = () => {
                if (answered) return;
                const customVal = otherInput.value.trim();
                if (customVal) {
                    lockAndSubmit(null, customVal, { selectedIndex: null, isCustom: true, isSkipped: false });
                } else {
                    lockAndSubmit(
                        null,
                        'Пропустить опрос — выбери оптимальный вариант на своё усмотрение.',
                        { selectedIndex: null, isCustom: false, isSkipped: true }
                    );
                }
            };

            form.addEventListener('submit', (e) => {
                e.preventDefault();
                submitOtherOrSkip();
            });

            actionBtn.addEventListener('click', (e) => {
                e.preventDefault();
                submitOtherOrSkip();
            });

            label.append(otherBadge, otherInput, actionBtn);
            form.appendChild(label);
            otherWrap.append(otherDivider, form);
            scrollBox.appendChild(otherWrap);
        } else {
            const lastDiv = listEl.lastElementChild?.querySelector('.ed-poll-divider');
            if (lastDiv) lastDiv.remove();
        }

        root._applySavedAnswer = (ans) => {
            if (answered || !ans || typeof ans !== 'object') return;
            const selIdx = ans.selected_index != null ? Number(ans.selected_index) : null;
            const selVal = String(ans.selected_value || '').trim();
            if (selIdx != null && optionButtons[selIdx]) {
                markLockedUI(optionButtons[selIdx]);
                return;
            }
            if (!ans.is_custom && !ans.is_skipped && selVal) {
                const matched = optionButtons.find(
                    b => b.dataset.optValue === selVal || b.dataset.optLabel === selVal
                );
                if (matched) {
                    markLockedUI(matched);
                    return;
                }
            }
            if (ans.is_custom && otherInput && selVal) {
                otherInput.value = selVal;
                if (actionBtnText) actionBtnText.textContent = sendLabel;
                if (actionBtn) actionBtn.classList.add('is-primary');
            }
            markLockedUI(null);
        };

        root._syncWithUserReply = (replyText) => {
            if (answered) return;
            const norm = String(replyText || '').trim();
            if (!norm) return;
            const matchedBtn = optionButtons.find(
                b => b.dataset.optValue === norm || b.dataset.optLabel === norm
            );
            if (matchedBtn) {
                markLockedUI(matchedBtn);
            } else {
                if (otherInput && !norm.startsWith('Пропустить опрос')) {
                    otherInput.value = norm;
                    if (actionBtnText) actionBtnText.textContent = sendLabel;
                    if (actionBtn) actionBtn.classList.add('is-primary');
                }
                markLockedUI(null);
            }
        };

        if (savedAnswer) {
            root._applySavedAnswer(savedAnswer);
        }

        bodyInner.appendChild(scrollBox);
        bodyWrap.appendChild(bodyInner);
        root.append(header, bodyWrap);
        return root;
    }

    _renderBotStructuredContent(containerEl, rawText, meta = {}) {
        if (!containerEl) return;
        const src = String(rawText || '');
        const segments = this._extractStructuredSegments(src);
        const hasComponents = segments.some(s => s.kind === 'component');
        if (!hasComponents) {
            if (src.trimStart().startsWith('<')) {
                let html = src;
                if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);
                const tmp = document.createElement('div');
                tmp.innerHTML = html;
                tmp.querySelectorAll('a[href]').forEach(a => {
                    a.setAttribute('target', '_blank');
                    a.setAttribute('rel', 'noopener noreferrer');
                });
                containerEl.innerHTML = tmp.innerHTML;
            } else {
                containerEl.innerHTML = this._renderSafeMarkdownHtml(src);
            }
            return;
        }

        const botMessageId = String(meta.botMessageId || '').trim();
        const pollAnswers = Array.isArray(meta.pollAnswers) ? meta.pollAnswers : [];
        let pollIdx = 0;

        containerEl.innerHTML = '';
        for (const seg of segments) {
            if (seg.kind === 'component' && seg.component === 'InteractivePoll') {
                const curIdx = pollIdx++;
                const savedAnswer = pollAnswers.find(a => Number(a.poll_index || 0) === curIdx) || null;
                containerEl.appendChild(this._createInteractivePollEl(seg.data, {
                    botMessageId,
                    pollIndex: curIdx,
                    savedAnswer,
                }));
            } else {
                const mdBlock = document.createElement('div');
                mdBlock.className = 'bot-md-segment';
                mdBlock.innerHTML = this._renderSafeMarkdownHtml(seg.text);
                containerEl.appendChild(mdBlock);
            }
        }
    }

    addMessage(message, scroll = true, animate = false) {
        const container = this.getMessagesContainer();
        if (!container) return;

        const isBot        = message.is_from_bot;
        const hasFiles     = Array.isArray(message.files) && message.files.length > 0;
        const hasUserFiles = !isBot && hasFiles;
        const emptyNotice  = isBot
            ? (hasFiles ? '' : 'Агент завершил шаг без текстового ответа — попробуйте повторить или уточнить запрос.')
            : (hasUserFiles ? '' : 'Сообщение без текста');
        const rawContent   = (message.message && String(message.message).trim()) ? message.message : emptyNotice;
        // Убираем статус-индикатор при ответе бота (через метод, чтобы захватить субагентов)
        if (isBot) this._clearAllStatuses();

        const messageEl = document.createElement('div');
        messageEl.className = isBot ? 'agent-message' : 'user-message';

        let content = rawContent;
        if (rawContent.trimStart().startsWith('<')) {
            content = rawContent; // уже HTML — не трогаем
        } else {
            content = this._parseMarkdown(rawContent);
        }
        if (isBot) {
            if (typeof DOMPurify !== 'undefined') content = DOMPurify.sanitize(content);
            // открываем все ссылки в новой вкладке
            const tmp = document.createElement('div');
            tmp.innerHTML = content;
            tmp.querySelectorAll('a').forEach(a => {
                a.setAttribute('target', '_blank');
                a.setAttribute('rel', 'noopener noreferrer');
            });
            content = tmp.innerHTML;
        } else {
            content = this.escapeHtml(rawContent);
        }

        const time = new Date(message.created_at || Date.now())
            .toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        if (!isBot) {
            // Длинный текст (>1000 символов) сворачиваем в чип-файлик,
            // клик по которому открывает превью в правом сайдбаре.
            // Длинный текст (Claude-style): рендерим обычным текстом,
            // но помечаем как collapsible — CSS обрежет по высоте + mask,
            // а кнопка «Показать больше/Свернуть» развернёт на месте.
            // NB: прикреплённые файлы (чипы) рендерятся НЕ внутри пузыря,
            // а над ним — вставляются в wrapper ниже (см. блок else).
            const TEXT_COLLAPSE_LIMIT = 1000;
            const isLong = rawContent && rawContent.length > TEXT_COLLAPSE_LIMIT;
            const textCls = isLong ? 'message-text message-text--collapsible' : 'message-text';
            const toggleHtml = isLong ? this._renderCollapseToggle() : '';
            messageEl.innerHTML = `<div class="${textCls}">${content}</div>${toggleHtml}<div class="timestamp">${time}</div>`;
        } else {
            messageEl.dataset.id = message.id;
            if (message.message_id) messageEl.dataset.messageId = message.message_id;
            const _botMsgKey = message.message_id || (message.id != null ? String(message.id) : '');
            if (animate) {
                messageEl.innerHTML = '';
                this._streamRenderMessage(messageEl, rawContent, container, message.files, message.message_id);
            } else {
                this._renderBotStructuredContent(messageEl, rawContent, {
                    botMessageId: _botMsgKey,
                    pollAnswers: message.poll_answers || [],
                });
                // Файлы агента из истории — вставляем после текста, перед actions
                if (message.files?.length) {
                    messageEl.appendChild(
                        this._renderBotFiles(message.files, message.message_id)
                    );
                }
            }
            this._removeBotPlaceholder();
        }

        const actions = this.createMessageActions(message.id, rawContent, isBot, {
            messageId: message.message_id || message.id || '',
            dbId: message.id || '',
            reaction: message.reaction ?? (typeof message.reactions === 'string' ? message.reactions : null),
            createdAt: message.created_at || message.timestamp || new Date().toISOString(),
        });

        if (isBot) {
            // Если есть placeholder и это стриминг — рендерим прямо внутрь него
            const placeholder = document.getElementById('agent-response-placeholder');
            if (animate && placeholder) {
                // Рендерим el ПРЯМО в container, placeholder просто держит место
                container.appendChild(messageEl);
                messageEl.style.opacity = '0';
                messageEl.style.transition = 'opacity 0.25s ease';
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => { messageEl.style.opacity = '1'; });
                });
            } else {
                container.appendChild(messageEl);
            }
            if (!animate) {
                // История — сразу вешаем actions и hljs
                this._applyCodeHeaders(messageEl);
                messageEl.appendChild(actions);
            }
            // При animate — actions и code-header вешает _streamRenderMessage в финале
        } else {
            const _uClientMsgId = message.message_id || message.id || '';
            if (_uClientMsgId && container.querySelector(
                `.user-message-wrapper[data-message-id="${_uClientMsgId}"], .user-message-wrapper[data-id="${_uClientMsgId}"]`
            )) {
                return;
            }
            const wrapper = document.createElement('div');
            wrapper.className = 'user-message-wrapper';
            // id и message_id сообщения на wrapper — нужны для дедупа WS-эха и истории БД
            if (message.id) wrapper.dataset.id = message.id;
            if (_uClientMsgId) wrapper.dataset.messageId = _uClientMsgId;
            // Прикреплённые файлы (чипы) — НАД пузырём сообщения, а не внутри него.
            const filesHtml = this._renderMessageFiles(message.files, _uClientMsgId);
            if (filesHtml) {
                const filesWrap = document.createElement('div');
                filesWrap.innerHTML = filesHtml;
                const filesNode = filesWrap.firstElementChild;
                if (filesNode) wrapper.appendChild(filesNode);
            }
            if (message.poll_answer && typeof message.poll_answer === 'object') {
                const pa = message.poll_answer;
                const bmid = String(pa.bot_message_id || '');
                const pidx = String(pa.poll_index ?? 0);
                if (bmid) {
                    container.querySelectorAll(`.ed-poll[data-bot-message-id="${bmid}"][data-poll-index="${pidx}"]`).forEach(pollEl => {
                        pollEl._applySavedAnswer?.(pa);
                    });
                }
            }
            container.querySelectorAll('.ed-poll:not(.is-answered)').forEach(pollEl => {
                pollEl._syncWithUserReply?.(rawContent);
            });
            if (rawContent.trim() || !hasUserFiles) {
                wrapper.appendChild(messageEl);
            }
            wrapper.appendChild(actions);
            container.appendChild(wrapper);
            this._repositionActiveBotNodesAfterUser(container);

            // Claude-collapsible: после вставки в DOM проверяем реальную высоту
            this._initCollapsibles(wrapper);

            // 🚀 Fly-анимация только для новых сообщений (scroll=true), не при загрузке истории
            if (scroll) {
                this._flyMessageFromInput(wrapper, container);
            }
        }

        if (!isBot) {
            // Скролл вниз только при загрузке истории (scroll=false), если не отключён принудительно.
            // При новом сообщении (scroll=true) скроллом управляет _scrollToUserMessageAndReserve.
            if (!scroll && !this._suppressHistoryAutoScroll) container.scrollTop = container.scrollHeight;
        } else if (scroll) {
            // Сообщение агента — только для живых ws-сообщений (scroll=true)
            const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
            if (distFromBottom < 80) {
                container.scrollTop = container.scrollHeight;
            }
        }

        // Пересчёт тумана по краям после добавления сообщения.
        container._syncFade && container._syncFade();
    }

    /* ══════════════════════════════════════════
       SCROLL TO BOTTOM ARROW (> 500px)
    ══════════════════════════════════════════ */
    _ensureScrollArrowBtn() {
        const inputArea = this.getInputContainer()?.querySelector('.chat-input-area');
        if (!inputArea) return null;
        let btn = inputArea.querySelector('#scroll-to-bottom-btn');
        if (!btn) {
            btn = document.createElement('button');
            btn.id = 'scroll-to-bottom-btn';
            btn.type = 'button';
            btn.className = 'chat-send-btn active scroll-to-bottom';
            btn.setAttribute('aria-label', 'Прокрутить вниз');
            btn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none">
                    <path d="M12 5v14M18 13l-6 6-6-6"
                          stroke="currentColor" stroke-width="2"
                          stroke-linecap="round" stroke-linejoin="round"/>
                </svg>`;
            btn.addEventListener('click', async () => {
                const container = this.getMessagesContainer();
                if (this._pagination && String(this._pagination.chatId) === String(this.currentChat) && this._pagination.hasNewer) {
                    await this.loadMessages(this.currentChat);
                } else if (container) {
                    container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
                }
                btn.classList.remove('visible');
            });
            inputArea.insertBefore(btn, inputArea.firstChild);
        }
        return btn;
    }

    _syncScrollArrow(container) {
        container = container || this.getMessagesContainer();
        const btn = this._ensureScrollArrowBtn();
        if (!btn) return;
        if (!container || container.classList.contains('hidden')) {
            btn.classList.remove('visible');
            return;
        }
        const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
        btn.classList.toggle('visible', distFromBottom > 500);
    }

    /**
     * FLIP-анимация: wrapper вылетает из зоны инпута наверх к chat-messages.
     * @param {HTMLElement} wrapper   — .user-message-wrapper только что добавленный в DOM
     * @param {HTMLElement} container — .chat-messages
     */
    _flyMessageFromInput(wrapper, container) {
        // ── LAST: текущая позиция враппера (уже в DOM) ──
        const lastRect = wrapper.getBoundingClientRect();

        // ── FIRST: берём нижнюю границу инпут-контейнера ──
        const inputField = this.getInputContainer()?.querySelector('#message-input');
        const inputRect  = inputField?.getBoundingClientRect();
        if (!inputRect) return;

        // Стартовая Y — верхний край поля ввода ("рождаемся" там)
        const startY = inputRect.top;

        // Дельта: насколько надо сдвинуть вниз чтобы wrapper оказался у инпута
        const deltaY = startY - lastRect.top;

        // ── INVERT: мгновенно смещаем в стартовую позицию без transition ──
        wrapper.style.transition = 'none';
        wrapper.style.transform  = `translateY(${deltaY}px)`;
        wrapper.style.opacity    = '0.6';

        // ── PLAY: в следующем кадре — плавно возвращаем на место ──
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                wrapper.style.transition = 'transform 0.38s cubic-bezier(0.22, 0.61, 0.36, 1), opacity 0.28s ease';
                wrapper.style.transform  = 'translateY(0)';
                wrapper.style.opacity    = '1';

                // Чистим инлайн-стили после окончания анимации
                wrapper.addEventListener('transitionend', () => {
                    wrapper.style.transition = '';
                    wrapper.style.transform  = '';
                    wrapper.style.opacity    = '';
                }, { once: true });
            });
        });
    }


    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /* ══════════════════════════════════════════
       RENDER FILES IN HISTORY MESSAGE
    ══════════════════════════════════════════ */
    /**
     * Строит HTML-блок с чипами файлов для исторического сообщения.
     * Если файлов > 3 — показываем первые 3, остальные скрываем за кнопкой.
     *
     * @param {Array<{name: string, ext: string}>|undefined} files
     * @returns {string} HTML-строка или '' если файлов нет
     */
    /* ══════════════════════════════════════════
       TEXT CHIP — свёрнутый длинный текст (>1000 симв.)
       Рендерит чип в стиле прикреплённого файла. Оригинальный
       текст кладём в data-атрибут (base64), чтобы клик открыл его
       в правом сайдбаре без искажений. */
    _renderCollapseToggle() {
        return `<button class="msg-collapse-toggle" type="button" aria-expanded="false" hidden
        onclick="window.__catchpmApp && window.__catchpmApp._toggleCollapse(this)">
    <span class="msg-collapse-toggle__label">Показать больше</span>
    <svg class="msg-collapse-toggle__chevron" viewBox="0 0 12 12" width="12" height="12" fill="none" aria-hidden="true">
      <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </button>`;
    }

    _toggleCollapse(btn) {
        const wrap = btn.closest('.user-message, .message-content, .user-message-wrapper') || btn.parentElement;
        const text = wrap ? wrap.querySelector('.message-text--collapsible') : null;
        if (!text) return;
        const expanded = text.classList.toggle('is-expanded');
        btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        const label = btn.querySelector('.msg-collapse-toggle__label');
        if (label) label.textContent = expanded ? 'Свернуть' : 'Показать больше';
    }

    _initCollapsibles(root) {
        const scope = root || document;
        const blocks = scope.querySelectorAll('.message-text--collapsible:not([data-collapse-checked])');
        blocks.forEach((el) => {
            el.dataset.collapseChecked = '1';
            const toggle = el.parentElement
                ? el.parentElement.querySelector('.msg-collapse-toggle')
                : null;
            const overflow = el.scrollHeight - el.clientHeight > 4;
            if (overflow) {
                if (toggle) toggle.hidden = false;
            } else {
                el.classList.remove('message-text--collapsible');
                if (toggle) toggle.remove();
            }
        });
    }

    _renderTextChip(rawText) {
        const chars = rawText.length;
        const lines = rawText.split('\n').length;
        // Первая непустая строка как «имя файла»
        const firstLine = (rawText.split('\n').find(l => l.trim()) || 'Текст').trim();
        const title = firstLine.length > 40 ? firstLine.slice(0, 40) + '…' : firstLine;
        const safeTitle = this.escapeHtml(title);
        const meta = `${chars} симв · ${lines} стр`;
        // base64(utf8) — безопасно переносим любой текст через data-атрибут
        let b64 = '';
        try {
            b64 = btoa(unescape(encodeURIComponent(rawText)));
        } catch (e) {
            b64 = '';
        }
        return `<div class="fa-chip fa-chip--text fa-chip--gray" role="button" tabindex="0"
     title="Открыть текст" data-text-b64="${b64}"
     onclick="window.__catchpmApp && window.__catchpmApp.openTextPreview(this)"
     onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();window.__catchpmApp && window.__catchpmApp.openTextPreview(this);}">
<span class="fa-chip__icon" aria-hidden="true">
  <svg viewBox="0 0 36 44" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M4 0h20l12 12v28a4 4 0 0 1-4 4H4a4 4 0 0 1-4-4V4a4 4 0 0 1 4-4z" class="fa-chip__bg"/>
    <path d="M24 0l12 12H28a4 4 0 0 1-4-4V0z" class="fa-chip__fold"/>
    <text x="18" y="32" text-anchor="middle" class="fa-chip__text">TXT</text>
  </svg>
</span>
<span class="fa-chip__name">${safeTitle}</span>
<span class="fa-chip__meta">${meta}</span>
</div>`;
    }

    _encodeTextB64(rawText) {
        try {
            return btoa(unescape(encodeURIComponent(rawText || '')));
        } catch (e) {
            return '';
        }
    }

    _decodeTextB64(b64) {
        if (!b64) return '';
        try {
            return decodeURIComponent(escape(atob(b64)));
        } catch (e) {
            return '';
        }
    }

    _updateChipData(chipEl, newText) {
        const text = String(newText ?? '');
        const chipId = chipEl?.getAttribute?.('data-chip-id') || null;
        if (chipId && this._pendingChips) {
            this._pendingChips.set(chipId, text);
        }
        if (!chipEl) return;

        const chars = text.length;
        const lines = chars > 0 ? text.split('\n').length : 0;
        const firstLine = (text.split('\n').find(l => l.trim()) || 'Текст').trim();
        const maxTitle = chipEl.classList.contains('input-chip') ? 32 : 40;
        const title = firstLine.length > maxTitle ? firstLine.slice(0, maxTitle) + '…' : firstLine;

        chipEl.setAttribute('data-text-b64', this._encodeTextB64(text));
        const nameEl = chipEl.querySelector('.fa-chip__name');
        if (nameEl) nameEl.textContent = title;
        const metaEl = chipEl.querySelector('.fa-chip__meta');
        if (metaEl) metaEl.textContent = `${chars} симв · ${lines} стр`;
    }

    _syncActivePreviewChipFromBody() {
        const body = document.getElementById('text-preview-body');
        if (!body) return;
        const raw = (body.textContent || '') === ''
            ? ''
            : String(body.innerText ?? body.textContent ?? '').replace(/\r\n/g, '\n');

        let activeChip = this._activePreviewChipEl;
        if ((!activeChip || !activeChip.isConnected) && this._activePreviewChipId) {
            activeChip = document.querySelector(`.input-chip[data-chip-id="${this._activePreviewChipId}"]`);
            this._activePreviewChipEl = activeChip || null;
        }

        if (raw.trim().length === 0) {
            const chipId = this._activePreviewChipId || activeChip?.getAttribute?.('data-chip-id') || null;
            if (chipId) {
                this.removeInputChip(chipId);
            } else {
                if (activeChip) {
                    const bar = activeChip.closest('.fa-preview-bar');
                    activeChip.remove();
                    if (bar) this._syncInputPreviewBar(bar);
                }
                this.closeTextPreview();
            }
            return;
        }

        if (activeChip) {
            this._updateChipData(activeChip, raw);
        } else if (this._activePreviewChipId && this._pendingChips) {
            this._pendingChips.set(this._activePreviewChipId, raw);
        }

        const titleEl = document.getElementById('text-preview-title');
        if (titleEl) {
            const first = (raw.split('\n').find(l => l.trim()) || 'Просмотр текста').trim();
            titleEl.textContent = first.length > 60 ? first.slice(0, 60) + '…' : first;
        }

        const field = this.getInputContainer()?.querySelector('#message-input');
        if (field) {
            try { field.dispatchEvent(new Event('input')); } catch (_) {}
        }
    }

    /* Открывает правый сайдбар-превью с оригинальным текстом чипа. */
    openTextPreview(chipEl) {
        if (!chipEl) return;
        const chipId = chipEl.getAttribute?.('data-chip-id') || null;
        let text = '';
        if (chipId && this._pendingChips && this._pendingChips.has(chipId)) {
            text = this._pendingChips.get(chipId);
        } else {
            text = this._decodeTextB64(chipEl.dataset?.textB64 || '');
        }

        if (!text || text.trim().length === 0) {
            if (chipId) {
                this.removeInputChip(chipId);
            } else {
                const bar = chipEl.closest('.fa-preview-bar');
                chipEl.remove();
                if (bar) this._syncInputPreviewBar(bar);
            }
            return;
        }

        const panel = document.getElementById('text-preview-panel');
        const body  = document.getElementById('text-preview-body');
        const titleEl = document.getElementById('text-preview-title');
        const view  = document.getElementById('view-agent');
        if (!panel || !body || !view) return;

        if (this._activePreviewChipEl && this._activePreviewChipEl !== chipEl) {
            this._activePreviewChipEl.classList.remove('is-preview-open');
        }
        this._activePreviewChipEl = chipEl;
        this._activePreviewChipId = chipId;
        chipEl.classList.add('is-preview-open');

        body.textContent = text;
        if (titleEl) {
            const first = (text.split('\n').find(l => l.trim()) || 'Просмотр текста').trim();
            titleEl.textContent = first.length > 60 ? first.slice(0, 60) + '…' : first;
        }
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
        body.scrollTop = 0;
    }

    /* Закрывает правый сайдбар-превью. */
    closeTextPreview() {
        const activeId = this._activePreviewChipId;
        if (this._activePreviewChipEl) {
            this._activePreviewChipEl.classList.remove('is-preview-open');
        }
        this._activePreviewChipEl = null;
        this._activePreviewChipId = null;

        const panel = document.getElementById('text-preview-panel');
        if (panel) {
            panel.classList.remove('open');
            panel.setAttribute('aria-hidden', 'true');
        }

        if (activeId && this._pendingChips && this._pendingChips.has(activeId)) {
            const curText = this._pendingChips.get(activeId) || '';
            if (curText.trim().length === 0) {
                this.removeInputChip(activeId);
            }
        }
    }

    _bindVideoPreviewLoop(videoEl, maxSec = 5) {
        if (!videoEl) return;
        videoEl.muted = true;
        videoEl.defaultMuted = true;
        videoEl.autoplay = true;
        videoEl.playsInline = true;
        videoEl.preload = 'auto';
        videoEl.setAttribute('muted', '');
        videoEl.setAttribute('autoplay', '');
        videoEl.setAttribute('playsinline', '');
        videoEl.setAttribute('webkit-playsinline', '');
        videoEl.dataset.previewLoop = '1';

        const restartLoop = () => {
            if (videoEl.dataset.previewLoop === '0') return;
            try { videoEl.currentTime = 0; } catch (_) {}
            const pr = videoEl.play();
            if (pr && typeof pr.catch === 'function') pr.catch(() => {});
        };
        videoEl.addEventListener('timeupdate', () => {
            if (videoEl.dataset.previewLoop === '0') return;
            if (videoEl.currentTime >= maxSec) {
                restartLoop();
            }
        });
        videoEl.addEventListener('ended', restartLoop);
        videoEl.addEventListener('loadeddata', () => {
            if (videoEl.paused && videoEl.dataset.previewLoop !== '0') {
                const pr = videoEl.play();
                if (pr && typeof pr.catch === 'function') pr.catch(() => {});
            }
        }, { once: true });
    }

    _applyVideoCardAspect(videoEl) {
        if (!videoEl) return;
        const card = videoEl.closest('.fa-video-card');
        if (!card) return;
        card.classList.remove('fa-image-card--loading');
        const vw = videoEl.videoWidth || 0;
        const vh = videoEl.videoHeight || 0;
        if (vw > 0 && vh > 0) {
            const MAX_W = 420;
            const MAX_H = 420;
            const MIN_W = 300;
            const MIN_H = 180;
            const r = vw / vh;
            let vidW, vidH;
            if (r >= MAX_W / MAX_H) {
                vidW = MAX_W;
                vidH = Math.round(MAX_W / r);
            } else {
                vidH = MAX_H;
                vidW = Math.round(MAX_H * r);
            }
            const boxW = Math.min(MAX_W, Math.max(MIN_W, vidW));
            const boxH = Math.min(MAX_H, Math.max(MIN_H, vidH));
            card.style.setProperty('--fa-card-w', boxW + 'px');
            card.style.setProperty('--fa-card-ar', `${boxW} / ${boxH}`);
            if (boxW > vidW + 2 || boxH > vidH + 2) {
                card.classList.add('fa-image-card--glass');
            } else {
                card.classList.remove('fa-image-card--glass');
            }
        }
    }

    _renderMessageFiles(files, messageId = '') {
        try {
            if (!files || !Array.isArray(files) || files.length === 0) return '';

            const EXT_LABEL = {
                pdf:'PDF', docx:'DOC', doc:'DOC',
                xlsx:'XLS', xls:'XLS', csv:'CSV',
                pptx:'PPT', ppt:'PPT',
                txt:'TXT', md:'MD',
                png:'PNG', jpg:'JPG', jpeg:'JPG', gif:'GIF', webp:'WEBP', svg:'SVG',
                mp4:'MP4', mov:'MOV', avi:'AVI',
                zip:'ZIP', rar:'RAR', '7z':'7Z',
                json:'JSON', xml:'XML', html:'HTML', py:'PY',
                js:'JS', ts:'TS', css:'CSS',
            };
            const EXT_COLOR = {
                pdf:'red', doc:'blue', docx:'blue',
                xls:'green', xlsx:'green', csv:'green',
                ppt:'orange', pptx:'orange',
                png:'purple', jpg:'purple', jpeg:'purple',
                gif:'purple', webp:'purple', svg:'purple', bmp:'purple', avif:'purple',
                mp4:'purple', mov:'purple', avi:'purple', webm:'purple', mkv:'purple', m4v:'purple', ogv:'purple',
                zip:'gray', rar:'gray', '7z':'gray',
                json:'yellow', xml:'yellow',
                py:'teal', js:'yellow', ts:'blue', css:'purple',
            };

            const getLabel = (ext) => EXT_LABEL[ext] || ext.toUpperCase().slice(0, 4) || 'FILE';
            const getColor = (ext) => EXT_COLOR[ext] || 'gray';

            const makeChip = (f) => {
                try {
                    const rawName = (f && f.name) ? String(f.name) : 'файл';
                    const ext   = ((f && f.ext) || (rawName.includes('.') ? rawName.split('.').pop() : '') || '').toLowerCase();
                    const label = getLabel(ext);
                    const color = getColor(ext);
                    const name  = this.escapeHtml(rawName);
                    return `<div class="fa-chip fa-chip--${color} fa-chip--readonly" title="${name}">
<span class="fa-chip__icon" aria-hidden="true">
  <svg viewBox="0 0 36 44" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M4 0h20l12 12v28a4 4 0 0 1-4 4H4a4 4 0 0 1-4-4V4a4 4 0 0 1 4-4z" class="fa-chip__bg"/>
    <path d="M24 0l12 12H28a4 4 0 0 1-4-4V0z" class="fa-chip__fold"/>
    <text x="18" y="32" text-anchor="middle" class="fa-chip__text">${label}</text>
  </svg>
</span>
<span class="fa-chip__name">${name}</span>
</div>`;
                } catch (_) {
                    return `<div class="fa-chip fa-chip--gray fa-chip--readonly"><span class="fa-chip__name">файл</span></div>`;
                }
            };

            const VISIBLE_LIMIT = 3;
            const visible  = files.slice(0, VISIBLE_LIMIT);
            const hidden   = files.slice(VISIBLE_LIMIT);

            let chipsHtml = visible.map(makeChip).join('');

            let collapseHtml = '';
            if (hidden.length > 0) {
                const hiddenChips = hidden.map(makeChip).join('');
                const uid = `fa-collapse-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
                collapseHtml = `
<div class="fa-chips-hidden" id="${uid}">${hiddenChips}</div>
<button class="fa-chips-toggle" type="button"
        onclick="(function(btn){
            var box = document.getElementById('${uid}');
            var open = box.classList.toggle('fa-chips-hidden--open');
            btn.textContent = open ? 'Скрыть' : '+${hidden.length} файл${hidden.length === 1 ? '' : hidden.length < 5 ? 'а' : 'ов'}';
        })(this)">+${hidden.length} файл${hidden.length === 1 ? '' : hidden.length < 5 ? 'а' : 'ов'}</button>`;
            }

            return `<div class="fa-message-files">${chipsHtml}${collapseHtml}</div>`;
        } catch (err) {
            console.warn('_renderMessageFiles error:', err);
            return '';
        }
    }
    /* ══════════════════════════════════════════
       RENDER BOT FILES (под сообщением агента, над кнопкой копировать)
    ══════════════════════════════════════════ */
    /**
     * Строит DOM-узел с чипами файлов агента.
     * Каждый чип — <a download> → GET /api/chats/{chat_id}/files/{message_id}/{name}
     * При > 3 файлах — показываем первые 3, остальные прячем за «+N файлов».
     *
     * @param {Array<{name: string, ext: string}>} files
     * @param {string|undefined} messageId
     * @returns {HTMLElement}
     */
    _renderBotFiles(files, messageId) {
        const EXT_LABEL = {
            pdf:'PDF', docx:'DOC', doc:'DOC',
            xlsx:'XLS', xls:'XLS', csv:'CSV',
            pptx:'PPT', ppt:'PPT',
            txt:'TXT', md:'MD',
            png:'PNG', jpg:'JPG', jpeg:'JPG', gif:'GIF', webp:'WEBP', svg:'SVG',
            mp4:'MP4', mov:'MOV', avi:'AVI',
            zip:'ZIP', rar:'RAR', '7z':'7Z',
            json:'JSON', xml:'XML', html:'HTML', py:'PY',
            js:'JS', ts:'TS', css:'CSS',
        };
        const EXT_COLOR = {
            pdf:'red', doc:'blue', docx:'blue',
            xls:'green', xlsx:'green', csv:'green',
            ppt:'orange', pptx:'orange',
            png:'purple', jpg:'purple', jpeg:'purple',
            gif:'purple', webp:'purple', svg:'purple',
            mp4:'pink', mov:'pink', avi:'pink',
            zip:'gray', rar:'gray', '7z':'gray',
            json:'yellow', xml:'yellow',
            py:'teal', js:'yellow', ts:'blue', css:'purple',
        };

        const getLabel = (ext) => EXT_LABEL[ext] || ext.toUpperCase().slice(0, 4) || 'FILE';
        const getColor = (ext) => EXT_COLOR[ext] || 'gray';

        const AUDIO_EXTS = new Set(['mp3', 'mpga', 'wav', 'wave', 'ogg', 'oga', 'opus', 'm4a', 'aac', 'flac', 'weba', 'caf', 'aif', 'aiff']);
        const isAudioFile = (f) => {
            if (window.ChatAnimations && typeof window.ChatAnimations.isPlayableAudioFile === 'function') {
                return window.ChatAnimations.isPlayableAudioFile(f);
            }
            let ext = (f.ext || '').toLowerCase();
            if (!ext && f.name && f.name.includes('.')) {
                ext = f.name.split('.').pop().toLowerCase();
            }
            return f.kind === 'audio' || AUDIO_EXTS.has(ext);
        };

        const makeChip = (f) => {
            const ext   = (f.ext || '').toLowerCase();
            const label = getLabel(ext);
            const color = getColor(ext);
            const name  = f.name || 'файл';
            const safeName = this.escapeHtml(name);

            // URL через gateway — авторизация по куке, проверка в chats_service
            const url = messageId
                ? `/api/chats/${this.currentChat}/files/${encodeURIComponent(messageId)}/${encodeURIComponent(name)}`
                : '#';

            if (isAudioFile(f) && window.ChatAnimations && typeof window.ChatAnimations.createAudioChip === 'function') {
                const dlUrl = url !== '#' ? `${url}?download=1` : '#';
                return window.ChatAnimations.createAudioChip({
                    name,
                    url,
                    dlUrl,
                    onContextMenu: (e) => {
                        e.preventDefault();
                        this._showFileContextMenu(e, { name, url: dlUrl, chatId: this.currentChat });
                    },
                });
            }

            const chip = document.createElement('a');
            chip.className = `fa-chip fa-chip--${color} fa-chip--bot`;
            chip.href      = url;
            chip.download  = name;
            chip.title     = name;
            chip.setAttribute('rel', 'noopener noreferrer');

            chip.innerHTML = `
<span class="fa-chip__icon" aria-hidden="true">
  <svg viewBox="0 0 36 44" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M4 0h20l12 12v28a4 4 0 0 1-4 4H4a4 4 0 0 1-4-4V4a4 4 0 0 1 4-4z" class="fa-chip__bg"/>
    <path d="M24 0l12 12H28a4 4 0 0 1-4-4V0z" class="fa-chip__fold"/>
    <text x="18" y="32" text-anchor="middle" class="fa-chip__text">${label}</text>
  </svg>
</span>
<span class="fa-chip__name">${safeName}</span>
<span class="fa-chip__dl-icon" aria-hidden="true">
  <svg viewBox="0 0 14 14" fill="none">
    <path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
</span>`;
            return chip;
        };

        // Картинка? kind==='image' (сгенерированные тулом generate_image)
        // ИЛИ расширение растрового изображения — рисуем превью-карточку.
        const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif']);
        const isImageFile = (f) => {
            let ext = (f.ext || '').toLowerCase();
            if (!ext && f.name && f.name.includes('.')) {
                ext = f.name.split('.').pop().toLowerCase();
            }
            return f.kind === 'image' || IMAGE_EXTS.has(ext);
        };

        const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v', 'ogv', 'avi', 'mkv']);
        const isVideoFile = (f) => {
            let ext = (f.ext || '').toLowerCase();
            if (!ext && f.name && f.name.includes('.')) {
                ext = f.name.split('.').pop().toLowerCase();
            }
            return f.kind === 'video' || VIDEO_EXTS.has(ext);
        };
        const getVideoMime = (ext) => {
            if (ext === 'webm') return 'video/webm';
            if (ext === 'mov') return 'video/quicktime';
            if (ext === 'ogv' || ext === 'ogg') return 'video/ogg';
            return 'video/mp4';
        };

        const resolveMediaItem = (f) => {
            const isVid = !isImageFile(f) && isVideoFile(f);
            const name = f.name || (isVid ? 'video.mp4' : 'image');
            const ext = (f.ext || (name.includes('.') ? name.split('.').pop() : '') || (isVid ? 'mp4' : 'png')).toLowerCase();
            if (isVid) {
                const streamUrl = messageId
                    ? `/api/chats/${this.currentChat}/files/${encodeURIComponent(messageId)}/${encodeURIComponent(name)}`
                    : '#';
                const dlUrl = streamUrl !== '#' ? `${streamUrl}?download=1` : '#';
                return {
                    isVideo: true,
                    name,
                    ext,
                    url: streamUrl,
                    dlUrl,
                    mime: getVideoMime(ext),
                    file: f,
                };
            }
            const imageKeyQs = f.image_key
                ? `?image_key=${encodeURIComponent(f.image_key)}`
                : '';
            const dbImageUrl = messageId
                ? `/api/chats/${this.currentChat}/image/${encodeURIComponent(messageId)}/${encodeURIComponent(name)}${imageKeyQs}`
                : '#';
            const fileUrl = messageId
                ? `/api/chats/${this.currentChat}/files/${encodeURIComponent(messageId)}/${encodeURIComponent(name)}`
                : '#';
            const preferFileEndpoint = (f.kind === 'file' && !f.image_key);
            const url = preferFileEndpoint ? fileUrl : dbImageUrl;
            const fallbackUrl = preferFileEndpoint ? dbImageUrl : fileUrl;
            return {
                isVideo: false,
                name,
                ext,
                url,
                fallbackUrl,
                dlUrl: url,
                imageKey: f.image_key || null,
                file: f,
            };
        };

        const makeImageCard = (item, idx, allItems) => {
            const name = item.name || 'image';
            const safeName = this.escapeHtml(name);
            const url = item.url;
            const fallbackUrl = item.fallbackUrl;

            const card = document.createElement('div');
            card.className = 'fa-image-card';
            card.title = name;

            const bg = document.createElement('span');
            bg.className = 'fa-image-card__bg';
            bg.setAttribute('aria-hidden', 'true');

            const img = document.createElement('img');
            img.className = 'fa-image-card__img';
            img.alt = safeName;
            img.loading = 'lazy';
            img.src = url;
            card.classList.add('fa-image-card--loading');
            let triedFallback = false;
            const applyCardAspect = () => {
                card.classList.remove('fa-image-card--loading');
                card.classList.remove('fa-image-card--error');
                const activeSrc = img.currentSrc || img.src || url;
                item.url = activeSrc;
                if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                    const MAX_W = 420;
                    const MAX_H = 420;
                    const MIN_W = 300;
                    const MIN_H = 110;
                    const r = img.naturalWidth / img.naturalHeight;
                    let imgW, imgH;
                    if (r >= MAX_W / MAX_H) {
                        imgW = MAX_W;
                        imgH = Math.round(MAX_W / r);
                    } else {
                        imgH = MAX_H;
                        imgW = Math.round(MAX_H * r);
                    }
                    const boxW = Math.min(MAX_W, Math.max(MIN_W, imgW));
                    const boxH = Math.min(MAX_H, Math.max(MIN_H, imgH));
                    card.style.setProperty('--fa-card-w', boxW + 'px');
                    card.style.setProperty('--fa-card-ar', `${boxW} / ${boxH}`);
                    if (boxW > imgW + 2 || boxH > imgH + 2) {
                        if (activeSrc && activeSrc !== '#') {
                            bg.style.backgroundImage = `url("${activeSrc.replace(/"/g, '\"')}")`;
                            if (item._dlBtn) item._dlBtn.href = item.dlUrl || activeSrc;
                        }
                        card.classList.add('fa-image-card--glass');
                    } else {
                        card.classList.remove('fa-image-card--glass');
                    }
                }
            };
            if (img.complete && img.naturalWidth > 0) {
                applyCardAspect();
            } else {
                img.addEventListener('load', applyCardAspect);
            }
            img.addEventListener('error', () => {
                if (!triedFallback && fallbackUrl && fallbackUrl !== '#' && fallbackUrl !== img.getAttribute('src')) {
                    triedFallback = true;
                    img.src = fallbackUrl;
                    return;
                }
                card.classList.remove('fa-image-card--loading');
                card.classList.add('fa-image-card--error');
            });

            card.addEventListener('click', () => this._openMediaLightbox(allItems, idx));
            if (typeof this._showFileContextMenu === 'function') {
                card.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    this._showFileContextMenu(e, { name, url: item.url, chatId: this.currentChat });
                });
            }

            const overlay = document.createElement('div');
            overlay.className = 'fa-image-card__overlay';
            overlay.innerHTML = `
<span class="fa-image-card__zoom" aria-hidden="true">
  <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="M20 20l-3.5-3.5M11 8v6M8 11h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
</span>`;

            const dlBtn = document.createElement('a');
            dlBtn.className = 'image-lightbox__dl fa-card-dl-btn';
            dlBtn.href = item.dlUrl || item.url;
            dlBtn.download = safeName;
            dlBtn.title = 'Скачать';
            dlBtn.innerHTML = '<svg viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            dlBtn.addEventListener('click', (e) => e.stopPropagation());

            // Обновляем ссылку при фолбэке
            item._dlBtn = dlBtn;

            const skel = document.createElement('div');
            skel.className = 'fa-image-card__skeleton';
            skel.innerHTML = '<div class="fa-pixel-grid"></div>';

            card.appendChild(bg);
            card.appendChild(skel);
            card.appendChild(img);
            card.appendChild(overlay);
            if (!allItems || allItems.length <= 1) { card.appendChild(dlBtn); }
            return card;
        };

        const makeVideoCard = (item, idx, allItems) => {
            const name = item.name || 'video.mp4';
            const streamUrl = item.url;
            const dlUrl = item.dlUrl;

            const card = document.createElement('div');
            card.className = 'fa-image-card fa-video-card fa-image-card--loading';
            card.title = name;

            const bg = document.createElement('span');
            bg.className = 'fa-image-card__bg';
            bg.setAttribute('aria-hidden', 'true');

            const skel = document.createElement('div');
            skel.className = 'fa-image-card__skeleton';
            skel.innerHTML = '<div class="fa-pixel-grid"></div>';

            const video = document.createElement('video');
            video.className = 'fa-video-card__video';
            video.src = streamUrl;
            const source = document.createElement('source');
            source.src = streamUrl;
            source.type = item.mime;
            video.appendChild(source);

            this._bindVideoPreviewLoop(video, 5);

            const onMeta = () => this._applyVideoCardAspect(video);
            if (video.readyState >= 1 && video.videoWidth > 0) {
                onMeta();
            } else {
                video.addEventListener('loadedmetadata', onMeta, { once: true });
            }
            video.addEventListener('error', () => {
                card.classList.remove('fa-image-card--loading');
            }, { once: true });

            const playBadge = document.createElement('span');
            playBadge.className = 'fa-collage__play';
            playBadge.setAttribute('aria-hidden', 'true');
            playBadge.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
            
            const dlBtn = document.createElement('a');
            dlBtn.className = 'image-lightbox__dl fa-card-dl-btn';
            dlBtn.href = dlUrl;
            dlBtn.download = name;
            dlBtn.title = 'Скачать';
            dlBtn.innerHTML = '<svg viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
            dlBtn.addEventListener('click', (e) => e.stopPropagation());

            card.addEventListener('click', () => this._openMediaLightbox(allItems, idx));
            if (typeof this._showFileContextMenu === 'function') {
                card.addEventListener('contextmenu', (e) => {
                    e.preventDefault();
                    this._showFileContextMenu(e, { name, url: dlUrl, chatId: this.currentChat });
                });
            }

            card.appendChild(bg);
            card.appendChild(skel);
            card.appendChild(video);
            card.appendChild(playBadge);
            if (!allItems || allItems.length <= 1) { card.appendChild(dlBtn); }
            return card;
        };

        const makeMediaCollage = (allItems) => {
            const n = allItems.length;
            const shown = allItems.slice(0, 4);
            const layout = n === 2 ? 'two' : n === 3 ? 'three' : 'four';
            const collage = document.createElement('div');
            collage.className = `fa-media-collage fa-media-collage--${layout}`;

            shown.forEach((item, i) => {
                const cell = document.createElement('button');
                cell.type = 'button';
                cell.className = 'fa-collage__cell fa-collage__cell--loading' + (item.isVideo ? ' fa-collage__cell--video' : '');
                cell.title = item.name || '';

                const skel = document.createElement('div');
                skel.className = 'fa-image-card__skeleton';
                skel.innerHTML = '<div class="fa-pixel-grid"></div>';
                cell.appendChild(skel);

                if (item.isVideo) {
                    const video = document.createElement('video');
                    video.className = 'fa-collage__video';
                    video.src = item.url;
                    const source = document.createElement('source');
                    source.src = item.url;
                    source.type = item.mime;
                    video.appendChild(source);

                    this._bindVideoPreviewLoop(video, 5);

                    const onLoaded = () => cell.classList.remove('fa-collage__cell--loading');
                    if (video.readyState >= 1) onLoaded();
                    else {
                        video.addEventListener('loadedmetadata', onLoaded, { once: true });
                        video.addEventListener('loadeddata', onLoaded, { once: true });
                        video.addEventListener('error', onLoaded, { once: true });
                    }
                    cell.appendChild(video);

                    const playBadge = document.createElement('span');
                    playBadge.className = 'fa-collage__play';
                    playBadge.setAttribute('aria-hidden', 'true');
                    playBadge.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
                    cell.appendChild(playBadge);
                } else {
                    const bg = document.createElement('span');
                    bg.className = 'fa-image-card__bg';
                    bg.setAttribute('aria-hidden', 'true');
                    if (item.url && item.url !== '#') {
                        bg.style.backgroundImage = `url("${item.url.replace(/"/g, '\"')}")`;
                    }
                    cell.appendChild(bg);

                    const img = document.createElement('img');
                    img.className = 'fa-collage__img';
                    img.alt = this.escapeHtml(item.name || 'image');
                    img.loading = 'lazy';
                    img.src = item.url;
                    let triedFallback = false;
                    const onImgLoad = () => {
                        cell.classList.remove('fa-collage__cell--loading');
                        const activeSrc = img.currentSrc || img.src || item.url;
                        item.url = activeSrc;
                        if (activeSrc && activeSrc !== '#') {
                            bg.style.backgroundImage = `url("${activeSrc.replace(/"/g, '\"')}")`;
                            if (item._dlBtn) item._dlBtn.href = item.dlUrl || activeSrc;
                        }
                    };
                    if (img.complete && img.naturalWidth > 0) onImgLoad();
                    else img.addEventListener('load', onImgLoad);
                    img.addEventListener('error', () => {
                        if (!triedFallback && item.fallbackUrl && item.fallbackUrl !== '#' && item.fallbackUrl !== img.getAttribute('src')) {
                            triedFallback = true;
                            img.src = item.fallbackUrl;
                            return;
                        }
                        cell.classList.remove('fa-collage__cell--loading');
                        cell.classList.add('fa-image-card--error');
                    });
                    cell.appendChild(img);

                    if (!(n > 4 && i === 3)) {
                        const overlay = document.createElement('div');
                        overlay.className = 'fa-image-card__overlay';
                        overlay.innerHTML = `
<span class="fa-image-card__zoom" aria-hidden="true">
  <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="M20 20l-3.5-3.5M11 8v6M8 11h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
</span>`;
                        cell.appendChild(overlay);
                    }
                }

                if (n > 4 && i === 3) {
                    const more = document.createElement('span');
                    more.className = 'fa-collage__more';
                    more.textContent = `+${n - 4}`;
                    cell.appendChild(more);
                } else {
                    
                }

                cell.addEventListener('click', () => this._openMediaLightbox(allItems, i));
                if (typeof this._showFileContextMenu === 'function') {
                    cell.addEventListener('contextmenu', (e) => {
                        e.preventDefault();
                        this._showFileContextMenu(e, {
                            name: item.name,
                            url: item.isVideo ? item.dlUrl : item.url,
                            chatId: this.currentChat
                        });
                    });
                }

                collage.appendChild(cell);
            });

            return collage;
        };

        // Разделяем на визуальные медиафайлы (в порядке следования) и обычные файлы.
        const visualFiles = files.filter(f => isImageFile(f) || isVideoFile(f));
        const otherFiles = files.filter(f => !isImageFile(f) && !isVideoFile(f));

        const wrap = document.createElement('div');
        wrap.className = 'fa-bot-files';

        if (visualFiles.length) {
            if (visualFiles.some(isImageFile)) this._removeImageGenSkeleton();
            const mediaItems = visualFiles.map(resolveMediaItem);
            const gallery = document.createElement('div');
            gallery.className = 'fa-image-gallery';
            if (mediaItems.length === 1) {
                const single = mediaItems[0];
                gallery.appendChild(single.isVideo
                    ? makeVideoCard(single, 0, mediaItems)
                    : makeImageCard(single, 0, mediaItems));
            } else {
                gallery.appendChild(makeMediaCollage(mediaItems));
            }
            wrap.appendChild(gallery);
        }

        const VISIBLE_LIMIT = 3;
        const visible = otherFiles.slice(0, VISIBLE_LIMIT);
        const hidden  = otherFiles.slice(VISIBLE_LIMIT);

        visible.forEach(f => wrap.appendChild(makeChip(f)));

        if (hidden.length > 0) {
            const hiddenWrap = document.createElement('div');
            hiddenWrap.className = 'fa-chips-hidden';
            hiddenWrap.style.display = 'none';
            hidden.forEach(f => hiddenWrap.appendChild(makeChip(f)));
            wrap.appendChild(hiddenWrap);

            const count   = hidden.length;
            const suffix  = count === 1 ? '' : count < 5 ? 'а' : 'ов';
            const toggle  = document.createElement('button');
            toggle.className   = 'fa-chips-toggle';
            toggle.type        = 'button';
            toggle.textContent = `+${count} файл${suffix}`;
            toggle.addEventListener('click', () => {
                const isOpen = hiddenWrap.style.display !== 'none';
                hiddenWrap.style.display = isOpen ? 'none' : 'flex';
                toggle.textContent = isOpen ? `+${count} файл${suffix}` : 'Скрыть';
            });
            wrap.appendChild(toggle);
        }

        return wrap;
    }

    /* ══════════════════════════════════════════
       CHAT DROPDOWN (три точки)
    ══════════════════════════════════════════ */
    _toggleChatDropdown(btn) {
        const existing = document.querySelector('.chat-item-dropdown');
        if (existing) {
            existing.remove();
            if (existing.dataset.forChat === btn.dataset.menuChatId) return;
        }

        const chatId = btn.dataset.menuChatId;
        const chat = this.chats.find(c => String(c.id) === String(chatId)) || {};

        const dropdown = document.createElement('div');
        dropdown.className = 'chat-item-dropdown';
        dropdown.dataset.forChat = btn.dataset.menuChatId;

        const makeItem = (className, text, iconSvg, onClick) => {
            const item = document.createElement('button');
            item.className = className;
            item.type = 'button';
            item.innerHTML = `<span class="chat-item-dropdown-icon" aria-hidden="true">${iconSvg}</span><span class="chat-item-dropdown-text">${text}</span>`;
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                dropdown.remove();
                onClick();
            });
            return item;
        };

        const iconEdit = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>';
        const iconStar = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 3.8l2.56 5.2 5.74.84-4.15 4.05.98 5.72L12 16.9 6.87 19.6l.98-5.72L3.7 9.84l5.74-.84L12 3.8z"/></svg>';
        const iconTrash = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg>';

        dropdown.appendChild(makeItem('chat-item-dropdown-action', 'Переименовать', iconEdit, () => this._renameChat(chatId, chat)));
        dropdown.appendChild(makeItem('chat-item-dropdown-action', chat.is_favorite ? 'Убрать из избранного' : 'В избранное', iconStar, () => this._favoriteChat(chatId, !chat.is_favorite)));
        dropdown.appendChild(makeItem('chat-item-dropdown-delete', 'Удалить', iconTrash, () => this._deleteChat(chatId)));

        // Позиционируем dropdown через fixed, чтобы он не выходил за viewport
        // даже когда chat-item находится в самом низу скролл-контейнера.
        document.body.appendChild(dropdown);

        // Сначала вставляем без координат, чтобы измерить высоту.
        dropdown.style.visibility = 'hidden';
        dropdown.style.top = '0px';
        dropdown.style.left = '0px';

        requestAnimationFrame(() => {
            const btnRect      = btn.getBoundingClientRect();
            const ddHeight     = dropdown.offsetHeight;
            const ddWidth      = dropdown.offsetWidth;
            const viewportH    = window.innerHeight;

            const spaceBelow   = viewportH - btnRect.bottom;
            const spaceAbove   = btnRect.top;

            let top;
            if (spaceBelow >= ddHeight + 4 || spaceBelow >= spaceAbove) {
                // показываем снизу
                top = btnRect.bottom + 4;
            } else {
                // не влезает снизу — показываем сверху
                top = btnRect.top - ddHeight - 4;
            }

            // Не выходить за правый край
            let left = btnRect.right - ddWidth;
            if (left < 4) left = 4;

            dropdown.style.top        = `${top}px`;
            dropdown.style.left       = `${left}px`;
            dropdown.style.visibility = '';
        });
    }

    async _renameChat(chatId, chat) {
        const initialTitle = (chat?.renamed_title || `Чат ${chatId}`).trim();
        const newTitle = await this._askChatRename(initialTitle);
        if (newTitle === null) return;

        try {
            await fetch(`/api/chats/${chatId}/rename`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ new_title: newTitle })
            });
            this.chats = this.chats.map(c => c.id === chatId ? { ...c, renamed_title: newTitle } : c);
            this.renderChats(this.chats);
        } catch (err) {
            console.error('renameChat error:', err);
        }
    }

    _askChatRename(initialTitle) {
        return new Promise((resolve) => {
            document.querySelector('.sidebar-rename-overlay')?.remove();

            const overlay = document.createElement('div');
            overlay.className = 'sidebar-rename-overlay';

            const modal = document.createElement('div');
            modal.className = 'sidebar-rename-modal';

            const title = document.createElement('div');
            title.className = 'sidebar-rename-title';
            title.textContent = 'Переименовать чат';

            const input = document.createElement('input');
            input.className = 'sidebar-rename-input';
            input.type = 'text';
            input.maxLength = 120;
            input.value = initialTitle || '';

            const actions = document.createElement('div');
            actions.className = 'sidebar-rename-actions';

            const cancelBtn = document.createElement('button');
            cancelBtn.className = 'sidebar-rename-btn sidebar-rename-cancel';
            cancelBtn.type = 'button';
            cancelBtn.textContent = 'Отмена';

            const saveBtn = document.createElement('button');
            saveBtn.className = 'sidebar-rename-btn sidebar-rename-save';
            saveBtn.type = 'button';
            saveBtn.textContent = 'Сохранить';

            actions.appendChild(cancelBtn);
            actions.appendChild(saveBtn);

            modal.appendChild(title);
            modal.appendChild(input);
            modal.appendChild(actions);
            overlay.appendChild(modal);
            document.body.appendChild(overlay);

            const cleanup = (value) => {
                document.removeEventListener('keydown', onKeydown, true);
                overlay.remove();
                resolve(value);
            };

            const submit = () => {
                const value = (input.value || '').trim();
                if (!value) return;
                cleanup(value);
            };

            const onKeydown = (e) => {
                if (e.key === 'Escape') {
                    e.preventDefault();
                    cleanup(null);
                }
                if (e.key === 'Enter') {
                    e.preventDefault();
                    submit();
                }
            };

            document.addEventListener('keydown', onKeydown, true);

            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) cleanup(null);
            });
            cancelBtn.addEventListener('click', () => cleanup(null));
            saveBtn.addEventListener('click', submit);

            setTimeout(() => {
                input.focus();
                input.select();
            }, 0);
        });
    }

    async _favoriteChat(chatId, isFavorite) {
        try {
            await fetch(`/api/chats/${chatId}/favorite`, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_favorite: isFavorite })
            });

            const card = document.querySelector(`.chat-item[data-chat-id="${chatId}"]`);
            if (card) {
                card.classList.add('chat-item-moving');
                setTimeout(() => card.classList.remove('chat-item-moving'), 280);
            }

            this.chats = this.chats.map(c => c.id === chatId ? { ...c, is_favorite: isFavorite } : c);
            this.renderChats(this.chats);
        } catch (err) {
            console.error('favoriteChat error:', err);
        }
    }

    _deleteChat(chatId) {
        // Убираем старую модалку если была
        document.getElementById('delete-chat-modal-overlay')?.remove();

        const overlay = document.createElement('div');
        overlay.id = 'delete-chat-modal-overlay';
        overlay.className = 'delete-chat-modal-overlay';

        overlay.innerHTML = `
<div class="delete-chat-modal">
    <div class="delete-chat-modal__icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 7h16M9 7V5h6v2m-8 0l1 12h8l1-12"/>
        </svg>
    </div>
    <div class="delete-chat-modal__title">Удалить чат?</div>
    <p class="delete-chat-modal__text">Это действие необратимо. История сообщений будет удалена.</p>
    <div class="delete-chat-modal__actions">
        <button class="delete-chat-modal__cancel" type="button">Отмена</button>
        <button class="delete-chat-modal__confirm" type="button">Удалить</button>
    </div>
</div>`;

        overlay.querySelector('.delete-chat-modal__cancel').addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') overlay.remove(); }, { once: true });

        overlay.querySelector('.delete-chat-modal__confirm').addEventListener('click', async () => {
            overlay.remove();
            try {
                await fetch(`/api/chats/${chatId}/delete`, {
                    method: 'POST',
                    credentials: 'include',
                });
            } catch (err) {
                console.error('deleteChat error:', err);
            }
            // Убираем из локального списка и перерисовываем только сайдбар
            this.chats = this.chats.filter(c => c.id !== chatId);
            this.renderChats(this.chats);
            // Черновик удалённого чата больше не нужен
            if (this._inputDrafts) delete this._inputDrafts[this._inputDraftKey(chatId)];
            // Сбрасываем интерфейс ТОЛЬКО если удалили именно открытый чат
            if (this.currentChat === chatId) {
                this.currentChat = null;
                window.ToolsPanel?.saveUserSettings?.({ active_chat_id: null });
                const messagesContainer = document.querySelector('.chat-messages');
                if (messagesContainer) {
                    messagesContainer.innerHTML = ''; 
                }
                document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
                document.querySelector('.chat-main-inner')?.classList.remove('chat-active');
                // Сбрасываем dataset чтобы renderMessageInput не пропустил рендер
                const inputContainer = this.getInputContainer();
                if (inputContainer) delete inputContainer.dataset.renderedChat;
                await this.renderMessageInput();
            }
        });

        document.body.appendChild(overlay);
    }

    generateChatId() {
        // UUID v4 — глобально уникальный id чата, исключает коллизии.
        if (window.crypto && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        // Фолбэк для старых браузеров (RFC4122 v4).
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            const r = (Math.random() * 16) | 0;
            const v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    /* ══════════════════════════════════════════
       SEND
    ══════════════════════════════════════════ */
    async sendMessage(customText = null, pollAnswer = null) {
        const isCustom = typeof customText === 'string';
        const container = this.getInputContainer();
        const field   = container?.querySelector('#message-input');
        const sendBtn = container?.querySelector('#send-btn');
        if (!field && !isCustom) return;

        if (!isCustom && this._runState === 'running') {
            await this.stopCurrentRun();
            return;
        }
        if (this._runState !== 'idle') return;

        const text = isCustom ? customText.trim() : this.getInputText();
        const hasAttached = !isCustom && this.hasAttachedFiles();
        if (!text && !hasAttached) return;

        if (!isCustom && text && /^\/(chat-id|workspaces)$/i.test(text.trim())) {
            await this._executeSlashCommand(text.trim().toLowerCase());
            return;
        }

        const attachedMeta = hasAttached ? (window.FileAttach?.getFilesMeta?.() || []) : [];
        const chatPreviewText = text || (attachedMeta[0]?.name ? `📎 ${attachedMeta[0].name}` : 'Файл');

        if (!this.currentChat) {
            this.currentChat = this.generateChatId();
            this.chats.push({ id: this.currentChat, last_message: chatPreviewText, timestamp: new Date().toISOString() });
            this.renderChats(this.chats);
            await this.openChat(this.currentChat);
        } else {
            const existing = this.chats.find(c => c.id === this.currentChat);
            if (existing) {
                existing.last_message = chatPreviewText;
                existing.timestamp = new Date().toISOString();
                this.renderChats(this.chats);
            }
        }

        this._botAnswered = false;
        this._hardResetStreamState(this.currentChat);

        const msgId = `msg-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
        this.addMessage({ message: text, is_from_bot: false, id: msgId, created_at: new Date().toISOString(), files: [] });
        this._scrollToUserMessageAndReserve();

        // Если ни одна категория инструментов не выбрана — агент работает
        // с ограниченным функционалом. Подсказываем облачком прямо в ленте.
        try {
            const _selTools = window.ToolsPanel?.getSelectedCategories() ?? [];
            if (_selTools.length === 0) this._showNoToolsHint();
        } catch (_) {}

        if (!isCustom && field) {
            this.clearInput(field);
            if (this._inputDrafts) {
                delete this._inputDrafts[this._inputDraftKey(this.currentChat)];
                delete this._inputDrafts['__start__'];
            }
        }
        sendBtn?.classList.remove('active');
        const _curSt = this._ensureStream(this.currentChat);
        if (_curSt) {
            _curSt.runStartedAt = Date.now();
            _curSt.reasoningDurationSec = 1;
        }

        this._runState = 'running';
        this._setSendButtonMode('stop');

        try {
            await this._waitForWS();

            const resp = await fetch('/api/chats/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                redirect: 'manual',
                body: JSON.stringify({
                    chat_id:        this.currentChat,
                    message:        text,
                    message_id:     msgId,
                    type_agent:     'pm',
                    agent_model:    window.ModelPicker?.getModel() ?? 'coder',
                    selected_tools: window.ToolsPanel?.getSelectedCategories() ?? [],
                    context_size:   window.ToolsPanel?.getContextSize()   ?? 'medium',
                    has_files:      false,
                    files: [],
                    ...(pollAnswer ? { poll_answer: pollAnswer } : {})
                })
            });
            if (window.__isAuthRedirectOrUnauthorized?.(resp)) {
                try {
                    sessionStorage.setItem('catchpm:pending_send_after_auth', JSON.stringify({
                        chatId: this.currentChat,
                        text: text,
                        pollAnswer: pollAnswer || null,
                        ts: Date.now()
                    }));
                    sessionStorage.setItem('catchpm:restore_chat_id', String(this.currentChat || ''));
                } catch (_) {}
                window.__onAuthSessionExpired?.({ forceReload: true });
                return;
            }
            try {
                const j = await resp.clone().json();
                this._activeRequestId = j?.request_id || null;
                if (j?.active_requests !== undefined) {
                    this._updateActiveRequestsUI(this.currentChat, j.active_requests);
                }
            } catch (_) {
                this._activeRequestId = null;
            }
            // SIGMA: WS отключён, поэтому финал ответа забираем polling-ом статуса.
            this._startStatusPolling(this.currentChat);
        } catch (err) {
            console.error('sendMessage error:', err);
            this._runState = 'idle';
            this._setSendButtonMode('send');
            this._activeRequestId = null;
        }
    }

    /**
     * После отправки сообщения пользователя:
     * 1. Скроллит chat-messages так, чтобы сообщение было у верхней границы (+ небольшой margin)
     * 2. Создаёт растущий placeholder под ответ бота
     * 3. Удаляет placeholder когда бот начинает отвечать
     */
    _scrollToUserMessageAndReserve() {
        const msgContainer = this.getMessagesContainer();
        if (!msgContainer) return;

        // Находим последний wrapper пользователя
        const wrappers = msgContainer.querySelectorAll('.user-message-wrapper');
        const lastWrapper = wrappers[wrappers.length - 1];
        if (!lastWrapper) return;

        const TOP_MARGIN = 16; // px от верхней границы chat-messages

        // Ждём окончания fly-анимации (0.38s = 380ms) и скроллим
        setTimeout(() => {
            const containerRect = msgContainer.getBoundingClientRect();
            const wrapperRect   = lastWrapper.getBoundingClientRect();

            // Целевой scrollTop: сообщение оказывается у верха + отступ
            const targetScrollTop = msgContainer.scrollTop
                + (wrapperRect.top - containerRect.top)
                - TOP_MARGIN;

            msgContainer.scrollTo({ top: targetScrollTop, behavior: 'smooth' });

            // Создаём placeholder под ответ бота
            this._createBotPlaceholder(msgContainer);
        }, 400); // чуть позже fly-анимации
    }

    /**
     * Создаёт placeholder-блок под ответ бота.
     * Блок растёт вместе с контентом контейнера.
     * Удаляется автоматически когда бот начинает реально отвечать.
     */
    _createBotPlaceholder(msgContainer) {
        const st = this._resolveStream(this.currentChat);
        if (this._botAnswered && (st?.streamDone || st?.streamIsFinal)) return;

        this._removeBotPlaceholder();

        const placeholder = document.createElement('div');
        placeholder.id = 'agent-response-placeholder';
        placeholder.className = 'agent-placeholder';
        placeholder.innerHTML = this._waveIndicatorSVG();

        const userWrappers = msgContainer.querySelectorAll('.user-message-wrapper');
        const lastUser = userWrappers.length ? userWrappers[userWrappers.length - 1] : null;
        let activeBotEl = (st?.pendingBotEl && st.pendingBotEl.parentNode === msgContainer) ? st.pendingBotEl : null;
        if (!activeBotEl && lastUser) {
            let cur = lastUser.nextElementSibling;
            while (cur) {
                if (cur.classList?.contains('agent-message')) {
                    activeBotEl = cur;
                    break;
                }
                cur = cur.nextElementSibling;
            }
        }

        if (activeBotEl && activeBotEl.parentNode === msgContainer) {
            msgContainer.insertBefore(placeholder, activeBotEl);
        } else if (lastUser && lastUser.nextSibling) {
            msgContainer.insertBefore(placeholder, lastUser.nextSibling);
        } else {
            msgContainer.appendChild(placeholder);
        }

        this._initPlaceholderGraph(placeholder);
        requestAnimationFrame(() => placeholder.classList.add('bg-ready'));

        msgContainer.scrollTop = msgContainer.scrollHeight;
    }

    /** Убирает placeholder — вызывается явно из WS-обработчика */
    _removeBotPlaceholder() {
        if (this._placeholderGuardTimer) {
            clearTimeout(this._placeholderGuardTimer);
            this._placeholderGuardTimer = null;
        }
        this._stopPlaceholderGraph();
        const ph = document.getElementById('agent-response-placeholder');
        if (!ph) return;
        ph.classList.add('placeholder-hide');
        setTimeout(() => ph.remove(), 300);
    }

    /* ══════════════════════════════════════════
       ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ: скелетон + лайтбокс
    ══════════════════════════════════════════ */

    /** Показывает пиксельный скелетон генерации картинки в ленте.
     *  Живёт, пока тул generate_image работает; снимается, когда пришли
     *  реальные файлы (в _renderBotFiles) или ответ финализирован. */
    _showImageGenSkeleton() {
        const container = this.getMessagesContainer();
        if (!container) return;
        if (document.getElementById('image-gen-skeleton')) return;

        const skel = document.createElement('div');
        skel.id = 'image-gen-skeleton';
        skel.className = 'fa-image-gen-skeleton';
        skel.innerHTML = `
<div class="fa-image-card fa-image-card--gen">
  <div class="fa-image-card__skeleton">
    <div class="fa-pixel-grid"></div>
    <div class="fa-gen-scan"></div>
  </div>
  <div class="fa-gen-caption">
    <span class="fa-gen-dot"></span>
    <span class="fa-gen-caption__text">Генерация изображения…</span>
  </div>
</div>`;
        container.appendChild(skel);
        container.scrollTop = container.scrollHeight;
    }

    _removeImageGenSkeleton() {
        const el = document.getElementById('image-gen-skeleton');
        if (!el) return;
        el.classList.add('fa-image-gen-skeleton--hide');
        setTimeout(() => el.remove(), 250);
    }

    /** Открывает картинку или галерею медиа в модальном лайтбоксе. */
    _openImageLightbox(url, name, imageKey) {
        this._openMediaLightbox([{
            isVideo: false,
            url,
            dlUrl: url,
            name: name || 'image',
            imageKey: imageKey || null,
        }], 0);
    }

    _openMediaLightbox(items, startIdx = 0) {
        if (!Array.isArray(items) || !items.length) return;
        document.getElementById('image-lightbox')?.remove();

        let idx = Math.max(0, Math.min(items.length - 1, Number(startIdx) || 0));

        const buildDlName = (rawName, key, isVideo) => {
            const fallback = isVideo ? 'video.mp4' : 'image';
            const nm = String(rawName || fallback);
            let ext = nm.includes('.') ? nm.split('.').pop().toLowerCase() : (isVideo ? 'mp4' : 'jpg');
            if (!/^[a-z0-9]{2,5}$/.test(ext)) ext = isVideo ? 'mp4' : 'jpg';
            return key ? `img_${String(key).slice(0, 8)}.${ext}` : nm;
        };

        const overlay = document.createElement('div');
        overlay.id = 'image-lightbox';
        overlay.className = 'image-lightbox gc-lightbox';

        const box = document.createElement('div');
        box.className = 'image-lightbox__box';

        const bar = document.createElement('div');
        bar.className = 'image-lightbox__bar';
        bar.innerHTML = `
<span class="image-lightbox__name"></span>
<span class="image-lightbox__dim" title="Разрешение"></span>
<span class="gc-lb__counter"></span>
<a class="image-lightbox__dl" href="#" download title="Скачать">
  <svg viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
</a>
<button class="image-lightbox__close" type="button" title="Закрыть" aria-label="Закрыть">
  <svg viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
</button>`;

        const mediaContainer = document.createElement('div');
        mediaContainer.className = 'image-lightbox__media-container';

        const prevBtn = document.createElement('button');
        prevBtn.type = 'button';
        prevBtn.className = 'gc-lb__nav gc-lb__nav--prev';
        prevBtn.setAttribute('aria-label', 'Назад');
        if (items.length < 2) prevBtn.hidden = true;
        prevBtn.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        const nextBtn = document.createElement('button');
        nextBtn.type = 'button';
        nextBtn.className = 'gc-lb__nav gc-lb__nav--next';
        nextBtn.setAttribute('aria-label', 'Вперёд');
        if (items.length < 2) nextBtn.hidden = true;
        nextBtn.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none"><path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        box.appendChild(bar);
        box.appendChild(mediaContainer);
        box.appendChild(prevBtn);
        box.appendChild(nextBtn);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        const nameEl = bar.querySelector('.image-lightbox__name');
        const dimEl = bar.querySelector('.image-lightbox__dim');
        const cntEl = bar.querySelector('.gc-lb__counter');
        const dlEl = bar.querySelector('.image-lightbox__dl');

        const revokeObjUrl = () => {
            if (overlay._objUrl) {
                URL.revokeObjectURL(overlay._objUrl);
                overlay._objUrl = null;
            }
        };

        let renderToken = 0;
        const showCurrent = () => {
            renderToken += 1;
            const myToken = renderToken;
            revokeObjUrl();
            mediaContainer.innerHTML = '';

            const it = items[idx] || {};
            const isVid = !!it.isVideo;
            const dlName0 = buildDlName(it.name, it.imageKey, isVid);
            nameEl.textContent = dlName0;
            nameEl.title = it.imageKey || dlName0;
            dimEl.textContent = '…';
            cntEl.textContent = items.length > 1 ? `${idx + 1}/${items.length}` : '';
            const href0 = (isVid ? (it.dlUrl || it.url) : it.url) || '#';
            dlEl.setAttribute('href', href0);
            dlEl.setAttribute('download', dlName0);

            if (isVid) {
                const video = document.createElement('video');
                video.className = 'image-lightbox__video';
                video.controls = true;
                video.autoplay = true;
                video.playsInline = true;
                video.setAttribute('playsinline', '');
                video.setAttribute('webkit-playsinline', '');
                video.src = it.url || '';
                if (it.mime) {
                    const source = document.createElement('source');
                    source.src = it.url || '';
                    source.type = it.mime;
                    video.appendChild(source);
                }
                video.addEventListener('loadedmetadata', () => {
                    if (myToken !== renderToken) return;
                    if (video.videoWidth && video.videoHeight) {
                        dimEl.textContent = `${video.videoWidth}×${video.videoHeight}`;
                    } else {
                        dimEl.textContent = '';
                    }
                });
                video.addEventListener('error', () => {
                    if (myToken !== renderToken) return;
                    dimEl.textContent = '';
                });
                mediaContainer.appendChild(video);
            } else {
                const img = document.createElement('img');
                img.className = 'image-lightbox__img';
                img.alt = this.escapeHtml(dlName0);
                img.addEventListener('load', () => {
                    if (myToken !== renderToken) return;
                    if (img.naturalWidth && img.naturalHeight) {
                        dimEl.textContent = `${img.naturalWidth}×${img.naturalHeight}`;
                    } else {
                        dimEl.textContent = '';
                    }
                });
                img.addEventListener('error', () => {
                    if (myToken !== renderToken) return;
                    dimEl.textContent = '';
                });

                const applyKey = (key) => {
                    if (!key || myToken !== renderToken) return;
                    const dlName = buildDlName(it.name, key, false);
                    nameEl.textContent = dlName;
                    nameEl.title = key;
                    dlEl.setAttribute('download', dlName);
                };

                if (it.imageKey) {
                    img.src = it.url;
                } else {
                    fetch(it.url, { credentials: 'same-origin' })
                        .then((resp) => {
                            const key = resp.headers.get('X-Image-Key');
                            return resp.blob().then((blob) => ({ key, blob }));
                        })
                        .then(({ key, blob }) => {
                            if (myToken !== renderToken) return;
                            const objUrl = URL.createObjectURL(blob);
                            img.src = objUrl;
                            dlEl.setAttribute('href', objUrl);
                            applyKey(key);
                            overlay._objUrl = objUrl;
                        })
                        .catch(() => {
                            if (myToken !== renderToken) return;
                            img.src = it.url;
                        });
                }
                

                                // --- ЗУМ И МОБИЛЬНЫЕ ЖЕСТЫ ---

                                let scale = 1, panX = 0, panY = 0;

                                const applyTransform = () => {

                                    img.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;

                                };

                                const resetZoom = () => {

                                    scale = 1; panX = 0; panY = 0;

                                    img.style.transition = 'transform 0.2s ease-out';

                                    applyTransform();

                                };

                                

                                let dragging = false, lastX, lastY;

                                let touchStartDist = 0, touchStartScale = 1;

                                let lastTap = 0;

                                

                                const clampPan = () => {

                                    if (scale <= 1) { panX = 0; panY = 0; return; }

                                    const rect = img.getBoundingClientRect();

                                    const boxRect = mediaContainer.getBoundingClientRect();

                                    const maxPx = Math.max(0, (rect.width - boxRect.width) / 2);

                                    const maxPy = Math.max(0, (rect.height - boxRect.height) / 2);

                                    panX = Math.max(-maxPx, Math.min(maxPx, panX));

                                    panY = Math.max(-maxPy, Math.min(maxPy, panY));

                                };

                                

                                img.addEventListener('wheel', (e) => {

                                    e.preventDefault();

                                    const step = e.deltaY < 0 ? 1.15 : (1 / 1.15);

                                    scale = Math.min(6, Math.max(1, scale * step));

                                    if (scale === 1) { panX = 0; panY = 0; } else clampPan();

                                    applyTransform();

                                }, { passive: false });

                                

                                img.addEventListener('dblclick', (e) => {

                                    e.stopPropagation();

                                    if (scale > 1.05) resetZoom();

                                    else { scale = 2.25; applyTransform(); }

                                });

                                img.addEventListener('mousedown', (e) => {

                                    if (scale <= 1) return;

                                    e.preventDefault(); dragging = true; lastX = e.clientX; lastY = e.clientY;

                                    img.style.cursor = 'grabbing';

                                    img.style.transition = 'none';

                                });

                                window.addEventListener('mousemove', (e) => {

                                    if (!dragging) return;

                                    panX += e.clientX - lastX; panY += e.clientY - lastY;

                                    lastX = e.clientX; lastY = e.clientY;

                                    clampPan(); applyTransform();

                                });

                                window.addEventListener('mouseup', () => {

                                    if (dragging) { dragging = false; img.style.cursor = scale > 1 ? 'grab' : 'zoom-in'; img.style.transition = 'transform 0.08s ease-out'; }

                                });

                                

                                img.addEventListener('touchstart', (e) => {

                                    if (e.touches.length === 1) {

                                        lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;

                                        img.style.transition = 'none';

                                    } else if (e.touches.length === 2) {

                                        const dx = e.touches[0].clientX - e.touches[1].clientX;

                                        const dy = e.touches[0].clientY - e.touches[1].clientY;

                                        touchStartDist = Math.sqrt(dx*dx + dy*dy);

                                        touchStartScale = scale;

                                        img.style.transition = 'none';

                                    }

                                }, { passive: false });

                

                                img.addEventListener('touchmove', (e) => {

                                    if (e.touches.length === 1 && scale > 1.01) {

                                        e.preventDefault();

                                        panX += e.touches[0].clientX - lastX; panY += e.touches[0].clientY - lastY;

                                        lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;

                                        clampPan(); applyTransform();

                                    } else if (e.touches.length === 2) {

                                        e.preventDefault();

                                        const dx = e.touches[0].clientX - e.touches[1].clientX;

                                        const dy = e.touches[0].clientY - e.touches[1].clientY;

                                        const dist = Math.sqrt(dx*dx + dy*dy);

                                        scale = Math.min(6, Math.max(1, touchStartScale * (dist / touchStartDist)));

                                        clampPan(); applyTransform();

                                    }

                                }, { passive: false });

                

                                img.addEventListener('touchend', (e) => {

                                    if (e.touches.length === 0) {

                                        const now = Date.now();

                                        if (now - lastTap < 300) {

                                            if (scale > 1.05) resetZoom();

                                            else { scale = 2.25; applyTransform(); }

                                        }

                                        lastTap = now;

                                        if (scale <= 1) resetZoom();

                                        img.style.transition = 'transform 0.08s ease-out';

                                    }

                                });

                                // -----------------------------

                
                mediaContainer.appendChild(img);
            }
        };

        const nav = (delta) => {
            if (items.length < 2) return;
            idx = (idx + delta + items.length) % items.length;
            showCurrent();
        };

        prevBtn.addEventListener('click', (e) => { e.stopPropagation(); nav(-1); });
        nextBtn.addEventListener('click', (e) => { e.stopPropagation(); nav(1); });

        const close = () => {
            overlay.classList.add('image-lightbox--hide');
            setTimeout(() => overlay.remove(), 200);
            document.removeEventListener('keydown', onKey);
            revokeObjUrl();
        };
        const onKey = (e) => {
            if (e.key === 'Escape') close();
            else if (e.key === 'ArrowLeft') nav(-1);
            else if (e.key === 'ArrowRight') nav(1);
        };

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target === box || e.target === mediaContainer) close();
        });
        bar.querySelector('.image-lightbox__close').addEventListener('click', close);
        bar.querySelector('.image-lightbox__dl').addEventListener('click', (e) => e.stopPropagation());
        document.addEventListener('keydown', onKey);

        showCurrent();
        requestAnimationFrame(() => overlay.classList.add('image-lightbox--shown'));
    }



    /* ══════════════════════════════════════════
       TYPEWRITER-СТРИМИНГ
       Приём чанков отвязан от отрисовки: чанки копятся
       в _streamBuffer, а ровный RAF-цикл каждый кадр
       «подъедает» из буфера фиксированную порцию символов
       и дорисовывает. Тяжёлый marked.parse троттлится.
    ══════════════════════════════════════════ */

    // Базовый темп: символов за кадр (~60fps → ~240 симв/сек).
    // При переполнении буфера порция плавно растёт, чтобы не отставать
    // от быстрой генерации, но не рвать плавность.
    _streamTypewriterStep(st) {
        const CHARS_PER_FRAME = 7;      // базовый темп (чуть быстрее, ~420 симв/сек)
        const buffered = st.streamBuffer ? st.streamBuffer.length : 0;

        // Автоускорение: чем больше накопилось, тем крупнее порция.
        let step = CHARS_PER_FRAME;
        if (buffered > 400)  step = Math.min(16, CHARS_PER_FRAME + Math.floor(buffered / 150));
        if (buffered > 2000) step = Math.min(48, Math.floor(buffered / 50));

        return step;
    }

    _startStreamLoop(chatId) {
        const st = this._resolveStream(chatId);
        if (st.streamRaf) return;  // цикл уже крутится

        const RENDER_THROTTLE_MS = 70;  // не чаще — тяжёлый marked.parse
        st.lastStreamRender = 0;

        const tick = (ts) => {
            if (!st.liveBlockEl) {
                st.streamRaf = null;
                return;
            }

            // Вытаскиваем порцию символов из буфера
            const step = this._streamTypewriterStep(st);
            if (st.streamBuffer && st.streamBuffer.length) {
                const take = st.streamBuffer.slice(0, step);
                st.streamShown  += take;
                st.streamBuffer  = st.streamBuffer.slice(step);
            }

            // Троттлим тяжёлый Markdown-ре-рендер
            if (!ts) ts = performance.now();
            if (ts - st.lastStreamRender >= RENDER_THROTTLE_MS) {
                this._renderStreamPartial(chatId);
                st.lastStreamRender = ts;
            }

            // Условие завершения: сервер сказал done И буфер пуст
            const drained = !st.streamBuffer || st.streamBuffer.length === 0;
            if (st.streamDone && drained) {
                this._renderStreamPartial(chatId);   // финальный partial-рендер текста
                this._finalizeStream(chatId);        // файлы, actions, подсветка
                st.streamRaf = null;
                return;
            }

            st.streamRaf = requestAnimationFrame(tick);
        };

        st.streamRaf = requestAnimationFrame(tick);
    }

    _renderStreamPartial(chatId) {
        const st = this._resolveStream(chatId);
        // Рендерим только в ЖИВОЙ блок (reasoning-блоки уже свёрнуты выше).
        if (!st.liveBlockEl) return;
        // Для фонового чата контейнера в DOM нет — узел откреплён, но рендер
        // всё равно идёт в его innerHTML (виден при возврате). Автоскролл —
        // только для активного чата.
        const container = this._isActiveChat(chatId) ? this.getMessagesContainer() : null;

        let html = this._parseMarkdown(st.streamShown || '');
        if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
            ADD_TAGS: ['table','thead','tbody','tr','th','td'],
            ADD_ATTR: ['colspan','rowspan','align']
        });
        st.liveBlockEl.innerHTML = html;

        if (container) {
            const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
            if (distFromBottom < 80) container.scrollTop = container.scrollHeight;
        }
    }

    /* SVG-каретка (Tabler chevron-right). Один стиль на весь свёрток —
       поворачивается через CSS-transform при раскрытии. */
    _reasoningCaretSVG() {
        return `<svg class="reasoning-caret" viewBox="0 0 24 24" width="12" height="12" `
             + `fill="none" stroke="currentColor" stroke-width="2" `
             + `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">`
             + `<path d="M9 6l6 6-6 6"/></svg>`;
    }

    /* Живой индикатор ожидания/размышления: граф из трёх точек,
       соединённых линией. Крутится рывками: быстрый прокрут → пауза →
       быстрый прокрут → пауза (CSS keyframes).
       На финале индикатор просто замирает и плавно гаснет. */
    _waveIndicatorSVG() {
        return window.ChatAnimations?.waveIndicatorSVG?.()
            || '<div class="wave-indicator"><canvas class="placeholder-graph" aria-hidden="true"></canvas></div>';
    }

    _stopPlaceholderGraph() {
        window.ChatAnimations?.stopPlaceholderGraph?.();
    }

    _initPlaceholderGraph(placeholder) {
        window.ChatAnimations?.initPlaceholderGraph?.(placeholder);
    }

    _flattenWaveIndicator() {
        window.ChatAnimations?.flattenWaveIndicator?.();
    }

    _formatReasoningDuration(sec) {
        const s = Math.max(1, Math.round(Number(sec) || 1));
        if (s < 60) return `${s} сек.`;
        const m = Math.floor(s / 60);
        const rem = s % 60;
        return rem > 0 ? `${m} мин. ${rem} сек.` : `${m} мин.`;
    }

    _updateActiveRequestsUI(chatId, count) {
        if (count === undefined || count === null) return;
        const n = Math.max(0, Number(count) || 0);
        this._activeRequestsCount = n;
        const st = this._resolveStream(chatId);
        if (st) st.activeRequestsCount = n;
        const liveEl = document.getElementById('presence-overlay-live');
        if (liveEl) {
            liveEl.textContent = String(n);
            liveEl.classList.toggle('is-busy', n > 0);
        }
    }

    _setReasoningHeaderLabel(chatId, text, isFinal = false) {
        const st = this._resolveStream(chatId);
        const root = st?.reasoningWrapEl || st?.pendingBotEl?.querySelector('.reasoning-block');
        if (!root) return;
        const labelEl = root.querySelector('.reasoning-header .reasoning-label');
        if (!labelEl) return;
        if (isFinal) {
            labelEl.textContent = 'Размышления';
            return;
        }
        const clean = String(text || '')
            .replace(/<[^>]+>/g, ' ')
            .replace(/[`*~#>]+/g, '')
            .replace(/\s+/g, ' ')
            .trim();
        labelEl.textContent = clean || 'Размышления';
    }

    _updateReasoningDurationUI(chatId, overrideSec = null) {
        const st = this._resolveStream(chatId);
        if (!st) return;
        let sec = overrideSec;
        if (sec == null) {
            if (st.runStartedAt) {
                sec = Math.max(1, Math.round((Date.now() - st.runStartedAt) / 1000));
            } else {
                sec = Math.max(1, Number(st.reasoningDurationSec) || 1);
            }
        }
        st.reasoningDurationSec = sec;
        const label = this._formatReasoningDuration(sec);
        const root = st.pendingBotEl || st.reasoningWrapEl;
        if (root) {
            root.querySelectorAll('[data-reasoning-duration]').forEach(el => {
                el.textContent = label;
            });
        }
    }

    _startReasoningTimer(chatId) {
        const st = this._resolveStream(chatId);
        if (!st) return;
        if (!st.runStartedAt) st.runStartedAt = Date.now();
        this._updateReasoningDurationUI(chatId);
        if (st.reasoningTimer) return;
        st._pollTick = 0;
        st.reasoningTimer = setInterval(() => {
            this._updateReasoningDurationUI(chatId);
            st._pollTick = (st._pollTick || 0) + 1;
            if (st._pollTick % 3 === 0 && chatId && !window.__authSessionExpired && document.visibilityState !== 'hidden') {
                fetch(`/api/chats/${chatId}/run_status`, { credentials: 'include', redirect: 'manual' })
                    .then(r => {
                        if (window.__isAuthRedirectOrUnauthorized?.(r)) {
                            window.__onAuthSessionExpired?.();
                            return null;
                        }
                        return r.ok ? r.json() : null;
                    })
                    .then(j => {
                        if (j && j.active_requests !== undefined) {
                            this._updateActiveRequestsUI(chatId, j.active_requests);
                        }
                    })
                    .catch(() => {});
            }
        }, 1000);
    }

    _stopReasoningTimer(chatId, finalSec = null) {
        const st = this._resolveStream(chatId);
        if (!st) return;
        if (st.reasoningTimer) {
            clearInterval(st.reasoningTimer);
            st.reasoningTimer = null;
        }
        this._updateReasoningDurationUI(chatId, finalSec);
        this._setReasoningHeaderLabel(chatId, 'Размышления', true);
        if (st.reasoningWrapEl) {
            st.reasoningWrapEl.setAttribute('data-state', 'done');
        }
    }

    _ensureBotAfterLastUserMessage(container, botEl) {
        if (!container || !botEl) return;
        const placeholder = container.querySelector('#agent-response-placeholder');
        const userWrappers = container.querySelectorAll('.user-message-wrapper');
        const lastUser = userWrappers.length ? userWrappers[userWrappers.length - 1] : null;
        if (botEl.parentNode !== container) {
            container.appendChild(botEl);
        } else if (lastUser && (lastUser.compareDocumentPosition(botEl) & Node.DOCUMENT_POSITION_PRECEDING)) {
            container.appendChild(botEl);
        }
        // Плашка #agent-response-placeholder всегда строго СВЕРХУ рассуждений (перед botEl)
        if (placeholder && placeholder.parentNode === container && botEl.parentNode === container) {
            container.insertBefore(placeholder, botEl);
        }
    }

    _repositionActiveBotNodesAfterUser(container) {
        if (!container) return;
        const placeholder = container.querySelector('#agent-response-placeholder');
        if (placeholder && placeholder.parentNode === container) {
            container.appendChild(placeholder);
        }
        const st = this._resolveStream(this.currentChat);
        const botId = String(st?.pendingBotEl?.dataset?.id || '');
        const isCommittedDbMsg = /^\d+$/.test(botId);
        if (st?.pendingBotEl && st.pendingBotEl.parentNode === container && st.streamingActive && !st.streamDone && !isCommittedDbMsg) {
            container.appendChild(st.pendingBotEl);
        }
        if (placeholder && placeholder.parentNode === container && st?.pendingBotEl && st.pendingBotEl.parentNode === container && st.streamingActive && !st.streamDone && !isCommittedDbMsg) {
            container.insertBefore(placeholder, st.pendingBotEl);
        }
    }

    _dedupeTurnReasoningAndActions(container, st, incomingId = null) {
        if (!container) return;
        const userWrappers = container.querySelectorAll('.user-message-wrapper');
        const lastUser = userWrappers.length ? userWrappers[userWrappers.length - 1] : null;

        const botCandidates = [];
        if (lastUser) {
            let cur = lastUser.nextElementSibling;
            while (cur) {
                if (cur.classList?.contains('agent-message')) botCandidates.push(cur);
                cur = cur.nextElementSibling;
            }
        } else {
            container.querySelectorAll(':scope > .agent-message').forEach(el => {
                if (el.querySelector('.reasoning-block, .reasoning-live') || String(el.dataset.id || '').startsWith('msg-')) {
                    botCandidates.push(el);
                }
            });
        }

        if (!botCandidates.length) return;

        const primary = (st?.pendingBotEl && botCandidates.includes(st.pendingBotEl))
            ? st.pendingBotEl
            : botCandidates[0];

        const primaryId = String(primary.dataset.id || '');
        const isDbCommittedNode = /^\d+$/.test(primaryId);
        if (st && (!st.pendingBotEl || !container.contains(st.pendingBotEl)) && !isDbCommittedNode) {
            const hasActiveStreamChild = !!primary.querySelector('.reasoning-block, .reasoning-live, .live-block');
            const matchesIncoming = incomingId && primaryId === String(incomingId);
            if (hasActiveStreamChild && (primaryId.startsWith('msg-') || matchesIncoming)) {
                st.pendingBotEl = primary;
            }
        }

        if (botCandidates.length > 1) {
            for (let i = 1; i < botCandidates.length; i++) {
                const dup = botCandidates[i];
                if (dup === primary) continue;
                const dupReasoning = dup.querySelector('.reasoning-block');
                const dupLive = dup.querySelector('.reasoning-live, .live-block');
                const isSplitTurn = dupReasoning || dupLive || String(dup.dataset.id || '').startsWith('msg-');
                if (!isSplitTurn) continue;

                while (dup.firstChild) {
                    primary.appendChild(dup.firstChild);
                }
                dup.remove();
            }
        }

        const rBlocks = [...primary.querySelectorAll('.reasoning-block')];
        if (rBlocks.length > 0) {
            const firstWrap = rBlocks[0];
            const firstBody = firstWrap.querySelector('.reasoning-body');
            const seenSteps = new Set(
                [...(firstBody?.querySelectorAll('.reasoning-step') || [])].map(s => s.textContent.trim())
            );
            for (let i = 1; i < rBlocks.length; i++) {
                const extra = rBlocks[i];
                extra.querySelectorAll('.reasoning-step').forEach(step => {
                    const key = step.textContent.trim();
                    if (key && !seenSteps.has(key) && firstBody) {
                        seenSteps.add(key);
                        firstBody.appendChild(step);
                    }
                });
                extra.remove();
            }
            if (st && st.pendingBotEl === primary) {
                st.reasoningWrapEl = firstWrap;
            }
            if (primary.firstElementChild !== firstWrap) {
                primary.insertBefore(firstWrap, primary.firstElementChild);
            }
        }

        const actionsList = [...primary.querySelectorAll(':scope > .message-actions')];
        if (st && st.pendingBotEl === primary && !st.streamDone) {
            actionsList.forEach(a => a.remove());
        } else if (actionsList.length > 1) {
            for (let i = 0; i < actionsList.length - 1; i++) {
                actionsList[i].remove();
            }
        }
    }

    _restoreReasoningFromServer(chatId, reasoningState, isRunning) {
        if (!reasoningState) return;
        const steps = Array.isArray(reasoningState.steps) ? reasoningState.steps.filter(Boolean) : [];
        const liveText = (reasoningState.live_text || '').trim();
        if (!steps.length && !liveText) return;

        const container = this.getMessagesContainer();
        if (!container) return;
        const st = this._ensureStream(chatId);

        const durSec = Math.max(1, Number(reasoningState.duration_sec || reasoningState.elapsed_sec || st?.reasoningDurationSec || 1));
        if (st) {
            st.reasoningDurationSec = durSec;
            if (!st.runStartedAt && reasoningState.started_at) {
                st.runStartedAt = Number(reasoningState.started_at) * 1000;
            }
        }

        if (isRunning) {
            this._dedupeTurnReasoningAndActions(container, st, reasoningState.request_id);
            if (!st.pendingBotEl) {
                st.pendingBotEl = document.createElement('div');
                st.pendingBotEl.className = 'agent-message';
                st.pendingBotEl.dataset.id = reasoningState.request_id || '';
                this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
                st.botAnswered = true;
                st.streamingActive = true;
            } else {
                this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
            }
            const body = this._ensureReasoningWrap(chatId);
            if (body) {
                const existingTexts = new Set(
                    [...body.querySelectorAll('.reasoning-step')].map(el => el.textContent.trim())
                );
                steps.forEach(rawStep => {
                    let html = this._parseMarkdown(rawStep);
                    if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);
                    const step = document.createElement('div');
                    step.className = 'reasoning-step';
                    step.setAttribute('data-cds', 'Prose');
                    step.setAttribute('data-prose-font', 'sans');
                    step.setAttribute('data-prose-size', 'inherit');
                    step.innerHTML = html;
                    const t = step.textContent.trim();
                    if (t && !existingTexts.has(t)) {
                        existingTexts.add(t);
                        body.appendChild(step);
                    }
                });
            }
            if (reasoningState.is_final) {
                this._stopReasoningTimer(chatId, durSec);
            } else {
                const latestStep = liveText || (steps.length ? steps[steps.length - 1] : '');
                if (latestStep) {
                    this._setReasoningHeaderLabel(chatId, latestStep, false);
                }
                this._startReasoningTimer(chatId);
            }
        } else if (steps.length) {
            const userWrappers = container.querySelectorAll('.user-message-wrapper');
            const lastUser = userWrappers.length ? userWrappers[userWrappers.length - 1] : null;
            let targetBotEl = null;
            if (lastUser) {
                let cur = lastUser.nextElementSibling;
                while (cur) {
                    if (cur.classList?.contains('agent-message')) {
                        targetBotEl = cur;
                        break;
                    }
                    cur = cur.nextElementSibling;
                }
            }
            if (targetBotEl && !targetBotEl.querySelector('.reasoning-block')) {
                const wrap = document.createElement('div');
                wrap.className = 'reasoning-block reasoning-collapsed';
                wrap.setAttribute('data-cds', 'TurnStatus');
                wrap.setAttribute('data-state', 'done');
                wrap.innerHTML = `
                    <div class="reasoning-header" data-cds-row="">
                        <span class="reasoning-label">Размышления</span>
                        <span class="reasoning-timer" data-reasoning-duration="">${this._formatReasoningDuration(durSec)}</span>
                        ${this._reasoningCaretSVG()}
                    </div>
                    <div class="reasoning-body" data-cds-row-panel=""></div>
                `;
                wrap.querySelector('.reasoning-header')?.addEventListener('click', () => {
                    wrap.classList.toggle('reasoning-collapsed');
                });
                const body = wrap.querySelector('.reasoning-body');
                steps.forEach(rawStep => {
                    let html = this._parseMarkdown(rawStep);
                    if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);
                    const step = document.createElement('div');
                    step.className = 'reasoning-step';
                    step.setAttribute('data-cds', 'Prose');
                    step.setAttribute('data-prose-font', 'sans');
                    step.setAttribute('data-prose-size', 'inherit');
                    step.innerHTML = html;
                    body.appendChild(step);
                });
                targetBotEl.insertBefore(wrap, targetBotEl.firstElementChild);
            }
        }
    }

    _ensureReasoningWrap(chatId) {
        const st = this._resolveStream(chatId);
        if (!st) return null;
        const container = this._isActiveChat(chatId) ? this.getMessagesContainer() : null;
        if (container) {
            this._dedupeTurnReasoningAndActions(container, st);
        }
        if (!st.reasoningWrapEl && st.pendingBotEl) {
            const existingWrap = st.pendingBotEl.querySelector('.reasoning-block');
            if (existingWrap) st.reasoningWrapEl = existingWrap;
        }
        if (st.reasoningWrapEl && st.pendingBotEl?.contains(st.reasoningWrapEl)) {
            const body = st.reasoningWrapEl.querySelector('.reasoning-body');
            this._startReasoningTimer(chatId);
            return body;
        }
        const wrap = document.createElement('div');
        wrap.className = 'reasoning-block reasoning-collapsed';
        wrap.setAttribute('data-cds', 'TurnStatus');
        wrap.innerHTML = `
            <div class="reasoning-header" data-cds-row="">
                <span class="reasoning-label">Размышления</span>
                <span class="reasoning-timer" data-reasoning-duration="">${this._formatReasoningDuration(st.reasoningDurationSec || 1)}</span>
                ${this._reasoningCaretSVG()}
            </div>
            <div class="reasoning-body" data-cds-row-panel=""></div>
        `;
        const header = wrap.querySelector('.reasoning-header');
        header?.addEventListener('click', () => {
            wrap.classList.toggle('reasoning-collapsed');
        });
        if (st.pendingBotEl) {
            st.pendingBotEl.querySelectorAll(':scope > .reasoning-live').forEach(r => r.remove());
        st.pendingBotEl.querySelectorAll(':scope > .message-actions').forEach(a => a.remove());
            if (st.liveBlockEl && st.pendingBotEl.contains(st.liveBlockEl)) {
                st.pendingBotEl.insertBefore(wrap, st.liveBlockEl);
            } else {
                st.pendingBotEl.insertBefore(wrap, st.pendingBotEl.firstElementChild);
            }
            if (container) {
                this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
            }
        }
        st.reasoningWrapEl = wrap;
        this._startReasoningTimer(chatId);
        return wrap.querySelector('.reasoning-body');
    }

    /* Текущий живой блок был размышлением перед тулзой. Дописываем его текст
       в ЕДИНЫЙ свёрток «Размышления» и убираем живой блок. Мысли из разных
       шагов копятся в одном свёртке как параграфы, а не отдельными блоками. */
    _commitReasoningBlock(chatId) {
        const st = this._resolveStream(chatId);
        if (!st.liveBlockEl) return;

        // Дренаж буфера: забираем всю накопленную мысль сразу
        st.streamShown = (st.streamShown || '') + (st.streamBuffer || '');
        st.streamBuffer = '';
        if (st.streamRaf) { cancelAnimationFrame(st.streamRaf); st.streamRaf = null; }

        const text = st.streamShown;
        const liveEl = st.liveBlockEl;
        // Отвязываем ссылки СРАЗУ, чтобы новые чанки не сыпались в лопающийся блок.
        // Сам DOM-узел (liveEl) убираем чуть позже — после анимации «лопания».
        st.liveBlockEl = null;
        st.streamShown = '';
        st.streamDone  = false;

        // Пустую мысль не сохраняем — живой блок просто убираем без пузыря.
        if (!text.trim()) {
            liveEl.remove();
            return;
        }

        let html = this._parseMarkdown(text);
        if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
            ADD_TAGS: ['table','thead','tbody','tr','th','td'],
            ADD_ATTR: ['colspan','rowspan','align']
        });

        liveEl.remove();

        const body = this._ensureReasoningWrap(chatId);
        const step = document.createElement('div');
        step.className = 'reasoning-step reasoning-step--pop';
        step.setAttribute('data-cds', 'Prose');
        step.setAttribute('data-prose-font', 'sans');
        step.setAttribute('data-prose-size', 'inherit');
        step.innerHTML = html;
        const stepPlain = step.textContent.trim();
        const alreadyExists = [...body.querySelectorAll('.reasoning-step')].some(
            s => s.textContent.trim() === stepPlain
        );
        if (!alreadyExists) {
            body.appendChild(step);
        }
        if (stepPlain && !st.streamIsFinal && !st.streamDone) {
            this._setReasoningHeaderLabel(chatId, text, false);
        }

        const container = this._isActiveChat(chatId) ? this.getMessagesContainer() : null;
        const nearBottom = () => {
            if (!container) return false;
            return (container.scrollHeight - container.scrollTop - container.clientHeight) < 120;
        };
        if (nearBottom() && container) {
            container.scrollTop = container.scrollHeight;
        }
    }

    /* Живой блок оказался финальным ответом — повышаем его стиль
       с приглушённого reasoning-live до обычного (основной шрифт). */
    _promoteLiveToFinal(chatId) {
        const st = this._resolveStream(chatId);
        this._stopReasoningTimer(chatId);
        st.streamIsFinal = true;
        if (st.liveBlockEl) {
            st.liveBlockEl.className = 'live-block';
            st.liveBlockEl.style.display = '';
            // Форсим немедленный ре-рендер уже видимого текста, чтобы новый
            // (обычный) стиль применился сразу, а не «дожидался» финализации.
            // Иначе финал визуально досыпается в приглушённом reasoning-стиле.
            this._renderStreamPartial(chatId);
        }
    }

    /* Жёсткий сброс всего стрим-состояния БЕЗ финализации DOM.
       Вызывается перед отправкой нового сообщения: если ссылки на элементы
       прошлого ответа ещё живы (finalizeStream не отработал), отвязываем их —
       чтобы чанки нового ответа не дописывались в предыдущий пузырь бота.
       DOM прошлого ответа при этом не трогаем (он уже показан пользователю),
       только «дорисовываем» его в обычный стиль на случай залипшего reasoning. */
    _hardResetStreamState(chatId) {
        // Сбрасываем ТОЛЬКО указанный/текущий чат. Фоновые стримящие чаты
        // (свой this._streams[chatId] + открытый SSE) сброс не затрагивает.
        const st = this._resolveStream(chatId);
        if (st.streamRaf) {
            cancelAnimationFrame(st.streamRaf);
            st.streamRaf = null;
        }
        if (st.reasoningTimer) {
            clearInterval(st.reasoningTimer);
            st.reasoningTimer = null;
        }
        // Если прошлый живой блок так и остался в reasoning-стиле — нормализуем,
        // чтобы предыдущий ответ не выглядел как незавершённое размышление.
        if (st.liveBlockEl && st.liveBlockEl.classList.contains('reasoning-live')) {
            st.liveBlockEl.className = 'live-block';
        }
        st.pendingBotEl    = null;
        st.liveBlockEl     = null;
        st.reasoningWrapEl = null;
        st.pendingBotText  = '';
        st.streamBuffer    = '';
        st.streamShown     = '';
        st.streamDone      = false;
        st.streamIsFinal   = false;
        st.streamFinalData = null;
        st.streamingActive = false;
    }

    _finalizeStream(chatId) {
        const st = this._resolveStream(chatId);
        const isActive = this._isActiveChat(chatId);
        if (!st.pendingBotEl) return;

        // Страховка: если генерация картинки завершилась (успех или ошибка),
        // а превью так и не пришло — снимаем висящий скелетон на финале.
        // Скелетон живёт в DOM активного чата — чистим только для активного.
        if (isActive) this._removeImageGenSkeleton();

        const data = st.streamFinalData || {};
        const rawFullText = data.fullText ?? st.streamShown ?? '';
        const fullText = (rawFullText && String(rawFullText).trim())
            ? rawFullText
            : (data.files?.length ? '' : 'Агент завершил шаг без текстового ответа — попробуйте повторить или уточнить запрос.');

        // Финальный полный рендер финального ответа — в живой блок (если он есть).
        // reasoning-блоки остаются выше нетронутыми.
        const targetBlock = st.liveBlockEl || (() => {
            const b = document.createElement('div');
            b.className = 'live-block';
            st.pendingBotEl.appendChild(b);
            return b;
        })();
        // Гарантия: финальный ответ всегда в обычном стиле, даже если маркер
        // final_commit не пришёл (например обрыв SSE) — снимаем reasoning-стиль.
        targetBlock.className = 'live-block';
        if (data.message_id) st.pendingBotEl.dataset.messageId = data.message_id;

        this._renderBotStructuredContent(targetBlock, fullText, {
            botMessageId: data.message_id || st.pendingBotEl?.dataset?.messageId || st.pendingBotEl?.dataset?.id || '',
            pollAnswers: data.poll_answers || [],
        });

        // Файлы / actions вешаем на общую обёртку ответа
        if (data.files?.length) {
            st.pendingBotEl.appendChild(
                this._renderBotFiles(data.files, data.message_id)
            );
        }
        if (data.message_id) {
            st.pendingBotEl.dataset.id = data.message_id;
        }
        this._stopReasoningTimer(chatId);
        const activeContainer = isActive ? this.getMessagesContainer() : null;
        if (activeContainer) {
            this._ensureBotAfterLastUserMessage(activeContainer, st.pendingBotEl);
            this._dedupeTurnReasoningAndActions(activeContainer, st);
        }
        this._applyCodeHeaders(st.pendingBotEl);
        st.pendingBotEl.querySelectorAll(':scope > .reasoning-live').forEach(r => r.remove());
        st.pendingBotEl.querySelectorAll(':scope > .message-actions').forEach(a => a.remove());
        const actions = this.createMessageActions(st.pendingBotEl.dataset.id, fullText, true, {
            messageId: data.message_id || st.pendingBotEl.dataset.messageId || st.pendingBotEl.dataset.id || '',
            dbId: data.id || '',
            reaction: data.reaction ?? (typeof data.reactions === 'string' ? data.reactions : null),
            createdAt: data.created_at || new Date().toISOString(),
        });
        st.pendingBotEl.appendChild(actions);
        st.pendingBotEl.style.transition = '';
        st.pendingBotEl.style.opacity    = '';

        // Сброс всего стрим-состояния этого чата
        st.pendingBotEl    = null;
        st.liveBlockEl     = null;
        st.reasoningWrapEl = null;   // критично: иначе следующий ответ приклеится к старому свёртку
        st.pendingBotText  = '';
        st.streamBuffer    = '';
        st.streamShown     = '';
        st.streamDone      = false;
        st.streamIsFinal   = false;
        st.streamFinalData = null;
        st.streamingActive = false;
        if (st.streamRaf) { cancelAnimationFrame(st.streamRaf); st.streamRaf = null; }

        // Фоновый SSE больше не нужен — стрим этого чата завершён, закрываем
        // его источник (иначе повиснет открытым до следующего openChat).
        if (!isActive) {
            const src = this._sources[String(chatId)];
            if (src) { try { src.close(); } catch (e) {} delete this._sources[String(chatId)]; }
        }

        // UI-эффекты (волна, placeholder, стоп-кнопка) — только для активного
        // чата: у фонового placeholder/индикатора в DOM нет.
        st.botAnswered = true;
        if (this._chatStatuses) delete this._chatStatuses[String(chatId)];
        if (isActive) {
            this._flattenWaveIndicator();
            setTimeout(() => this._removeBotPlaceholder(), 500);
            this._clearAllStatuses();
            this._onRunFinished();
        }
    }

    _setSendButtonMode(mode = 'send') {
        const container = this.getInputContainer();
        const sendBtn = container?.querySelector('#send-btn');
        if (!sendBtn) return;
        if (mode === 'stop') {
            sendBtn.disabled = false;
            sendBtn.classList.add('active', 'stop-mode');
            sendBtn.setAttribute('aria-label', 'Остановить');
            sendBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
            return;
        }
        sendBtn.classList.remove('stop-mode');
        sendBtn.setAttribute('aria-label', 'Отправить');
        sendBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        const canSend = this.getInputText().length > 0 || this.hasAttachedFiles();
        sendBtn.disabled = !canSend;
        sendBtn.classList.toggle('active', canSend);
    }

    hasAttachedFiles() {
        try {
            const files = window.FileAttach?.getFiles?.() || [];
            return Array.isArray(files) && files.length > 0;
        } catch (_) {
            return false;
        }
    }

    _syncSendButtonState() {
        const container = this.getInputContainer();
        const sendBtn = container?.querySelector('#send-btn');
        if (!sendBtn) return;
        if (this._runState !== 'idle') {
            sendBtn.disabled = false;
            sendBtn.classList.add('active', 'stop-mode');
            return;
        }
        sendBtn.classList.remove('stop-mode');
        const canSend = this.getInputText().length > 0 || this.hasAttachedFiles();
        sendBtn.classList.toggle('active', canSend);
        sendBtn.disabled = !canSend;
    }

    async stopCurrentRun() {
        if (this._runState !== 'running') return;
        this._runState = 'stopping';
        try {
            if (this._activeRequestId) {
                await fetch('/api/chats/stop_message', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    credentials: 'include',
                    body: JSON.stringify({ request_id: this._activeRequestId })
                });
            }
        } catch (e) {
            console.warn('stopCurrentRun error:', e);
        }
    }

    _onRunFinished() {
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }
        if (this._statusSse) {
            this._statusSse.close();
            this._statusSse = null;
        }
        this._runState = 'idle';
        this._activeRequestId = null;
        this._setSendButtonMode('send');
    }

    // Финал ответа готов: перечитываем историю чата и завершаем run.
    //
    // ВАЖНО про конфликт с живым стримом: пока шёл Redis-стрим, в DOM уже
    // нарисованы _pendingBotEl (обёртка бота), свёрток «Размышления» и живой
    // финальный блок. loadMessages() делает container.innerHTML = '' и
    // перерисовывает ленту из БД — то есть живой блок будет снесён и заменён
    // финальной версией из chats. Дубля не будет (innerHTML полностью
    // очищается), но если RAF-цикл ещё не дорисовал _streamBuffer или
    // _finalizeStream не отработал — висячие ссылки (_pendingBotEl,
    // _streamRaf, _liveBlockEl) останутся живыми и СЛЕДУЮЩИЙ ответ приклеится
    // к мёртвому пузырю. Поэтому перед перечиткой жёстко гасим стрим-состояние
    // (RAF + все ссылки), не финализируя DOM: сам DOM всё равно будет заменён
    // историей из БД.
    // Достаёт auto_title из финального SSE-события 'done' и обновляет строку
    // чата в сайдбаре немедленно, без перезапроса списка. auto_title кладётся
    // в message бота только на первом обмене (см. chats_service consumer).
    _applyAutoTitleFromDone(ev, chatId) {
        try {
            if (!ev || !ev.data) return;
            const payload = JSON.parse(ev.data);
            const msg = payload && payload.message;
            const auto = (msg && msg.auto_title || '').trim();
            const cid = chatId != null ? String(chatId) : null;
            if (!auto || !cid) return;
            let touched = false;
            this.chats = (this.chats || []).map(c => {
                if (String(c.id) === cid && !(c.renamed_title || '').trim()) {
                    touched = true;
                    return { ...c, auto_title: auto };
                }
                return c;
            });
            if (touched) this.renderChats(this.chats);
        } catch (_) { /* fail-open: заголовок не критичен */ }
    }

    async _handleRunDone(chatId) {
        const key = String(chatId);
        const isActive = this._isActiveChat(key);
        const st = this._getStream(key);
        this._stopReasoningTimer(key);
        const savedReasoningWrap = st?.reasoningWrapEl || st?.pendingBotEl?.querySelector('.reasoning-block') || null;
        this._hardResetStreamState(key);
        const src = this._sources[key];
        if (src) { try { src.close(); } catch (_) {} delete this._sources[key]; }
        if (this._chatStatuses) delete this._chatStatuses[key];

        if (isActive) {
            this._botAnswered = true;
            this._flattenWaveIndicator();
            this._removeBotPlaceholder();
            this._clearAllStatuses();
            await this.loadMessages(chatId);
            if (savedReasoningWrap) {
                const container = this.getMessagesContainer();
                const userWrappers = container?.querySelectorAll('.user-message-wrapper') || [];
                const lastUser = userWrappers.length ? userWrappers[userWrappers.length - 1] : null;
                let lastBot = null;
                if (lastUser) {
                    let cur = lastUser.nextElementSibling;
                    while (cur) {
                        if (cur.classList?.contains('agent-message')) { lastBot = cur; break; }
                        cur = cur.nextElementSibling;
                    }
                }
                if (lastBot && !lastBot.querySelector('.reasoning-block')) {
                    lastBot.insertBefore(savedReasoningWrap, lastBot.firstElementChild);
                }
            }
            this._onRunFinished();
        }
    }

    // Замена polling'а: держим один SSE (EventSource) и ждём push-события
    // done/idle. Если браузер/сеть не дали открыть SSE — падаем на старый
    // polling GET /api/chats/{id}/status (эндпоинт оставлен как фолбэк).
    _startStatusPolling(chatId) {
        if (!chatId) return;
        const key = String(chatId);
        // Стрим этого чата пишем строго в его собственный объект — иначе при
        // уходе из чата прокси this._* переключатся на новый currentChat и
        // фоновый SSE начнёт бить не по тому стриму.
        const st = this._ensureStream(key);
        // подчистить прошлые подписки ЭТОГО чата
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }
        const prevSrc = this._sources[key];
        if (prevSrc) {
            try { prevSrc.close(); } catch (_) {}
            delete this._sources[key];
        }

        if (typeof EventSource === 'undefined') {
            this._startStatusPollingFallback(chatId);
            return;
        }

        let es;
        try {
            es = new EventSource(`/api/chats/${chatId}/stream`, { withCredentials: true });
        } catch (_) {
            this._startStatusPollingFallback(chatId);
            return;
        }
        this._sources[key] = es;

        // ── Живой токен-стрим (Redis pub/sub → SSE) ───────────────────────
        // Агент публикует фреймы в stream:{request_id}; sigma-гейтвей релеит
        // их как SSE-события того же kind. Логика 1:1 с main-гейтвеем (WS),
        // только транспорт другой (EventSource вместо WebSocket).
        //
        //   live          — фрагмент текста: дописываем в _streamBuffer,
        //                   отрисовкой занимается RAF-цикл (_startStreamLoop).
        //                   Печатается СРАЗУ в приглушённом reasoning-стиле,
        //                   пока не пришёл final_commit.
        //   reasoning_end — живой блок был размышлением перед тулзой: сворачиваем
        //                   его в единый свёрток «Размышления» (chunk пустой).
        //   final_commit  — живой блок оказался финальным ответом: повышаем
        //                   стиль до основного (chunk пустой). Финализация
        //                   (файлы, actions) придёт с 'done' из Kafka.
        const onLive = (ev) => {
            let frame;
            try { frame = JSON.parse(ev.data); } catch (_) { return; }
            if (frame.active_requests !== undefined) {
                this._updateActiveRequestsUI(key, frame.active_requests);
            }
            const chunk = frame.chunk;
            if (chunk === undefined || chunk === null || chunk === '') return;

            const isActive = this._isActiveChat(key);
            // Активный чат рисует в ленту; фоновый — в откреплённый узел
            // (pendingBotEl не добавляется в DOM, живёт в st до возврата).
            const container = isActive ? this.getMessagesContainer() : null;

            if (!st.runStartedAt) st.runStartedAt = Date.now();
            if (isActive && container) {
                this._dedupeTurnReasoningAndActions(container, st, frame.id);
            }
            if (!st.liveBlockEl) {
                if (!st.pendingBotEl) {
                    st.pendingBotEl = document.createElement('div');
                    st.pendingBotEl.className = 'agent-message';
                    st.pendingBotEl.dataset.id = frame.id || '';
                    if (isActive && container && this._loadingMessagesChat !== key) {
                        st.pendingBotEl.style.opacity = '0';
                        st.pendingBotEl.style.transition = 'opacity 0.25s ease';
                        this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
                        requestAnimationFrame(() => requestAnimationFrame(() => {
                            if (st.pendingBotEl) st.pendingBotEl.style.opacity = '1';
                        }));
                        this._clearAllStatuses();
                    }
                    st.botAnswered = true;
                    st.streamingActive = true;
                    st.streamIsFinal   = false;
                } else if (isActive && container && this._loadingMessagesChat !== key) {
                    st.pendingBotEl.querySelectorAll(':scope > .reasoning-live').forEach(r => r.remove());
        st.pendingBotEl.querySelectorAll(':scope > .message-actions').forEach(a => a.remove());
                    this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
                }
                // Живой блок печатается в reasoning-стиле, пока не пришёл
                // final_commit. Если финал уже объявлен — сразу .live-block.
                if (!st.streamIsFinal) {
                    this._ensureReasoningWrap(key);
                }
                st.liveBlockEl = document.createElement('div');
                st.liveBlockEl.className = st.streamIsFinal ? 'live-block' : 'reasoning-live';
                if (!st.streamIsFinal) {
                    st.liveBlockEl.style.display = 'none';
                }
                st.pendingBotEl.appendChild(st.liveBlockEl);
                st.streamBuffer = '';
                st.streamShown  = '';
                st.streamDone   = false;
                this._startStreamLoop(key);
            }

            st.streamBuffer += chunk;
            if (!st.streamIsFinal) {
                this._setReasoningHeaderLabel(key, (st.streamShown || '') + (st.streamBuffer || ''), false);
            }
        };

        es.addEventListener('live', onLive);
        es.addEventListener('reasoning_end', () => {
            // Текущий живой блок был размышлением перед вызовом тулзы —
            // сливаем его в общий свёрток «Размышления».
            this._commitReasoningBlock(key);
        });
        es.addEventListener('final_commit', () => {
            // Живой блок оказался финальным ответом — повышаем его стиль.
            // Реальная финализация (файлы, actions) придёт с 'done' из Kafka.
            this._promoteLiveToFinal(key);
            st.streamIsFinal = true;
        });

        const onDone = async (ev) => {
            es.close();
            if (this._sources[key] === es) delete this._sources[key];
            // Автогенерированный заголовок чата приходит в message финала —
            // обновляем строку чата в сайдбаре в реалтайме (на первом обмене).
            this._applyAutoTitleFromDone(ev, chatId);
            await this._handleRunDone(chatId);
        };

        es.addEventListener('done', onDone);
        es.addEventListener('idle', onDone);
        es.addEventListener('timeout', () => {
            // Сервер закрыл стрим по потолку жизни. Если ран ещё идёт —
            // EventSource сам переподключится; страхуемся ручным reopen.
            if (st.streamingActive && this._sources[key] === es) {
                es.close();
                delete this._sources[key];
                this._startStatusPolling(chatId);
            }
        });
        // 'running' игнорируем — индикатор уже показан оптимистично.

        es.onerror = async () => {
            if (window.__authSessionExpired) {
                try { es.close(); } catch (_) {}
                if (this._sources[key] === es) delete this._sources[key];
                return;
            }
            try {
                const probe = await fetch('/api/presence/count', {
                    credentials: 'same-origin',
                    cache: 'no-store',
                    redirect: 'manual'
                });
                if (window.__isAuthRedirectOrUnauthorized?.(probe)) {
                    try { es.close(); } catch (_) {}
                    if (this._sources[key] === es) delete this._sources[key];
                    window.__onAuthSessionExpired?.();
                    return;
                }
            } catch (_) {}
            if (es.readyState === EventSource.CLOSED && st.streamingActive) {
                if (this._sources[key] === es) delete this._sources[key];
                if (this._isActiveChat(key)) this._startStatusPollingFallback(chatId);
            }
        };
    }

    // Старый short-polling — фолбэк на случай недоступности SSE.
    _startStatusPollingFallback(chatId) {
        if (!chatId || window.__authSessionExpired) return;
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }

        const POLL_MS = 1500;
        let failCount = 0;
        this._statusPollTimer = setInterval(async () => {
            if (this._runState === 'idle' || window.__authSessionExpired || document.visibilityState === 'hidden') {
                if (this._runState === 'idle' || window.__authSessionExpired) {
                    clearInterval(this._statusPollTimer);
                    this._statusPollTimer = null;
                }
                return;
            }

            try {
                const resp = await fetch(`/api/chats/${chatId}/status`, {
                    method: 'GET',
                    credentials: 'include',
                    cache: 'no-store',
                    redirect: 'manual'
                });
                if (window.__isAuthRedirectOrUnauthorized?.(resp)) {
                    clearInterval(this._statusPollTimer);
                    this._statusPollTimer = null;
                    window.__onAuthSessionExpired?.();
                    return;
                }
                if (!resp.ok) {
                    if (++failCount >= 6) {
                        clearInterval(this._statusPollTimer);
                        this._statusPollTimer = null;
                    }
                    return;
                }
                failCount = 0;

                const state = await resp.json();
                if (state?.status === 'done' || state?.status === 'idle') {
                    // Сообщение уже сохранено в БД chats-consumer'ом —
                    // перечитываем историю чата и завершаем run.
                    await this._handleRunDone(chatId);
                }
            } catch (_) {
                // Молчаливо ждём следующий тик polling
            }
        }, POLL_MS);
    }

    updateStatus(statusText) {
        // Пока идёт живой стриминг финального ответа — статусы тулов больше
        // не показываем и не воскрешаем. Асинхронные статусы, прилетевшие
        // после первого чанка, игнорируем полностью.
        if (this._streamingActive) return;

        const container = this.getMessagesContainer();
        if (!container) return;

        // Определяем: субагент [name] или PMыч
        const subMatch = statusText.match(/^\[([^\]]+)\]\s*(.*)/);
        if (subMatch) {
            this._updateSubagentStatus(subMatch[1], subMatch[2], container);
        } else {
            this._updateMainStatus(statusText, container);
        }
    }

     _updateMainStatus(statusText, container) {
        container = container || this.getMessagesContainer();
        if (!container) return;

        let el = document.getElementById('agent-status-indicator');
        if (!el) {
            el = document.createElement('div');
            el.id = 'agent-status-indicator';
            el.className = 'agent-status agent-status--main';
            container.appendChild(el);
        }
        el.innerHTML = `
            <span class="status-spinner"></span>
            <span class="status-text">${this.escapeHtml(statusText)}</span>
        `;
    }

    _updateSubagentStatus(agentName, text, container) {
        container = container || this.getMessagesContainer();
        if (!container) return;

        if (!this.subagentStates) this.subagentStates = {};

        // Общий wrap для всех субагентов — вставляем перед статусом PMыча
        let wrap = document.getElementById('subagent-status-wrap');
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.id = 'subagent-status-wrap';
            wrap.className = 'subagent-status-wrap';
            const mainStatus = document.getElementById('agent-status-indicator');
            if (mainStatus) {
                container.insertBefore(wrap, mainStatus);
            } else {
                container.appendChild(wrap);
            }
        }

        const blockId = `subagent-status-${agentName}`;
        let block = document.getElementById(blockId);
        if (!block) {
            block = document.createElement('details');
            block.id = blockId;
            block.className = 'subagent-status-block';
            block.open = true;

            const summary = document.createElement('summary');
            summary.className = 'subagent-status-summary';
            summary.innerHTML = `
                <span class="subagent-summary-arrow">▶</span>
                <span class="subagent-badge">${this.escapeHtml(agentName)}</span>
                <span class="subagent-status-text" id="${blockId}-text"></span>
            `;
            const log = document.createElement('div');
            log.className = 'subagent-log';
            log.id = `${blockId}-log`;
            block.appendChild(summary);
            block.appendChild(log);
            wrap.appendChild(block);

            this.subagentStates[agentName] = { steps: [], done: false };
        }

        // Обновляем текст в summary
        const textEl = document.getElementById(`${blockId}-text`);
        if (textEl) textEl.textContent = text;

        // Добавляем шаг в лог
        const logEl = document.getElementById(`${blockId}-log`);
        if (logEl) {
            const step = document.createElement('div');
            step.className = 'subagent-log-step';
            step.textContent = text;
            logEl.appendChild(step);
        }

        // Сохраняем состояние
        if (this.subagentStates[agentName]) {
            this.subagentStates[agentName].steps.push(text);
            if (text.startsWith('✅')) {
                this.subagentStates[agentName].done = true;
                block.classList.add('subagent-status-block--done');
                // Сворачиваем завершённый блок через небольшую паузу
                setTimeout(() => { block.open = false; }, 1200);
            }
        }

        container.scrollTop = container.scrollHeight;
    }

    _restoreSubagentStatuses(states) {
        const container = this.getMessagesContainer();
        if (!container) return;

        // Убираем старый wrap если есть
        document.getElementById('subagent-status-wrap')?.remove();

        const wrap = document.createElement('div');
        wrap.id = 'subagent-status-wrap';
        wrap.className = 'subagent-status-wrap subagent-status-wrap--restored';

        for (const [agentName, state] of Object.entries(states)) {
            if (!state.steps.length) continue;
            const block = document.createElement('details');
            block.id = `subagent-status-${agentName}`;
            block.className = 'subagent-status-block' + (state.done ? ' subagent-status-block--done' : '');
            block.open = false; // восстановленные — свёрнуты

            const lastStep = state.steps[state.steps.length - 1] || '';
            block.innerHTML = `
                <summary class="subagent-status-summary">
                    <span class="subagent-summary-arrow">▶</span>
                    <span class="subagent-badge">${this.escapeHtml(agentName)}</span>
                    <span class="subagent-status-text">${this.escapeHtml(lastStep)}</span>
                </summary>
                <div class="subagent-log">
                    ${state.steps.map(s => `<div class="subagent-log-step">${this.escapeHtml(s)}</div>`).join('')}
                </div>
            `;
            wrap.appendChild(block);
        }

        if (wrap.children.length) container.appendChild(wrap);
    }

    _clearAllStatuses() {
        document.getElementById('agent-status-indicator')?.remove();
        document.getElementById('subagent-status-wrap')?.remove();
        this.subagentStates = {};
    }

    /* ══════════════════════════════════════════
       RENDER INPUT
    ══════════════════════════════════════════ */
    async renderMessageInput() {
        const container = this.getInputContainer();
        if (!container) return;

        // Гард: не перерисовывать если экран уже соответствует состоянию
        const hasStart     = !!container.querySelector('.chat-start-screen');
        const hasActive    = !!container.querySelector('.chat-input-area');
        const renderedChat = container.dataset.renderedChat;
        const chatMain = document.querySelector('.chat-main');

        // Синхронизация режимов перед гард-выходами
        if (!this.currentChat) {
            container.classList.add('start-screen-host');
            chatMain?.classList.add('start-screen-mode');
        } else {
            container.classList.remove('start-screen-host');
            chatMain?.classList.remove('start-screen-mode');
        }

        // Стартовый экран уже есть и чат не выбран — пропускаем
        if (!this.currentChat && hasStart) return;
        // Инпут активного чата уже есть И это тот же чат — пропускаем
        if (this.currentChat && hasActive && renderedChat === String(this.currentChat)) return;
        // Если чат выбран, но рендерился стартовый — рендерим инпут (смена состояния)

        container.classList.add('rending');

        const profileName = document.querySelector('.profile-name')?.textContent?.trim();
        // name может прийти как литерал "Unknown" (когда бэк не получил
        // заголовок X-Authentik-Name) — такое значение считаем невалидным,
        // чтобы сработал фолбэк на username / profile-name из сайдбара.
        const rawName = this.currentUser?.name?.trim();
        const firstName = (rawName && rawName !== 'Unknown')
            ? rawName.split(' ')[0]
            : '';
        const username = firstName
                      || this.currentUser?.username
                      || profileName
                      || 'друг';

        if (!this.currentChat) {
            // ── СТАРТОВЫЙ ЭКРАН ──────────────────────
            container.classList.add('start-screen-host');
            document.querySelector('.chat-main')?.classList.add('start-screen-mode');
            container.innerHTML = `
<div class="chat-start-screen">

    <div class="start-bg" aria-hidden="true">
        <canvas id="start-graph"></canvas>
    </div>

    <div class="start-body">
        <div class="start-header">
            <p class="start-greeting">Привет, <span class="username">${this.escapeHtml(username)}</span> 👋</p>
            <h1 class="start-title">Чем могу помочь?</h1>
        </div>

        <div class="start-input-area">
            <div class="start-input-box" id="start-input-box">
                <div
                    class="start-input-field"
                    id="message-input"
                    contenteditable="true"
                    data-placeholder="Спросите что-нибудь..."
                    role="textbox"
                    aria-multiline="true"
                    aria-label="Поле ввода"
                ></div>
                <div class="start-input-footer">
                    <button class="start-send-btn" id="send-btn" disabled aria-label="Отправить">
                        <svg viewBox="0 0 24 24" fill="none">
                            <path d="M5 12h14M13 6l6 6-6 6"
                                  stroke="currentColor" stroke-width="2"
                                  stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    </div>

    <p class="start-footer-note">Агент может ошибаться — перепроверяйте важную информацию</p>

</div>`;
        } else {
            // ── АКТИВНЫЙ ЧАТ ─────────────────────────
            container.classList.remove('start-screen-host');
            document.querySelector('.chat-main')?.classList.remove('start-screen-mode');
            container.innerHTML = `
<div class="chat-input-area">
    <div class="chat-input-box" id="chat-input-box">

        <div
            class="chat-input-field"
            id="message-input"
            contenteditable="true"
            data-placeholder="Напишите сообщение..."
            role="textbox"
            aria-multiline="true"
            aria-label="Поле ввода"
        ></div>

        <div class="chat-input-footer">
            <button class="chat-send-btn" id="send-btn" disabled aria-label="Отправить">
                <svg viewBox="0 0 24 24" fill="none">
                    <path d="M5 12h14M13 6l6 6-6 6"
                          stroke="currentColor" stroke-width="2"
                          stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </button>
        </div>

    </div>
    <span class="input-footer-note">Агент может ошибаться — перепроверяйте важную информацию</span>
</div>`;
        }

        this.setupUI();
        this.attachInputHandlers();
        this.attachHintActions();
        this.attachProfileHandlers();

        // Инжектируем кнопку тулов и пикер модели в футер инпута
        window.ToolsPanel?.inject(container);
        window.ModelPicker?.inject(container);

        // Посимвольная анимация start-header
        // Запоминаем для какого чата отрендерили
        container.dataset.renderedChat = this.currentChat ? String(this.currentChat) : '';

        if (!this.currentChat) {
            this._animateStartHeader(container);
            this._initStartGraph(container);
        } else {
            this._syncScrollArrow();
        }

        setTimeout(() => container.classList.remove('rending'), 0);
    }

    /* ══════════════════════════════════════════
       TYPEWRITER: start-header
    ══════════════════════════════════════════ */
    /* ══════════════════════════════════════════
       TYPEWRITER: agent-message (пословно)
    ══════════════════════════════════════════ */
    async _streamRenderMessage(el, rawText, container, files, messageId) {
        let accumulated = '';
        const tokens = rawText.split(/(\s+)/);
        const BATCH = 2;
        const DELAY = 22;

        let userScrolled = false;
        const onScroll = () => {
            const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
            userScrolled = distFromBottom > 60;
        };
        container.addEventListener('scroll', onScroll, { passive: true });

        for (let i = 0; i < tokens.length; i += BATCH) {
            accumulated += tokens.slice(i, i + BATCH).join('');

            let html = this._parseMarkdown(accumulated);
            if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);

            const tmp = document.createElement('div');
            tmp.innerHTML = html;
            tmp.querySelectorAll('a').forEach(a => {
                a.setAttribute('target', '_blank');
                a.setAttribute('rel', 'noopener noreferrer');
            });

            el.innerHTML = tmp.innerHTML;
            el.querySelectorAll('pre code').forEach(b => {
                delete b.dataset.highlighted;
            });

            if (!userScrolled && container) {
                container.scrollTop = container.scrollHeight;
            }

            await this._sleep(DELAY);
        }

        container.removeEventListener('scroll', onScroll);

        this._renderBotStructuredContent(el, rawText, {
            botMessageId: messageId || el?.dataset?.messageId || el?.dataset?.id || '',
        });
        this._applyCodeHeaders(el);

        // 🎯 Файлы агента — добавляем ПОСЛЕ цикла, раз цикл больше не перезапишет innerHTML
        if (files?.length) {
            const existingFiles = el.querySelector('.fa-bot-files');
            if (!existingFiles) {
                el.appendChild(this._renderBotFiles(files, messageId));
            }
        }

        const existingActions = el.querySelector('.message-actions');
        if (!existingActions) {
            const msgId = el.dataset.id;
            const actions = this.createMessageActions(msgId, rawText, true, {
                messageId: messageId || el.dataset.messageId || msgId || '',
                dbId: msgId || '',
                createdAt: new Date().toISOString(),
            });
            el.appendChild(actions);
        }

        // Убираем placeholder и статусы через методы (с clearTimeout гарда)
        this._removeBotPlaceholder();
        this._clearAllStatuses();
        el.style.transition = '';
        el.style.opacity = '';
    }

    _applyCodeHeaders(el) {
        el.querySelectorAll('pre code').forEach(block => {
            if (block.dataset.highlighted) return;
            hljs.highlightElement(block);

            const pre = block.parentElement;
            if (!pre || pre.querySelector('.code-header')) return;

            const langClass = [...block.classList].find(c => c.startsWith('language-'));
            const lang = langClass ? langClass.replace('language-', '') : 'code';

            const copyIcon = `<svg viewBox="0 0 24 24" fill="none" width="14" height="14">
                <rect x="9" y="9" width="11" height="13" rx="2" stroke="currentColor" stroke-width="1.8"/>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"
                    stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
            </svg>`;
            const checkIcon = `<svg viewBox="0 0 24 24" fill="none" width="14" height="14">
                <path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2"
                    stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`;

            const copyCodeBtn = document.createElement('button');
            copyCodeBtn.className = 'code-copy-btn';
            copyCodeBtn.title = 'Копировать код';
            copyCodeBtn.innerHTML = copyIcon;
            copyCodeBtn.addEventListener('click', async () => {
                await navigator.clipboard.writeText(block.innerText);
                copyCodeBtn.innerHTML = checkIcon;
                copyCodeBtn.classList.add('copied');
                setTimeout(() => {
                    copyCodeBtn.innerHTML = copyIcon;
                    copyCodeBtn.classList.remove('copied');
                }, 1500);
            });

            const header = document.createElement('div');
            header.className = 'code-header';
            header.innerHTML = `<span class="code-lang">${lang}</span>`;
            header.appendChild(copyCodeBtn);

            pre.style.position = 'relative';
            pre.insertBefore(header, block);
        });
    }


    _sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
    /** WS отключен в sigma — ожидание не требуется */
    _waitForWS(timeout = 5000) {
        return Promise.resolve();
    }

    /* ══════════════════════════════════════════
       СТАРТОВЫЙ ЭКРАН: живой фон «за стеклом».
       Граф (canvas) на весь экран БЕЗ цветных пятен — цвет узлов жёстко
       завязан на --accent (3 тона от базового), меняется с палитрой.
       Параллакс: граф > наклон панели. reduced-motion / touch —
       деградируют мягко. Один rAF, курсор сглажен lerp-ом.
    ══════════════════════════════════════════ */
    _animateStartHeader(container) {
        window.ChatAnimations?.animateStartHeader?.(container);
    }

    _initStartGraph(container) {
        window.ChatAnimations?.initStartGraph?.(container);
    }

    /* ══════════════════════════════════════════
       INPUT HANDLERS
    ══════════════════════════════════════════ */
    // Получить текст из поля — поддержка и textarea и contenteditable
    getInputText() {
        const container = this.getInputContainer();
        const field = container?.querySelector('#message-input');
        if (!field) return '';
        if (field.tagName === 'TEXTAREA') return field.value.trim();
        return this._readInputWithChips(field).trim();
    }

    /* Собирает финальный текст из contenteditable, подставляя вместо
       чипов-плейсхолдеров (свёрнутый длинный текст) их полное содержимое.
       Чип хранит текст в this._pendingChips по data-chip-id. Так пользователь
       может печатать текст ДО и ПОСЛЕ чипа — порядок сохранится. */
    _readInputWithChips(field) {
        if (!this._pendingChips || this._pendingChips.size === 0) {
            return (field.innerText || field.textContent || '').replace(/\u00A0/g, ' ');
        }
        let out = '';
        const usedChipIds = new Set();
        const walk = (node) => {
            node.childNodes.forEach((n) => {
                if (n.nodeType === Node.TEXT_NODE) {
                    out += n.textContent.replace(/\u00A0/g, ' ');
                } else if (n.nodeType === Node.ELEMENT_NODE) {
                    const chipId = n.getAttribute && n.getAttribute('data-chip-id');
                    if (chipId && this._pendingChips.has(chipId)) {
                        usedChipIds.add(chipId);
                        out += this._pendingChips.get(chipId);
                    } else if (n.tagName === 'BR') {
                        out += '\n';
                    } else if (n.classList && n.classList.contains('fa-chip')) {
                        // пропускаем визуальную метку
                    } else {
                        const isBlock = n.tagName === 'DIV' || n.tagName === 'P';
                        if (isBlock && out.length > 0 && !out.endsWith('\n')) {
                            out += '\n';
                        }
                        walk(n);
                    }
                }
            });
        };
        walk(field);

        for (const [chipId, chipText] of this._pendingChips.entries()) {
            if (!usedChipIds.has(chipId) && chipText) {
                if (out.length > 0 && !out.endsWith('\n') && !/\s$/.test(out)) {
                    out += '\n';
                }
                out += chipText;
            }
        }
        return out;
    }

    setInputText(field, text) {
        if (field.tagName === 'TEXTAREA') {
            field.value = text;
            field.dispatchEvent(new Event('input'));
        } else {
            field.innerText = text;
            field.dispatchEvent(new Event('input'));
            // Ставим курсор в конец
            const range = document.createRange();
            const sel   = window.getSelection();
            range.selectNodeContents(field);
            range.collapse(false);
            sel.removeAllRanges();
            sel.addRange(range);
        }
    }

    _ensureInputPreviewBar(field) {
        const box = field?.closest?.('.chat-input-box, .start-input-box, .gc-input-wrapper')
                 || this.getInputContainer()?.querySelector('.chat-input-box, .start-input-box, .gc-input-wrapper');
        if (!box) return null;
        let bar = box.querySelector('.fa-preview-bar');
        if (!bar) {
            bar = document.createElement('div');
            bar.className = 'fa-preview-bar';
            box.insertBefore(bar, box.firstChild);
        }
        return bar;
    }

    _syncInputPreviewBar(barOrField) {
        const bar = (barOrField && barOrField.classList?.contains('fa-preview-bar'))
            ? barOrField
            : this._ensureInputPreviewBar(barOrField);
        if (!bar) return;
        const hasChips = bar.querySelector('.fa-chip') !== null;
        bar.classList.toggle('fa-preview-bar--visible', hasChips);
    }

    removeInputChip(chipId) {
        if (!chipId) return;
        if (this._pendingChips) {
            this._pendingChips.delete(chipId);
        }
        if (this._activePreviewChipId === chipId) {
            this._activePreviewChipId = null;
            this.closeTextPreview();
        }
        const container = this.getInputContainer() || document;
        container.querySelectorAll(`.input-chip[data-chip-id="${chipId}"]`).forEach(el => {
            const bar = el.closest('.fa-preview-bar');
            el.remove();
            if (bar) this._syncInputPreviewBar(bar);
        });
        const field = container.querySelector('#message-input') || document.getElementById('message-input');
        if (field) {
            field.querySelectorAll(`.input-chip-anchor[data-chip-id="${chipId}"], [data-chip-id="${chipId}"]`).forEach(el => el.remove());
            this._syncInputPreviewBar(field);
            try { field.dispatchEvent(new Event('input')); } catch (_) {}
        }
    }

    clearInput(field) {
        if (this._activePreviewChipId) {
            this._activePreviewChipId = null;
            this.closeTextPreview();
        }
        if (this._pendingChips) this._pendingChips.clear();
        this._chipSeq = 0;
        if (field) {
            if (field.tagName === 'TEXTAREA') {
                field.value = '';
                field.style.height = 'auto';
            } else {
                field.innerHTML = '';
            }
        }
        const roots = new Set([field?.closest?.('.chat-input-box, .start-input-box'), this.getInputContainer(), document]);
        roots.forEach(r => {
            if (!r || !r.querySelectorAll) return;
            r.querySelectorAll('.fa-preview-bar .input-chip').forEach(el => el.remove());
            r.querySelectorAll('.fa-preview-bar').forEach(bar => this._syncInputPreviewBar(bar));
        });
    }

    /* ── Черновики поля ввода per-chat ──────────────────────────────
       Как и в разделах, сохраняем недописанный текст при уходе из чата
       (включая стартовый экран, ключ '__start__') и возвращаем при входе.
       Храним ПЛОСКИЙ текст (с раскрытыми чипами) — при восстановлении
       кладём его как обычный текст, чипы заново не собираем. */
    _inputDraftKey(chatId) {
        return (chatId === null || chatId === undefined || chatId === '')
            ? '__start__'
            : String(chatId);
    }

    _saveInputDraft(chatId) {
        if (!this._inputDrafts) this._inputDrafts = {};
        const container = this.getInputContainer();
        const field = container?.querySelector('#message-input');
        if (!field) return;
        const text = this.getInputText();
        const key = this._inputDraftKey(chatId);
        if (text && text.length) {
            this._inputDrafts[key] = text;
        } else {
            delete this._inputDrafts[key];
        }
    }

    _restoreInputDraft(chatId) {
        if (!this._inputDrafts) this._inputDrafts = {};
        const container = this.getInputContainer();
        const field = container?.querySelector('#message-input');
        if (!field) return;
        const key = this._inputDraftKey(chatId);
        const draft = this._inputDrafts[key];
        if (!draft) return;
        // setInputText диспатчит input → пересчёт кнопки/лимита.
        this.setInputText(field, draft);
    }

    _clearInputDraft(chatId) {
        if (!this._inputDrafts) return;
        delete this._inputDrafts[this._inputDraftKey(chatId)];
    }

    /* Вставляет свёрнутый длинный текст как чип в полосу .fa-preview-bar,
       а в поле ввода (#message-input) на текущую позицию каретки помещает
       невидимый якорь .input-chip-anchor, чтобы сохранялся точный порядок
       между текстом и вставленными чипами. */
    _insertInputChip(field, rawText) {
        if (!this._pendingChips) this._pendingChips = new Map();
        this._chipSeq = (this._chipSeq || 0) + 1;
        const chipId = 'ic-' + Date.now() + '-' + this._chipSeq;
        this._pendingChips.set(chipId, rawText);

        const chars = rawText.length;
        const lines = rawText.length ? rawText.split('\n').length : 0;
        const firstLine = (rawText.split('\n').find(l => l.trim()) || 'Текст').trim();
        const title = firstLine.length > 32 ? firstLine.slice(0, 32) + '…' : firstLine;
        const safeTitle = this.escapeHtml(title);
        const b64 = this._encodeTextB64(rawText);

        const chip = document.createElement('div');
        chip.className = 'fa-chip fa-chip--text fa-chip--gray input-chip';
        chip.setAttribute('role', 'button');
        chip.setAttribute('tabindex', '0');
        chip.setAttribute('data-chip-id', chipId);
        chip.setAttribute('data-text-b64', b64);
        chip.setAttribute('title', 'Открыть текст');
        chip.innerHTML =
            '<span class="fa-chip__icon" aria-hidden="true">' +
            '<svg viewBox="0 0 36 44" fill="none" xmlns="http://www.w3.org/2000/svg">' +
            '<path d="M4 0h20l12 12v28a4 4 0 0 1-4 4H4a4 4 0 0 1-4-4V4a4 4 0 0 1 4-4z" class="fa-chip__bg"/>' +
            '<path d="M24 0l12 12H28a4 4 0 0 1-4-4V0z" class="fa-chip__fold"/>' +
            '<text x="18" y="32" text-anchor="middle" class="fa-chip__text">TXT</text>' +
            '</svg></span>' +
            '<span class="fa-chip__name">' + safeTitle + '</span>' +
            '<span class="fa-chip__meta">' + chars + ' симв · ' + lines + ' стр</span>' +
            '<button class="fa-chip__remove" type="button" title="Удалить" aria-label="Удалить вставленный текст">' +
            '<svg viewBox="0 0 14 14" fill="none">' +
            '<path d="M2 2l10 10M12 2L2 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
            '</svg></button>';

        const removeBtn = chip.querySelector('.fa-chip__remove');
        if (removeBtn) {
            removeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.removeInputChip(chipId);
            });
        }
        chip.addEventListener('click', () => this.openTextPreview(chip));
        chip.addEventListener('keydown', (e) => {
            if (e.target === removeBtn) return;
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                this.openTextPreview(chip);
            }
        });

        const bar = this._ensureInputPreviewBar(field);
        if (bar) {
            bar.appendChild(chip);
            this._syncInputPreviewBar(bar);
        }

        const anchor = document.createElement('span');
        anchor.className = 'input-chip-anchor';
        anchor.setAttribute('data-chip-id', chipId);
        anchor.setAttribute('contenteditable', 'false');
        anchor.setAttribute('aria-hidden', 'true');

        const sel = window.getSelection();
        let range;
        if (sel && sel.rangeCount && field.contains(sel.anchorNode)) {
            range = sel.getRangeAt(0);
            const preservedAnchors = [];
            try {
                const frag = range.cloneContents();
                frag.querySelectorAll?.('.input-chip-anchor[data-chip-id]')?.forEach(a => {
                    const clone = a.cloneNode(true);
                    preservedAnchors.push(clone);
                });
            } catch (_) {}
            range.deleteContents();
            for (const pa of preservedAnchors) {
                range.insertNode(pa);
                range.setStartAfter(pa);
                range.collapse(true);
            }
        } else {
            range = document.createRange();
            range.selectNodeContents(field);
            range.collapse(false);
        }
        range.insertNode(anchor);
        range.setStartAfter(anchor);
        range.collapse(true);
        if (sel) {
            sel.removeAllRanges();
            sel.addRange(range);
        }
        field.focus();
    }

    /* Сбрасывает активное inline-форматирование в поле ввода: снимает
       bold/italic/underline и «выводит» каретку из code/pre/span-обёрток,
       вставляя нейтральный текстовый узел на текущую позицию. Вызывается
       после Shift+Enter, чтобы залипший стиль прервался с новой строки. */
    _resetInputFormatting() {
        const sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        // Снимаем возможные активные команды форматирования
        ['bold', 'italic', 'underline', 'strikeThrough'].forEach(cmd => {
            if (document.queryCommandState && document.queryCommandState(cmd)) {
                document.execCommand(cmd, false, null);
            }
        });
        // Каретка могла остаться внутри <code>/<pre>/<span>: поднимаем её
        // на уровень поля, вставив пустой inline-контейнер обычного стиля.
        const range = sel.getRangeAt(0);
        let node = range.startContainer;
        const field = document.getElementById('message-input');
        if (!field) return;
        let inFormatted = false;
        while (node && node !== field) {
            if (node.nodeType === 1) {
                const tag = node.tagName.toLowerCase();
                if (['code', 'pre', 'span', 'b', 'i', 'u', 'strong', 'em', 'font'].includes(tag)) {
                    inFormatted = true;
                }
            }
            node = node.parentNode;
        }
        if (inFormatted) {
            // Вставляем нейтральный маркер-узел вне форматированной обёртки,
            // ставим каретку в него — дальнейший ввод пойдёт обычным стилем.
            const marker = document.createElement('span');
            marker.className = 'fmt-reset';
            marker.appendChild(document.createTextNode('\u200b')); // zero-width
            // Вставляем в конец поля (после текущего блока)
            field.appendChild(marker);
            const r = document.createRange();
            r.setStart(marker.firstChild, 1);
            r.collapse(true);
            sel.removeAllRanges();
            sel.addRange(r);
        }
    }

    /* ══════════════════════════════════════════
       SLASH COMMANDS (/chat-id, /workspaces)
    ══════════════════════════════════════════ */
    _getSlashCommands() {
        return [
            {
                cmd: '/chat-id',
                name: 'chat-id',
                icon: `<svg viewBox="0 0 20 20" fill="none" width="16" height="16"><path d="M7.5 3.5L5.5 16.5M14.5 3.5L12.5 16.5M3.5 7.5H16.5M3 12.5H16" stroke="currentColor" stroke-width="1.65" stroke-linecap="round"/></svg>`,
            },
            {
                cmd: '/workspaces',
                name: 'workspaces',
                icon: `<svg viewBox="0 0 20 20" fill="none" width="16" height="16"><rect x="2.75" y="3.5" width="14.5" height="13" rx="2.5" stroke="currentColor" stroke-width="1.6"/><path d="M7.5 3.5V16.5M2.75 8H7.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`,
            },
        ];
    }

    _getInputAreaEl() {
        const container = this.getInputContainer();
        return container?.querySelector('.chat-input-area, .start-input-area') || null;
    }

    _getInputBoxEl() {
        const container = this.getInputContainer();
        return container?.querySelector('#chat-input-box, #start-input-box, .chat-input-box, .start-input-box') || null;
    }

    _syncSlashCommandMenu() {
        const raw = (this.getInputText() || '').trim();
        if (!/^\/[^\s]*$/.test(raw)) {
            this._closeSlashCommandMenu();
            return;
        }
        const query = raw.slice(1).toLowerCase();
        const commands = this._getSlashCommands().filter(
            c => c.name.startsWith(query) || c.cmd.startsWith(raw.toLowerCase())
        );
        if (!commands.length) {
            this._closeSlashCommandMenu();
            return;
        }
        this._slashFiltered = commands;
        if (typeof this._slashActiveIdx !== 'number' || this._slashActiveIdx >= commands.length || this._slashActiveIdx < 0) {
            this._slashActiveIdx = 0;
        }
        this._renderSlashCommandMenu();
    }

    _renderSlashCommandMenu() {
        const area = this._getInputAreaEl();
        if (!area) return;
        const commands = this._slashFiltered || [];
        if (!commands.length) {
            this._closeSlashCommandMenu();
            return;
        }
        let popover = area.querySelector('.slash-cmd-popover');
        if (!popover) {
            popover = document.createElement('div');
            popover.className = 'slash-cmd-popover';
            popover.setAttribute('role', 'menu');
            area.appendChild(popover);
        }
        const activeIdx = Math.min(Math.max(0, this._slashActiveIdx || 0), commands.length - 1);
        const itemsHtml = commands.map((c, idx) => {
            const hl = idx === activeIdx ? ' data-highlighted=""' : '';
            return `<div role="menuitem" tabindex="-1" data-cmd="${this.escapeHtml(c.cmd)}" data-idx="${idx}" class="slash-cmd-item cds-reset flex w-full items-center gap-xs compact:px-2 comfortable:px-2.5 py-[calc((var(--cds-h-control)-var(--cds-leading-body))/2)] rounded text-body font-normal font-control [--cds-shortcut-cap-ink:var(--cds-text-muted)] select-none outline-none focus-visible:outline-hidden data-[disabled]:opacity-disabled data-[disabled]:pointer-events-none data-[disabled]:focus-visible:shadow-focus text-primary data-[highlighted]:bg-fill-ghost-hover"${hl}><div class="slash-cmd-icon flex size-icon shrink-0 items-center justify-center opacity-50"><span data-cds="Icon" aria-hidden="true" style="font-size: calc(1.25rem * var(--cds-rem-scale, 1)); font-weight: 433.3;">${c.icon}</span></div><span class="slash-cmd-name min-w-0 flex-1 truncate">${this.escapeHtml(c.name)}</span></div>`;
        }).join('');

        popover.innerHTML = `<div class="slash-cmd-scroll relative min-h-0 overflow-y-auto overflow-x-hidden scroll-fade-y rounded-[inherit] border-b-[length:var(--cds-ring-inner)] border-transparent forced-colors:border-b-0 p-[var(--cds-menu-gutter,0.25rem)]"><div class="slash-cmd-list flex flex-col">${itemsHtml}</div></div>`;

        popover.querySelectorAll('.slash-cmd-item').forEach(item => {
            item.addEventListener('mouseenter', () => {
                const idx = Number(item.dataset.idx || 0);
                this._slashActiveIdx = idx;
                popover.querySelectorAll('.slash-cmd-item').forEach((el, i) => {
                    if (i === idx) el.setAttribute('data-highlighted', '');
                    else el.removeAttribute('data-highlighted');
                });
            });
            item.addEventListener('mousedown', (e) => {
                e.preventDefault();
                const cmd = item.dataset.cmd;
                if (cmd) this._executeSlashCommand(cmd);
            });
        });
    }

    _closeSlashCommandMenu() {
        this._slashFiltered = null;
        this._slashActiveIdx = 0;
        const container = this.getInputContainer();
        container?.querySelectorAll('.slash-cmd-popover').forEach(el => el.remove());
    }

    _isSlashMenuOpen() {
        const area = this._getInputAreaEl();
        return Boolean(area?.querySelector('.slash-cmd-popover') && this._slashFiltered?.length);
    }

    _handleSlashKeydown(e) {
        if (this._isSlashMenuOpen()) {
            const len = this._slashFiltered.length;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                this._slashActiveIdx = ((this._slashActiveIdx || 0) + 1) % len;
                this._renderSlashCommandMenu();
                return true;
            }
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                this._slashActiveIdx = ((this._slashActiveIdx || 0) - 1 + len) % len;
                this._renderSlashCommandMenu();
                return true;
            }
            if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
                e.preventDefault();
                const chosen = this._slashFiltered[this._slashActiveIdx || 0] || this._slashFiltered[0];
                if (chosen) this._executeSlashCommand(chosen.cmd);
                return true;
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                this._closeSlashCommandMenu();
                return true;
            }
        } else if (e.key === 'Escape') {
            const area = this._getInputAreaEl();
            const card = area?.querySelector('.slash-cmd-card');
            if (card) {
                e.preventDefault();
                card.remove();
                return true;
            }
        }
        return false;
    }

    async _executeSlashCommand(cmd) {
        const container = this.getInputContainer();
        const field   = container?.querySelector('#message-input');
        const sendBtn = container?.querySelector('#send-btn');
        this._closeSlashCommandMenu();
        if (field) {
            this.clearInput(field);
            if (typeof this._clearInputDraft === 'function') {
                this._clearInputDraft(null);
                this._clearInputDraft(this.currentChat);
            } else if (this._inputDrafts) {
                delete this._inputDrafts[this._inputDraftKey(this.currentChat)];
                delete this._inputDrafts['__start__'];
            }
        }
        if (sendBtn && this._runState === 'idle') {
            sendBtn.classList.remove('active');
            sendBtn.disabled = true;
        }
        const normalized = String(cmd || '').trim().toLowerCase();
        if (normalized === '/chat-id') {
            this._showChatIdSlashCard();
        } else if (normalized === '/workspaces') {
            await this._showWorkspacesSlashCard();
        }
    }

    _mountSlashCard() {
        const area = this._getInputAreaEl();
        const box  = this._getInputBoxEl();
        if (!area || !box) return null;
        area.querySelectorAll('.slash-cmd-card').forEach(el => el.remove());
        const card = document.createElement('div');
        card.className = 'slash-cmd-card';
        area.insertBefore(card, box);
        return card;
    }

    _showChatIdSlashCard() {
        const card = this._mountSlashCard();
        if (!card) return;
        const chatId = this.currentChat ? String(this.currentChat) : '';
        const copySvg = `<svg viewBox="0 0 20 20" fill="none" width="14" height="14"><rect x="7" y="7" width="9.5" height="9.5" rx="2" stroke="currentColor" stroke-width="1.6"/><path d="M4.5 13H4a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 4 2.5h7.5A1.5 1.5 0 0 1 13 4v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
        const checkSvg = `<svg viewBox="0 0 20 20" fill="none" width="14" height="14"><path d="M4 10.5l4 4L16 6.5" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        const closeSvg = `<svg viewBox="0 0 20 20" fill="none" width="15" height="15"><path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`;

        card.innerHTML = `
            <div class="slash-card-row">
                <div class="slash-card-info">
                    <span class="slash-card-badge">/chat-id</span>
                    <span class="slash-card-value ${chatId ? '' : 'is-muted'}">${chatId ? this.escapeHtml(chatId) : 'Чат ещё не создан (отправьте сообщение)'}</span>
                </div>
                <div class="slash-card-actions">
                    ${chatId ? `<button type="button" class="slash-card-copy-btn" title="Скопировать chat_id">${copySvg}<span>Копировать</span></button>` : ''}
                    <button type="button" class="slash-card-close-btn" title="Закрыть" aria-label="Закрыть">${closeSvg}</button>
                </div>
            </div>`;

        card.querySelector('.slash-card-close-btn')?.addEventListener('click', () => card.remove());
        const copyBtn = card.querySelector('.slash-card-copy-btn');
        if (copyBtn && chatId) {
            copyBtn.addEventListener('click', async () => {
                try {
                    await navigator.clipboard.writeText(chatId);
                } catch (_) {
                    const ta = document.createElement('textarea');
                    ta.value = chatId;
                    document.body.appendChild(ta);
                    ta.select();
                    document.execCommand('copy');
                    ta.remove();
                }
                copyBtn.classList.add('copied');
                copyBtn.innerHTML = `${checkSvg}<span>Скопировано</span>`;
                setTimeout(() => {
                    if (!copyBtn.isConnected) return;
                    copyBtn.classList.remove('copied');
                    copyBtn.innerHTML = `${copySvg}<span>Копировать</span>`;
                }, 1600);
            });
        }
    }

    async _showWorkspacesSlashCard() {
        const card = this._mountSlashCard();
        if (!card) return;
        const closeSvg = `<svg viewBox="0 0 20 20" fill="none" width="15" height="15"><path d="M5.5 5.5l9 9M14.5 5.5l-9 9" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`;
        const wsSvg = `<svg viewBox="0 0 20 20" fill="none" width="15" height="15"><rect x="2.75" y="3.5" width="14.5" height="13" rx="2.5" stroke="currentColor" stroke-width="1.6"/><path d="M7.5 3.5V16.5M2.75 8H7.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>`;
        const extSvg = `<svg viewBox="0 0 20 20" fill="none" width="14" height="14"><path d="M6 14L14 6M8 6h6v6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        const checkSvg = `<svg viewBox="0 0 14 14" fill="none" width="11" height="11"><path d="M2.5 7.2L5.6 10.3L11.5 3.8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

        const initialOnlyCurrent = Boolean(this._slashWsOnlyCurrentChat);

        card.innerHTML = `
            <div class="slash-card-header">
                <div class="slash-card-info">
                    <span class="slash-card-badge">/workspaces</span>
                    <span class="slash-card-value is-muted">Загрузка областей…</span>
                </div>
                <div class="slash-card-actions">
                    <label class="slash-ws-filter" title="Показать воркспейсы только для текущего чата">
                        <input type="checkbox" class="slash-ws-filter-cb" ${initialOnlyCurrent ? 'checked' : ''}>
                        <span class="slash-ws-filter-box" aria-hidden="true">${checkSvg}</span>
                        <span class="slash-ws-filter-label">Текущий чат</span>
                    </label>
                    <button type="button" class="slash-card-close-btn" title="Закрыть" aria-label="Закрыть">${closeSvg}</button>
                </div>
            </div>
            <div class="slash-ws-list"></div>`;

        card.querySelector('.slash-card-close-btn')?.addEventListener('click', () => card.remove());
        const filterCb = card.querySelector('.slash-ws-filter-cb');

        let workspaces = [];
        try {
            let resp = await fetch('/api/chats/workspaces', { credentials: 'include' });
            if (!resp.ok) {
                resp = await fetch('/api/editor/workspaces', { credentials: 'include' });
            }
            if (resp.ok) {
                const data = await resp.json();
                workspaces = Array.isArray(data?.workspaces) ? data.workspaces : [];
            }
        } catch (_) {}

        if (!card.isConnected) return;
        const statusEl = card.querySelector('.slash-card-header .slash-card-value');
        const listEl   = card.querySelector('.slash-ws-list');
        if (!listEl) return;

        const renderList = () => {
            const onlyCurrent = Boolean(filterCb?.checked);
            this._slashWsOnlyCurrentChat = onlyCurrent;
            const curChatId = this.currentChat !== null && this.currentChat !== undefined
                ? String(this.currentChat).trim()
                : '';

            if (!workspaces.length) {
                if (statusEl) {
                    statusEl.textContent = 'Нет активных областей редактора';
                    statusEl.classList.add('is-muted');
                }
                listEl.innerHTML = '';
                listEl.style.display = 'none';
                return;
            }

            const filtered = onlyCurrent
                ? workspaces.filter(w => curChatId && String(w.chat_id ?? '').trim() === curChatId)
                : workspaces;

            if (statusEl) {
                statusEl.classList.remove('is-muted');
                statusEl.textContent = onlyCurrent
                    ? `Области текущего чата (${filtered.length} из ${workspaces.length})`
                    : `Области редактора (${workspaces.length})`;
            }

            listEl.style.display = '';
            if (!filtered.length) {
                listEl.innerHTML = `<div class="slash-ws-empty">${
                    curChatId
                        ? 'В текущем чате нет привязанных областей редактора'
                        : 'Откройте чат, чтобы отфильтровать области по текущему чату'
                }</div>`;
                return;
            }

            const formatWsTtl = (w) => {
                const isPerm = Boolean(
                    w.is_permanent || w.immortal || w.ttl_text === 'бессрочно' ||
                    (w.ttl_hours !== undefined && w.ttl_hours !== null && Number(w.ttl_hours) <= 0)
                );
                if (isPerm) {
                    return { text: '∞ бессрочно', cls: ' is-permanent', title: 'Бессрочное хранение (.ws_hold)' };
                }
                let remSec = (typeof w.remaining_seconds === 'number') ? w.remaining_seconds : null;
                if (remSec === null && w.expires_at) {
                    const expMs = Date.parse(w.expires_at);
                    if (!Number.isNaN(expMs)) {
                        remSec = Math.max(0, Math.floor((expMs - Date.now()) / 1000));
                    }
                }
                const ttlHours = Number(w.ttl_hours) > 0 ? Number(w.ttl_hours) : 10;
                if (remSec === null && w.created_at) {
                    const baseMs = Date.parse(w.updated_at || w.created_at);
                    if (!Number.isNaN(baseMs)) {
                        remSec = Math.max(0, Math.floor((baseMs + ttlHours * 3600 * 1000 - Date.now()) / 1000));
                    }
                }
                if (remSec !== null) {
                    const h = Math.floor(remSec / 3600);
                    const m = Math.floor((remSec % 3600) / 60);
                    const label = h > 0 ? (m > 0 ? `${h}ч ${m}м` : `${h}ч`) : (m > 0 ? `${m}м` : `${remSec}с`);
                    const cls = remSec < 1800 ? ' is-crit' : (remSec < 3600 ? ' is-warn' : '');
                    return { text: label, cls, title: `Время жизни области: осталось ~${label} (TTL ${ttlHours}ч)` };
                }
                return { text: `${ttlHours}ч`, cls: '', title: `Время жизни области: TTL ${ttlHours}ч` };
            };

            listEl.innerHTML = filtered.map(w => {
                const wsId = String(w.workspace_id || '');
                const title = String(w.title || wsId);
                const wsChatId = w.chat_id !== null && w.chat_id !== undefined ? String(w.chat_id).trim() : '';
                const isCurrentChatWs = Boolean(curChatId && wsChatId && wsChatId === curChatId);
                const targetChat = wsChatId || curChatId || '';
                const href = `/editor?workspace_id=${encodeURIComponent(wsId)}${targetChat ? `&chat_id=${encodeURIComponent(targetChat)}` : ''}`;
                const ttlInfo = formatWsTtl(w);
                return `
                    <a class="slash-ws-item${isCurrentChatWs ? ' is-current-chat' : ''}" href="${this.escapeHtml(href)}" target="_blank" rel="noopener noreferrer">
                        <div class="slash-ws-main">
                            <span class="slash-ws-icon">${wsSvg}</span>
                            <span class="slash-ws-title">${this.escapeHtml(title)}</span>
                            ${isCurrentChatWs ? '<span class="slash-ws-chat-pill">этот чат</span>' : ''}
                        </div>
                        <div class="slash-ws-meta">
                            <span class="slash-ws-ttl${ttlInfo.cls}" title="${this.escapeHtml(ttlInfo.title)}">${this.escapeHtml(ttlInfo.text)}</span>
                            <span class="slash-ws-id">${this.escapeHtml(wsId)}</span>
                            <span class="slash-ws-ext" aria-hidden="true">${extSvg}</span>
                        </div>
                    </a>`;
            }).join('');
        };

        filterCb?.addEventListener('change', renderList);
        renderList();
    }

    attachInputHandlers() {
        const container = this.getInputContainer();
        const field   = container?.querySelector('#message-input');
        const sendBtn = container?.querySelector('#send-btn');
        if (!field || !sendBtn) return;

        const onInput = () => {
            this._syncSlashCommandMenu();
            // Жёсткий предел длины ввода — обрезаем всё, что сверх лимита.
            this._enforceInputLimit(field);
            if (this._runState !== "idle") {
                sendBtn.disabled = false;
                sendBtn.classList.add('active', 'stop-mode');
                return;
            }
            sendBtn.classList.remove('stop-mode');
            const len = this.getInputText().length;
            const canSend = len > 0 || this.hasAttachedFiles();
            sendBtn.classList.toggle('active', canSend);
            sendBtn.disabled = !canSend;
        };

        field.addEventListener('input', onInput);

        // Вставка ДЛИННОГО текста (>1000 симв) в поле ввода: вместо простыни
        // текста втыкаем компактный чип, полный текст храним отдельно.
        // Пользователь может печатать текст до и после чипа.
        const INPUT_COLLAPSE_LIMIT = 1000;
        field.addEventListener('paste', (e) => {
            if (field.contentEditable !== 'true') return;
            const cd = e.clipboardData || window.clipboardData;

            // ── Блокируем вставку изображений/файлов через Ctrl+V ──────
            // Картинка из буфера приходит как элемент в cd.items с kind='file'
            // и type='image/...'. Такой контент вставлять запрещено —
            // отменяем событие и показываем тост.
            if (cd) {
                const items = cd.items ? Array.from(cd.items) : [];
                const hasImage =
                    (cd.files && cd.files.length > 0 &&
                        Array.from(cd.files).some(f => f.type && f.type.startsWith('image/'))) ||
                    items.some(it => it.kind === 'file' && it.type && it.type.startsWith('image/'));
                if (hasImage) {
                    e.preventDefault();
                    this.showToast('Вставка изображений в поле ввода недоступна');
                    return;
                }
            }

            // Берём именно plain-text из буфера, игнорируя text/html —
            // иначе браузер вставит форматированный код (цвета/шрифты/pre)
            // из IDE. Нам нужен только чистый текст.
            const pasted = cd ? (cd.getData('text/plain') || cd.getData('text') || '') : '';
            if (!pasted) {
                // Нет текстовой части — блокируем дефолтную HTML-вставку.
                e.preventDefault();
                return;
            }

            // Вставку всегда обрабатываем вручную (plain-text), поэтому
            // отменяем дефолтное поведение браузера в любом случае.
            e.preventDefault();

            // ── Учитываем лимит 10000 символов ──────────────────────────
            const remaining = this.INPUT_MAX_CHARS - this.getInputText().length;
            if (remaining <= 0) {
                this.showToast('Достигнут предел ' + this.INPUT_MAX_CHARS + ' символов');
                return;
            }
            const clipped = pasted.length > remaining ? pasted.slice(0, remaining) : pasted;

            if (clipped.length > INPUT_COLLAPSE_LIMIT) {
                this._insertInputChip(field, clipped);
            } else {
                // Короткий текст — вставляем как обычный текст без форматирования.
                document.execCommand('insertText', false, clipped);
            }
            if (clipped.length < pasted.length) {
                this.showToast('Текст обрезан до предела ' + this.INPUT_MAX_CHARS + ' символов');
            }
            onInput();
        });

        field.addEventListener('keydown', (e) => {
            if (this._handleSlashKeydown(e)) {
                return;
            }
            if ((e.key === 'Backspace' || e.key === 'Delete') && field.contentEditable === 'true') {
                const sel = window.getSelection();
                if (sel && sel.rangeCount) {
                    const r = sel.getRangeAt(0);
                    if (sel.isCollapsed) {
                        const sc = r.startContainer;
                        const so = r.startOffset;
                        if (e.key === 'Backspace') {
                            let prev = null;
                            if (sc.nodeType === Node.TEXT_NODE) {
                                if (so === 0) prev = sc.previousSibling;
                            } else if (sc.nodeType === Node.ELEMENT_NODE) {
                                prev = so > 0 ? sc.childNodes[so - 1] : null;
                            }
                            while (prev && prev.nodeType === Node.TEXT_NODE && prev.textContent.length === 0) {
                                prev = prev.previousSibling;
                            }
                            if (prev && prev.nodeType === Node.ELEMENT_NODE && prev.classList?.contains('input-chip-anchor')) {
                                while (prev.previousSibling && prev.previousSibling.nodeType === Node.ELEMENT_NODE && prev.previousSibling.classList?.contains('input-chip-anchor')) {
                                    prev = prev.previousSibling;
                                }
                                const nr = document.createRange();
                                nr.setStartBefore(prev);
                                nr.collapse(true);
                                sel.removeAllRanges();
                                sel.addRange(nr);
                            }
                        } else if (e.key === 'Delete') {
                            let next = null;
                            if (sc.nodeType === Node.TEXT_NODE) {
                                if (so === sc.textContent.length) next = sc.nextSibling;
                            } else if (sc.nodeType === Node.ELEMENT_NODE) {
                                next = so < sc.childNodes.length ? sc.childNodes[so] : null;
                            }
                            while (next && next.nodeType === Node.TEXT_NODE && next.textContent.length === 0) {
                                next = next.nextSibling;
                            }
                            if (next && next.nodeType === Node.ELEMENT_NODE && next.classList?.contains('input-chip-anchor')) {
                                while (next.nextSibling && next.nextSibling.nodeType === Node.ELEMENT_NODE && next.nextSibling.classList?.contains('input-chip-anchor')) {
                                    next = next.nextSibling;
                                }
                                const nr = document.createRange();
                                nr.setStartAfter(next);
                                nr.collapse(true);
                                sel.removeAllRanges();
                                sel.addRange(nr);
                            }
                        }
                    } else {
                        try {
                            const frag = r.cloneContents();
                            const anchors = Array.from(frag.querySelectorAll?.('.input-chip-anchor[data-chip-id]') || []);
                            if (anchors.length > 0) {
                                e.preventDefault();
                                r.deleteContents();
                                for (const a of anchors) {
                                    const clone = a.cloneNode(true);
                                    r.insertNode(clone);
                                    r.setStartAfter(clone);
                                    r.collapse(true);
                                }
                                sel.removeAllRanges();
                                sel.addRange(r);
                                onInput();
                                return;
                            }
                        } catch (_) {}
                    }
                }
            }
            const isMobileViewport = window.matchMedia && window.matchMedia('(max-width: 900px)').matches;
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (isMobileViewport && field.contentEditable === 'true') {
                    document.execCommand('insertLineBreak');
                    this._resetInputFormatting();
                    onInput();
                    return;
                }
                if (this._runState === "idle" && !sendBtn.disabled) this.sendMessage();
                return;
            }
            // Shift+Enter в contenteditable: перенос строки + СБРОС активного
            // форматирования, чтобы «залипший» стиль (код/SQL/HTML) прервался
            // с новой строки. Вставленный ранее контент сохраняет свой стиль,
            // но дальнейший ввод после Shift+Enter идёт обычным текстом.
            if (e.key === 'Enter' && e.shiftKey && field.contentEditable === 'true') {
                e.preventDefault();
                document.execCommand('insertLineBreak');
                this._resetInputFormatting();
                onInput();
            }
        });

        // ── Блокируем drag&drop файлов/картинок в поле ввода ──────────
        // Drop идёт мимо paste-обработчика, поэтому глушим отдельно: если
        // в переносимых данных есть файлы — отменяем дроп.
        field.addEventListener('dragover', (e) => {
            const dt = e.dataTransfer;
            if (dt && Array.from(dt.types || []).includes('Files')) {
                e.preventDefault();
                dt.dropEffect = 'none';
            }
        });
        field.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            if (dt && dt.files && dt.files.length > 0) {
                e.preventDefault();
                this.showToast('Перетаскивание файлов в поле ввода недоступно');
            }
        });

        // Placeholder для contenteditable
        if (field.contentEditable === 'true') {
            field.addEventListener('focus', () => field.classList.add('focused'));
            field.addEventListener('blur',  () => field.classList.remove('focused'));
        }

        sendBtn.onclick = () => this.sendMessage();
        this._setSendButtonMode(this._runState === "idle" ? "send" : "stop");
    }
    /* ══════════════════════════════════════════
       VISIBILITY / ONLINE RECONNECT (disabled in sigma)
    ══════════════════════════════════════════ */
    initVisibilityReconnect() {
        const resync = async (forceSyncMessages = false) => {
            if (window.__authSessionExpired) return;
            const needSync = forceSyncMessages || this._wasOfflineOrDisconnected ||
                (this.currentChat && this._messagesLoadFailedChat === String(this.currentChat));
            this._wasOfflineOrDisconnected = false;

            if (needSync || !this.chats?.length) {
                this.loadChats?.().catch(() => {});
            }
            if (!this.currentChat) return;

            if (needSync) {
                await this._syncMissedMessages(this.currentChat).catch(() => {});
            }
            if (window.__authSessionExpired) return;
            await this._restoreRunningUI(this.currentChat).catch(() => {});
            if (window.__authSessionExpired) return;
            if (this._runState === 'running') {
                const key = String(this.currentChat);
                const es = this._sources?.[key];
                if (!es || es.readyState === EventSource.CLOSED) {
                    this._startStatusPolling(this.currentChat);
                }
            }
        };
        window.addEventListener('offline', () => {
            this._wasOfflineOrDisconnected = true;
        });
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') resync(true);
        });
        window.addEventListener('pageshow', () => {
            if (document.visibilityState === 'visible') resync(true);
        });
        window.addEventListener('focus', () => {
            if (document.visibilityState === 'visible' && (this._wasOfflineOrDisconnected || (this.currentChat && this._messagesLoadFailedChat === String(this.currentChat)))) {
                resync(true);
            }
        });
        window.addEventListener('online', () => {
            resync(true);
        });
    }

    initChatsSectionToggle() {
        // Делегируем на document — кнопки гарантированно найдутся
        document.addEventListener('click', (e) => {
            // Ваши чаты — toggle (независимо от избранного)
            const chatsBtn = e.target.closest('#chats-section-toggle');
            if (chatsBtn) {
                // Коллапсим ТОЛЬКО блок "Ваши чаты", а не весь #chats-section,
                // чтобы избранное оставалось отдельной независимой единицей.
                const block = document.getElementById('your-chats-block');
                if (!block) return;
                const isCollapsed = block.classList.toggle('collapsed');
                chatsBtn.setAttribute('aria-expanded', String(!isCollapsed));
                return;
            }

            // Избранное — toggle
            const favBtn = e.target.closest('#favorites-toggle');
            if (favBtn) {
                const section = document.getElementById('favorites-section');
                if (!section) return;
                const isCollapsed = section.classList.toggle('collapsed');
                favBtn.setAttribute('aria-expanded', String(!isCollapsed));
            }
        });

        // Верхний туман появляется только при прокрутке общего списка.
        // В начальном положении сверху fade нет (только постоянный fade справа).
        const scrollHost = document.getElementById('chats-section');
        if (scrollHost) {
            const syncScrolled = () => {
                if (scrollHost.scrollTop > 4) {
                    scrollHost.setAttribute('data-scrolled', '');
                } else {
                    scrollHost.removeAttribute('data-scrolled');
                }
            };
            scrollHost.addEventListener('scroll', syncScrolled, { passive: true });
            syncScrolled();
        }
    }

    initHelp() {
        const overlay  = document.getElementById('help-overlay');
        const openBtn  = document.getElementById('open-help');
        const closeBtn = document.getElementById('help-close');
        if (!overlay || !openBtn) return;

        // Управление только через класс help-visible
        // display:none/flex задаётся в CSS (.help-page / .help-page.help-visible)
        // атрибут [hidden] в HTML нужен только как дефолт до первого открытия,
        // его убираем при первом open и больше не трогаем

        const open = () => {
            overlay.removeAttribute('hidden');          // снять дефолтный [hidden] если есть
            overlay.classList.add('help-visible');      // display:flex + opacity:1
        };

        const close = () => {
            overlay.classList.remove('help-visible');   // display:none + opacity:0
        };

        openBtn.addEventListener('click', open);
        closeBtn?.addEventListener('click', close);

        // Закрыть по Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && overlay.classList.contains('help-visible')) close();
        });
    }

    showToast(text) {
        let toast = document.getElementById('pm-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'pm-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = text;
        toast.classList.add('visible');
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
    }

    attachHintActions() {
        const container = this.getInputContainer();
        const field = container?.querySelector('#message-input');
        if (!field) return;

        container.querySelectorAll('.hint-chip, .hint-item').forEach(chip => {
            chip.addEventListener('click', () => {
                const prompt = chip.dataset.prompt;
                if (!prompt) return;
                this.setInputText(field, prompt);
                field.focus();
                chip.classList.add('hint-clicked');
                setTimeout(() => chip.classList.remove('hint-clicked'), 300);
            });
        });
    }

    /* ══════════════════════════════════════════
       PROFILE MODAL
    ══════════════════════════════════════════ */
    attachProfileHandlers() {
        const btn     = document.getElementById('profile-button');
        const overlay = document.getElementById('profile-overlay');
        const close   = document.getElementById('profile-close-btn');
        if (!btn || !overlay) return;

        btn.onclick = () => overlay.classList.add('active');
        close?.addEventListener('click', () => overlay.classList.remove('active'));
        overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('active'); });
        document.addEventListener('keydown', (e) => { if (e.key === 'Escape') overlay.classList.remove('active'); });

        document.getElementById('save-token-btn')?.addEventListener('click', async () => {
            const token = document.getElementById('scrapingdog-token')?.value;
            try {
                await fetch('/api/user/api-token', {
                    method: 'POST',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: 'scrapingdog', token })
                });
            } catch (e) { console.log('token save stub'); }
        });
    }

    /* ══════════════════════════════════════════
       ACTIONS (copy + reactions + bot time)
    ══════════════════════════════════════════ */
    _parseMessageDate(raw) {
        if (!raw) return new Date();
        if (raw instanceof Date) return raw;
        if (typeof raw === 'number') return new Date(raw);
        const s = String(raw).trim();
        if (!s) return new Date();
        if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
            return new Date(s.replace(' ', 'T') + 'Z');
        }
        const d = new Date(s);
        return Number.isNaN(d.getTime()) ? new Date() : d;
    }

    _formatBotMessageTime(rawCreatedAt) {
        const msgDate = this._parseMessageDate(rawCreatedAt);
        const diffMs = Math.max(0, Date.now() - msgDate.getTime());
        const oneHourMs = 60 * 60 * 1000;
        if (diffMs < oneHourMs) {
            const mins = Math.max(1, Math.floor(diffMs / 60000));
            return `${mins} мин назад`;
        }
        return msgDate.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    }

    _ensureMessageTimeTicker() {
        if (this._msgTimeTicker) return;
        this._msgTimeTicker = setInterval(() => {
            document.querySelectorAll('.message-actions .message-time[data-created-at]').forEach(el => {
                const iso = el.dataset.createdAt;
                if (iso) el.textContent = this._formatBotMessageTime(iso);
            });
        }, 30000);
    }

    createMessageActions(messageId, messageText, isBot, meta = {}) {
        const div = document.createElement('div');
        div.className = isBot ? 'message-actions' : 'user-actions';
        div.dataset.messageId = messageId ?? '';

        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'action-button copy-button';
        copyBtn.title = 'Копировать';

        const iconSVG = `<svg class="action-icon" viewBox="0 0 24 24" fill="none">
            <rect x="9" y="9" width="11" height="13" rx="2" stroke="currentColor" stroke-width="1.8"/>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"
                stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        </svg>`;

        copyBtn.innerHTML = iconSVG;

        copyBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await navigator.clipboard.writeText(messageText);
            const checkSVG = `<svg class="action-icon" viewBox="0 0 24 24" fill="none">
                <path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2"
                    stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`;
            copyBtn.innerHTML = checkSVG;
            copyBtn.classList.add('copied');
            setTimeout(() => {
                copyBtn.classList.remove('copied');
                copyBtn.innerHTML = iconSVG;
            }, 1500);
        });

        div.appendChild(copyBtn);

        if (!isBot) {
            const skillsCount = document.createElement('span');
            skillsCount.className = 'user-skills-count';
            skillsCount.title = 'Выбранные скиллы';
            const selSize = window.SkillsPanel?._selectedIds?.size || 0;
            const selLimit = window.SkillsPanel?._selectedLimit || 5;
            skillsCount.textContent = `${selSize}/${selLimit}`;
            div.appendChild(skillsCount);
        }

        if (isBot) {
            const likeSvg = `<svg class="action-icon" viewBox="0 0 24 24" fill="none">
                <path d="M7 22V11M2 13v7a2 2 0 0 0 2 2h12.4a2 2 0 0 0 1.96-1.61l1.38-7A2 2 0 0 0 17.78 11H14V6.5a2.5 2.5 0 0 0-2.5-2.5L7 11H4a2 2 0 0 0-2 2z"
                    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`;
            const dislikeSvg = `<svg class="action-icon" viewBox="0 0 24 24" fill="none">
                <path d="M17 2v11M22 11V4a2 2 0 0 0-2-2H7.6a2 2 0 0 0-1.96 1.61l-1.38 7A2 2 0 0 0 6.22 13H10v4.5a2.5 2.5 0 0 0 2.5 2.5L17 13h3a2 2 0 0 0 2-2z"
                    stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>`;

            const likeBtn = document.createElement('button');
            likeBtn.type = 'button';
            likeBtn.className = 'action-button reaction-button like-button';
            likeBtn.title = 'Нравится';
            likeBtn.dataset.reaction = 'like';
            likeBtn.innerHTML = likeSvg;

            const dislikeBtn = document.createElement('button');
            dislikeBtn.type = 'button';
            dislikeBtn.className = 'action-button reaction-button dislike-button';
            dislikeBtn.title = 'Не нравится';
            dislikeBtn.dataset.reaction = 'dislike';
            dislikeBtn.innerHTML = dislikeSvg;

            const initReaction = String(meta.reaction || '').trim().toLowerCase();
            if (initReaction === 'like') {
                likeBtn.classList.add('active');
            } else if (initReaction === 'dislike') {
                dislikeBtn.classList.add('active');
            }

            const sendReaction = async (targetReaction) => {
                const isAlreadyActive =
                    (targetReaction === 'like' && likeBtn.classList.contains('active')) ||
                    (targetReaction === 'dislike' && dislikeBtn.classList.contains('active'));
                const nextReaction = isAlreadyActive ? null : targetReaction;

                likeBtn.classList.toggle('active', nextReaction === 'like');
                dislikeBtn.classList.toggle('active', nextReaction === 'dislike');

                const parentMsg = div.closest('.agent-message');
                const resolvedMsgId =
                    meta.messageId ||
                    parentMsg?.dataset?.messageId ||
                    parentMsg?.dataset?.id ||
                    messageId ||
                    '';
                const resolvedDbId =
                    meta.dbId ||
                    parentMsg?.dataset?.id ||
                    messageId ||
                    '';
                const chatId = this.currentChat;
                if (!chatId || (!resolvedMsgId && !resolvedDbId)) return;

                try {
                    await fetch(`/api/chats/${encodeURIComponent(chatId)}/reaction`, {
                        method: 'POST',
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            chat_id: String(chatId),
                            message_id: String(resolvedMsgId),
                            id: String(resolvedDbId),
                            reaction: nextReaction,
                        }),
                    });
                } catch (err) {
                    console.warn('[reaction] failed to save:', err);
                }
            };

            likeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                sendReaction('like');
            });
            dislikeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                sendReaction('dislike');
            });

            div.appendChild(likeBtn);
            div.appendChild(dislikeBtn);

            const createdAtIso = this._parseMessageDate(meta.createdAt || new Date()).toISOString();
            const timeEl = document.createElement('span');
            timeEl.className = 'message-time';
            timeEl.dataset.createdAt = createdAtIso;
            timeEl.textContent = this._formatBotMessageTime(createdAtIso);
            div.appendChild(timeEl);

            this._ensureMessageTimeTicker();
        }

        return div;
    }

    /* ══════════════════════════════════════════
       HELPERS
    ══════════════════════════════════════════ */
    /* ══════════════════════════════════════════
       NO-TOOLS HINT (облачко в ленте)
       Показывается когда юзер шлёт запрос, а ни одна
       категория инструментов не выбрана. Закрывается
       по «Хорошо» или крестику. Не дублируется.
    ══════════════════════════════════════════ */
    _resetNoToolsWarn(chatId = null) {
        this._noToolsWarnCount = 0;
        this._noToolsWarnChatId = chatId != null ? String(chatId) : null;
        try { sessionStorage.removeItem('catchpm:no_tools_warn_count'); } catch (_) {}
        if (this._noToolsCleanup) {
            this._noToolsCleanup(true);
        }
    }

    _showNoToolsHint() {
        try { sessionStorage.removeItem('catchpm:no_tools_warn_count'); } catch (_) {}
        const curChatKey = this.currentChat != null ? String(this.currentChat) : null;
        if (curChatKey !== (this._noToolsWarnChatId ?? null)) {
            this._noToolsWarnCount = 0;
            this._noToolsWarnChatId = curChatKey;
        }
        let warnCount = Number(this._noToolsWarnCount) || 0;
        if (warnCount >= 2) return;
        warnCount += 1;
        this._noToolsWarnCount = warnCount;

        const inputContainer = this.getInputContainer();
        const triggerBtn = inputContainer?.querySelector('.tools-trigger-btn.attach-btn, .tools-trigger-btn')
            || document.querySelector('.chat-input-footer .tools-trigger-btn, .start-input-footer .tools-trigger-btn');
        if (!triggerBtn) return;

        if (this._noToolsCleanup) {
            this._noToolsCleanup(true);
        }

        document.querySelectorAll('.tools-trigger-btn').forEach(btn => {
            btn.classList.add('tools-trigger-btn--alert');
        });

        const hint = document.createElement('div');
        hint.className = 'no-tools-hint';
        hint.setAttribute('role', 'status');
        hint.innerHTML = `
            <span class="no-tools-hint__dot" aria-hidden="true"></span>
            <div class="no-tools-hint__body">
                <div class="no-tools-hint__title">Инструменты не выбраны</div>
                <div class="no-tools-hint__text">Нажмите на ключ, чтобы включить поиск, файлы и запуск кода</div>
            </div>
            <span class="no-tools-hint__arrow" aria-hidden="true"></span>
        `;
        document.body.appendChild(hint);

        const positionHint = () => {
            const activeBtn = this.getInputContainer()?.querySelector('.tools-trigger-btn')
                || triggerBtn;
            if (!activeBtn || !activeBtn.isConnected) return;
            const rect = activeBtn.getBoundingClientRect();
            const hintRect = hint.getBoundingClientRect();
            const margin = 8;
            const gap = 10;

            let left = rect.left + rect.width / 2 - 28;
            if (left < margin) left = margin;
            if (left + hintRect.width > window.innerWidth - margin) {
                left = window.innerWidth - hintRect.width - margin;
            }
            const top = Math.max(margin, rect.top - hintRect.height - gap);
            const arrowX = Math.max(14, Math.min(hintRect.width - 14, (rect.left + rect.width / 2) - left));

            hint.style.left = `${Math.round(left)}px`;
            hint.style.top = `${Math.round(top)}px`;
            hint.style.setProperty('--arrow-left', `${Math.round(arrowX)}px`);
        };

        positionHint();
        requestAnimationFrame(() => {
            positionHint();
            hint.classList.add('no-tools-hint--in');
        });

        let closed = false;
        const cleanup = (immediate = false) => {
            if (closed) return;
            closed = true;
            if (this._noToolsTimer) {
                clearTimeout(this._noToolsTimer);
                this._noToolsTimer = null;
            }
            window.removeEventListener('resize', positionHint);
            window.removeEventListener('scroll', positionHint, true);
            triggerBtn.removeEventListener('click', onTriggerClick);
            document.querySelectorAll('.tools-trigger-btn--alert').forEach(btn => {
                btn.classList.remove('tools-trigger-btn--alert');
            });
            if (immediate) {
                hint.remove();
            } else {
                hint.classList.add('no-tools-hint--closing');
                setTimeout(() => hint.remove(), 200);
            }
            if (this._noToolsCleanup === cleanup) {
                this._noToolsCleanup = null;
            }
        };

        const onTriggerClick = () => cleanup(false);
        triggerBtn.addEventListener('click', onTriggerClick, { once: true });
        hint.addEventListener('click', (e) => {
            e.stopPropagation();
            cleanup(false);
            const activeBtn = this.getInputContainer()?.querySelector('.tools-trigger-btn') || triggerBtn;
            activeBtn?.click();
        });

        window.addEventListener('resize', positionHint);
        window.addEventListener('scroll', positionHint, true);
        this._noToolsCleanup = cleanup;
        this._noToolsTimer = setTimeout(() => cleanup(false), 5000);
    }

    getMessagesContainer() {
        return document.getElementById('view-agent')?.querySelector('.chat-messages');
    }

    /* Туман по краям области сообщений: fade-top когда есть что скрыть сверху,
       fade-bottom когда контент уходит вниз под message-input-container.
       Вешается один раз на контейнер; классы переставляются по scrollTop. */
    _initMessagesFade(container) {
        container = container || this.getMessagesContainer();
        if (!container || container._fadeBound) return;
        container._fadeBound = true;

        const sync = () => {
            const scrollable = container.scrollHeight - container.clientHeight > 4;
            const atTop = container.scrollTop <= 4;
            const atBottom =
                container.scrollHeight - container.scrollTop - container.clientHeight <= 4;
            container.classList.toggle('fade-top', scrollable && !atTop);
            container.classList.toggle('fade-bottom', scrollable && !atBottom);
            this._syncScrollArrow(container);
        };

        // rAF-throttle на scroll: не чаще одного пересчёта layout на кадр.
        let fadeTicking = false;
        const onScrollSync = () => {
            if (fadeTicking) return;
            fadeTicking = true;
            requestAnimationFrame(() => { fadeTicking = false; sync(); });
        };

        container.addEventListener('scroll', onScrollSync, { passive: true });
        container._syncFade = sync;
        // Пересчёт при изменении содержимого/размеров (новые сообщения, ресайз)
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(sync);
            ro.observe(container);
        }
        sync();
    }

    getInputContainer() {
        return document.getElementById('view-agent')?.querySelector('.message-input-container');
    }
}

/* ── Точка входа ──────────────────────────── */
const app = window.__catchpmApp || new ChatApp();
if (!window.__catchpmApp) {
    app.init();
}
export default app;
