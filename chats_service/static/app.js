// static/js/app.js
let ws = null;
const CHAT_ID = window.CHAT_ID || 1; // ? ����� ����������� �� �������

function connectWS() {
    if (ws) return; // ��� ����������

    ws = new WebSocket(`ws://127.0.0.1:8001/ws/chat/${CHAT_ID}`);

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        const div = document.createElement("div");
       if (msg.is_from_bot == true){
            bot_or_user = "bot"
        }
        else{
            bot_or_user = "user"
        }
        console.log(bot_or_user)
        div.className = bot_or_user === "bot" ? "bot" : "user";
        div.textContent = `${msg.is_from_bot ? "🤖" : "👤"}: ${msg.message}`;
        document.getElementById("history").appendChild(div);
    };

    ws.onopen = () => {
        console.log("WS connected to chat", CHAT_ID);
    };

    ws.onclose = () => {
        ws = null;
        console.log("WS disconnected");
    };
}

async function sendMessage() {
    const userId = document.getElementById("userId").value;
    const message = document.getElementById("message").value;

    if (!message.trim()) return;

    const payload = {
        user_id: parseInt(userId),
        chat_id: CHAT_ID,  // ? ���� �� URL
        message: message.trim()
    };

    const resp = await fetch("/send_message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
    });

    if (resp.ok) {
        document.getElementById("message").value = "";
        connectWS(); // ���������� WS, ���� ��� �� ����������
    } else {
        alert("������ ��������");
    }
}

async function loadMessages() {
    const resp = await fetch(`/chat/${CHAT_ID}/messages`);
    const data = await resp.json();

    const historyEl = document.getElementById("history");
    let bot_or_user;
    historyEl.innerHTML = "";
    data.messages.forEach(msg => {
        const div = document.createElement("div");
        console.log(msg.is_from_bot)
        if (msg.is_from_bot == true){
            bot_or_user = "bot"
        }
        else{
            bot_or_user = "user"
        }
        console.log(bot_or_user)
        div.className = bot_or_user === "bot" ? "bot" : "user";
        div.textContent = `${msg.is_from_bot ? "🤖" : "👤"}: ${msg.message}`;
        historyEl.appendChild(div);
    });

    connectWS();
}

// ��������� ������� ��� ������
loadMessages();
window.sendMessage = sendMessage;
window.loadMessages = loadMessages;

function ensureWS() {
    if (!ws || ws.readyState === WebSocket.CLOSED) {
        connectWS();
    }
}

setInterval(ensureWS, 3000);