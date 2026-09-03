// static/js/chatSearchModal.js
// Модалка живого поиска по сообщениям пользователя.
// Открывается по кнопке-лупе в футере сайдбара или Ctrl+K,
// блюрит фон (как profile-overlay), ищет от 2 символов с дебаунсом,
// клик по результату открывает чат через app.openChat().

import app from './app.js';

const MIN_CHARS = 2;
const DEBOUNCE_MS = 220;

const overlay = document.getElementById('chat-search-overlay');
const modal = document.getElementById('chat-search-modal');
const input = document.getElementById('chat-search-input');
const closeBtn = document.getElementById('chat-search-close');
const resultsBox = document.getElementById('chat-search-results');
const openBtn = document.getElementById('chat-search-btn');

let debounceTimer = null;
let currentReqId = 0;

function setLifted(isLifted) {
  if (!overlay) return;
  overlay.classList.toggle('lifted', Boolean(isLifted));
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Подсветка найденной подстроки в тексте (регистронезависимо).
function highlight(text, query) {
  const safe = escapeHtml(text);
  if (!query) return safe;
  const q = query.trim();
  if (!q) return safe;
  // экранируем regex-спецсимволы в запросе
  const rx = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
  return safe.replace(rx, '<mark class="cse-mark">$1</mark>');
}

// Обрезаем длинный сниппет вокруг найденного вхождения.
function makeSnippet(text, query, radius = 60) {
  const t = String(text ?? '');
  if (!query) return t.slice(0, radius * 2);
  const idx = t.toLowerCase().indexOf(query.trim().toLowerCase());
  if (idx < 0) return t.slice(0, radius * 2);
  const start = Math.max(0, idx - radius);
  const end = Math.min(t.length, idx + query.length + radius);
  let s = t.slice(start, end);
  if (start > 0) s = '…' + s;
  if (end < t.length) s = s + '…';
  return s;
}

function titleFor(item) {
  if (item.renamed_title && item.renamed_title.trim()) return item.renamed_title.trim();
  const lm = (item.last_message || '').trim();
  if (lm) return lm.length > 48 ? lm.slice(0, 48) + '…' : lm;
  return 'Чат #' + item.chat_id;
}

function setHint(msg) {
  resultsBox.innerHTML = `<div class="cse-hint" id="chat-search-hint">${escapeHtml(msg)}</div>`;
}

function renderResults(items, query) {
  if (!items || !items.length) {
    setHint('Ничего не найдено');
    return;
  }
  const html = items.map((item) => {
    const title = escapeHtml(titleFor(item));
    const snippet = highlight(makeSnippet(item.snippet, query), query);
    const who = item.is_from_bot ? 'Ассистент' : 'Вы';
    return `
      <button class="cse-item" data-chat-id="${escapeHtml(String(item.chat_id))}">
        <div class="cse-item-title">${title}</div>
        <div class="cse-item-snippet"><span class="cse-who">${who}:</span> ${snippet}</div>
      </button>`;
  }).join('');
  resultsBox.innerHTML = html;

  resultsBox.querySelectorAll('.cse-item').forEach((el) => {
    el.addEventListener('click', () => {
      const chatId = Number(el.dataset.chatId);
      closeModal();
      if (chatId && typeof app.openChat === 'function') {
        app.openChat(chatId);
      }
    });
  });
}

async function doSearch(query) {
  const reqId = ++currentReqId;
  try {
    const resp = await fetch(`/api/chats/search?q=${encodeURIComponent(query)}&limit=30`, {
      credentials: 'same-origin',
    });
    if (reqId !== currentReqId) return; // пришёл устаревший ответ — игнор
    if (!resp.ok) {
      setHint('Ошибка поиска, попробуйте позже');
      return;
    }
    const data = await resp.json();
    if (reqId !== currentReqId) return;
    renderResults(data.results || [], query);
  } catch (e) {
    if (reqId !== currentReqId) return;
    setHint('Ошибка сети');
  }
}

function onInput() {
  const q = input.value.trim();
  clearTimeout(debounceTimer);

  // Поведение по ТЗ:
  // пусто -> модалка в центре;
  // есть ввод -> анимированно поднимается вверх.
  setLifted(q.length > 0);

  if (q.length < MIN_CHARS) {
    setHint(`Введите минимум ${MIN_CHARS} символа для поиска`);
    return;
  }
  setHint('Поиск…');
  debounceTimer = setTimeout(() => doSearch(q), DEBOUNCE_MS);
}

function openModal() {
  if (!overlay) return;
  overlay.classList.add('active');
  document.body.classList.add('cse-open');
  input.value = '';
  setLifted(false);
  setHint(`Введите минимум ${MIN_CHARS} символа для поиска`);
  // фокус после старта transition
  setTimeout(() => input.focus(), 60);
}

function closeModal() {
  if (!overlay) return;
  overlay.classList.remove('active');
  overlay.classList.add('closing');
  overlay.classList.remove('lifted');
  document.body.classList.remove('cse-open');
  clearTimeout(debounceTimer);
  currentReqId++; // отменяем висящие ответы
  setTimeout(() => overlay.classList.remove('closing'), 300);
}

// --- События ---
if (openBtn) openBtn.addEventListener('click', openModal);
if (closeBtn) closeBtn.addEventListener('click', closeModal);
if (input) input.addEventListener('input', onInput);

// клик по затемнённому фону закрывает (клик внутри модалки — нет)
if (overlay) {
  overlay.addEventListener('mousedown', (e) => {
    if (e.target === overlay) closeModal();
  });
}

// Ctrl+K / Cmd+K открывает, Escape закрывает
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault();
    if (overlay && overlay.classList.contains('active')) closeModal();
    else openModal();
  } else if (e.key === 'Escape' && overlay && overlay.classList.contains('active')) {
    closeModal();
  }
});

if (input) {
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      input.value = '';
      setLifted(false);
      setHint(`Введите минимум ${MIN_CHARS} символа для поиска`);
    }
  });
}

console.log('Chat search modal loaded.');
