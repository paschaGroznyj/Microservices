// CatchPM — Chat SPA
class ChatApp {
    constructor() {
        this.currentUser   = null;
        this.currentChat   = null;
        this.isUIReady     = false;
        this.chats         = [];
        this.currentTab    = 'agent'; // 'agent' | 'arxiv'
        this._runState = 'idle';      // idle | running | stopping
        this._activeRequestId = null;

        /* ══════════════════════════════════════════
           ВАРИАНТ 2: МУЛЬТИПЛЕКС СТРИМИНГА ПО ЧАТАМ
           Раньше весь стрим-стейт был глобальным (this._pendingBotEl,
           this._streamBuffer, this.ws, this._streamRaf ...) — один на инстанс.
           При переключении чата WS закрывался и стрим обрывался.

           Теперь на КАЖДЫЙ чат — свой объект стрима (this._streams[chatId])
           и свой сокет (this._sockets[chatId]). Уходя из стримящего чата,
           мы НЕ закрываем его сокет: он продолжает копить чанки в свой
           (открепленный от DOM) узел в фоне. При возврате — просто
           re-attach готового узла.

           Совместимость: старые методы стрима обращаются к глобальным
           this._pendingBotEl / this._streamBuffer и т.д. Мы делаем эти поля
           ПРОКСИ-геттерами на активный стрим (this._streams[currentChat]),
           а фоновые сокеты пишут напрямую в свой стрим по chatId из
           замыкания (см. connectWebSocket / _onWsMessage).
        ══════════════════════════════════════════ */
        this._streams = {};    // chatId -> объект стрима (см. _getStream)
        this._sockets = {};     // chatId -> WebSocket

        // Поля стрима, которые исторически были this._<name>. Теперь это
        // прокси на активный стрим. Список ровно повторяет то, что читают/пишут
        // методы стриминга — менять их тела не нужно.
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
                    // Пишем в активный стрим. Создаём его лениво.
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

    /* Возвращает (создавая при необходимости) объект стрима для чата. */
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

    /* Прокси на активный сокет — сохраняем совместимость со старым this.ws. */
    get ws() {
        return this._sockets[this.currentChat] || null;
    }
    set ws(v) {
        if (this.currentChat == null) return;
        const key = String(this.currentChat);
        if (v == null) delete this._sockets[key];
        else this._sockets[key] = v;
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

        // Передаём username в ToolsPanel для проверки прав
        window.ToolsPanel?.setUser(this.currentUser.username);

        await this.loadChats();
        // await loadFeedbacks();
        initMobileMenu();
        this.initTabSwitcher();
        this.initSidebarCollapse();
        this.initHelp();
        this.initChatsSectionToggle();
        this.initVisibilityReconnect();
        this.initTextPreview();
        window.SkillsPanel?.init(this);
        await this.renderMessageInput();
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
       TAB SWITCHER (agent ↔ arxiv)
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
            // renderMessageInput может выйти на гарде (инпут уже отрисован
            // для текущего чата) и НЕ переинициализировать FileAttach.
            // После общего чата синглтон FileAttach держит контекст группы,
            // поэтому принудительно возвращаем его на инпут агента.
            this._rebindFileAttachToAgent();
        }
        if (tab === 'graph') {
            console.log('INIT GRAPH')
            knowledgeGraph.init();
        }
        if (tab === 'skills') {
            window.SkillsPanel?.open(this);
        }
        if (tab === 'group') {
            chatMain?.classList.remove('start-screen-mode');
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
    async openChat(chatId) {
        // Сохраняем черновик поля ввода уходящего чата (в т.ч. стартовый
        // экран, если currentChat == null) перед сносом DOM.
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
            // ВАРИАНТ 2 (мультиплекс): сокет уходящего чата НЕ закрываем, если он
            // ещё стримит — пусть достримит в фоне. Стрим-стейт этого чата живёт
            // изолированно в this._streams[oldChatId], а RAF-цикл продолжает
            // тикать по его _liveBlockEl (узел остаётся в DOM ленты этого чата
            // до момента, пока мы не очистим контейнер под новый чат — см. ниже:
            // мы ОТКРЕПЛЯЕМ узел из ленты, но НЕ обнуляем ссылки, чтобы фоновый
            // RAF дорисовывал прямо в него). При возврате re-attach.
            const oldKey = String(this.currentChat);
            const oldStream = this._getStream(oldKey);
            let streamDetached = false;
            if (oldStream && oldStream.pendingBotEl) {
                // Открепляем узел из ленты (сейчас её DOM будет очищен под новый
                // чат), но ссылки в oldStream сохраняем — фоновый RAF пишет туда.
                const el = oldStream.pendingBotEl;
                if (el.parentNode) el.parentNode.removeChild(el);
                streamDetached = true;
            }
            if (mainStatusEl || (this.subagentStates && Object.keys(this.subagentStates).length)
                || runningMeta.running || streamDetached) {
                this._chatStatuses[this.currentChat] = {
                    main: mainStatusEl?.querySelector('.status-text')?.textContent || '',
                    subagents: this.subagentStates ? { ...this.subagentStates } : {},
                    // Вариант 2: снапшот больше не нужен — узел и стрим живут в
                    // this._streams[oldKey], сокет открыт, RAF тикает в фоне.
                    stream: null,
                    ...runningMeta
                };
            }
        }

        if (String(this._noToolsWarnChatId ?? '') !== String(chatId)) {
            this._resetNoToolsWarn(chatId);
        }
        this.currentChat = chatId;
        this.subagentStates = {};

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

        // Переключаемся на вкладку агента если были на arxiv
        if (this.currentTab !== 'agent') {
            this.switchTab('agent');

            const titleEl = document.getElementById("main-title");
            titleEl.textContent = 'Агент';
        }

        await Promise.all([
            this.loadMessages(chatId),
            this.renderMessageInput()
        ]);

        // Восстанавливаем черновик поля ввода этого чата (если был сохранён).
        this._restoreInputDraft(chatId);

        this.connectWebSocket(chatId);

        // ВАЖНО: раньше здесь был ранний return по наличию любого
        // .agent-message в ленте («ответ уже есть → агент свободен»).
        // Это ломало восстановление в многоходовых чатах: старые ответы
        // бота присутствуют всегда, поэтому ранний выход срабатывал даже
        // когда агент реально в работе над НОВЫМ сообщением, и
        // _restoreRunningUI (а с ним и запрос /run_status) не вызывался.
        // Теперь источник истины — Redis-флаг: всегда доходим до
        // _restoreRunningUI, он авторитетно спросит сервер и сам вызовет
        // _onRunFinished(), если работа завершена.

        // Восстанавливаем сохранённый статус если есть (чат ещё в процессе)
        const savedState = this._chatStatuses?.[chatId];
        if (savedState) {
            if (savedState.main) this._updateMainStatus(savedState.main);
            if (savedState.subagents && Object.keys(savedState.subagents).length) {
                this.subagentStates = savedState.subagents;
                this._restoreSubagentStatuses(savedState.subagents);
            }
        }

        // ВАРИАНТ 2: если этот чат стримил в фоне (сокет был открыт, RAF тикал,
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

        try {
            const r = await fetch(`/api/chats/${chatId}/run_status`, {
                credentials: 'include'
            });
            if (r.ok) {
                const j = await r.json();
                serverStatus = j;
                if (j?.running || j?.loop_active) {
                    running = true;
                    requestId = j.request_id || requestId;
                    this._loopActive = !!j.loop_active;
                } else {
                    running = false;
                    this._loopActive = false;
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
            if (st) {
                st.pendingBotEl = null;
                st.liveBlockEl = null;
                st.reasoningWrapEl = null;
            }
            this._onRunFinished();
            return;
        }

        if (container && st?.pendingBotEl) {
            this._dedupeTurnReasoningAndActions(container, st);
            this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        }

        this._runState = 'running';
        this._activeRequestId = requestId;
        this._setSendButtonMode('stop');

        const alreadyHasFinal = container?.querySelector('.live-block');
        const hasPlaceholder = container?.querySelector('#agent-response-placeholder');
        if (container && !alreadyHasFinal && !hasPlaceholder) {
            this._createBotPlaceholder(container);
        } else if (container && hasPlaceholder && st?.pendingBotEl) {
            this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        }
    }

    /* ══════════════════════════════════════════
       ВАРИАНТ 2: мультиплекс стриминга по чатам.
       Каждый чат стримит в СВОЙ this._streams[chatId] через СВОЙ сокет
       this._sockets[chatId]. При уходе из стримящего чата сокет НЕ
       закрывается, RAF продолжает тикать в фоне (узел ответа откреплён от
       ленты, но жив по ссылке в стриме). При возврате — re-attach узла в
       ленту: размышления и уже напечатанный текст на месте, стрим без
       разрывов, финал финализируется в своём чате.
    ══════════════════════════════════════════ */

    /**
     * Возвращает откреплённый фоновый стрим чата обратно в ленту.
     * Вызывается из openChat после loadMessages/connectWebSocket.
     * Если чат не стримил в фоне — тихо выходит.
     */
    _reattachBackgroundStream(chatId) {
        const st = this._getStream(chatId);
        if (!st || !st.pendingBotEl) return;
        // Узел уже в текущей ленте (например, только что отрисован) — не дублируем.
        const container = this.getMessagesContainer();
        if (!container) return;
        if (st.pendingBotEl.parentNode === container) return;

        // Убираем плейсхолдер-волну, если _restoreRunningUI успел её создать —
        // у нас есть реальный узел ответа.
        if (st.streamDone || st.streamIsFinal) {
            this._removeBotPlaceholder();
        }

        // Re-attach узла строго после последнего user-message (и дедуп рассуждений).
        this._ensureBotAfterLastUserMessage(container, st.pendingBotEl);
        if (!st.streamDone && !st.streamIsFinal && !container.querySelector('#agent-response-placeholder')) {
            this._createBotPlaceholder(container);
        }
        this._dedupeTurnReasoningAndActions(container, st);
        this._botAnswered = true;

        // Показываем накопленное и (пере)запускаем RAF, если он вдруг был снят
        // (например финал уже пришёл, а буфер ещё не дренирован).
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
        // Сохраняем черновик поля ввода уходящего чата, чтобы недописанный
        // текст не потерялся при переходе на стартовый экран.
        this._saveInputDraft(this.currentChat);

        // ВАРИАНТ 2: сокет активного чата закрываем ТОЛЬКО если он не стримит.
        // Если чат ещё генерит ответ — оставляем его сокет открытым, пусть
        // достримит в фоне (this._streams[chatId] живёт, узел откреплён при
        // openChat). При возврате в тот чат _reattachBackgroundStream покажет.
        const cur = this.currentChat != null ? String(this.currentChat) : null;
        if (cur) {
            const st = this._getStream(cur);
            const sock = this._sockets[cur];
            const streaming = !!(st && (st.streamingActive || st.pendingBotEl));
            // ВАРИАНТ 2: если чат ещё стримит — открепляем его узел ответа из
            // ленты (сейчас она очистится), но сокет/стрим НЕ трогаем — пусть
            // достримит в фоне. При возврате _reattachBackgroundStream вернёт.
            if (st && st.pendingBotEl && st.pendingBotEl.parentNode) {
                st.pendingBotEl.parentNode.removeChild(st.pendingBotEl);
            }
            if (sock && !streaming) {
                sock._intentionalClose = true;
                sock.close();
                delete this._sockets[cur];
            }
            // Запоминаем, что этот чат ещё в работе — чтобы при возврате
            // мгновенно поднять стоп-кнопку (как в openChat).
            if (streaming || this._runState === 'running') {
                if (!this._chatStatuses) this._chatStatuses = {};
                const mainStatusEl = document.getElementById('agent-status-indicator');
                this._chatStatuses[cur] = {
                    main: mainStatusEl?.querySelector('.status-text')?.textContent || '',
                    subagents: this.subagentStates ? { ...this.subagentStates } : {},
                    stream: null,
                    running: this._runState === 'running',
                    activeRequestId: this._activeRequestId || null,
                };
            }
        }

        this._resetNoToolsWarn(null);
        this.currentChat = null;

        // Сбрасываем run-UI, чтобы стоп-кнопка/индикатор стримящего чата не
        // «протекли» на стартовый экран. Остановить фоновый стрим можно
        // только вернувшись в его чат.
        this._runState = 'idle';
        this._activeRequestId = null;
        this._botAnswered = false;
        this._setSendButtonMode('send');

        const msgContainer = this.getMessagesContainer();
        if (msgContainer) {
            msgContainer.innerHTML = '';
            msgContainer.classList.add('hidden');
        }

        document.querySelectorAll('.chat-item').forEach(i => i.classList.remove('active'));
        document.querySelector('.chat-main-inner')?.classList.remove('chat-active');

        // Переключаемся на агента если были на arxiv
        if (this.currentTab !== 'agent') {
            this.switchTab('agent');
        }

        this.renderMessageInput();
        // Восстанавливаем черновик стартового экрана (если был сохранён ранее).
        this._restoreInputDraft(null);
    }

    /* ══════════════════════════════════════════
       MESSAGES
    ══════════════════════════════════════════ */
    async loadMessages(chatId) {
        const PAGE_SIZE = 50;
        this._loadingMessagesChat = String(chatId);
        let res;
        try {
            res = await fetch(`/api/chats/${chatId}/messages?limit=${PAGE_SIZE}&offset=0`);
        } finally {
            if (this._loadingMessagesChat === String(chatId)) {
                this._loadingMessagesChat = null;
            }
        }
        if (!res || !res.ok) return;
        const data = await res.json();
        if (chatId !== this.currentChat) return; // гонка

        // Состояние пагинации по конкретному чату
        this._pagination = {
            chatId,
            pageSize: PAGE_SIZE,
            loaded: (data.messages || []).length,
            hasMore: !!data.has_more,
            loading: false,
        };

        this.renderMessages(data.messages);
        this._initHistoryScrollLoader();
    }

    renderMessages(messages) {
        const container = this.getMessagesContainer();
        if (!container) return;
        container.innerHTML = '';
        messages.forEach(msg => this.addMessage(msg, false));
        container.scrollTop = container.scrollHeight;
        this._initMessagesFade(container);
        container._syncFade && container._syncFade();
    }

    /**
     * Дозагрузка более старых сообщений при скролле вверх.
     * offset считается "с конца": уже загружено `loaded` последних сообщений,
     * следующая пачка — offset=loaded.
     */
    async loadOlderMessages() {
        const p = this._pagination;
        if (!p || p.loading || !p.hasMore) return;
        if (p.chatId !== this.currentChat) return;

        const container = this.getMessagesContainer();
        if (!container) return;

        p.loading = true;

        // Anchor: запоминаем позицию, чтобы после вставки сверху
        // видимая часть чата не "прыгнула".
        const prevScrollHeight = container.scrollHeight;
        const prevScrollTop = container.scrollTop;

        try {
            const res = await fetch(
                `/api/chats/${p.chatId}/messages?limit=${p.pageSize}&offset=${p.loaded}`
            );
            if (!res.ok) { p.loading = false; return; }
            const data = await res.json();
            if (p.chatId !== this.currentChat) return; // чат переключили пока грузили

            const older = data.messages || [];
            if (!older.length) {
                p.hasMore = false;
                p.loading = false;
                return;
            }

            // Собираем узлы старых сообщений в отдельный фрагмент и вставляем
            // ПЕРЕД текущими, в верх контейнера.
            const buffer = document.createDocumentFragment();
            const staging = document.createElement('div');
            older.forEach(msg => this._renderMessageInto(staging, msg));
            while (staging.firstChild) buffer.appendChild(staging.firstChild);
            container.insertBefore(buffer, container.firstChild);

            p.loaded += older.length;
            p.hasMore = !!data.has_more;

            // Восстанавливаем позицию скролла
            const newScrollHeight = container.scrollHeight;
            container.scrollTop = prevScrollTop + (newScrollHeight - prevScrollHeight);
        } catch (e) {
            console.error('[pagination] loadOlderMessages error', e);
        } finally {
            p.loading = false;
        }
    }

    /**
     * Рендерит одно сообщение из истории в произвольный контейнер
     * (используется для вставки старых сообщений сверху).
     */
    _renderMessageInto(targetContainer, message) {
        const realContainer = this.getMessagesContainer();
        // addMessage жёстко пишет в getMessagesContainer(); временно подменяем,
        // чтобы переиспользовать всю логику рендера без дублирования.
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

        // rAF-throttle: scroll сыплется десятками событий/кадр, а чтение
        // container.scrollTop форсит layout. Сводим к одной проверке на кадр.
        let ticking = false;
        container.addEventListener('scroll', () => {
            if (ticking) return;
            ticking = true;
            requestAnimationFrame(() => {
                ticking = false;
                // Триггерим подгрузку, когда почти доскроллили до верха
                if (container.scrollTop < 120) {
                    this.loadOlderMessages();
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

    _renderSafeMarkdownHtml(mdText) {
        let html = marked.parse(String(mdText || ''));
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

        const isBot      = message.is_from_bot;
        const rawContent = message.message || 'Тут было пусто';
        // Убираем статус-индикатор при ответе бота (через метод, чтобы захватить субагентов)
        if (isBot) this._clearAllStatuses();

        const messageEl = document.createElement('div');
        messageEl.className = isBot ? 'agent-message' : 'user-message';

        let content = rawContent;
        if (rawContent.trimStart().startsWith('<')) {
            content = rawContent; // уже HTML — не трогаем
        } else {
            content = marked.parse(rawContent);
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

        const actions = this.createMessageActions(message.id, rawContent, isBot);

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
            const filesHtml = this._renderMessageFiles(message.files);
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
            wrapper.appendChild(messageEl);
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
            // Скролл вниз только при загрузке истории (scroll=false).
            // При новом сообщении (scroll=true) скроллом управляет _scrollToUserMessageAndReserve.
            if (!scroll) container.scrollTop = container.scrollHeight;
        } else if (scroll) {
            // Сообщение агента — только для живых ws-сообщений (scroll=true)
            const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
            if (distFromBottom < 80) {
                container.scrollTop = container.scrollHeight;
            } else {
                this._showScrollArrow(container);
            }
        }

        // Пересчёт тумана по краям после добавления сообщения.
        container._syncFade && container._syncFade();
    }

    /* ══════════════════════════════════════════
       SCROLL ARROW
    ══════════════════════════════════════════ */
    _initScrollArrow(container) {
        // Создаём кнопку один раз — вешаем на body (position: fixed)
        if (document.getElementById('scroll-to-bottom-btn')) return;

        const btn = document.createElement('button');
        btn.id = 'scroll-to-bottom-btn';
        btn.className = 'scroll-to-bottom';
        btn.innerHTML = `
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M8 3v10M3 9l5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Новое сообщение`;
        btn.addEventListener('click', () => {
            container.scrollTop = container.scrollHeight;
            btn.classList.remove('visible');
        });

        // Скрываем если пользователь сам доскроллил до низа
        container.addEventListener('scroll', () => {
            const dist = container.scrollHeight - container.scrollTop - container.clientHeight;
            if (dist < 40) btn.classList.remove('visible');
        }, { passive: true });

        document.body.appendChild(btn);
    }

    _showScrollArrow(container) {
        this._initScrollArrow(container);
        const btn = document.getElementById('scroll-to-bottom-btn');
        if (btn) btn.classList.add('visible');
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

    _renderMessageFiles(files) {
        if (!files || files.length === 0) return '';

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

        const makeChip = (f) => {
            const ext   = (f.ext || '').toLowerCase();
            const label = getLabel(ext);
            const color = getColor(ext);
            const name  = this.escapeHtml(f.name || 'файл');
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
    /* Контекстное меню по файлу агента: «Открыть в редакторе» / «Скачать». */
    _showFileContextMenu(e, { name, url, chatId }) {
        document.querySelectorAll('.fa-file-ctxmenu').forEach(m => m.remove());
        const menu = document.createElement('div');
        menu.className = 'fa-file-ctxmenu';
        menu.style.cssText = `position:fixed;z-index:9999;left:${e.clientX}px;top:${e.clientY}px;`
            + 'background:#252526;border:1px solid #444;border-radius:8px;padding:4px;'
            + 'box-shadow:0 6px 24px rgba(0,0,0,.4);min-width:190px;font-size:13px;';

        const mkItem = (label, onClick) => {
            const it = document.createElement('div');
            it.textContent = label;
            it.style.cssText = 'padding:8px 12px;cursor:pointer;color:#ddd;border-radius:5px;';
            it.addEventListener('mouseenter', () => it.style.background = '#094771');
            it.addEventListener('mouseleave', () => it.style.background = 'transparent');
            it.addEventListener('click', () => { onClick(); menu.remove(); });
            return it;
        };

        menu.appendChild(mkItem('Открыть в редакторе', () => {
            const q = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : '';
            window.open(`/editor${q}`, '_blank');
        }));
        menu.appendChild(mkItem('Скачать', () => {
            if (url && url !== '#') {
                const a = document.createElement('a');
                a.href = url; a.download = name; a.rel = 'noopener noreferrer';
                document.body.appendChild(a); a.click(); a.remove();
            }
        }));

        document.body.appendChild(menu);
        const close = (ev) => {
            if (!menu.contains(ev.target)) { menu.remove(); document.removeEventListener('mousedown', close); }
        };
        setTimeout(() => document.addEventListener('mousedown', close), 0);
    }

    _renderBotFiles(files, messageId) {
        console.log('ВНУТРИ РЕНДИНДА ФАЙЛОВ ', files, messageId)
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

            const chip = document.createElement('a');
            chip.className = `fa-chip fa-chip--${color} fa-chip--bot`;
            chip.href      = url;
            chip.download  = name;
            chip.title     = name;
            chip.setAttribute('rel', 'noopener noreferrer');

            // ПКМ по файлу: «Открыть в редакторе» / «Скачать».
            chip.addEventListener('contextmenu', (e) => {
                e.preventDefault();
                this._showFileContextMenu(e, { name, url, chatId: this.currentChat });
            });

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

        const makeImageCard = (f) => {
            const name = f.name || 'image';
            const safeName = this.escapeHtml(name);
            // Сгенерированные тулом картинки (kind==='image') тянем ИЗ БД
            // (generated_images), а не с диска: эфемерный файл живёт ~30 мин
            // и чистится по TTL, после чего /files/... вернёт 404. Эндпоинт
            // /image/... проксирует бинарник из таблицы по (message_id, name).
            // Обычные вложения (kind иной) оставляем на файловом пути.
            // Любой файл-картинку тянем из БД через /image/... — эндпоинт умеет
            // искать и по image_key, и по паре (message_id, file_name). Это чинит
            // legacy-записи (kind/image_key = NULL), у которых файл на диске уже
            // вычищен по TTL: раньше они шли в /files/... и падали в 404.
            const imageKeyQs = f.image_key
                ? `?image_key=${encodeURIComponent(f.image_key)}`
                : '';
            const url = messageId
                ? `/api/chats/${this.currentChat}/image/${encodeURIComponent(messageId)}/${encodeURIComponent(name)}${imageKeyQs}`
                : '#';

            const card = document.createElement('div');
            card.className = 'fa-image-card';
            card.title = name;

            const img = document.createElement('img');
            img.className = 'fa-image-card__img';
            img.alt = safeName;
            img.loading = 'lazy';
            img.src = url;
            // Пока грузится src — держим скелетон; снимаем по load/error.
            card.classList.add('fa-image-card--loading');
            img.addEventListener('load', () => card.classList.remove('fa-image-card--loading'));
            img.addEventListener('error', () => {
                card.classList.remove('fa-image-card--loading');
                card.classList.add('fa-image-card--error');
            });

            // Клик — превью в лайтбоксе.
            card.addEventListener('click', () => this._openImageLightbox(url, name, f.image_key));

            const overlay = document.createElement('div');
            overlay.className = 'fa-image-card__overlay';
            overlay.innerHTML = `
<span class="fa-image-card__zoom" aria-hidden="true">
  <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="M20 20l-3.5-3.5M11 8v6M8 11h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
</span>`;

            const skel = document.createElement('div');
            skel.className = 'fa-image-card__skeleton';
            skel.innerHTML = '<div class="fa-pixel-grid"></div>';

            card.appendChild(skel);
            card.appendChild(img);
            card.appendChild(overlay);
            return card;
        };

        // Разделяем на картинки и обычные файлы.
        const imageFiles = files.filter(isImageFile);
        const otherFiles = files.filter(f => !isImageFile(f));

        const wrap = document.createElement('div');
        wrap.className = 'fa-bot-files';

        if (imageFiles.length) {
            // Пришли реальные картинки — снимаем скелетон генерации.
            this._removeImageGenSkeleton();
            const gallery = document.createElement('div');
            gallery.className = 'fa-image-gallery';
            imageFiles.forEach(f => gallery.appendChild(makeImageCard(f)));
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

        const iconEdit = '<svg viewBox="0 0 24 24" fill="none"><path d="M4 20h4l10-10a2 2 0 0 0-4-4L4 16v4z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        const iconStar = '<svg viewBox="0 0 24 24" fill="none"><path d="M12 3.8l2.56 5.2 5.74.84-4.15 4.05.98 5.72L12 16.9 6.87 19.6l.98-5.72L3.7 9.84l5.74-.84L12 3.8z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
        const iconTrash = '<svg viewBox="0 0 24 24" fill="none"><path d="M4 7h16M9 7V5h6v2m-8 0l1 12h8l1-12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

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
            // Чат удалён — выбрасываем его черновик ввода.
            this._clearInputDraft(chatId);
            // Сбрасываем интерфейс ТОЛЬКО если удалили именно открытый чат
            if (this.currentChat === chatId) {
                this.currentChat = null;
                if (this.ws) {
                    this._wsIntentionalClose = true;
                    this.ws.close();
                    this.ws = null;
                }
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
        if (!text) return;

        if (!this.currentChat) {
            this.currentChat = this.generateChatId();
            this.chats.push({ id: this.currentChat, last_message: text, timestamp: new Date().toISOString() });
            this.renderChats(this.chats);
            await this.openChat(this.currentChat);
        } else {
            const existing = this.chats.find(c => c.id === this.currentChat);
            if (existing) {
                existing.last_message = text;
                existing.timestamp = new Date().toISOString();
                this.renderChats(this.chats);
            }
        }

        this._botAnswered = false;
        this._hardResetStreamState(this.currentChat);

        const msgId = `msg-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
        const _optimisticFiles = isCustom ? [] : (window.FileAttach?.getFilesMeta() ?? []);
        this.addMessage({ message: text, is_from_bot: false, id: msgId, created_at: new Date().toISOString(), files: _optimisticFiles });
        this._scrollToUserMessageAndReserve();

        // Если ни одна категория инструментов не выбрана — агент работает
        // с ограниченным функционалом. Подсказываем облачком прямо в ленте.
        try {
            const _selTools = window.ToolsPanel?.getSelectedCategories() ?? [];
            if (_selTools.length === 0) this._showNoToolsHint();
        } catch (_) {}

        if (!isCustom && field) {
            this.clearInput(field);
            // Черновик отправлен — чистим и стартовый ключ, и ключ этого чата.
            this._clearInputDraft(null);
            this._clearInputDraft(this.currentChat);
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

            // ВАЖНО: файлы заливаем ДО отправки сообщения. Иначе на сервере
            // возникает гонка — kafka-сообщение долетает мгновенно и
            // wait_for_files стартует раньше, чем upload_files успеет
            // закодировать/сохранить картинку (для тяжёлых png не влезали
            // в 20с ожидания → images=[] → analyze/generate работали впустую).
            // Текстовые файлы лёгкие и успевали случайно — отсюда «git.txt
            // подгрузился, а картинка нет». Теперь порядок детерминирован.
            const _hasFiles = !isCustom && ((window.FileAttach?.getFiles().length ?? 0) > 0);
            if (_hasFiles) {
                await window.FileAttach?.upload(this.currentChat, msgId);
            }

            const resp = await fetch('/api/chats/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    chat_id:        this.currentChat,
                    message:        text,
                    message_id:     msgId,
                    type_agent:     'pm',
                    agent_model:    window.ModelPicker?.getModel() ?? 'coder',
                    selected_tools: window.ToolsPanel?.getSelectedCategories() ?? [],
                    context_size:   window.ToolsPanel?.getContextSize()   ?? 'medium',
                    loop:            window.ToolsPanel?.getLoopEnabled() ?? false,
                    loop_instruction: window.ToolsPanel?.getLoopEnabled() ? text : '',
                    has_files:      _hasFiles,
                    files: isCustom ? [] : (window.FileAttach?.getFilesMeta() ?? []),
                    ...(pollAnswer ? { poll_answer: pollAnswer } : {})
                })
            });
            try {
                const j = await resp.clone().json();
                this._activeRequestId = j?.request_id || null;
                if (j?.active_requests !== undefined) {
                    this._updateActiveRequestsUI(this.currentChat, j.active_requests);
                }
            } catch (_) {
                this._activeRequestId = null;
            }
            if (!isCustom) {
                window.FileAttach?.clear();
                this.getInputContainer()?.querySelector('#attach-btn')?.classList.remove('has-files');
            }
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

    /** Открывает картинку в модальном лайтбоксе (превью в общем стиле). */
    _openImageLightbox(url, name, imageKey) {
        // Один оверлей на весь UI — пересоздаём при каждом открытии.
        document.getElementById('image-lightbox')?.remove();

        // Расширение для имени файла берём из исходного name (там .jpg/.png от тула).
        const rawName = name || 'image';
        let ext = rawName.includes('.') ? rawName.split('.').pop().toLowerCase() : 'jpg';
        if (!/^[a-z0-9]{2,5}$/.test(ext)) ext = 'jpg';

        // Уникальное имя для скачивания: img_{первые 8 символов image_key}.{ext}.
        // image_key (вариант Б) НЕ полагается на WS-пейлоад: если он не пришёл
        // аргументом, добираем его из заголовка X-Image-Key ответа /image/...
        // (см. загрузку через fetch ниже). До этого показываем rawName.
        const buildDlName = (key) => (key
            ? `img_${String(key).slice(0, 8)}.${ext}`
            : rawName);

        const overlay = document.createElement('div');
        overlay.id = 'image-lightbox';
        overlay.className = 'image-lightbox';

        const box = document.createElement('div');
        box.className = 'image-lightbox__box';

        const img = document.createElement('img');
        img.className = 'image-lightbox__img';
        img.alt = this.escapeHtml(rawName);

        const bar = document.createElement('div');
        bar.className = 'image-lightbox__bar';
        const dlName0 = buildDlName(imageKey);
        bar.innerHTML = `
<span class="image-lightbox__name" title="${this.escapeHtml(imageKey || dlName0)}">${this.escapeHtml(dlName0)}</span>
<span class="image-lightbox__dim" title="Разрешение">…</span>
<a class="image-lightbox__dl" href="${url}" download="${this.escapeHtml(dlName0)}" title="Скачать">
  <svg viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
</a>
<button class="image-lightbox__close" type="button" title="Закрыть" aria-label="Закрыть">
  <svg viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
</button>`;

        const nameEl = bar.querySelector('.image-lightbox__name');
        const dimEl = bar.querySelector('.image-lightbox__dim');
        const dlEl = bar.querySelector('.image-lightbox__dl');

        // Разрешение проставляем после загрузки картинки (natural размеры).
        const setDim = () => {
            if (img.naturalWidth && img.naturalHeight) {
                dimEl.textContent = `${img.naturalWidth}×${img.naturalHeight}`;
            } else {
                dimEl.textContent = '';
            }
        };
        img.addEventListener('load', setDim);
        img.addEventListener('error', () => { dimEl.textContent = ''; });

        // Обновляем имя/скачивание по image_key из заголовка X-Image-Key.
        const applyKey = (key) => {
            if (!key) return;
            const dlName = buildDlName(key);
            nameEl.textContent = dlName;
            nameEl.title = key;
            dlEl.setAttribute('download', dlName);
        };

        // Грузим картинку через fetch, чтобы прочитать X-Image-Key из ответа
        // (браузер не отдаёт заголовки для обычного <img src>). Blob-URL идёт
        // и в <img>, и в href скачивания — чтобы скачивался тот же байт-в-байт
        // файл под уникальным именем.
        if (imageKey) {
            // Ключ уже есть — незачем ходить через fetch, ставим src напрямую.
            img.src = url;
        } else {
            fetch(url, { credentials: 'same-origin' })
                .then((resp) => {
                    const key = resp.headers.get('X-Image-Key');
                    return resp.blob().then((blob) => ({ key, blob }));
                })
                .then(({ key, blob }) => {
                    const objUrl = URL.createObjectURL(blob);
                    img.src = objUrl;
                    dlEl.setAttribute('href', objUrl);
                    applyKey(key);
                    // objUrl живёт до закрытия оверлея — освобождаем в close().
                    overlay._objUrl = objUrl;
                })
                .catch(() => { img.src = url; });  // фолбэк на прямой src
        }

        box.appendChild(bar);
        box.appendChild(img);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        const close = () => {
            overlay.classList.add('image-lightbox--hide');
            setTimeout(() => overlay.remove(), 200);
            document.removeEventListener('keydown', onKey);
            if (overlay._objUrl) { URL.revokeObjectURL(overlay._objUrl); overlay._objUrl = null; }
        };
        const onKey = (e) => { if (e.key === 'Escape') close(); };

        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        bar.querySelector('.image-lightbox__close').addEventListener('click', close);
        bar.querySelector('.image-lightbox__dl').addEventListener('click', (e) => e.stopPropagation());
        document.addEventListener('keydown', onKey);

        requestAnimationFrame(() => overlay.classList.add('image-lightbox--shown'));
    }


    /* ══════════════════════════════════════════
       WEBSOCKET
    ══════════════════════════════════════════ */
    connectWebSocket(chatId) {
        const key = String(chatId);

        // ВАРИАНТ 2: если для ЭТОГО чата уже есть живой сокет — не пересоздаём.
        // (могли вернуться в фоновый стримящий чат — его сокет держим открытым)
        const existing = this._sockets[key];
        if (existing &&
            (existing.readyState === WebSocket.OPEN ||
             existing.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const st = this._ensureStream(key);
        if (!st.streamingActive && !st.reasoningWrapEl) {
            st.pendingBotEl   = null;
            st.pendingBotText = '';
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${chatId}`);
        this._sockets[key] = ws;

        // ws.onopen = () => console.log(`✅ WS connected chat=${chatId}`);

        ws.onmessage = (event) => this._onWsMessage(key, event);
        ws.onerror   = (e) => console.warn(`WS error chat=${key}:`, e);
        ws.onclose   = (event) => this._onWsClose(key, ws, event);
    }

    /* Обработчик входящих сообщений WS конкретного чата (замкнут на chatId).
       Пишет в стрим ЭТОГО чата (this._streams[chatId]) напрямую — поэтому
       фоновый чат стримит корректно, даже когда открыт другой. */
    _onWsMessage(chatId, event) {
            const key = String(chatId);
            const st = this._ensureStream(key);
            // Активен ли этот чат сейчас на экране — только для него трогаем
            // общий UI (статусы, скелетоны, скролл). Фоновый пишет в свой узел.
            const isActive = String(this.currentChat) === key;
            const msg = JSON.parse(event.data);

            if (msg.type === 'agent_status') {
                // Статус-строку и скелетон рисуем только для АКТИВНОГО чата.
                if (!isActive) return;
                this.updateStatus(msg.status);
                // Скелетон генерации: показываем, когда статус — про рисование.
                const status = (msg.status || '').toLowerCase();
                if (/генерир|рису|картин|композиц|детал.*свет/.test(status)) {
                    this._showImageGenSkeleton();
                }
                return;
            }

            // Автогенерированный заголовок чата — обновляем строку чата в сайдбаре в реалтайме.
            if (msg.type === 'title_update') {
                const cid = msg.chat_id != null ? String(msg.chat_id) : null;
                const t = (msg.auto_title || '').trim();
                if (cid && t) {
                    let touched = false;
                    this.chats = (this.chats || []).map(c => {
                        if (String(c.id) === cid) { touched = true; return { ...c, auto_title: t }; }
                        return c;
                    });
                    if (touched) this.renderChats(this.chats);
                }
                return;
            }
            // Дедупликация: пользовательское сообщение уже показано оптимистично
            if (!msg.is_from_bot) {
                // Эхо пользовательского сообщения рисуем ТОЛЬКО в активном чате.
                // Иначе сообщение, отправленное в другой чат (например, из /editor),
                // приходит по фоновому сокету и ложно дорисовывается в открытый чат.
                if (!isActive) return;
                // Основной ключ дедупа — message_id (совпадает с клиентским msgId,
                // который бэк возвращает в эхе). Надёжно для длинного текста-чипа,
                // где textContent не содержит полного сообщения.
                if (msg.message_id &&
                    document.querySelector(`.user-message-wrapper[data-message-id="${msg.message_id}"], .user-message-wrapper[data-id="${msg.message_id}"]`)) {
                    return;
                }
                // Fallback по тексту — для старых сообщений/эх без message_id (с нормализацией переводов строк).
                const _normEcho = String(msg.message || '').replace(/\s+/g, ' ').trim();
                const dup = [...document.querySelectorAll('.user-message .message-text, .user-message')].find(el => {
                    const _normEl = (el.textContent || '').replace(/\s+/g, ' ').trim();
                    return _normEl && _normEcho && _normEl.includes(_normEcho);
                });
                if (dup) return;
                this.addMessage(msg, true, false);
                return;
            }

            // Финал стриминга: если уже был живой стриминг (_pendingBotEl),
            // а пришло целое Kafka-сообщение того же ответа — не создаём новый
            // пузырь через addMessage, а даём финализировать существующий ниже.
            // (ветка msg.message при живом _pendingBotEl это уже делает корректно)

            // ── Стриминг ответа бота ──────────────────────────────
            // msg.chunk  — фрагмент текста (стриминг)
            // msg.done   — финальный флаг (конец ответа)
            // msg.message — полный текст (если сервер шлёт сразу целиком)
            //
            // ВАЖНО (Вариант 2): пишем в st.* (стрим ЭТОГО чата), НЕ в this._*.
            // this._* — прокси на активный чат; для фонового чата они бы указали
            // не туда. Узел ответа фонового чата держим ОТКРЕПЛЁННЫМ от DOM:
            // рендерим прямо в st.pendingBotEl, при возврате делаем appendChild.

            // Контейнер ленты нужен только активному чату (для скролла).
            const container = isActive ? this.getMessagesContainer() : null;

            if (msg.active_requests !== undefined) {
                this._updateActiveRequestsUI(key, msg.active_requests);
            }

            // ── Управляющие маркеры стриминга (kind без нового текста) ──
            if (msg.kind === 'reasoning_end') {
                this._commitReasoningBlock(key);
                return;
            }
            if (msg.kind === 'final_commit') {
                this._promoteLiveToFinal(key);
                st.streamIsFinal = true;
                return;
            }

            if (msg.chunk !== undefined) {
                if (!st.runStartedAt) st.runStartedAt = Date.now();
                if (isActive && container) {
                    this._dedupeTurnReasoningAndActions(container, st, msg.id);
                }
                if (!st.liveBlockEl) {
                    if (!st.pendingBotEl) {
                        st.pendingBotEl = document.createElement('div');
                        st.pendingBotEl.className = 'agent-message';
                        st.pendingBotEl.dataset.id = msg.id || '';
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

                st.streamBuffer += msg.chunk;
                if (!st.streamIsFinal) {
                    this._setReasoningHeaderLabel(key, (st.streamShown || '') + (st.streamBuffer || ''), false);
                }

            } else if (msg.done || msg.message !== undefined) {
                const fullText = msg.message ?? st.pendingBotText;
                const incomingDbId = msg.id != null ? String(msg.id) : null;
                const incomingReqId = msg.message_id ? String(msg.message_id) : null;
                if (isActive && container && !st.pendingBotEl) {
                    const existingBot = [...container.querySelectorAll(':scope > .agent-message')].find(el => {
                        const eid = String(el.dataset.id || '');
                        const emid = String(el.dataset.messageId || '');
                        return (incomingDbId && eid === incomingDbId)
                            || (incomingReqId && (eid === incomingReqId || emid === incomingReqId));
                    });
                    if (existingBot) {
                        if (incomingDbId) existingBot.dataset.id = incomingDbId;
                        if (incomingReqId) existingBot.dataset.messageId = incomingReqId;
                        if (!existingBot.querySelector(':scope > .message-actions')) {
                            existingBot.appendChild(this.createMessageActions(existingBot.dataset.id, fullText, true));
                        }
                        this._removeBotPlaceholder();
                        this._clearAllStatuses();
                        st.streamingActive = false;
                        this._onRunFinished(msg.loop === true);
                        return;
                    }
                }
                if (isActive && container && st.pendingBotEl) {
                    this._dedupeTurnReasoningAndActions(container, st, msg.id || msg.message_id);
                }

                if (st.pendingBotEl) {
                    if (!st.liveBlockEl) {
                        st.liveBlockEl = document.createElement('div');
                        st.liveBlockEl.className = 'live-block';
                        st.pendingBotEl.appendChild(st.liveBlockEl);
                        st.streamShown = '';
                        st.streamBuffer = '';
                    }
                    const alreadyQueued = (st.streamShown || '') + (st.streamBuffer || '');
                    if (fullText.startsWith(alreadyQueued)) {
                        st.streamBuffer += fullText.slice(alreadyQueued.length);
                    } else if (fullText.length > alreadyQueued.length) {
                        st.streamBuffer += fullText.slice(alreadyQueued.length);
                    }

                    st.streamFinalData = {
                        fullText,
                        files: msg.files,
                        message_id: msg.message_id,
                        loop: msg.loop === true,
                    };
                    st.streamDone = true;
                    this._startStreamLoop(key);
                } else if (isActive) {
                    // Целое сообщение без стриминга — обычный addMessage (только активный)
                    this.addMessage({ ...msg, message: fullText }, true, true);
                    st.botAnswered = true;
                    this._removeBotPlaceholder();
                    this._clearAllStatuses();
                    st.streamingActive = false;
                    if (this._chatStatuses) delete this._chatStatuses[key];
                    this._onRunFinished(msg.loop === true);
                } else {
                    // Фоновый чат без живого стрима, но пришёл финал — сохраняем
                    // как готовый узел, чтобы отрисовать при возврате.
                    const el = document.createElement('div');
                    el.className = 'agent-message';
                    el.dataset.id = msg.message_id || msg.id || '';
                    st.pendingBotEl = el;
                    st.liveBlockEl = document.createElement('div');
                    st.liveBlockEl.className = 'live-block';
                    el.appendChild(st.liveBlockEl);
                    st.streamShown = fullText;
                    st.streamBuffer = '';
                    st.streamFinalData = { fullText, files: msg.files, message_id: msg.message_id, loop: msg.loop === true };
                    st.streamDone = true;
                    this._finalizeStream(key);
                }
            }
    }

    /* Обработчик закрытия WS конкретного чата. */
    _onWsClose(chatId, ws, event) {
            const key = String(chatId);
            const st = this._getStream(key);
            const isActive = String(this.currentChat) === key;

            // Если это НЕ актуальный сокет для чата (уже пересоздан) — игнор.
            if (this._sockets[key] && this._sockets[key] !== ws) return;

            // При обрыве/переподключении WS НЕ финализируем незавершённый узел и НЕ вешаем
            // .message-actions посреди рассуждений — иначе блок рассуждений расщепится
            // на два с .message-actions между ними. Финализируем только если ответ уже помечен done.
            if (st && st.pendingBotEl && st.streamDone) {
                this._finalizeStream(key);
            }

            // Намеренное закрытие (переключение чата и т.п.) — не переподключаемся.
            if (ws._intentionalClose) {
                delete this._sockets[key];
                return;
            }

            // Автопереподключение (только для АКТИВНОГО чата):
            // - только если закрытие не было намеренным
            // - только если не нормальное закрытие (code 1000)
            // Фоновые чаты не реконнектим — если их сокет упал, стрим финализируется
            // тем, что накопили; лишние соединения не плодим.
            const isNormalClose = event.code === 1000;
            delete this._sockets[key];
            if (isActive && this.currentChat && !isNormalClose) {
                clearTimeout(this._wsReconnectTimer);
                this._wsReconnectAttempts = (this._wsReconnectAttempts || 0) + 1;
                // Exponential backoff: 2s, 4s, 8s, 16s, cap 30s
                const delay = Math.min(2000 * Math.pow(2, this._wsReconnectAttempts - 1), 30000);
                this._wsReconnectTimer = setTimeout(() => {
                    if (String(this.currentChat) === key) {
                        this.connectWebSocket(this.currentChat);
                    }
                }, delay);
            } else {
                // Штатное закрытие — сбрасываем счётчик попыток
                this._wsReconnectAttempts = 0;
            }
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
        st = st || this._resolveStream(null);
        const CHARS_PER_FRAME = 7;      // базовый темп (чуть быстрее, ~420 симв/сек)
        const buffered = (st && st.streamBuffer) ? st.streamBuffer.length : 0;

        // Автоускорение: чем больше накопилось, тем крупнее порция.
        let step = CHARS_PER_FRAME;
        if (buffered > 400)  step = Math.min(16, CHARS_PER_FRAME + Math.floor(buffered / 150));
        if (buffered > 2000) step = Math.min(48, Math.floor(buffered / 50));

        return step;
    }

    /* Резолвер стрима: если передан chatId — работаем с ЕГО стримом (нужно
       для фонового чата); иначе — активный (через прокси / currentChat).
       Возвращает объект стрима, либо null. */
    _resolveStream(chatId) {
        if (chatId != null) return this._ensureStream(chatId);
        return this._ensureStream(this.currentChat);
    }

    /* Активен ли данный чат на экране (для скролла/DOM в ленте). */
    _isActiveChat(chatId) {
        if (chatId == null) return true;
        return String(this.currentChat) === String(chatId);
    }

    _startStreamLoop(chatId) {
        const st = this._resolveStream(chatId);
        if (!st) return;
        if (st.streamRaf) return;  // цикл уже крутится для этого чата

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
        if (!st || !st.liveBlockEl) return;
        // Скролл только для активного чата (фоновый пишет в открепленный узел).
        const container = this._isActiveChat(chatId) ? this.getMessagesContainer() : null;

        let html = marked.parse(st.streamShown || '');
        if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
            ADD_TAGS: ['table','thead','tbody','tr','th','td'],
            ADD_ATTR: ['colspan','rowspan','align']
        });
        st.liveBlockEl.innerHTML = html;
        st.liveBlockEl.querySelectorAll('a[href]').forEach(a => {
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer');
        });

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
        return `<div class="wave-indicator">`
             + `<canvas class="placeholder-graph" aria-hidden="true"></canvas>`
             + `</div>`;
    }

    _stopPlaceholderGraph() {
        if (this._placeholderGraphRAF) {
            cancelAnimationFrame(this._placeholderGraphRAF);
            this._placeholderGraphRAF = null;
        }
        if (this._placeholderGraphCleanup) {
            this._placeholderGraphCleanup();
            this._placeholderGraphCleanup = null;
        }
    }

    _initPlaceholderGraph(placeholder) {
        this._stopPlaceholderGraph();
        const cv = placeholder?.querySelector('.placeholder-graph');
        if (!cv) return;

        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const W = 116, H = 28, L = 40;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        cv.width = W * dpr;
        cv.height = H * dpr;
        cv.style.width = W + 'px';
        cv.style.height = H + 'px';

        const g = cv.getContext('2d');
        if (!g) return;
        g.setTransform(dpr, 0, 0, dpr, 0, 0);

        const root = document.documentElement;
        const parseAccent = () => {
            const cs = getComputedStyle(root);
            let v = cs.getPropertyValue('--accent').trim() || '#6366f1';
            let r, gg, b;
            if (v.startsWith('#')) {
                if (v.length === 4) v = '#' + v[1]+v[1]+v[2]+v[2]+v[3]+v[3];
                r = parseInt(v.slice(1,3),16); gg = parseInt(v.slice(3,5),16); b = parseInt(v.slice(5,7),16);
            } else {
                const mm = v.match(/\d+/g) || [99,102,241];
                r = +mm[0]; gg = +mm[1]; b = +mm[2];
            }
            const clamp = (x) => Math.max(0, Math.min(255, Math.round(x)));
            const shade = (k) => `${clamp(r*k)},${clamp(gg*k)},${clamp(b*k)}`;
            return [`${r},${gg},${b}`, shade(1.18), shade(0.82)];
        };
        let COLS = parseAccent();

        const N = 11;
        const nodes = Array.from({ length: N }, (_, i) => ({
            x: 8 + (i / Math.max(1, N - 1)) * (W - 16) + (Math.random() - 0.5) * 6,
            y: 5 + Math.random() * (H - 10),
            vx: (Math.random() - 0.5) * 0.42 || 0.22,
            vy: (Math.random() - 0.5) * 0.34 || 0.18,
            r: 1.5 + (i % 3) * 0.55,
            c: COLS[i % 3],
        }));

        const m = { x: 0, y: 0, active: false };
        const onMove = (e) => { m.x = e.clientX; m.y = e.clientY; m.active = true; };
        const onLeave = () => { m.active = false; };
        document.addEventListener('pointermove', onMove, { passive: true });
        document.addEventListener('pointerleave', onLeave);
        this._placeholderGraphCleanup = () => {
            document.removeEventListener('pointermove', onMove);
            document.removeEventListener('pointerleave', onLeave);
        };

        let frames = 0;
        const vk = reduce ? 0.55 : 1;
        const frame = () => {
            if (!placeholder.isConnected) {
                this._stopPlaceholderGraph();
                return;
            }
            if ((frames & 63) === 0) {
                COLS = parseAccent();
                for (let i = 0; i < nodes.length; i++) nodes[i].c = COLS[i % 3];
            }
            frames++;

            g.clearRect(0, 0, W, H);
            const t = frames * 0.045;
            let cx = W * 0.5 + Math.sin(t) * (W * 0.38);
            let cy = H * 0.5 + Math.cos(t * 0.8) * (H * 0.26);
            if (m.active) {
                const rect = cv.getBoundingClientRect();
                const mx = m.x - rect.left;
                const my = m.y - rect.top;
                if (mx >= -40 && mx <= W + 40 && my >= -40 && my <= H + 40) {
                    cx = mx;
                    cy = my;
                }
            }

            const pad = 3.5;
            for (const n of nodes) {
                n.x += n.vx * vk;
                n.y += n.vy * vk;
                if (n.x < pad) { n.x = pad; n.vx = Math.abs(n.vx); }
                if (n.x > W - pad) { n.x = W - pad; n.vx = -Math.abs(n.vx); }
                if (n.y < pad) { n.y = pad; n.vy = Math.abs(n.vy); }
                if (n.y > H - pad) { n.y = H - pad; n.vy = -Math.abs(n.vy); }
            }

            const GR = 46;
            for (let i = 0; i < nodes.length; i++) {
                const a = nodes[i];
                for (let j = i + 1; j < nodes.length; j++) {
                    const b = nodes[j];
                    const d = Math.hypot(a.x - b.x, a.y - b.y);
                    if (d < L) {
                        const near = Math.max(0, 1 - Math.hypot((a.x + b.x) / 2 - cx, (a.y + b.y) / 2 - cy) / GR);
                        const alpha = (1 - d / L) * (0.28 + near * 0.55);
                        g.strokeStyle = `rgba(${a.c}, ${alpha})`;
                        g.lineWidth = 1 + near * 0.85;
                        g.beginPath();
                        g.moveTo(a.x, a.y);
                        g.lineTo(b.x, b.y);
                        g.stroke();
                    }
                }
            }
            for (const n of nodes) {
                const near = Math.max(0, 1 - Math.hypot(n.x - cx, n.y - cy) / GR);
                g.fillStyle = `rgba(${n.c}, ${0.58 + near * 0.42})`;
                g.beginPath();
                g.arc(n.x, n.y, n.r + near * 1.15, 0, Math.PI * 2);
                g.fill();
            }

            this._placeholderGraphRAF = requestAnimationFrame(frame);
        };
        this._placeholderGraphRAF = requestAnimationFrame(frame);
    }

    _flattenWaveIndicator() {
        const ind = document.querySelector('#agent-response-placeholder .wave-indicator');
        if (!ind) return;
        ind.classList.add('wave-flatten');
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
            .replace(/[`*_~#>]+/g, '')
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
            if (st._pollTick % 3 === 0 && chatId) {
                fetch(`/api/chats/${chatId}/run_status`, { credentials: 'include' })
                    .then(r => r.ok ? r.json() : null)
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

    /* Гарантирует, что узел ответа/рассуждений бота стоит строго ПОСЛЕ последнего .user-message-wrapper */
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

    /* Если новое .user-message добавилось в контейнер, где уже висел ранний узел рассуждений/плейсхолдер —
       переносим плейсхолдер и активный узел бота строго НИЖЕ этого user-message (плейсхолдер сверху рассуждений). */
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

    /* Защита от двойного рендеринга .reasoning-block и расщепления через промежуточный .message-actions */
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

        // Сливаем дублирующиеся .agent-message одного хода воедино
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

        // Дедупликация .reasoning-block внутри primary: оставляем ровно ОДИН блок
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

        // Удаляем промежуточные .message-actions, если ответ ещё не завершён или если есть дубликаты
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
                    let html = marked.parse(String(rawStep));
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
                    let html = marked.parse(String(rawStep));
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

    /* Гарантирует существование ЕДИНОГО свёртка «Размышления» для текущего
       ответа и возвращает его .reasoning-body — контейнер, куда дописываются
       все мысли между вызовами тулз. Не плодим блок на каждый шаг. */
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
        if (!st || !st.liveBlockEl) return;

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

        let html = marked.parse(text);
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
            this._setReasoningHeaderLabel(chatId, stepPlain, false);
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
        if (!st) return;
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
        // ВАЖНО (мультиплекс, Вариант 2): сбрасываем стрим ТОЛЬКО указанного
        // чата. Если chatId не передан — берём текущий. Фоновые стримящие
        // чаты этот сброс не трогает — их пузыри и буферы живут в своих
        // this._streams[chatId] и дописываются из фоновых сокетов.
        const st = this._resolveStream(chatId);
        if (!st) return;
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
        if (!st || !st.pendingBotEl) return;
        const key = chatId != null ? String(chatId) : String(this.currentChat);
        const isActive = this._isActiveChat(key);

        // Страховка: если генерация картинки завершилась (успех или ошибка),
        // а превью так и не пришло — снимаем висящий скелетон на финале.
        // Скелетон/статусы — общий UI, трогаем только для активного чата.
        if (isActive) this._removeImageGenSkeleton();

        const data = st.streamFinalData || {};
        const fullText = data.fullText ?? st.streamShown ?? '';

        // Финальный полный рендер финального ответа — в живой блок (если он есть).
        // reasoning-блоки остаются выше нетронутыми.
        const targetBlock = st.liveBlockEl || (() => {
            const b = document.createElement('div');
            b.className = 'live-block';
            st.pendingBotEl.appendChild(b);
            return b;
        })();
        // Гарантия: финальный ответ всегда в обычном стиле, даже если маркер
        // final_commit не пришёл (например обрыв WS) — снимаем reasoning-стиль.
        targetBlock.className = 'live-block';
        if (data.message_id) st.pendingBotEl.dataset.messageId = data.message_id;

        this._renderBotStructuredContent(targetBlock, fullText, {
            botMessageId: data.message_id || st.pendingBotEl?.dataset?.messageId || st.pendingBotEl?.dataset?.id || '',
            pollAnswers: data.poll_answers || [],
        });

        // Файлы / actions вешаем на общую обёртку ответа. Работает и для
        // фонового чата — узел откреплён, но полностью готов к вставке.
        if (data.files?.length) {
            st.pendingBotEl.appendChild(
                this._renderBotFiles(data.files, data.message_id)
            );
        }
        if (data.message_id) {
            st.pendingBotEl.dataset.id = data.message_id;
        }
        this._stopReasoningTimer(chatId);
        const activeContainer = this._isActiveChat(key) ? this.getMessagesContainer() : null;
        if (activeContainer) {
            this._ensureBotAfterLastUserMessage(activeContainer, st.pendingBotEl);
            this._dedupeTurnReasoningAndActions(activeContainer, st);
        }
        this._applyCodeHeaders(st.pendingBotEl);
        st.pendingBotEl.querySelectorAll(':scope > .reasoning-live').forEach(r => r.remove());
        st.pendingBotEl.querySelectorAll(':scope > .message-actions').forEach(a => a.remove());
        const actions = this.createMessageActions(st.pendingBotEl.dataset.id, fullText, true);
        st.pendingBotEl.appendChild(actions);
        st.pendingBotEl.style.transition = '';
        st.pendingBotEl.style.opacity    = '';

        // Сброс всего стрим-состояния ЭТОГО чата
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
        st.botAnswered     = true;
        if (st.streamRaf) { cancelAnimationFrame(st.streamRaf); st.streamRaf = null; }

        // Общий UI (плейсхолдер-волна, статусы, стоп-кнопка) — только для
        // активного чата. Для фонового финал тихо сложился в откреплённый узел.
        if (isActive) {
            this._flattenWaveIndicator();
            setTimeout(() => this._removeBotPlaceholder(), 500);
            this._clearAllStatuses();
            this._onRunFinished(data.loop === true);
        }
        if (this._chatStatuses) delete this._chatStatuses[key];
    }

    _setSendButtonMode(mode = 'send') {
        const container = this.getInputContainer();
        const sendBtn = container?.querySelector('#send-btn');
        if (!sendBtn) return;
        if (mode === 'stop') {
            sendBtn.disabled = false;
            sendBtn.classList.add('active');
            sendBtn.setAttribute('aria-label', 'Остановить');
            sendBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><path d="M9 9l6 6M15 9l-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
            return;
        }
        sendBtn.setAttribute('aria-label', 'Отправить');
        sendBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none"><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
        const hasText = this.getInputText().length > 0;
        sendBtn.disabled = !hasText;
        sendBtn.classList.toggle('active', hasText);
    }

    async stopCurrentRun() {
        if (this._runState !== 'running') return;
        this._runState = 'stopping';
        try {
            await fetch('/api/chats/stop_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ request_id: this._activeRequestId || '__loop__', chat_id: this.currentChat })
            });
        } catch (e) {
            console.warn('stopCurrentRun error:', e);
        }
    }

    _onRunFinished(keepRunning = false) {
        // Вариант A (самоцикл): если финальный ответ помечен loop:true и
        // пользователь НЕ нажимал стоп — петля жива, следующий тик придёт
        // через консьюмер. Кнопку-стоп НЕ гасим, держим running.
        // Как только придёт финал без loop (обычный ход) или после стопа
        // (_runState==='stopping') — гасим как обычно.
        if (keepRunning && this._runState === 'running') {
            // держим стоп-кнопку, request_id текущего тика больше не актуален
            // (следующий тик придёт со своим), но стоп гасим по chat_id — ок.
            this._setSendButtonMode('stop');
            return;
        }
        this._runState = 'idle';
        this._activeRequestId = null;
        this._setSendButtonMode('send');
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
    // Принудительно перепривязать FileAttach к инпуту агента.
    // Нужно при возврате с общего чата: renderMessageInput мог выйти
    // на гарде и не вызвать FileAttach.init(), из-за чего скрепка/drop/paste
    // остаются в контексте общего чата (файл уходил не в тот раздел).
    _rebindFileAttachToAgent() {
        const container = this.getInputContainer();
        if (!container) return;
        const attachBtn = container.querySelector('#attach-btn')
                       || container.querySelector('#start-attach-btn');
        if (!attachBtn) return; // стартовый экран без инпута — нечего привязывать
        try { window.FileAttach?.init(attachBtn, container); } catch (e) { /* noop */ }
    }

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

        const username = this.currentUser?.name?.split(' ')[0]
                      || this.currentUser?.username
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
                    <!-- tools-trigger вставляется сюда через ToolsPanel.inject() -->
                    <button class="attach-btn" id="start-attach-btn" title="Прикрепить файл" type="button">
                        <svg viewBox="0 0 24 24" fill="none">
                            <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19
                                     a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
                                  stroke="currentColor" stroke-width="2"
                                  stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </button>
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

    <p class="start-footer-note">ИИ может ошибаться — перепроверяйте важную информацию</p>

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
            <button class="attach-btn" id="attach-btn" title="Прикрепить файл" type="button">
                <svg viewBox="0 0 24 24" fill="none">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19
                             a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
                          stroke="currentColor" stroke-width="2"
                          stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </button>

            <span class="input-footer-note">ИИ может ошибаться — перепроверяйте важную информацию</span>

            <button class="chat-send-btn" id="send-btn" disabled aria-label="Отправить">
                <svg viewBox="0 0 24 24" fill="none">
                    <path d="M5 12h14M13 6l6 6-6 6"
                          stroke="currentColor" stroke-width="2"
                          stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
            </button>
        </div>

    </div>
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

            let html = marked.parse(accumulated);
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
            const actions = this.createMessageActions(msgId, rawText, true);
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

    /** Ждёт пока this.ws перейдёт в OPEN (max 5s) */
    _waitForWS(timeout = 5000) {
        return new Promise((resolve, reject) => {
            if (!this.ws) return resolve(); // нет WS — пропускаем
            if (this.ws.readyState === WebSocket.OPEN) return resolve();
            const deadline = Date.now() + timeout;
            const check = () => {
                if (!this.ws || this.ws.readyState === WebSocket.OPEN) return resolve();
                // Если WS уже мёртв — нет смысла ждать до таймаута
                if (this.ws.readyState === WebSocket.CLOSED ||
                    this.ws.readyState === WebSocket.CLOSING) {
                    return reject(new Error('WS closed before open'));
                }
                if (Date.now() > deadline) return reject(new Error('WS connect timeout'));
                setTimeout(check, 50);
            };
            check();
        });
    }

    _animateStartHeader(container) {
        const header = container.querySelector('.start-header');
        if (!header) return;

        // Оборачиваем каждый символ в <span class="letter">
        // Пробел заменяем на неразрывный чтобы не схлопывался между inline-block
        const wrap = (el) => {
            el.childNodes.forEach(node => {
                if (node.nodeType === Node.TEXT_NODE) {
                    const frag = document.createDocumentFragment();
                    [...node.textContent].forEach(ch => {
                        const s = document.createElement('span');
                        s.className = 'letter';
                        s.textContent = ch === ' ' ? '\u00A0' : ch;
                        frag.appendChild(s);
                    });
                    node.replaceWith(frag);
                } else if (node.nodeType === Node.ELEMENT_NODE) {
                    wrap(node);
                }
            });
        };

        header.querySelectorAll('.start-greeting, .start-title').forEach(wrap);

        // Назначаем transition-delay по порядку
        let i = 0;
        header.querySelectorAll('.letter').forEach(s => {
            s.style.transitionDelay = `${i * 28}ms`;
            i++;
        });

        // Запускаем в следующем кадре
        requestAnimationFrame(() => {
            requestAnimationFrame(() => header.classList.add('animate'));
        });
    }

    /* ══════════════════════════════════════════
       СТАРТОВЫЙ ЭКРАН: живой фон «за стеклом».
       Граф (canvas) на весь экран БЕЗ цветных пятен — цвет узлов жёстко
       завязан на --accent (3 тона от базового), меняется с палитрой.
       Параллакс: граф > наклон панели. reduced-motion / touch —
       деградируют мягко. Один rAF, курсор сглажен lerp-ом.
    ══════════════════════════════════════════ */
    _initStartGraph(container) {
        // Снять предыдущий цикл, если был (возврат на стартовый экран)
        if (this._startGraphRAF) { cancelAnimationFrame(this._startGraphRAF); this._startGraphRAF = null; }
        if (this._startGraphCleanup) { this._startGraphCleanup(); this._startGraphCleanup = null; }

        const screen = container.querySelector('.chat-start-screen');
        const cv = container.querySelector('#start-graph');
        const body = container.querySelector('.start-body');
        if (!screen || !cv || !body) return;

        requestAnimationFrame(() => screen.classList.add('bg-ready'));

        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        const interactive = window.matchMedia('(hover: hover) and (pointer: fine)').matches && !reduce;

        const CONFIG = {
            nodes: 64, linkDist: 132, cursorRadius: 150, cursorForce: 0.9,
            glowRadius: 180, cursorMode: 'repel', cursorLerp: 0.12,
            graphParallax: 14, tilt: 2.5,
        };

        const g = cv.getContext('2d');
        const root = document.documentElement;
        let W = 0, H = 0, dpr = 1, nodes = [];
        const m = { x: 0, y: 0, sx: 0, sy: 0, active: false, seeded: false };

        // Цвет узлов жёстко завязан на --accent (меняется с палитрой).
        // 3 тона: базовый + светлее/темнее для разнообразия.
        const parseAccent = () => {
            const cs = getComputedStyle(root);
            let v = cs.getPropertyValue('--accent').trim() || '#6366f1';
            let r, gg, b;
            if (v.startsWith('#')) {
                if (v.length === 4) v = '#' + v[1]+v[1]+v[2]+v[2]+v[3]+v[3];
                r = parseInt(v.slice(1,3),16); gg = parseInt(v.slice(3,5),16); b = parseInt(v.slice(5,7),16);
            } else {
                const mm = v.match(/\d+/g) || [99,102,241];
                r = +mm[0]; gg = +mm[1]; b = +mm[2];
            }
            const clamp = (x) => Math.max(0, Math.min(255, Math.round(x)));
            const shade = (k) => `${clamp(r*k)},${clamp(gg*k)},${clamp(b*k)}`;
            return [`${r},${gg},${b}`, shade(1.18), shade(0.82)];
        };
        let COLS = parseAccent();

        function resize() {
            const r = screen.getBoundingClientRect();
            dpr = Math.min(window.devicePixelRatio || 1, 2);
            W = Math.max(1, r.width); H = Math.max(1, r.height);
            cv.width = W * dpr; cv.height = H * dpr;
            cv.style.width = W + 'px'; cv.style.height = H + 'px';
            g.setTransform(dpr, 0, 0, dpr, 0, 0);
            const n = Math.round(CONFIG.nodes * Math.min(1, (W * H) / (1440 * 900)) + 16);
            nodes = Array.from({ length: n }, (_, i) => ({
                x: Math.random() * W, y: Math.random() * H,
                vx: (Math.random() - .5) * .22, vy: (Math.random() - .5) * .22,
                r: 1.5 + Math.random() * 2.3,
                c: COLS[i % 3],
            }));
        }

        // тилт панели — только мышь (desktop); реакция узлов графа — всегда (вкл. touch)
        const seed = () => { if (!m.seeded) { m.sx = m.x; m.sy = m.y; m.seeded = true; } };
        const onMove = (e) => { m.x = e.clientX; m.y = e.clientY; m.active = true; seed(); };
        const onLeave = () => {
            m.active = false;
            body.style.setProperty('--rx', '0deg');
            body.style.setProperty('--ry', '0deg');
        };
        const touchPoint = (e) => {
            const t = e.touches && e.touches[0];
            if (!t) return;
            m.x = t.clientX; m.y = t.clientY; m.active = true; seed();
        };
        const onTouchEnd = () => { m.active = false; };
        const onResize = () => resize();

        if (interactive) {
            document.addEventListener('pointermove', onMove, { passive: true });
            document.addEventListener('pointerleave', onLeave);
        }
        // тач-взаимодействие с точками на любом устройстве (тап/свайп по экрану)
        if (!reduce) {
            screen.addEventListener('touchstart', touchPoint, { passive: true });
            screen.addEventListener('touchmove', touchPoint, { passive: true });
            screen.addEventListener('touchend', onTouchEnd, { passive: true });
            screen.addEventListener('touchcancel', onTouchEnd, { passive: true });
        }
        window.addEventListener('resize', onResize);

        this._startGraphCleanup = () => {
            document.removeEventListener('pointermove', onMove);
            document.removeEventListener('pointerleave', onLeave);
            screen.removeEventListener('touchstart', touchPoint);
            screen.removeEventListener('touchmove', touchPoint);
            screen.removeEventListener('touchend', onTouchEnd);
            screen.removeEventListener('touchcancel', onTouchEnd);
            window.removeEventListener('resize', onResize);
        };

        resize();

        const drawGraph = () => {
            g.clearRect(0, 0, W, H);
            const L = CONFIG.linkDist, RR = CONFIG.cursorRadius, GR = CONFIG.glowRadius;
            const rect = screen.getBoundingClientRect();
            const cx = m.sx - rect.left, cy = m.sy - rect.top;
            const live = m.active;  // реакция узлов на курсор/тап — на любом устройстве

            for (const n of nodes) {
                if (!reduce) {
                    n.x += n.vx; n.y += n.vy;
                    if (n.x < -20) n.x = W + 20; if (n.x > W + 20) n.x = -20;
                    if (n.y < -20) n.y = H + 20; if (n.y > H + 20) n.y = -20;
                    if (live) {
                        const dx = n.x - cx, dy = n.y - cy, dist = Math.hypot(dx, dy);
                        if (dist < RR && dist > 0.1) {
                            const f = (1 - dist / RR) * CONFIG.cursorForce;
                            n.x += (dx / dist) * f; n.y += (dy / dist) * f;
                        }
                    }
                }
            }

            for (let i = 0; i < nodes.length; i++) {
                const a = nodes[i];
                for (let j = i + 1; j < nodes.length; j++) {
                    const b = nodes[j];
                    const d = Math.hypot(a.x - b.x, a.y - b.y);
                    if (d < L) {
                        const near = live ? Math.max(0, 1 - Math.hypot((a.x + b.x) / 2 - cx, (a.y + b.y) / 2 - cy) / GR) : 0;
                        const alpha = (1 - d / L) * (.26 + near * .5);
                        g.strokeStyle = `rgba(${a.c}, ${alpha})`;
                        g.lineWidth = 1 + near * .8;
                        g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
                    }
                }
            }
            for (const n of nodes) {
                const near = live ? Math.max(0, 1 - Math.hypot(n.x - cx, n.y - cy) / GR) : 0;
                g.fillStyle = `rgba(${n.c}, ${.55 + near * .45})`;
                g.beginPath(); g.arc(n.x, n.y, n.r + near * 1.5, 0, Math.PI * 2); g.fill();
            }
        };

        let frames = 0;
        const frame = () => {
            if (m.seeded) {
                m.sx += (m.x - m.sx) * CONFIG.cursorLerp;
                m.sy += (m.y - m.sy) * CONFIG.cursorLerp;
            }

            if (interactive && m.active) {
                const nx = (m.sx / window.innerWidth - .5) * 2;
                const ny = (m.sy / window.innerHeight - .5) * 2;
                const gp = CONFIG.graphParallax;
                cv.style.transform = `translate3d(${(-nx * gp).toFixed(1)}px, ${(-ny * gp).toFixed(1)}px, 0)`;

                const rect = body.getBoundingClientRect();
                const bx = (m.sx - (rect.left + rect.width / 2)) / (rect.width / 2);
                const by = (m.sy - (rect.top + rect.height / 2)) / (rect.height / 2);
                body.style.setProperty('--ry', (Math.max(-1, Math.min(1, bx)) * CONFIG.tilt).toFixed(2) + 'deg');
                body.style.setProperty('--rx', (-Math.max(-1, Math.min(1, by)) * CONFIG.tilt).toFixed(2) + 'deg');
                body.style.setProperty('--mx', ((m.sx - rect.left) / rect.width * 100).toFixed(1) + '%');
                body.style.setProperty('--my', ((m.sy - rect.top) / rect.height * 100).toFixed(1) + '%');
            }

            if ((frames++ & 63) === 0) {
                COLS = parseAccent();
                for (let i = 0; i < nodes.length; i++) nodes[i].c = COLS[i % 3];
            }
            drawGraph();
            this._startGraphRAF = requestAnimationFrame(frame);
        };
        this._startGraphRAF = requestAnimationFrame(frame);
    }

    /* ══════════════════════════════════════════
       INPUT HANDLERS
    ══════════════════════════════════════════ */
    // Получить текст из поля — поддержка и textarea и contenteditable
    getInputText() {
        const container = this.getInputContainer();
        const field = container?.querySelector('#message-input');
        if (!field) return '';
        const raw = (field.tagName === 'TEXTAREA')
            ? field.value
            : this._readInputWithChips(field);
        // Снимаем невидимые маркеры скилл-блока (skills.js _setSkillInInput),
        // чтобы они не улетали в агента.
        return raw.replace(/\u2063\[\/?skill\]\u2063/g, '').trim();
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

    /* ── Черновики поля ввода per-chat ──────────────────────────────
       При переключении между чатами (и на стартовый экран) недописанный
       текст поля #message-input сохраняется и восстанавливается по ключу
       чата. Ключ '__start__' — стартовый экран (currentChat == null).
       Храним ПЛОСКИЙ текст (с раскрытыми чипами), как в разделах. */
    _inputDraftKey(chatId) {
        return (chatId == null || chatId === '') ? '__start__' : String(chatId);
    }

    _saveInputDraft(chatId) {
        if (!this._inputDrafts) this._inputDrafts = {};
        const text = this.getInputText();
        const key = this._inputDraftKey(chatId);
        if (text) this._inputDrafts[key] = text;
        else delete this._inputDrafts[key];
    }

    _restoreInputDraft(chatId) {
        if (!this._inputDrafts) return;
        const key = this._inputDraftKey(chatId);
        const text = this._inputDrafts[key];
        if (!text) return;
        const field = this.getInputContainer()?.querySelector('#message-input');
        if (!field) return;
        this.setInputText(field, text);
        // Синхронизируем кнопку отправки/подсказки под восстановленный текст.
        try { field.dispatchEvent(new Event('input')); } catch (_) {}
    }

    _clearInputDraft(chatId) {
        if (!this._inputDrafts) return;
        delete this._inputDrafts[this._inputDraftKey(chatId)];
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

    attachInputHandlers() {
        const container = this.getInputContainer();
        const field   = container?.querySelector('#message-input');
        const sendBtn = container?.querySelector('#send-btn');
        if (!field || !sendBtn) return;

        const onInput = () => {
            if (this._runState !== "idle") {
                sendBtn.disabled = false;
                sendBtn.classList.add('active');
                return;
            }
            const hasText = this.getInputText().length > 0;
            sendBtn.classList.toggle('active', hasText);
            sendBtn.disabled = !hasText;
        };

        field.addEventListener('input', onInput);

        // Вставка ДЛИННОГО текста (>1000 симв) в поле ввода: вместо простыни
        // текста втыкаем компактный чип, полный текст храним отдельно.
        // Пользователь может печатать текст до и после чипа.
        // Короткий текст вставляется ПРИНУДИТЕЛЬНО как plain-text: без этого
        // браузер тянет из буфера text/html (цвета/шрифты/pre-code из IDE),
        // и в поле оказывается разноцветный код. Нам нужен только чистый текст.
        const INPUT_COLLAPSE_LIMIT = 1000;
        field.addEventListener('paste', (e) => {
            if (field.contentEditable !== 'true') return;
            const cd = e.clipboardData || window.clipboardData;
            // Берём именно plain-text из буфера, игнорируя text/html.
            const pasted = cd ? (cd.getData('text/plain') || cd.getData('text') || '') : '';
            if (!pasted) {
                // Нет текстовой части (например, только картинка/файл) —
                // блокируем дефолтную HTML-вставку, файлами займётся FileAttach.
                e.preventDefault();
                return;
            }
            e.preventDefault();
            if (pasted.length > INPUT_COLLAPSE_LIMIT) {
                // Длинный текст — сворачиваем в чип.
                this._insertInputChip(field, pasted);
            } else {
                // Короткий — вставляем как обычный текст без форматирования.
                document.execCommand('insertText', false, pasted);
            }
            onInput();
        });

        field.addEventListener('keydown', (e) => {
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

        // Placeholder для contenteditable
        if (field.contentEditable === 'true') {
            field.addEventListener('focus', () => field.classList.add('focused'));
            field.addEventListener('blur',  () => field.classList.remove('focused'));
        }

        sendBtn.onclick = () => this.sendMessage();
        this._setSendButtonMode(this._runState === "idle" ? "send" : "stop");

        // Кнопка прикрепления файла
        const attachBtn = container?.querySelector('#attach-btn')
                       || container?.querySelector('#start-attach-btn');
        window.FileAttach?.init(attachBtn, container);
    }


    /* ══════════════════════════════════════════
       VISIBILITY / ONLINE RECONNECT
    ══════════════════════════════════════════ */
    initVisibilityReconnect() {
        // Жив ли сокет АКТИВНОГО чата. Источник истины — this._sockets[key],
        // а не легаси-поле this.ws (при мультиплексе оно почти всегда undefined).
        const activeWsAlive = () => {
            const key = String(this.currentChat);
            const ws = this._sockets && this._sockets[key];
            return ws && (ws.readyState === WebSocket.OPEN ||
                          ws.readyState === WebSocket.CONNECTING);
        };

        // Пересоздать сокет при необходимости и ВСЕГДА авторитетно
        // пересинхронизировать UI по серверу: при обрыве сети onclose гасит
        // Stop-кнопку и открывает ввод, а генерация на бэке продолжается.
        // _restoreRunningUI спросит Redis /run_status и поднимет Stop/плейсхолдер,
        // если агент всё ещё в работе — ровно как это делает F5.
        const resync = (reason) => {
            if (!this.currentChat) return;
            if (!activeWsAlive()) {
                console.log(`[WS] ${reason} — reconnecting chat=${this.currentChat}`);
                this.connectWebSocket(this.currentChat);
            }
            this._restoreRunningUI(this.currentChat).catch(() => {});
        };

        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible' && this.currentChat) {
                resync('Tab became visible');
            }
        });

        window.addEventListener('online', () => {
            resync('Network back online');
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

        btn.onclick = () => {
            overlay.classList.add('active');
            // Раздел Telegram-чатов (только админ): подтягиваем свежий список
            // при каждом открытии профиля — иконки тулов к этому моменту готовы.
            try { window.TgChats?.init?.(); } catch (_) {}
        };
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
       ACTIONS (copy)
    ══════════════════════════════════════════ */
    createMessageActions(messageId, messageText, isBot) {
        const div = document.createElement('div');
        div.className = isBot ? 'message-actions' : 'user-actions';
        div.dataset.messageId = messageId;

        const copyBtn = document.createElement('button');
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
