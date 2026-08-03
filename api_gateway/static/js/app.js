// CatchPM — Chat SPA (без Ouroboros)
class ChatApp {
    constructor() {
        this.currentUser   = null;
        this.currentChat   = null;
        this.isUIReady     = false;
        this.ws            = null;
        this.chats         = [];
        this.currentTab    = 'agent'; // 'agent' | 'arxiv'
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
        await this.renderMessageInput();
    }


    /* ══════════════════════════════════════════
       TAB SWITCHER (agent ↔ arxiv)
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

        // Переключаем views
        document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
        document.getElementById(`view-${tab}`)?.classList.remove('hidden');

        // Активный pill / nav-item
        document.querySelectorAll('.pill[data-tab], .nav-item[data-tab]').forEach(n => {
            n.classList.toggle('active', n.dataset.tab === tab);
        });

        // Таб-специфичная логика
        if (tab === 'agent') {
            this.renderMessageInput();
        }
        if (tab === 'graph') {
            console.log('INIT GRAPH')
            knowledgeGraph.init();
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
        if (!list) return;

        const stripHtml = (html) => {
            const tmp = document.createElement('div');
            tmp.innerHTML = html;
            return tmp.textContent || tmp.innerText || '';
        };

        const visible = chats
            .filter(c => c.last_message && c.last_message.trim() && c.last_message !== 'Чат создан')
            .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));

        list.innerHTML = visible.map(chat => `
            <div class="chat-item ${chat.id === this.currentChat ? 'active' : ''}"
                 data-chat-id="${chat.id}">
                <div class="chat-preview">${stripHtml(chat.last_message)}</div>
            </div>
        `).join('');
    }

    /* ══════════════════════════════════════════
       UI SETUP (вешается один раз после рендера инпута)
    ══════════════════════════════════════════ */
    setupUI() {
        if (this.isUIReady) return;
        this.isUIReady = true;

        // Список чатов — делегирование
        document.getElementById('chat-list')?.addEventListener('click', (e) => {
            const item = e.target.closest('.chat-item');
            if (!item) return;
            this.openChat(Number(item.dataset.chatId));
        });

        // Новый чат
        document.getElementById('new-chat-btn')?.addEventListener('click', () => this.startNewChat());

        // Профиль
        document.getElementById('profile-button')?.addEventListener('click', () => this.attachProfileHandlers());
    }

    /* ══════════════════════════════════════════
       OPEN / CREATE CHAT
    ══════════════════════════════════════════ */
    async openChat(chatId) {
        this.currentChat = chatId;

        const msgContainer = this.getMessagesContainer();
        if (msgContainer) {
            msgContainer.classList.remove('hidden');
            msgContainer.innerHTML = '';
        }

        document.querySelector('.chat-main-inner')?.classList.add('chat-active');

        document.querySelectorAll('.chat-item').forEach(item => {
            item.classList.toggle('active', Number(item.dataset.chatId) === chatId);
        });

        // Переключаемся на вкладку агента если были на arxiv
        if (this.currentTab !== 'agent') {
            this.switchTab('agent');
        }

        await Promise.all([
            this.loadMessages(chatId),
            this.renderMessageInput()
        ]);

        this.connectWebSocket(chatId);
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
        if (this.ws) { this.ws.close(); this.ws = null; }

        this.currentChat = null;

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
    }

    /* ══════════════════════════════════════════
       MESSAGES
    ══════════════════════════════════════════ */
    async loadMessages(chatId) {
        const res = await fetch(`/api/chats/${chatId}/messages`);
        if (!res.ok) return;
        const data = await res.json();
        if (chatId !== this.currentChat) return; // гонка
        this.renderMessages(data.messages);
    }

    renderMessages(messages) {
        const container = this.getMessagesContainer();
        if (!container) return;
        container.innerHTML = '';
        messages.forEach(msg => this.addMessage(msg, false));
        container.scrollTop = container.scrollHeight;
    }

    addMessage(message, scroll = true, animate = false) {
        const container = this.getMessagesContainer();
        if (!container) return;

        const isBot      = message.is_from_bot;
        const rawContent = message.message || '';

        // Убираем статус-индикатор при ответе бота
        if (isBot) document.getElementById('agent-status-indicator')?.remove();

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
            messageEl.innerHTML = `${filesHtml}<div class="message-text">${content}</div><div class="timestamp">${time}</div>`;
        } else {
            messageEl.dataset.id = message.id;
            messageEl.innerHTML = content;
            // Файлы агента из истории — вставляем после текста, перед actions
            if (message.files?.length) {
                messageEl.appendChild(
                    this._renderBotFiles(message.files, message.message_id)
                );
            }
            document.getElementById('agent-response-placeholder')?.remove();

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

        const VISIBLE_LIMIT = 3;
        const visible = files.slice(0, VISIBLE_LIMIT);
        const hidden  = files.slice(VISIBLE_LIMIT);

        const wrap = document.createElement('div');
        wrap.className = 'fa-bot-files';

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

        const text = this.getInputText();
        if (!text) return;

        // Создаём чат если нет
        if (!this.currentChat) {
            this.currentChat = this.generateChatId();
            // Добавляем в список сразу с текстом первого сообщения
            this.chats.push({ id: this.currentChat, last_message: text, timestamp: new Date().toISOString() });
            this.renderChats(this.chats);
            await this.openChat(this.currentChat);
        } else {
            // Обновляем last_message у существующего чата
            const existing = this.chats.find(c => c.id === this.currentChat);
            if (existing) {
                existing.last_message = text;
                existing.timestamp = new Date().toISOString();
                this.renderChats(this.chats);
            }
        }

        // Оптимистичный рендер — передаём файлы до upload/clear
        const _optimisticFiles = window.FileAttach?.getFilesMeta() ?? [];
        this.addMessage({ message: text, is_from_bot: false, id: `temp-${Date.now()}`, created_at: new Date().toISOString(), files: _optimisticFiles });

        // 🎯 После fly-анимации: скролл + резерв места под ответ бота
        this._scrollToUserMessageAndReserve();

        this.clearInput(field);
        sendBtn?.classList.remove('active');
        if (sendBtn) sendBtn.disabled = true;

        // Сбрасываем флаг: ответ ещё не пришёл
        this._botAnswered = false;

        try {
            // Ждём WS-соединения (актуально для новых чатов)
            await this._waitForWS();
            const msgId = `msg-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
            await fetch('/api/chats/send_message', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    chat_id:        this.currentChat,
                    message:        text,
                    message_id:     msgId,
                    type_agent:     'pm',
                    selected_tools: window.ToolsPanel?.getSelectedCategories() ?? [],
                    context_size:   window.ToolsPanel?.getContextSize()   ?? 'medium',
                    has_files:      (window.FileAttach?.getFiles().length ?? 0) > 0,
                    files: window.FileAttach?.getFilesMeta() ?? []
                })
            });
            // Загружаем прикреплённые файлы отдельным запросом
            await window.FileAttach?.upload(this.currentChat, msgId);
            window.FileAttach?.clear();
            // Убираем подсветку attach-btn
            this.getInputContainer()
                ?.querySelector('#attach-btn')
                ?.classList.remove('has-files');
        } catch (err) {
            console.error('sendMessage error:', err);
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
        document.getElementById('agent-response-placeholder')?.remove();

        const placeholder = document.createElement('div');
        placeholder.id = 'agent-response-placeholder';
        placeholder.className = 'agent-placeholder';
        msgContainer.appendChild(placeholder);

        // Скроллим контейнер чтобы placeholder был виден снизу
        msgContainer.scrollTop = msgContainer.scrollHeight;

        // Таймаут-гард: убрать через 30с если бот так и не ответил
        setTimeout(() => {
            const ph = document.getElementById('agent-response-placeholder');
            if (ph) ph.remove();
        }, 30000);
    }

    /** Убирает placeholder — вызывается явно из WS-обработчика */
    _removeBotPlaceholder() {
        const ph = document.getElementById('agent-response-placeholder');
        if (!ph) return;
        ph.classList.add('placeholder-hide');
        setTimeout(() => ph.remove(), 300);
    }

    /* ══════════════════════════════════════════
       WEBSOCKET
    ══════════════════════════════════════════ */
    connectWebSocket(chatId) {
        if (this.ws) this.ws.close();

        this._pendingBotEl    = null;  // текущий элемент ответа бота (стриминг)
        this._pendingBotText  = '';    // накопленный текст

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat/${chatId}`);

        this.ws.onopen = () => console.log(`✅ WS connected chat=${chatId}`);

        this.ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);

            if (msg.type === 'agent_status') {
                this.updateStatus(msg.status);
                return;
            }
            console.log('Сообщение от агента', msg)
            // Дедупликация: пользовательское сообщение уже показано оптимистично
            if (!msg.is_from_bot) {
                const dup = [...document.querySelectorAll('.user-message')].find(el =>
                    el.textContent?.trim().includes(msg.message?.trim())
                );
                if (dup) return;
                this.addMessage(msg, true, false);
                return;
            }

            // ── Стриминг ответа бота ──────────────────────────────
            // msg.chunk  — фрагмент текста (стриминг)
            // msg.done   — финальный флаг (конец ответа)
            // msg.message — полный текст (если сервер шлёт сразу целиком)

            const container = this.getMessagesContainer();
            if (!container) return;

            if (msg.chunk !== undefined) {
                // Режим стриминга: накапливаем чанки в один элемент
                if (!this._pendingBotEl) {
                    this._pendingBotEl   = document.createElement('div');
                    this._pendingBotEl.className = 'agent-message';
                    this._pendingBotEl.dataset.id = msg.id || '';
                    this._pendingBotEl.style.opacity = '0';
                    this._pendingBotEl.style.transition = 'opacity 0.25s ease';
                    container.appendChild(this._pendingBotEl);
                    requestAnimationFrame(() => requestAnimationFrame(() => {
                        if (this._pendingBotEl) this._pendingBotEl.style.opacity = '1';
                    }));
                    // Placeholder живёт пока идёт стриминг — убираем только здесь, при первом чанке
                    this._botAnswered = true;
                    this._removeBotPlaceholder();
                    document.getElementById('agent-status-indicator')?.remove();
                }

                this._pendingBotText += msg.chunk;

                let html = marked.parse(this._pendingBotText);
                if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
                    ADD_TAGS: ['table','thead','tbody','tr','th','td'],
                    ADD_ATTR: ['colspan','rowspan','align']
                });
                this._pendingBotEl.innerHTML = html;

                const distFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
                if (distFromBottom < 80) container.scrollTop = container.scrollHeight;

            } else if (msg.done || msg.message !== undefined) {
                // Финал стриминга или целое сообщение
                const fullText = msg.message ?? this._pendingBotText;
                if (this._pendingBotEl) {
                    // Дорисовываем финальный вариант
                    let html = marked.parse(fullText);
                    if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html, {
                        ADD_TAGS: ['table','thead','tbody','tr','th','td'],
                        ADD_ATTR: ['colspan','rowspan','align']
                    });
                    this._pendingBotEl.innerHTML = html;
                    // Рендерим файлы агента между текстом и кнопкой копировать
                    if (msg.files?.length) {
                        this._pendingBotEl.appendChild(
                            this._renderBotFiles(msg.files, msg.message_id)
                        );
                    }
                    this._applyCodeHeaders(this._pendingBotEl);
                    const actions = this.createMessageActions(this._pendingBotEl.dataset.id, fullText, true);
                    this._pendingBotEl.appendChild(actions);
                    this._pendingBotEl.style.transition = '';
                    this._pendingBotEl.style.opacity    = '';
                    this._pendingBotEl = null;
                    this._pendingBotText = '';
                } else {
                    // Целое сообщение без стриминга — обычный addMessage
                    this.addMessage({ ...msg, message: fullText }, true, true);
                }

                // Убираем placeholder если ещё жив (целое сообщение без стриминга)
                this._botAnswered = true;
                this._removeBotPlaceholder();
                document.getElementById('agent-status-indicator')?.remove();
            }
        };

        this.ws.onerror = (e) => console.warn('WS error:', e);

        this.ws.onclose = () => {
            // Если стриминг оборвался — финализируем что накопили
            if (this._pendingBotEl && this._pendingBotText) {
                this._applyCodeHeaders(this._pendingBotEl);
                this._pendingBotEl.style.transition = '';
                this._pendingBotEl.style.opacity    = '';
                this._pendingBotEl = null;
                this._pendingBotText = '';
            }
            this._removeBotPlaceholder();
        };
    }

    updateStatus(statusText) {
        const container = this.getMessagesContainer();
        if (!container) return;

        let statusEl = document.getElementById('agent-status-indicator');
        if (!statusEl) {
            statusEl = document.createElement('div');
            statusEl.id = 'agent-status-indicator';
            statusEl.className = 'agent-status';
            container.appendChild(statusEl);
        }
        statusEl.innerHTML = `
            <span class="status-spinner"></span>
            <span class="status-text">${this.escapeHtml(statusText)}</span>
        `;
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
            container.innerHTML = `
<div class="chat-start-screen">

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

        // Инжектируем кнопку тулов в футер инпута
        window.ToolsPanel?.inject(container);

        // Посимвольная анимация start-header
        // Запоминаем для какого чата отрендерили
        container.dataset.renderedChat = this.currentChat ? String(this.currentChat) : '';

        if (!this.currentChat) {
            this._animateStartHeader(container);
        }

        setTimeout(() => container.classList.remove('rending'), 0);
    }

    /* ══════════════════════════════════════════
       TYPEWRITER: start-header
    ══════════════════════════════════════════ */
    /* ══════════════════════════════════════════
       TYPEWRITER: agent-message (пословно)
    ══════════════════════════════════════════ */
    async _streamRenderMessage(el, rawText, container) {
    let accumulated = '';
    const tokens = rawText.split(/(\s+)/);
    const BATCH = 2;   // слов за тик
    const DELAY = 22;  // мс между тиками

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
        // Сбрасываем флаг highlighted — финал перекрасит чисто
        el.querySelectorAll('pre code').forEach(b => {
            delete b.dataset.highlighted;
        });

        // Автоскролл только если юзер не отмотал вверх
        if (!userScrolled && container) {
            container.scrollTop = container.scrollHeight;
        }

        await this._sleep(DELAY);
    }

    // Снимаем листенер после стриминга
    container.removeEventListener('scroll', onScroll);

        this._applyCodeHeaders(el);

        // Вешаем кнопку копирования сообщения — она была затёрта стримингом
        const existingActions = el.querySelector('.message-actions');
        if (!existingActions) {
            const msgId = el.dataset.id;
            const actions = this.createMessageActions(msgId, rawText, true);
            el.appendChild(actions);
        }

        // Убираем placeholder — el уже в container
        const placeholder = document.getElementById('agent-response-placeholder');
        if (placeholder) {
            placeholder.classList.add('placeholder-hide');
            setTimeout(() => placeholder.remove(), 300);
        }
        // Финальный fade-in если opacity ещё не сброшен
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
       INPUT HANDLERS
    ══════════════════════════════════════════ */
    // Получить текст из поля — поддержка и textarea и contenteditable
    getInputText() {
        const container = this.getInputContainer();
        const field = container?.querySelector('#message-input');
        if (!field) return '';
        if (field.tagName === 'TEXTAREA') return field.value.trim();
        return (field.innerText || field.textContent || '').trim();
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
        if (field.tagName === 'TEXTAREA') {
            field.value = '';
            field.style.height = 'auto';
        } else {
            field.innerText = '';
        }
    }

    attachInputHandlers() {
        const container = this.getInputContainer();
        const field   = container?.querySelector('#message-input');
        const sendBtn = container?.querySelector('#send-btn');
        if (!field || !sendBtn) return;

        const onInput = () => {
            const hasText = this.getInputText().length > 0;
            sendBtn.classList.toggle('active', hasText);
            sendBtn.disabled = !hasText;
        };

        field.addEventListener('input', onInput);

        field.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!sendBtn.disabled) this.sendMessage();
            }
        });

        // Placeholder для contenteditable
        if (field.contentEditable === 'true') {
            field.addEventListener('focus', () => field.classList.add('focused'));
            field.addEventListener('blur',  () => field.classList.remove('focused'));
        }

        sendBtn.onclick = () => this.sendMessage();

        // Кнопка прикрепления файла
        const attachBtn = container?.querySelector('#attach-btn')
                       || container?.querySelector('#start-attach-btn');
        window.FileAttach?.init(attachBtn, container);
    }

    /* ══════════════════════════════════════════
       HELP OVERLAY
    ══════════════════════════════════════════ */
    /* ══════════════════════════════════════════
       CHATS SECTION TOGGLE
    ══════════════════════════════════════════ */
    initChatsSectionToggle() {
        // Делегируем на document — кнопка гарантированно найдётся
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('#chats-section-toggle');
            if (!btn) return;
            const section = document.getElementById('chats-section');
            if (!section) return;
            const isCollapsed = section.classList.toggle('collapsed');
            btn.setAttribute('aria-expanded', String(!isCollapsed));
        });
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

    getInputContainer() {
        return document.getElementById('view-agent')?.querySelector('.message-input-container');
    }
}

/* ── Точка входа ──────────────────────────── */
const app = new ChatApp();
app.init();
export default app;
