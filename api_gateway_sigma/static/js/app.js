// CatchPM — Chat SPA (без Ouroboros)
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
        this._statusPollTimer = null;
        this.INPUT_MAX_CHARS = 5000;  // жёсткий предел длины поля ввода
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

        // Передаём username в ToolsPanel для проверки прав
        window.ToolsPanel?.setUser(this.currentUser.username);

        await this.loadChats();
        // await loadFeedbacks();
        initMobileMenu();
        this.initTabSwitcher();
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
        document.getElementById('text-preview-close')
            ?.addEventListener('click', () => this.closeTextPreview());
    }


    /* ══════════════════════════════════════════
       TAB SWITCHER (agent ↔ graph)
    ══════════════════════════════════════════ */
    initTabSwitcher() {
    document.querySelectorAll('.pill[data-tab], .nav-item[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            this.switchTab(tab);
        });
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
        document.querySelectorAll('.pill[data-tab], .nav-item[data-tab]').forEach(n => {
            n.classList.toggle('active', n.dataset.tab === tab);
        });

        // Заголовок шапки по активной вкладке
        const titleEl = document.getElementById("main-title");
        if (titleEl) {
            if (tab === 'skills') titleEl.textContent = 'Магазин скиллов';
            else if (tab === 'agent') titleEl.textContent = 'Agent Process Mining';
            else if (tab === 'graph') titleEl.textContent = 'Граф';
            else titleEl.textContent = 'CatchPM';
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
            window.SkillsPanel?.open(this);
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
            if (renamed) return { title: escapeHtml(renamed), preview: null };

            const raw = (chat.last_message || '').trim();
            if (!raw || raw === 'Чат создан') return { title: 'Новый чат', preview: null };
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
                const { title, preview } = getDisplayLine(chat);
                const lineHtml = title
                    ? `<div class="chat-line chat-line--title">${title}</div>`
                    : `<div class="chat-line chat-line--preview">${preview}</div>`;
                return `
                <div class="chat-item ${chat.id === this.currentChat ? 'active' : ''}"
                    data-chat-id="${escapeHtml(String(chat.id))}">
                    ${lineHtml}
                    <button class="chat-item-menu-btn" data-menu-chat-id="${escapeHtml(String(chat.id))}" title="Действия" type="button">
                        <svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14" style="transform:rotate(90deg)">
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
            this.openChat(Number(item.dataset.chatId));
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
        // Сохраняем статус уходящего чата перед сносом DOM
        if (this.currentChat && this.currentChat !== chatId) {
            if (!this._chatStatuses) this._chatStatuses = {};
            // Главный статус PMыча
            const mainStatusEl = document.getElementById('agent-status-indicator');
            if (mainStatusEl) {
                this._chatStatuses[this.currentChat] = {
                    main: mainStatusEl.querySelector('.status-text')?.textContent || '',
                    subagents: this.subagentStates ? { ...this.subagentStates } : {}
                };
            } else {
                // Сохраняем только субагентов если они есть
                if (this.subagentStates && Object.keys(this.subagentStates).length) {
                    this._chatStatuses[this.currentChat] = {
                        main: '',
                        subagents: { ...this.subagentStates }
                    };
                }
            }
        }

        this.currentChat = chatId;
        this.subagentStates = {};

        const msgContainer = this.getMessagesContainer();
        if (msgContainer) {
            msgContainer.classList.remove('hidden');
            msgContainer.innerHTML = '';
        }

        document.querySelector('.chat-main-inner')?.classList.add('chat-active');

        document.querySelectorAll('.chat-item').forEach(item => {
            item.classList.toggle('active', Number(item.dataset.chatId) === chatId);
        });

        // Переключаемся на вкладку агента если были не на ней
        if (this.currentTab !== 'agent') {
            this.switchTab('agent');

            const titleEl = document.getElementById("main-title");
            titleEl.textContent = 'Agent Process Mining';
        }

        await Promise.all([
            this.loadMessages(chatId),
            this.renderMessageInput()
        ]);

        // Если в чате уже есть финальное сообщение от бота — статус не нужен,
        // чистим сохранённое состояние и выходим
        const hasBotMessage = this.getMessagesContainer()
            ?.querySelector('.agent-message');

        if (hasBotMessage) {
            // Ответ уже есть — статус неуместен, удаляем сохранённое
            if (this._chatStatuses) delete this._chatStatuses[chatId];
            this._clearAllStatuses();
            return;
        }

        // Восстанавливаем сохранённый статус если есть (чат ещё в процессе)
        const savedState = this._chatStatuses?.[chatId];
        if (savedState) {
            if (savedState.main) this._updateMainStatus(savedState.main);
            if (savedState.subagents && Object.keys(savedState.subagents).length) {
                this.subagentStates = savedState.subagents;
                this._restoreSubagentStatuses(savedState.subagents);
            }
        }
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
        this.currentChat = null;

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
    }

    /* ══════════════════════════════════════════
       MESSAGES
    ══════════════════════════════════════════ */
    async loadMessages(chatId) {
        const PAGE_SIZE = 50;
        const res = await fetch(`/api/chats/${chatId}/messages?limit=${PAGE_SIZE}&offset=0`);
        if (!res.ok) return;
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

        container.addEventListener('scroll', () => {
            // Триггерим подгрузку, когда почти доскроллили до верха
            if (container.scrollTop < 120) {
                this.loadOlderMessages();
            }
        }, { passive: true });
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
            // Рендерим прикреплённые файлы над текстом сообщения
            const filesHtml = this._renderMessageFiles(message.files);
            // Длинный текст (>1000 символов) сворачиваем в чип-файлик,
            // клик по которому открывает превью в правом сайдбаре.
            const TEXT_COLLAPSE_LIMIT = 1000;
            if (rawContent && rawContent.length > TEXT_COLLAPSE_LIMIT) {
                const chipHtml = this._renderTextChip(rawContent);
                messageEl.innerHTML = `${filesHtml}<div class="message-text">${chipHtml}</div><div class="timestamp">${time}</div>`;
            } else {
                messageEl.innerHTML = `${filesHtml}<div class="message-text">${content}</div><div class="timestamp">${time}</div>`;
            }
        } else {
            messageEl.dataset.id = message.id;
            messageEl.innerHTML = content;
            if (animate) {
                messageEl.innerHTML = '';
                this._streamRenderMessage(messageEl, rawContent, container, message.files, message.message_id);
            } else {
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
            const wrapper = document.createElement('div');
            wrapper.className = 'user-message-wrapper';
            // id сообщения на wrapper — нужен для дедупа WS-эха (в т.ч. для чипов,
            // где textContent не содержит полного текста).
            if (message.id) wrapper.dataset.messageId = message.id;
            wrapper.appendChild(messageEl);
            wrapper.appendChild(actions);
            container.appendChild(wrapper);

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

    /* Открывает правый сайдбар-превью с оригинальным текстом чипа. */
    openTextPreview(chipEl) {
        const b64 = chipEl?.dataset?.textB64 || '';
        let text = '';
        try {
            text = decodeURIComponent(escape(atob(b64)));
        } catch (e) {
            text = '';
        }
        const panel = document.getElementById('text-preview-panel');
        const body  = document.getElementById('text-preview-body');
        const titleEl = document.getElementById('text-preview-title');
        const view  = document.getElementById('view-agent');
        if (!panel || !body || !view) return;

        body.textContent = text;   // textContent — без интерпретации HTML, оригинальный вид
        if (titleEl) {
            const first = (text.split('\n').find(l => l.trim()) || 'Просмотр текста').trim();
            titleEl.textContent = first.length > 60 ? first.slice(0, 60) + '…' : first;
        }
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');
        body.scrollTop = 0;
    }

    /* Закрывает правый сайдбар-превью. Панель уезжает по той же анимации
       (translateX 0 -> 100%), лента и поле ввода не двигаются. */
    closeTextPreview() {
        const panel = document.getElementById('text-preview-panel');
        if (panel) {
            panel.classList.remove('open');
            panel.setAttribute('aria-hidden', 'true');
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
<div class="fa-chips-hidden" id="${uid}" style="display:none;">${hiddenChips}</div>
<button class="fa-chips-toggle" type="button"
        onclick="(function(btn){
            var box = document.getElementById('${uid}');
            var open = box.style.display !== 'none';
            box.style.display = open ? 'none' : 'flex';
            btn.textContent = open ? '+${hidden.length} файл${hidden.length === 1 ? '' : hidden.length < 5 ? 'а' : 'ов'}' : 'Скрыть';
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

        const chatId = Number(btn.dataset.menuChatId);
        const chat = this.chats.find(c => Number(c.id) === chatId) || {};

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
            // Сбрасываем интерфейс ТОЛЬКО если удалили именно открытый чат
            if (this.currentChat === chatId) {
                this.currentChat = null;
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
        return Math.floor(100000 + Math.random() * 900000);
    }

    /* ══════════════════════════════════════════
       SEND
    ══════════════════════════════════════════ */
    async sendMessage() {
        const container = this.getInputContainer();
        const field   = container?.querySelector('#message-input');
        const sendBtn = container?.querySelector('#send-btn');
        if (!field) return;

        if (this._runState === 'running') {
            await this.stopCurrentRun();
            return;
        }
        if (this._runState !== 'idle') return;

        const text = this.getInputText();
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

        const msgId = `msg-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
        this.addMessage({ message: text, is_from_bot: false, id: msgId, created_at: new Date().toISOString(), files: [] });
        this._scrollToUserMessageAndReserve();

        this.clearInput(field);
        sendBtn?.classList.remove('active');
        this._botAnswered = false;
        this._hardResetStreamState();

        this._runState = 'running';
        this._setSendButtonMode('stop');

        try {
            await this._waitForWS();

            const resp = await fetch('/api/chats/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    chat_id:        this.currentChat,
                    message:        text,
                    message_id:     msgId,
                    type_agent:     'pm',
                    agent_model:    window.ModelPicker?.getModel() ?? 'universal',
                    selected_tools: window.ToolsPanel?.getSelectedCategories() ?? [],
                    context_size:   window.ToolsPanel?.getContextSize()   ?? 'medium',
                    has_files:      false,
                    files: []
                })
            });
            try {
                const j = await resp.clone().json();
                this._activeRequestId = j?.request_id || null;
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
        // Не создаём если ответ уже пришёл (race: бот ответил быстрее 400ms)
        if (this._botAnswered) return;

        // Убираем предыдущий если был
        this._removeBotPlaceholder();

        const placeholder = document.createElement('div');
        placeholder.id = 'agent-response-placeholder';
        placeholder.className = 'agent-placeholder';
        // Живой индикатор: синусоида в движении. Крутится всё время ожидания
        // и во время размышлений; на финале распрямляется в линию и гаснет.
        placeholder.innerHTML = this._waveIndicatorSVG();
        msgContainer.appendChild(placeholder);

        // Скроллим контейнер чтобы placeholder был виден снизу
        msgContainer.scrollTop = msgContainer.scrollHeight;
    }

    /** Убирает placeholder — вызывается явно из WS-обработчика */
    _removeBotPlaceholder() {
        // Отменяем гард-таймер чтобы он не убил следующий placeholder
        if (this._placeholderGuardTimer) {
            clearTimeout(this._placeholderGuardTimer);
            this._placeholderGuardTimer = null;
        }
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
       TYPEWRITER-СТРИМИНГ
       Приём чанков отвязан от отрисовки: чанки копятся
       в _streamBuffer, а ровный RAF-цикл каждый кадр
       «подъедает» из буфера фиксированную порцию символов
       и дорисовывает. Тяжёлый marked.parse троттлится.
    ══════════════════════════════════════════ */

    // Базовый темп: символов за кадр (~60fps → ~240 симв/сек).
    // При переполнении буфера порция плавно растёт, чтобы не отставать
    // от быстрой генерации, но не рвать плавность.
    _streamTypewriterStep() {
        const CHARS_PER_FRAME = 7;      // базовый темп (чуть быстрее, ~420 симв/сек)
        const buffered = this._streamBuffer ? this._streamBuffer.length : 0;

        // Автоускорение: чем больше накопилось, тем крупнее порция.
        let step = CHARS_PER_FRAME;
        if (buffered > 400)  step = Math.min(16, CHARS_PER_FRAME + Math.floor(buffered / 150));
        if (buffered > 2000) step = Math.min(48, Math.floor(buffered / 50));

        return step;
    }

    _startStreamLoop() {
        if (this._streamRaf) return;  // цикл уже крутится

        const RENDER_THROTTLE_MS = 70;  // не чаще — тяжёлый marked.parse
        this._lastStreamRender = 0;

        const tick = (ts) => {
            if (!this._liveBlockEl) {
                this._streamRaf = null;
                return;
            }

            // Вытаскиваем порцию символов из буфера
            const step = this._streamTypewriterStep();
            if (this._streamBuffer && this._streamBuffer.length) {
                const take = this._streamBuffer.slice(0, step);
                this._streamShown  += take;
                this._streamBuffer  = this._streamBuffer.slice(step);
            }

            // Троттлим тяжёлый Markdown-ре-рендер
            if (!ts) ts = performance.now();
            if (ts - this._lastStreamRender >= RENDER_THROTTLE_MS) {
                this._renderStreamPartial();
                this._lastStreamRender = ts;
            }

            // Условие завершения: сервер сказал done И буфер пуст
            const drained = !this._streamBuffer || this._streamBuffer.length === 0;
            if (this._streamDone && drained) {
                this._renderStreamPartial();   // финальный partial-рендер текста
                this._finalizeStream();        // файлы, actions, подсветка
                this._streamRaf = null;
                return;
            }

            this._streamRaf = requestAnimationFrame(tick);
        };

        this._streamRaf = requestAnimationFrame(tick);
    }

    _renderStreamPartial() {
        // Рендерим только в ЖИВОЙ блок (reasoning-блоки уже свёрнуты выше).
        if (!this._liveBlockEl) return;
        const container = this.getMessagesContainer();

        let html = marked.parse(this._streamShown || '');
        if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
            ADD_TAGS: ['table','thead','tbody','tr','th','td'],
            ADD_ATTR: ['colspan','rowspan','align']
        });
        this._liveBlockEl.innerHTML = html;

        if (container) {
            const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
            if (distFromBottom < 80) container.scrollTop = container.scrollHeight;
        }
    }

    /* SVG-каретка (Tabler chevron-right). Один стиль на весь свёрток —
       поворачивается через CSS-transform при раскрытии. */
    _reasoningCaretSVG() {
        return `<svg class="reasoning-caret" viewBox="0 0 24 24" width="14" height="14" `
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
             + `<svg viewBox="0 0 52 16" width="52" height="16" aria-hidden="true">`
             + `<g class="graph-net">`
             + `<path class="graph-link" d="M8 12 L26 4 L44 12"/>`
             + `<circle class="graph-dot graph-dot-a" cx="8" cy="12" r="2.2"/>`
             + `<circle class="graph-dot graph-dot-b" cx="26" cy="4" r="2.2"/>`
             + `<circle class="graph-dot graph-dot-c" cx="44" cy="12" r="2.2"/>`
             + `<circle class="graph-signal" r="2.6">`
             + `<animateMotion dur="1.6s" repeatCount="indefinite" keyPoints="0;1;0" keyTimes="0;0.5;1" calcMode="linear" path="M8 12 L26 4 L44 12"/>`
             + `</circle>`
             + `</g>`
             + `</svg></div>`;
    }

    /* Финализация индикатора: останавливаем вращение и плавно гасим.
       Индикатор живёт до рендера финального сообщения (_finalizeStream). */
    _flattenWaveIndicator() {
        const ind = document.querySelector('#agent-response-placeholder .wave-indicator');
        if (!ind) return;
        ind.classList.add('wave-flatten');
    }

    /* Гарантирует существование ЕДИНОГО свёртка «Размышления» для текущего
       ответа и возвращает его .reasoning-body — контейнер, куда дописываются
       все мысли между вызовами тулз. Не плодим блок на каждый шаг. */
    _ensureReasoningWrap() {
        if (this._reasoningWrapEl && this._pendingBotEl?.contains(this._reasoningWrapEl)) {
            return this._reasoningWrapEl.querySelector('.reasoning-body');
        }
        const wrap = document.createElement('div');
        wrap.className = 'reasoning-block reasoning-collapsed';
        wrap.innerHTML = `
            <div class="reasoning-header">
                ${this._reasoningCaretSVG()}
                <span class="reasoning-label">Размышления</span>
            </div>
            <div class="reasoning-body"></div>
        `;
        const header = wrap.querySelector('.reasoning-header');
        header?.addEventListener('click', () => {
            wrap.classList.toggle('reasoning-collapsed');
        });
        // Свёрток всегда выше живого блока
        if (this._liveBlockEl) {
            this._pendingBotEl.insertBefore(wrap, this._liveBlockEl);
        } else {
            this._pendingBotEl.appendChild(wrap);
        }
        this._reasoningWrapEl = wrap;
        return wrap.querySelector('.reasoning-body');
    }

    /* Текущий живой блок был размышлением перед тулзой. Дописываем его текст
       в ЕДИНЫЙ свёрток «Размышления» и убираем живой блок. Мысли из разных
       шагов копятся в одном свёртке как параграфы, а не отдельными блоками. */
    _commitReasoningBlock() {
        if (!this._liveBlockEl) return;

        // Дренаж буфера: забираем всю накопленную мысль сразу
        this._streamShown = (this._streamShown || '') + (this._streamBuffer || '');
        this._streamBuffer = '';
        if (this._streamRaf) { cancelAnimationFrame(this._streamRaf); this._streamRaf = null; }

        const text = this._streamShown;
        const liveEl = this._liveBlockEl;
        // Отвязываем ссылки СРАЗУ, чтобы новые чанки не сыпались в лопающийся блок.
        // Сам DOM-узел (liveEl) убираем чуть позже — после анимации «лопания».
        this._liveBlockEl = null;
        this._streamShown = '';
        this._streamDone  = false;

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

        const body = this._ensureReasoningWrap();
        const step = document.createElement('div');
        // Стартуем step в «зажатом» состоянии — анимация pop-in надует его,
        // как только лопнет живой пузырь (см. класс .reasoning-step--popin).
        step.className = 'reasoning-step reasoning-step--popin';
        step.innerHTML = html;
        body.appendChild(step);

        const container = this.getMessagesContainer();
        const nearBottom = () => {
            if (!container) return false;
            return (container.scrollHeight - container.scrollTop - container.clientHeight) < 120;
        };
        const stick = nearBottom();

        // Фаза 1 — «лопание» живого пузыря: схлопывается и растворяется.
        liveEl.classList.add('reasoning-live--pop');
        const POP_MS = 260;
        const removeLive = () => {
            liveEl.remove();
            // Фаза 2 — «надуваем» новый пузырь в свёртке.
            step.classList.add('reasoning-step--pop');
            if (stick && container) container.scrollTop = container.scrollHeight;
        };
        // reduce-motion / отсутствие поддержки анимаций — без задержек.
        const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce) {
            liveEl.remove();
            step.classList.add('reasoning-step--pop');
            if (stick && container) container.scrollTop = container.scrollHeight;
        } else {
            let done = false;
            const fire = () => { if (done) return; done = true; removeLive(); };
            liveEl.addEventListener('animationend', fire, { once: true });
            setTimeout(fire, POP_MS + 60);   // страховка, если animationend не придёт
        }

        if (stick && container) container.scrollTop = container.scrollHeight;
    }

    /* Живой блок оказался финальным ответом — повышаем его стиль
       с приглушённого reasoning-live до обычного (основной шрифт). */
    _promoteLiveToFinal() {
        // Помечаем флаг ДО работы с блоком: если живого блока ещё нет (финальные
        // чанки придут следом), новый блок создастся сразу как .live-block.
        this._streamIsFinal = true;
        if (this._liveBlockEl) {
            this._liveBlockEl.className = 'live-block';
            // Форсим немедленный ре-рендер уже видимого текста, чтобы новый
            // (обычный) стиль применился сразу, а не «дожидался» финализации.
            // Иначе финал визуально досыпается в приглушённом reasoning-стиле.
            this._renderStreamPartial();
        }
    }

    /* Жёсткий сброс всего стрим-состояния БЕЗ финализации DOM.
       Вызывается перед отправкой нового сообщения: если ссылки на элементы
       прошлого ответа ещё живы (finalizeStream не отработал), отвязываем их —
       чтобы чанки нового ответа не дописывались в предыдущий пузырь бота.
       DOM прошлого ответа при этом не трогаем (он уже показан пользователю),
       только «дорисовываем» его в обычный стиль на случай залипшего reasoning. */
    _hardResetStreamState() {
        if (this._streamRaf) {
            cancelAnimationFrame(this._streamRaf);
            this._streamRaf = null;
        }
        // Если прошлый живой блок так и остался в reasoning-стиле — нормализуем,
        // чтобы предыдущий ответ не выглядел как незавершённое размышление.
        if (this._liveBlockEl && this._liveBlockEl.classList.contains('reasoning-live')) {
            this._liveBlockEl.className = 'live-block';
        }
        this._pendingBotEl    = null;
        this._liveBlockEl     = null;
        this._reasoningWrapEl = null;
        this._pendingBotText  = '';
        this._streamBuffer    = '';
        this._streamShown     = '';
        this._streamDone      = false;
        this._streamIsFinal   = false;
        this._streamFinalData = null;
        this._streamingActive = false;
    }

    _finalizeStream() {
        if (!this._pendingBotEl) return;

        // Страховка: если генерация картинки завершилась (успех или ошибка),
        // а превью так и не пришло — снимаем висящий скелетон на финале.
        this._removeImageGenSkeleton();

        const data = this._streamFinalData || {};
        const fullText = data.fullText ?? this._streamShown ?? '';

        // Финальный полный рендер финального ответа — в живой блок (если он есть).
        // reasoning-блоки остаются выше нетронутыми.
        const targetBlock = this._liveBlockEl || (() => {
            const b = document.createElement('div');
            b.className = 'live-block';
            this._pendingBotEl.appendChild(b);
            return b;
        })();
        // Гарантия: финальный ответ всегда в обычном стиле, даже если маркер
        // final_commit не пришёл (например обрыв WS) — снимаем reasoning-стиль.
        targetBlock.className = 'live-block';

        let html = marked.parse(fullText);
        if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
            ADD_TAGS: ['table','thead','tbody','tr','th','td'],
            ADD_ATTR: ['colspan','rowspan','align']
        });
        targetBlock.innerHTML = html;

        // Файлы / actions вешаем на общую обёртку ответа
        if (data.files?.length) {
            this._pendingBotEl.appendChild(
                this._renderBotFiles(data.files, data.message_id)
            );
        }
        if (data.message_id) {
            this._pendingBotEl.dataset.id = data.message_id;
        }
        this._applyCodeHeaders(this._pendingBotEl);
        const actions = this.createMessageActions(this._pendingBotEl.dataset.id, fullText, true);
        this._pendingBotEl.appendChild(actions);
        this._pendingBotEl.style.transition = '';
        this._pendingBotEl.style.opacity    = '';

        // Сброс всего стрим-состояния
        this._pendingBotEl    = null;
        this._liveBlockEl     = null;
        this._reasoningWrapEl = null;   // критично: иначе следующий ответ приклеится к старому свёртку
        this._pendingBotText  = '';
        this._streamBuffer    = '';
        this._streamShown     = '';
        this._streamDone      = false;
        this._streamIsFinal   = false;
        this._streamFinalData = null;
        this._streamingActive = false;
        if (this._streamRaf) { cancelAnimationFrame(this._streamRaf); this._streamRaf = null; }

        // Финализация ответа: волна распрямляется в линию и гаснет,
        // затем убираем placeholder; чистим статусы.
        this._botAnswered = true;
        this._flattenWaveIndicator();
        setTimeout(() => this._removeBotPlaceholder(), 500);
        this._clearAllStatuses();
        if (this._chatStatuses) delete this._chatStatuses[this.currentChat];
        this._onRunFinished();
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
        this._runState = 'idle';
        this._activeRequestId = null;
        this._setSendButtonMode('send');
    }

    _startStatusPolling(chatId) {
        if (!chatId) return;
        if (this._statusPollTimer) {
            clearInterval(this._statusPollTimer);
            this._statusPollTimer = null;
        }

        const POLL_MS = 800;
        this._statusPollTimer = setInterval(async () => {
            // если ран уже завершён локально — polling больше не нужен
            if (this._runState === 'idle') {
                clearInterval(this._statusPollTimer);
                this._statusPollTimer = null;
                return;
            }

            try {
                const resp = await fetch(`/api/chats/${chatId}/status`, {
                    method: 'GET',
                    credentials: 'include',
                    cache: 'no-store'
                });
                if (!resp.ok) return;

                const state = await resp.json();
                if (state?.status === 'done') {
                    // Сообщение уже сохранено в БД chats-consumer'ом —
                    // просто перечитываем историю чата и завершаем run.
                    if (chatId === this.currentChat) {
                        await this.loadMessages(chatId);
                    }
                    this._onRunFinished();
                    return;
                }

                if (state?.status === 'idle') {
                    // На всякий случай: если done уже отдан/протух, перечитаем историю.
                    if (chatId === this.currentChat) {
                        await this.loadMessages(chatId);
                    }
                    this._onRunFinished();
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

    <div class="start-scrim"></div>

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
            this._revealStartBackground(container);
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
    /** WS отключен в sigma — ожидание не требуется */
    _waitForWS(timeout = 5000) {
        return Promise.resolve();
    }

    /* ══════════════════════════════════════════
       Плавное появление фонового изображения старта.
       Ставит .bg-ready на .chat-start-screen ТОЛЬКО после того,
       как активная для текущей темы картинка (day/night) реально
       загружена — чтобы не было резкого "прыжка" необлитого фона.
    ══════════════════════════════════════════ */
    _revealStartBackground(container) {
        const screen = container.querySelector('.chat-start-screen');
        if (!screen) return;

        const theme = document.documentElement.getAttribute('data-theme') || 'light';
        const src = theme === 'dark' ? '/static/img/night.jpg' : '/static/img/day.jpg';

        const reveal = () => requestAnimationFrame(() => {
            requestAnimationFrame(() => screen.classList.add('bg-ready'));
        });

        const img = new Image();
        img.onload = reveal;
        img.onerror = reveal;   // даже если картинка не загрузилась — не блокируем UI
        img.src = src;

        // Если картинка уже в кеше — onload может не сработать, подстрахуемся
        if (img.complete) reveal();
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
            return field.innerText || field.textContent || '';
        }
        let out = '';
        const walk = (node) => {
            node.childNodes.forEach((n) => {
                if (n.nodeType === Node.TEXT_NODE) {
                    out += n.textContent;
                } else if (n.nodeType === Node.ELEMENT_NODE) {
                    const chipId = n.getAttribute && n.getAttribute('data-chip-id');
                    if (chipId && this._pendingChips.has(chipId)) {
                        out += this._pendingChips.get(chipId);
                    } else if (n.tagName === 'BR') {
                        out += '\n';
                    } else if (n.classList && n.classList.contains('fa-chip')) {
                        // чип без активного id (edge) — пропускаем визуальную метку
                    } else {
                        walk(n);
                    }
                }
            });
        };
        walk(field);
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

    clearInput(field) {
        if (this._pendingChips) this._pendingChips.clear();
        this._chipSeq = 0;
        if (field.tagName === 'TEXTAREA') {
            field.value = '';
            field.style.height = 'auto';
        } else {
            field.innerHTML = '';
        }
    }

    /* Вставляет свёрнутый длинный текст как inline-чип в поле ввода.
       Полный текст кладём в this._pendingChips (не в DOM), а в поле —
       компактный неразрушаемый чип. Каретка встаёт ПОСЛЕ чипа, чтобы
       можно было продолжить печатать. */
    _insertInputChip(field, rawText) {
        if (!this._pendingChips) this._pendingChips = new Map();
        this._chipSeq = (this._chipSeq || 0) + 1;
        const chipId = 'ic-' + Date.now() + '-' + this._chipSeq;
        this._pendingChips.set(chipId, rawText);

        const chars = rawText.length;
        const lines = rawText.split('\n').length;
        const firstLine = (rawText.split('\n').find(l => l.trim()) || 'Текст').trim();
        const title = firstLine.length > 32 ? firstLine.slice(0, 32) + '…' : firstLine;
        const safeTitle = this.escapeHtml(title);
        let b64 = '';
        try { b64 = btoa(unescape(encodeURIComponent(rawText))); } catch (e) { b64 = ''; }

        const chip = document.createElement('span');
        chip.className = 'fa-chip fa-chip--text fa-chip--gray input-chip';
        chip.setAttribute('contenteditable', 'false');
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
            '<span class="fa-chip__meta">' + chars + ' симв · ' + lines + ' стр</span>';
        chip.addEventListener('click', () => this.openTextPreview(chip));

        // Вставляем чип в текущую позицию каретки
        const sel = window.getSelection();
        let range;
        if (sel && sel.rangeCount && field.contains(sel.anchorNode)) {
            range = sel.getRangeAt(0);
            range.deleteContents();
        } else {
            range = document.createRange();
            range.selectNodeContents(field);
            range.collapse(false);
        }
        range.insertNode(chip);
        // Пробел после чипа, чтобы каретка вышла из чипа и можно было печатать
        const spacer = document.createTextNode('\u00A0');
        chip.after(spacer);
        range.setStartAfter(spacer);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
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
            // Жёсткий предел длины ввода — обрезаем всё, что сверх лимита.
            this._enforceInputLimit(field);
            if (this._runState !== "idle") {
                sendBtn.disabled = false;
                sendBtn.classList.add('active');
                return;
            }
            const len = this.getInputText().length;
            const hasText = len > 0;
            sendBtn.classList.toggle('active', hasText);
            sendBtn.disabled = !hasText;
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

            const pasted = cd ? cd.getData('text') : '';
            if (!pasted) return;

            // ── Учитываем лимит 5000 символов ──────────────────────────
            const remaining = this.INPUT_MAX_CHARS - this.getInputText().length;
            if (remaining <= 0) {
                e.preventDefault();
                this.showToast('Достигнут предел ' + this.INPUT_MAX_CHARS + ' символов');
                return;
            }
            const clipped = pasted.length > remaining ? pasted.slice(0, remaining) : pasted;

            if (clipped.length > INPUT_COLLAPSE_LIMIT) {
                e.preventDefault();
                this._insertInputChip(field, clipped);
                onInput();
            } else if (clipped.length < pasted.length) {
                // Текст пришлось урезать под лимит — вставляем вручную усечённый.
                e.preventDefault();
                document.execCommand('insertText', false, clipped);
                this.showToast('Текст обрезан до предела ' + this.INPUT_MAX_CHARS + ' символов');
                onInput();
            }
            // короткий текст в пределах лимита — стандартная вставка (плейн-текст ниже)
        });

        field.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
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
        // WS отключен: переподключение не требуется
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

        container.addEventListener('scroll', sync, { passive: true });
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
const app = new ChatApp();
app.init();
export default app;
