// Упрощенный SPA для авторизованных пользователей
class ChatAppSimple {
    constructor() {
        this.currentUser = null;
        this.currentChat = null;
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
        this.setupUI();
        await this.loadChats();
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
    document.getElementById('chat-list').addEventListener('click', (e) => {
        const item = e.target.closest('.chat-item');
        if (!item) return;

        const chatId = Number(item.dataset.chatId);
        this.openChat(chatId);
    });

    document.getElementById('send-btn').addEventListener('click', () => this.sendMessage());
    document.getElementById('message-input').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') this.sendMessage();
    });

    document.getElementById("new-chat-btn").addEventListener("click", () => this.createNewChat());
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
            <div class="chat-name">Чат ${chat.id}</div>
            <div class="chat-preview">
                ${chat.last_message || 'Нет сообщений'}
            </div>
        </div>
    `).join('');
}


  async openChat(chatId) {
    this.currentChat = chatId;

    document.getElementById('chat-title').textContent = 'GigaChel';
    document.getElementById('message-input').disabled = false;
    document.getElementById('send-btn').disabled = false;

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
        console.log(messages);
        container.innerHTML = messages.map(msg => `
            <div class="message ${msg.is_from_bot ? 'bot' : 'user'}">
                <div class="message-header">
                    <span class="message-sender">${msg.is_from_bot ? '🤖 Бот' : '👤 Вы'}</span>
                    <span class="message-time">${new Date(msg.created_at).toLocaleTimeString()}</span>
                </div>
                <div class="message-text">${msg.message}</div>
            </div>
        `).join('');

        container.scrollTop = container.scrollHeight;
    }

    async sendMessage() {
        const input = document.getElementById('message-input');
        const text = input.value.trim();
        console.log('input= ', input, 'text ', text)
        if (!text) return;

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
            }
        } catch (error) {
            console.error('Error sending message:', error);
        }
    }

    connectWebSocket(chatId) {
        if (this.ws) {
            this.ws.close();
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const WS_HOST = "127.0.0.1:8001"; // chats-service

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
        const div = document.createElement('div');
        div.className = `message ${message.is_from_bot ? 'bot' : 'user'}`;
        div.innerHTML = `
            <div class="message-header">
                <span class="message-sender">${message.is_from_bot ? '🤖 Бот' : '👤 Вы'}</span>
                <span class="message-time">${new Date().toLocaleTimeString()}</span>
            </div>
            <div class="message-text">${message.message}</div>
        `;

        container.appendChild(div);
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

        } catch (err) {
            console.error(err);
            alert("Не удалось создать чат");
        }
    }


}



// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    // Если на странице есть элемент с id="chat-app"
    if (document.getElementById('chat-app')) {
        window.chatApp = new ChatAppSimple();
    }
});