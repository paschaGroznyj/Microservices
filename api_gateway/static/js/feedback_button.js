const TAG_PALETTE = [
  { bg: "#e8f0f8", text: "#1a3a52" },
  { bg: "#dff0e8", text: "#1a6644" },
  { bg: "#ede9fb", text: "#3c2f8a" },
  { bg: "#fef3e2", text: "#8a5a00" },
  { bg: "#fde8e8", text: "#8a1a1a" },
];

const CATEGORIES = [
  { label: "💡", key: "idea", title: "Идея" },
  { label: "🐛", key: "bug", title: "Баг" },
  { label: "👍", key: "praise", title: "Респект" },
  { label: "🔧", key: "request", title: "Запрос" },
  { label: "📧", key: "sms", title: "Сообщение" },
];

const AGENT_STYLES = [
  { id: "normal",      emoji: "✨", label: "Обычный" },
  { id: "formal",      emoji: "💼", label: "Деловой" },

  // 🇷🇺 Русская классика
  { id: "pushkin",     emoji: "🪶", label: "Пушкинский" },   // возвышенный, лирический, «душа моя»
  { id: "esenin",      emoji: "🌾", label: "Есенинский" },   // деревенский, тоскливый, берёзки
  { id: "chekhov",     emoji: "🎩", label: "Чеховский" },    // краткость, ирония, чуть грустно
  { id: "dostoevsky",  emoji: "🕯️", label: "Достоевский" }, // страдание, надрыв, философия
  { id: "tolstoy",     emoji: "📖", label: "Толстовский" },  // эпично, многословно, морализирует
  { id: "gogol",       emoji: "😈", label: "Гоголевский" },  // абсурд, чертовщина, народный юмор

  // 🎙️ Современное русское
  { id: "babushka",    emoji: "🧣", label: "Бабушкин" },     // ласково, «касатик», пословицы
  { id: "gen_z",       emoji: "😎", label: "Зумерский" },    // кринж/краш/вайб

  // Оставил из старых
  { id: "pirate",      emoji: "🏴‍☠️", label: "Пиратский" },
  { id: "strict",      emoji: "🧐", label: "Строгий" },
];

let feedbacks = [];
let counts_feedback = 0;
let selectedCategory = CATEGORIES[0].key;
let currentStyle = "normal";
let isOpen = false;
let isAgentMode = false;


function initStyleDropdown() {
  const dropdown = document.getElementById("fb-style-dropdown");
  dropdown.innerHTML = AGENT_STYLES.map(s => `
    <div class="fb-style-option${s.id===currentStyle?" selected":""}" data-style="${s.id}">
      <span class="s-emoji">${s.emoji}</span>${s.label}
    </div>
  `).join("");
  dropdown.addEventListener("click", e => {
    const opt = e.target.closest(".fb-style-option");
    if (!opt) return;
    currentStyle = opt.dataset.style;
    updateStyleUI();
    dropdown.classList.remove("open");
    document.getElementById("fb-style-pill").classList.remove("active");
  });
}

// Цвет категории
function categoryColor(key) {
  const map = { idea: 3, bug: 4, praise: 1, request: 2 };
  return TAG_PALETTE[map[key] ?? 0];
}

// function formatTime(iso) {
//   const d = new Date(iso);
//   return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
// }


// ── Рендер категорий ───────────────────────────────────────
function renderCategories() {
  const row = document.getElementById("fb-controls-row");
  const divider = document.getElementById("fb-divider");
  const styleWrap = divider.nextElementSibling;

  Array.from(row.querySelectorAll(".fb-cat-btn")).forEach(el => el.remove());

  CATEGORIES.forEach(c => {
    const btn = document.createElement("button");
    btn.className = "fb-cat-btn" + (c.key === selectedCategory ? " active" : "");
    btn.dataset.cat = c.key;
    btn.title = c.title;
    btn.textContent = c.label;
    if (c.key === selectedCategory) {
      const col = categoryColor(c.key);
      btn.style.background = col.bg;
      btn.style.color = col.text;
      btn.style.borderColor = "transparent";
    }
    row.insertBefore(btn, divider);
  });
}

function formatTime(dateString) {
  const date = new Date(dateString);

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit'
  });
}

// ── Рендер карточки ────────────────────────────────────────
function renderCard(fb) {
  const color = categoryColor(fb.category);
  const catLabel = CATEGORIES.find(c => c.key === fb.category)?.label || fb.category;
  return `
    <div class="fb-card" id="fb-card-${fb.id}">
      <div class="fb-card-header">
        <div class="fb-card-author">${escapeHtml(fb.author)}</div>
        <div class="fb-card-meta">
          <span class="fb-tag" style="background:${color.bg};color:${color.text}">${catLabel}</span>
          <span class="fb-time">${formatTime(fb.created_at)}</span>
        </div>
      </div>
      <div class="fb-card-text">${escapeHtml(fb.text)}</div>
    </div>
  `;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// ── Обновить счётчик ───────────────────────────────────────
function updateCount() {
  const el = document.getElementById("fb-count");
  const n = counts_feedback;
  el.textContent = n + " " + pluralOtzyv(n);
}

function pluralOtzyv(n) {
  if (n % 10 === 1 && n % 100 !== 11) return "отзыв";
  if ([2,3,4].includes(n%10) && ![12,13,14].includes(n%100)) return "отзыва";
  return "отзывов";
}

// ── Добавить карточку в ленту ──────────────────────────────
function appendCard(fb) {
  const feed = document.getElementById("fb-feed");
  const empty = document.getElementById("fb-empty");
  if (empty) empty.remove();

  const div = document.createElement("div");
  div.innerHTML = renderCard(fb);
  feed.appendChild(div.firstElementChild);

  // Скролл вниз
  feed.scrollTop = feed.scrollHeight;
}

// ── Загрузить историю с API ────────────────────────────────
async function loadFeedbacks() {
  try {
    const res = await fetch("/api/feedback", { credentials: "include" });
    if (!res.ok) return;
    const data = await res.json()
    console.log('Feedbacks: ', data)
    counts_feedback = data.count;
    feedbacks = data.feedbacks || [];
    updateCount();

    const feed = document.getElementById("fb-feed");
    feed.innerHTML = "";

    if (!feedbacks.length) {
      feed.innerHTML = `
        <div class="fb-empty" id="fb-empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          Пока нет отзывов
        </div>`;
      return;
    }

    feedbacks.forEach(fb => appendCard(fb));
  } catch (e) {
    console.error("Не удалось загрузить отзывы:", e);
  }
}

function getStyleLabelById(styleId) {
  const styleObj = AGENT_STYLES.find(s => s.id === styleId);
  return styleObj ? styleObj.label : "Обычный";
}

// ── Отправить отзыв ────────────────────────────────────────
async function sendFeedback(text) {
  const styleLabel = isAgentMode ? currentStyle : "normal";
  try {
    await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({
        text,
        category: selectedCategory,
        use_agent: isAgentMode,
        vibe_style: styleLabel,
      }),
    });
  } catch(e) { console.error("Ошибка отправки:", e); }
}

// ── WebSocket — получаем новые отзывы ─────────────────────
function initFeedbackSocket(username) {
  // Переиспользуем userSocket если уже есть, иначе отдельный
  // Здесь слушаем тип feedback_new
  if (window.userSocket) {
    const origOnMessage = window.userSocket.onmessage;
    window.userSocket.onmessage = (event) => {
      if (origOnMessage) origOnMessage(event);
      try {
        const data = JSON.parse(event.data);
        if (data.type === "feedback_new") {
          onNewFeedback(data.feedback);
        }
      } catch {}
    };
  }
}

function onNewFeedback(fb) {
  counts_feedback++;
  feedbacks.push(fb);
  updateCount();
  appendCard(fb);
  // Пульс на кнопке если панель закрыта
  if (!isOpen) {
    document.getElementById("feedback-trigger").classList.add("has-new");
  }
}

// ── UI: открыть/закрыть ────────────────────────────────────
function openFeedback() {
  isOpen = true;
  document.getElementById("feedback-panel").classList.add("open");
  document.getElementById("feedback-trigger").classList.remove("has-new");
  document.getElementById("fb-feed").scrollTop = 9999;
}

function closeFeedback() {
  isOpen = false;
  document.getElementById("feedback-panel").classList.remove("open");
}

// ── Авторазмер textarea ────────────────────────────────────
function autoResize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 80) + "px";
}

function updateStyleUI() {
  const s = AGENT_STYLES.find(x => x.id === currentStyle);
  document.getElementById("fb-style-label").textContent = s ? s.emoji + " " + s.label : "✨ Обычный";
  document.querySelectorAll(".fb-style-option").forEach(el => {
    el.classList.toggle("selected", el.dataset.style === currentStyle);
  });
}

// ── DOMContentLoaded ───────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  renderCategories();
  initStyleDropdown();
  updateCount();

  const agentBtn = document.getElementById("fb-agent-btn");
  const stylePill = document.getElementById("fb-style-pill");
  const styleDropdown = document.getElementById("fb-style-dropdown");
  const input = document.getElementById("fb-input");
  const sendBtn = document.getElementById("fb-send");

  agentBtn.addEventListener("click", () => {
    isAgentMode = !isAgentMode;
    agentBtn.classList.toggle("active", isAgentMode);
    stylePill.disabled = !isAgentMode;
    if (!isAgentMode) {
      currentStyle = "normal";
      updateStyleUI();
      styleDropdown.classList.remove("open");
      stylePill.classList.remove("active");
    }
    input.placeholder = isAgentMode ? "Улучшение отзыва моделью..." : "Напишите ваш отзыв...";
  });

  stylePill.addEventListener("click", e => {
    e.stopPropagation();
    if (stylePill.disabled) return;
    const open = styleDropdown.classList.toggle("open");
    stylePill.classList.toggle("active", open);
  });

  document.addEventListener("click", e => {
    if (!stylePill.contains(e.target) && !styleDropdown.contains(e.target)) {
      styleDropdown.classList.remove("open");
      stylePill.classList.remove("active");
    }
    const panel = document.getElementById("feedback-panel");
    const trigger = document.getElementById("feedback-trigger");
    if (isOpen && !panel.contains(e.target) && !trigger.contains(e.target)) closeFeedback();
  });

  document.getElementById("fb-controls-row").addEventListener("click", e => {
    const btn = e.target.closest(".fb-cat-btn");
    if (!btn) return;
    selectedCategory = btn.dataset.cat;
    renderCategories();
    e.stopPropagation();
  });

  document.getElementById("feedback-trigger").addEventListener("click", () => {
    isOpen ? closeFeedback() : openFeedback();
  });
  document.getElementById("fb-close").addEventListener("click", closeFeedback);

  input.addEventListener("input", () => {
    autoResize(input);
    sendBtn.disabled = !input.value.trim();
  });
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!sendBtn.disabled) sendBtn.click(); }
  });

  sendBtn.addEventListener("click", () => {
    const text = input.value.trim();
    if (!text) return;
    // appendCard({ id: Date.now(), author: "Вы", category: selectedCategory, text, created_at: new Date().toISOString() });
    feedbacks.push({ text });
    updateCount();
    sendFeedback(text);
    input.value = ""; input.style.height = "auto"; sendBtn.disabled = true;
  });
});