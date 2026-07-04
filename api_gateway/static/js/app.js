// Упрощенный SPA для авторизованных пользователей
class ChatAppSimple {
    constructor() {
        this.currentUser = null;
        this.currentChat = null;
        this.isUIInitialized = false;
        this.ws = null;
        this.chats = [];
        this.currentTab = 'agent'; // 'agent' | 'ouroboros' | 'arxiv'
        // this.init();
    }

    async init() {
        // Проверяем авторизацию
        await this.checkAuth();

        // Если не авторизован - редирект на страницу логина
        if (!this.currentUser) {
            window.location.href = '/login-page';
            console.log('Вы не авторизованы')
            return;
        }
        console.log('Вы авторизованы')
        // Загружаем интерфейс чата
        // this.setupUI();
        await this.loadChats();
        await loadFeedbacks()
        initMobileMenu(); // Вызовется только тогда, когда страница полностью будет загружена
        await this.renderMessageInput();
    }

    async checkAuth() {
    try {
        const response = await fetch('/api/auth/me', {
            credentials: 'include'
        });
        if (response.ok) {
            const data = await response.json();
            console.log('Данные пользователя:', data);
            
            // Ваш auth сервис возвращает напрямую объект пользователя
            // { "username": "next", "user_id": 16, "authenticated": true }
            this.currentUser = data;  // ← Без data.user!
            
            const usernameElement = document.getElementById('username');
            if (usernameElement && this.currentUser.username) {
                usernameElement.textContent = this.currentUser.username;
            }
            
            return true;
        } else {
            console.log('Не авторизован, статус:', response.status);
            return false;
        }
    } catch (error) {
        console.log('Auth check error:', error);
        return false;
    }
}

    setupUI() {

        if (this.isUIInitialized) return;
        this.isUIInitialized = true;

        // Клик по списку чатов (делегирование — вешается на родителя)
        document.getElementById('chat-list')?.addEventListener('click', (e) => {
            const item = e.target.closest('.chat-item');
            if (!item) return;
            const chatId = Number(item.dataset.chatId);
            this.openChat(chatId);
        });

        // Кнопка "Новый чат"
        document.getElementById('new-chat-btn')?.addEventListener('click', () => this.createNewChat());
        document.getElementById('profile-button')?.addEventListener('click', () => this.attachProfileHandlers());

        // Переключение вкладок (Ассистент / Уробор / Поиск статей)
        document.querySelectorAll('.sb-nav .nav-item').forEach(navItem => {
            navItem.addEventListener('click', () => {
                document.querySelectorAll('.sb-nav .nav-item').forEach(n => n.classList.remove('active'));
                navItem.classList.add('active');
                this.currentTab = navItem.dataset.tab;
                console.log('🔄 Активная вкладка:', this.currentTab);
            });
        });
    }


    /* ───────────────────────────── */
/* PROFILE MODAL */
/* ───────────────────────────── */
    attachProfileHandlers() {

        const profileButton =
            document.getElementById('profile-button');

        const overlay =
            document.getElementById('profile-overlay');

        const closeBtn =
            document.getElementById('profile-close-btn');

        if (!profileButton || !overlay) return;
        /* open */
        profileButton.addEventListener('click', async () => {

            overlay.classList.add('active');
            /* stub request */
            try {
                await fetch('/api/profile/open', {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        event: 'profile_open',
                        timestamp: Date.now()
                    })
                });

            } catch (e) {
                console.log('profile analytics stub');
            }
        });

        /* close button */
        closeBtn?.addEventListener('click', () => {
            overlay.classList.remove('active');
        });

        /* overlay close */
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                overlay.classList.remove('active');
            }
        });

        /* ESC */
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                overlay.classList.remove('active');
            }
        });
        /* save token */
        document
            .getElementById('save-token-btn')
            ?.addEventListener('click', async () => {
                const token =
                    document.getElementById('scrapingdog-token')?.value;
                console.log('save token', token);
                /* gateway stub */
                try {

                    await fetch('/api/user/api-token', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            provider: 'scrapingdog',
                            token
                        })
                    });

                } catch (e) {
                    console.log('token save stub');
                }
            });
    }


    async loadChats() {
        try {
            const response = await fetch('/api/chats', {
                credentials: 'include'
            });

            if (response.ok) {
                this.chats = await response.json();
                this.renderChats(this.chats);
            }
        } catch (error) {
            console.error('Error loading chats:', error);
        }
    }

    renderChats(chats) {
    const chatList = document.getElementById('chat-list');

    chatList.innerHTML = [...chats].reverse().map(chat => `
    <div class="chat-item ${chat.id === this.currentChat ? 'active' : ''}"
         data-chat-id="${chat.id}" data-tab="agent">
        <div class="chat-preview">
            ${chat.last_message || 'Нет сообщений'}
        </div>
        </div>
    `).join('');
}


  async openChat(chatId) {
      console.log('📂 openChat вызван с chatId:', chatId, 'type:', typeof chatId);
      this.currentChat = chatId;
      document.querySelector('.chat-main-inner')?.classList.add('chat-active');
      //  ШПОРА
      // <div className="chat-main-inner chat-active">
      //     <!-- Теперь у контейнера ЕСТЬ класс chat-active -->
      //     <div id="chat-messages">Сообщения</div>
      // </div>

      // document.getElementById('chat-title').textContent = 'GigaЧат';
      document.getElementById('message-input').disabled = false;
      document.getElementById('send-btn').disabled = false;
      await this.renderMessageInput()
      await this.loadMessages(chatId);
      this.connectWebSocket(chatId);

      // 🔥 просто обновляем классы
      document.querySelectorAll('.chat-item').forEach(item => {
          item.classList.toggle(
              'active',
              Number(item.dataset.chatId) === chatId
          );
      });
  }

    async loadMessages(chatId) {
        try {
            const response = await fetch(`/api/chats/${chatId}/messages`, {
                credentials: 'include'
            });

            if (response.ok) {
                const data = await response.json();
                this.renderMessages(data.messages);
            }
        } catch (error) {
            console.error('Error loading messages:', error);
        }
    }

    renderMessages(messages) {
        const container = document.getElementById('chat-messages');
        if (!container) return;

        container.innerHTML = '';

        messages.forEach(msg => {
            const isBot = msg.is_from_bot;
            const rawContent = msg.message || '';

            const messageEl = document.createElement('div');
            messageEl.className = isBot ? 'assistant-message' : 'user-message';

            let content = rawContent;

            if (isBot) {
                    content = marked.parse(rawContent);

                    if (typeof DOMPurify !== 'undefined') {
                        content = DOMPurify.sanitize(content);
                    }
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = content;
                    const links = tempDiv.querySelectorAll('a');

                    links.forEach(link => {
                        link.setAttribute('target', '_blank');
                        link.setAttribute('rel', 'noopener noreferrer');
                    });
                    content = tempDiv.innerHTML;
                } else {
                    content = this.escapeHtml(rawContent);
                }

            messageEl.innerHTML = `
                ${content}
                ${!isBot ? `
                    <div class="timestamp">
                        ${new Date(msg.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}
                    </div>
                ` : ''}
            `;

            const actions = this.createMessageActions(msg.id, rawContent, isBot);

            if (isBot) {
                messageEl.appendChild(actions);
                container.appendChild(messageEl);
            } else {
                const wrapper = document.createElement('div');
                wrapper.className = 'user-message-wrapper';

                wrapper.appendChild(messageEl);
                wrapper.appendChild(actions);

                container.appendChild(wrapper);
            }
        });

        container.scrollTop = container.scrollHeight;
    }

    // Вспомогательная функция для экранирования HTML (для пользовательских сообщений)
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

     async renderMessageInput() {
        const container = document.getElementById('message-input-container');
        container?.classList.add('rending');
        if (!container) return;

        // Получаем имя пользователя (из this.currentUser или фолбэк)
        const username = this.currentUser?.name.split(' ')[0] || 'Пользователь';

        if (!this.currentChat) {
            // 🎨 Стартовый экран (нет выбранного чата)
            container.innerHTML = `
<div class="chat-start-screen">

    <!-- PROCESS MINING BACKGROUND -->
    <div class="process-bg">
        <svg viewBox="0 0 1200 700" preserveAspectRatio="none">

            <!-- connections -->
            <path class="process-line"
                  d="M120 180 C240 120, 320 280, 450 220 S680 80, 820 180 S1040 320, 1140 220" />

            <path class="process-line"
                  d="M80 480 C200 420, 340 560, 500 470 S760 300, 980 430" />

            <path class="process-line"
                  d="M240 120 C360 220, 520 160, 620 300 S860 540, 1040 500" />


        </svg>
    </div>

    <div class="start-header">
        <h2>
            <span class="greeting">Привет,</span>
            <span class="username">${username}</span>
        </h2>

        <p class="start-subtitle">
            Поищем что-нибудь интересное?
        </p>
    </div>

    <div class="message-input-wrapper-start">
        <div class="message-input-box-start">
            <textarea
                id="message-input"
                placeholder="Введите запрос..."
                rows="1"
            ></textarea>

            <button class="send-btn" id="send-btn" disabled>
                <svg viewBox="0 0 24 24" class="send-icon">
                    <path d="M12 5v14M5 12l7-7 7 7"
                          stroke="currentColor"
                          stroke-width="2"
                          fill="none"
                          stroke-linecap="round"
                          stroke-linejoin="round"/>
                </svg>
            </button>
        </div>
    </div>

    <div class="start-hints">
        <span class="hint-item">🔍 Найти информацию</span>
        <span class="hint-item" data-prompt="Найди статьи по process mining">
            📊 Process Mining
        </span>
        <span class="hint-item">⭐ Сохранить в избранное</span>
    </div>

</div>
`;
        } else {
            // 💬 Обычный чат (выбрана беседа)
            container.innerHTML = `
                <div class="message-input-wrapper">
                    <div class="message-input-box">
                        <textarea
                            id="message-input"
                            placeholder="Введите сообщение..."
                            rows="1"
                        ></textarea>
                        <button id="send-btn" disabled>
                            <svg viewBox="0 0 24 24" class="send-icon">
                                <path d="M12 5v14M5 12l7-7 7 7"
                                      stroke="currentColor"
                                      stroke-width="2"
                                      fill="none"
                                      stroke-linecap="round"
                                      stroke-linejoin="round"/>
                            </svg>
                        </button>
                    </div>
                </div>
                <div class="input-footer">
                    ИИ постарается ответить на ваши запросы, но это не точно
                </div>
            `;
        }
        setTimeout(() => {
        const chatMessages = document.getElementById('chat-messages');
        console.log('🔍 chat-messages после рендера:', {
            exists: !!chatMessages,
            element: chatMessages,
            parent: chatMessages?.parentElement?.id
        });
        }, 50);

        this.setupUI();
        this.attachInputHandlers();
        this.attachHintActions();
        this.attachProfileHandlers();
        setTimeout(() => container?.classList.remove('rending'), 200);
    }

    attachHintActions() {
            const input = document.getElementById('message-input');

            if (!input) return;

            document.querySelectorAll('.hint-item').forEach(chip => {

                chip.addEventListener('click', () => {

                    const prompt = chip.dataset.prompt;

                    if (!prompt) return;

                    input.value = prompt;

                    // триггерим resize textarea
                    input.dispatchEvent(new Event('input'));

                    input.focus();

                    // optional animation
                    chip.classList.add('hint-clicked');

                    setTimeout(() => {
                        chip.classList.remove('hint-clicked');
                    }, 300);
                });

            });
        }

    attachInputHandlers() {
        const textarea = document.getElementById('message-input');
        const sendBtn = document.getElementById('send-btn');

        // 🔥 Если элементов нет (например, стартовый экран без textarea) — выходим
        if (!textarea || !sendBtn) {
            console.log('⚠️ Input elements not found, skipping handlers');
            return;
        }

        // 🔥 Очищаем старые клонированные события (на всякий случай)
        const newTextarea = textarea.cloneNode(true);
        textarea.parentNode.replaceChild(newTextarea, textarea);

        // 🔥 Вешаем события на НОВЫЕ элементы
        newTextarea.addEventListener('input', () => this.handleInputResize(newTextarea, sendBtn));

        newTextarea.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Клик по кнопке отправки
        sendBtn.onclick = () => this.sendMessage();

        console.log('✅ Input handlers attached');
    }

    handleInputResize(textarea, sendBtn) {
        textarea.style.height = "auto";
        textarea.style.height = textarea.scrollHeight + "px";

        if (textarea.value.trim().length > 0) {
            sendBtn.classList.add('active');
            sendBtn.disabled = false;
        } else {
            sendBtn.classList.remove('active');
            sendBtn.disabled = true;
        }
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const text = input.value.trim();
        console.log('input= ', input, 'text ', text)
        if (!text) return;

       if (!this.currentChat) {
            try {
                // Очищаем инпут ДО createNewChat, чтобы loadMessages
                // не застал уже отправленное сообщение в UI
                input.value = '';
                input.style.height = "auto";

                await this.createNewChat();
            } catch (err) {
                console.error('❌ Failed to create chat:', err);
                alert('Не удалось создать чат. Попробуйте ещё раз.');
                return;
            }
        }

        // Оптимистичный рендер с temp id
        const tempId = `temp-${Date.now()}`;
        this.addMessage({
            message: text,
            is_from_bot: false,
            id: tempId,
            created_at: new Date().toISOString()
        });

        try {
            const response = await fetch('/api/chats/send_message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({
                    chat_id: this.currentChat,
                    message: text,
                    type_agent: this.currentTab === 'ouroboros' ? 'ouroboros' : 'pm'
                })
            });

            if (response.ok) {
                input.value = '';
                input.style.height = "auto";

                const sendBtn = document.getElementById('send-btn');
                sendBtn.classList.remove('active');
                sendBtn.disabled = true;
            }
        } catch (error) {
            console.error('Error sending message:', error);
        }
    }

    connectWebSocket(chatId) {
        if (this.ws) {
            this.ws.close();
        }

	console.log("🧩 CONNECT WS chatId =", chatId);
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const WS_HOST = window.location.host; // на gateway

        this.ws = new WebSocket(
        `${protocol}//${WS_HOST}/ws/chat/${chatId}`
        );
        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            if (message.type === 'agent_status') {
                this.updateStatus(message.status);
            } else {
                // Проверяем: есть ли уже такое сообщение от пользователя (оптимистичный рендер)
                if (!message.is_from_bot) {
                    const existing = [...document.querySelectorAll('.user-message-wrapper')].find(el => {
                        const msgEl = el.querySelector('.user-message');
                        return msgEl?.textContent?.trim().includes(message.message?.trim());
                    });
                    if (existing) return; // уже есть — пропускаем
                }
                this.addMessage(message);
            }
        };
    }

            updateStatus(statusText) {
                const container = document.getElementById('chat-messages');
                if (!container) return;
                console.log('Статус агента', statusText)
                // Ищем существующий индикатор или создаём новый
                let statusEl = document.getElementById('agent-status-indicator');

                if (!statusEl) {
                    statusEl = document.createElement('div');
                    statusEl.id = 'agent-status-indicator';
                    statusEl.className = 'agent-status';
                    container.appendChild(statusEl);
                }

                statusEl.innerHTML = `
                    <span class="status-spinner"></span>
                    <span class="status-text">${statusText}</span>
                `;

                container.scrollTop = container.scrollHeight;
            }

           addMessage(message) {
                const container = document.getElementById('chat-messages');
                if (!container) return;

                const isBot = message.is_from_bot;
                const rawContent = message.message || '';

                if (isBot) {
                    const statusEl = document.getElementById('agent-status-indicator');
                    if (statusEl) statusEl.remove();
                }

                const messageEl = document.createElement('div');
                messageEl.className = isBot ? 'assistant-message' : 'user-message';

                let content = rawContent;

                if (isBot) {
                    content = marked.parse(rawContent);

                    if (typeof DOMPurify !== 'undefined') {
                        content = DOMPurify.sanitize(content);
                    }
                    const tempDiv = document.createElement('div');
                    tempDiv.innerHTML = content;
                    const links = tempDiv.querySelectorAll('a');

                    links.forEach(link => {
                        link.setAttribute('target', '_blank');
                        link.setAttribute('rel', 'noopener noreferrer');
                    });
                    content = tempDiv.innerHTML;
                } else {
                    content = this.escapeHtml(rawContent);
                }

                messageEl.innerHTML = `
                    ${content}
                    ${!isBot ? `<div class="timestamp">${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</div>` : ''}
                `;

                const actions = this.createMessageActions(message.id, rawContent, isBot);

                if (isBot) {
                    messageEl.dataset.id = message.id;
                    messageEl.appendChild(actions);
                    container.appendChild(messageEl);
                } else {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'user-message-wrapper';

                    wrapper.appendChild(messageEl);
                    wrapper.appendChild(actions);

                    container.appendChild(wrapper);
                }

                container.scrollTop = container.scrollHeight;
        }

        async createNewChat() {
        try {
            const response = await fetch("/api/chats/new", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                credentials: "include"
            });

            if (!response.ok) {
                throw new Error("Ошибка создания чата");
            }

            const data = await response.json();
            const chatId = data.chat_id;

            const newChat = {
            id: chatId,
            last_message: 'Новый чат',
            timestamp: new Date().toISOString()
                    };

            this.chats.push(newChat);

            this.renderChats(this.chats);

            // 🔓 сразу открываем новый чат
            await this.openChat(chatId);

            return chatId;

        } catch (err) {
            console.error(err);
            alert("Не удалось создать чат");
        }
    }
        /**
         * Создает кнопки действий для сообщения
         */
        createMessageActions(messageId, messageText, isBot) {
            const actionsDiv = document.createElement('div');
            actionsDiv.className = isBot ? 'message-actions' : 'user-actions';
            actionsDiv.dataset.messageId = messageId;

            const copyButton = document.createElement('button');
            copyButton.className = 'action-button copy-button';
            copyButton.title = 'Копировать';

            copyButton.innerHTML = `
                <svg class="action-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"> <path d="M6 5V3C6 2.44772 6.44772 2 7 2H16C16.5523 2 17 2.44772 17 3V14C17 14.5523 16.5523 15 16 15H14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/> <rect x="3" y="5" width="11" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/> </svg>
            `;

            copyButton.addEventListener('click', async (e) => {
                e.stopPropagation();

                await navigator.clipboard.writeText(messageText);

                copyButton.classList.add('copied');

                copyButton.innerHTML = `
                    <svg class="action-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"> <path d="M6 5V3C6 2.44772 6.44772 2 7 2H16C16.5523 2 17 2.44772 17 3V14C17 14.5523 16.5523 15 16 15H14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/> <rect x="3" y="5" width="11" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/> </svg>
                `;

                setTimeout(() => {
                    copyButton.classList.remove('copied');

                    copyButton.innerHTML = `
                        <svg class="action-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"> <path d="M6 5V3C6 2.44772 6.44772 2 7 2H16C16.5523 2 17 2.44772 17 3V14C17 14.5523 16.5523 15 16 15H14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/> <rect x="3" y="5" width="11" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/> </svg>
                    `;
                }, 1500);
            });

            actionsDiv.appendChild(copyButton);

            return actionsDiv;
        }


}

document.querySelectorAll('.hint-item').forEach(chip => {
    chip.addEventListener('click', () => {
        console.log('Клик по process mining')
        input.value = chip.dataset.prompt;
        input.focus();
    });
});
// // Инициализация при загрузке страницы. Ждет пока построится HTML дерево, только потом запускает код
// document.addEventListener('DOMContentLoaded', () => {
//     // Если на странице есть элемент с id="chat-app"
//     if (document.getElementById('chat-app')) {
//         window.chatApp = new ChatAppSimple();
//     }
//     initMobileMenu(); // Вызовется только тогда, когда страница полностью будет загружена
// });


const app = new ChatAppSimple();

app.init()
// Экспортируем именно экземпляр (объект), а не класс
export default app;