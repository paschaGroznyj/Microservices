// Упрощенный SPA для авторизованных пользователей
class ChatAppSimple {
    constructor() {
        this.currentUser = null;
        this.currentChat = null;
        this.isUIInitialized = false;
        this.ws = null;
        this.init();
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

        await this.renderMessageInput();
    }

    async checkAuth() {
    try {
        const response = await fetch('/api/auth/me', {
            credentials: 'include'
        });
        console.log(response)
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
    }


    async loadChats() {
        try {
            const response = await fetch('/api/chats', {
                credentials: 'include'
            });

            if (response.ok) {
                const chats = await response.json();
                this.renderChats(chats);
            }
        } catch (error) {
            console.error('Error loading chats:', error);
        }
    }

    renderChats(chats) {
    const chatList = document.getElementById('chat-list');

    chatList.innerHTML = chats.map(chat => `
        <div class="chat-item ${chat.id === this.currentChat ? 'active' : ''}"
             data-chat-id="${chat.id}">
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

    // renderMessages(messages) {
    //     const container = document.getElementById('chat-messages');
    //     if (!container) return;
    //
    //     container.innerHTML = '';
    //
    //     messages.forEach(msg => {
    //         const isBot = msg.is_from_bot;
    //         const rawContent = msg.message || '';
    //
    //         const messageEl = document.createElement('div');
    //         messageEl.className = isBot ? 'assistant-message' : 'user-message';
    //
    //         let content = rawContent;
    //
    //         if (isBot) {
    //             content = marked.parse(rawContent);
    //             if (typeof DOMPurify !== 'undefined') {
    //                 content = DOMPurify.sanitize(content);
    //             }
    //         } else {
    //             content = this.escapeHtml(rawContent);
    //         }
    //
    //         messageEl.innerHTML = `
    //             ${content}
    //             ${!isBot ? `
    //                 <div class="timestamp">
    //                     ${new Date(msg.created_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}
    //                 </div>
    //             ` : ''}
    //         `;
    //
    //         const actions = this.createMessageActions(msg.id, rawContent, isBot);
    //
    //         if (isBot) {
    //             messageEl.appendChild(actions);
    //             container.appendChild(messageEl);
    //         } else {
    //             const wrapper = document.createElement('div');
    //             wrapper.className = 'user-message-wrapper';
    //
    //             wrapper.appendChild(messageEl);
    //             wrapper.appendChild(actions);
    //
    //             container.appendChild(wrapper);
    //         }
    //     });
    //
    //     container.scrollTop = container.scrollHeight;
    // }

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
        const username = this.currentUser?.username || 'Пользователь';

        if (!this.currentChat) {
            // 🎨 Стартовый экран (нет выбранного чата)
            container.innerHTML = `
                <div class="chat-start-screen">
                    <div class="start-header">
                        <h2>Привет, ${username}!</h2>
                        <p class="start-subtitle">Чем сегодня займёмся?</p>
                    </div>
                    
                    <div class="message-input-wrapper-start">
                        <div class="message-input-box-start">
                            <textarea
                                id="message-input"
                                placeholder="Напишите сообщение, чтобы начать..."
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
                    
                    <div class="start-hints">
                        <span class="hint-item">💡 Задайте вопрос</span>
                        <span class="hint-item">✍️ Напишите команду</span>
                        <span class="hint-item">🔍 Попросите найти информацию</span>
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
        setTimeout(() => container?.classList.remove('rending'), 200);
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

        // 🔥 Если чат не выбран — создаём новый автоматически
        if (!this.currentChat) {
            console.log('📝 Чат не выбран, создаём новый...');
            try {
                const chatId = await this.createNewChat();
                // newChat уже вызывает openChat, так что this.currentChat установлен
                this.addMessage({
                    message: text,
                    is_from_bot: false,
                    id: `temp-${Date.now()}`, // временный ID
                    created_at: new Date().toISOString()
                });
                console.log('✅ Новый чат создан:', chatId);
            } catch (err) {
                console.error('❌ Failed to create chat:', err);
                alert('Не удалось создать чат. Попробуйте ещё раз.');
                return;
            }
        }

        try {
            const response = await fetch('/api/chats/send_message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                credentials: 'include',
                body: JSON.stringify({
                    chat_id: this.currentChat,
                    message: text
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
        const WS_HOST = "localhost:1000"; // на gateway

        this.ws = new WebSocket(
        `${protocol}//${WS_HOST}/ws/chat/${chatId}`
        );
        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.addMessage(message);
        };
    }

         addMessage(message) {
                const container = document.getElementById('chat-messages');
                if (!container) return;

                const isBot = message.is_from_bot;
                const rawContent = message.message || '';
                const sources = message.sources || [];
                console.log("Ссылки", sources)
                const messageEl = document.createElement('div');
                messageEl.className = isBot ? 'assistant-message' : 'user-message';

                let content = rawContent;
                if (isBot) {
                    content = marked.parse(rawContent);
                    if (typeof DOMPurify !== 'undefined') {
                        content = DOMPurify.sanitize(content);
                    }
                } else {
                    content = this.escapeHtml(rawContent);
                }

                messageEl.innerHTML = `
                    <div class="message-content">${content}</div>
                    ${!isBot ? `<div class="timestamp">${new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</div>` : ''}
                `;

                // actions — копирование + кнопка источников рядом
                const actions = this.createMessageActions(message.id, rawContent, isBot, sources);

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

            // 🔁 обновляем список чатов
            await this.loadChats();

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

        createMessageActions(messageId, messageText, isBot, sources = []) {
            const actionsDiv = document.createElement('div');
            actionsDiv.className = isBot ? 'message-actions' : 'user-actions';
            actionsDiv.dataset.messageId = messageId;

            // Кнопка копирования — твой код без изменений
            const copyButton = document.createElement('button');
            copyButton.className = 'action-button copy-button';
            copyButton.title = 'Копировать';
            copyButton.innerHTML = `
                <svg class="action-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <path d="M6 5V3C6 2.44772 6.44772 2 7 2H16C16.5523 2 17 2.44772 17 3V14C17 14.5523 16.5523 15 16 15H14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                    <rect x="3" y="5" width="11" height="13" rx="2" stroke="currentColor" stroke-width="1.5"/>
                </svg>
            `;
            copyButton.addEventListener('click', async (e) => {
                e.stopPropagation();
                await navigator.clipboard.writeText(messageText);
                copyButton.classList.add('copied');
                setTimeout(() => copyButton.classList.remove('copied'), 1500);
            });
            actionsDiv.appendChild(copyButton);

            // Кнопка источников — только для бота и только если есть ссылки
            if (isBot && sources.length > 0) {
                const sourcesBtn = document.createElement('button');
                sourcesBtn.className = 'action-button sources-button';
                sourcesBtn.title = 'Источники';
                sourcesBtn.innerHTML = `
                    <svg class="action-icon" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M4 6h12M4 10h8M4 14h5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
                    </svg>
                    <span class="sources-count-badge">${sources.length}</span>
                `;
                sourcesBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    // Снимаем active с других кнопок
                    document.querySelectorAll('.sources-button.active').forEach(b => {
                        if (b !== sourcesBtn) b.classList.remove('active');
                    });
                    sourcesBtn.classList.toggle('active');
                    this.toggleSourcesSidebar(sources, sourcesBtn);
                });
                actionsDiv.appendChild(sourcesBtn);
            }

            return actionsDiv;
        }

    toggleSourcesSidebar(sources, triggerBtn) {
        const existing = document.getElementById('sources-sidebar');
        console.log('Провалились в открытие сайдбара ', 'existing', existing)
        // Если сайдбар уже открыт — закрываем
        if (existing) {
            existing.remove();
            document.getElementById('chat-wrapper')?.classList.remove('sidebar-open');
            return;
        }

        const sidebar = document.createElement('div');
        sidebar.id = 'sources-sidebar';
        sidebar.innerHTML = `
            <div class="sidebar-header">
                <span>Источники</span>
                <button class="sidebar-close">✕</button>
            </div>
            <div class="sidebar-body">
                ${sources.map(({ label, url, preview }) => `
                    <a class="source-card" href="${url}" target="_blank" rel="noopener noreferrer">
                        ${preview ? `<div class="source-card-preview">${this.escapeHtml(preview)}</div>` : ''}
                        <div class="source-card-footer">
                            <span class="source-card-label">🔗 ${this.escapeHtml(label)}</span>
                            <span class="source-card-arrow">↗</span>
                        </div>
                    </a>
                `).join('')}
            </div>
        `;
        console.log('Создали сайдбар');
        sidebar.querySelector('.sidebar-close').onclick = () => {
            sidebar.remove();
            document.getElementById('chat-wrapper')?.classList.remove('sidebar-open');
            triggerBtn?.classList.remove('active');
        };

        document.getElementById('chat-wrapper')?.appendChild(sidebar);
        document.getElementById('chat-wrapper')?.classList.add('sidebar-open');
    }

    renderMessages(messages) {
            const container = document.getElementById('chat-messages');
            if (!container) return;
            container.innerHTML = '';

            messages.forEach(msg => {
                // sources из БД — если есть колонка, иначе []
                this.addMessage({
                    ...msg,
                    sources: msg.sources || []
                });
            });

            container.scrollTop = container.scrollHeight;
        }

}



// Инициализация при загрузке страницы. Ждет пока построится HTML дерево, только потом запускает код
document.addEventListener('DOMContentLoaded', () => {
    // Если на странице есть элемент с id="chat-app"
    if (document.getElementById('chat-app')) {
        window.chatApp = new ChatAppSimple();
    }
    initMobileMenu(); // Вызовется только тогда, когда страница полностью будет загружена
});
