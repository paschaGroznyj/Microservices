// static/js/chatSearchModal.js
// Модалка живого поиска по сообщениям пользователя (в стиле ed-poll).
// Открывается по кнопке-лупе в футере сайдбара или Ctrl+K,
// ищет от 2 символов с дебаунсом и перебрасывает к найденному message_id (±15 сообщений).

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
let lastResultsCount = 0;
let activeIndex = -1;
const CSE_ITEM_LIFT_COEFF = 0.50;

function setLiftPx(px = 0) {
  if (!modal) return;
  const lift = Math.max(0, Math.round(Number(px) || 0));
  modal.style.setProperty('--cse-lift-px', `${lift}px`);
}

const CSE_TOP_SAFE_VH = 0.10;

function calcMaxLiftPx() {
  const vh = window.innerHeight;
  const centerY = vh * 0.5;
  const modalHeight = modal ? modal.getBoundingClientRect().height : 0;
  const minTopEdge = vh * CSE_TOP_SAFE_VH;
  return Math.max(0, centerY - modalHeight / 2 - minTopEdge);
}

function applyLiftByResultsCount(count) {
  const safeCount = Number(count) || 0;
  lastResultsCount = safeCount;

  if (safeCount < 1) {
    setLiftPx(0);
    return;
  }

  const firstItem = resultsBox?.querySelector('.cse-item');
  const itemHeight = firstItem ? firstItem.getBoundingClientRect().height : 56;
  const desiredLift = itemHeight * safeCount * CSE_ITEM_LIFT_COEFF;
  const maxLift = calcMaxLiftPx();
  setLiftPx(Math.min(desiredLift, maxLift));
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function highlight(text, query) {
  const safe = escapeHtml(text);
  if (!query) return safe;
  const q = query.trim();
  if (!q) return safe;
  const rx = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
  return safe.replace(rx, '<mark class="cse-mark">$1</mark>');
}

function makeSnippet(text, query, radius = 64) {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim();
  if (!query) return t.slice(0, radius * 2);
  const q = query.trim();
  const idx = t.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return t.slice(0, radius * 2);
  const start = Math.max(0, idx - radius);
  const end = Math.min(t.length, idx + q.length + radius);
  let s = t.slice(start, end);
  if (start > 0) s = '…' + s;
  if (end < t.length) s = s + '…';
  return s;
}

function titleFor(item) {
  if (item.renamed_title && String(item.renamed_title).trim()) return String(item.renamed_title).trim();
  if (item.auto_title && String(item.auto_title).trim()) return String(item.auto_title).trim();
  const lm = String(item.last_message || '').replace(/\s+/g, ' ').trim();
  if (lm) return lm.length > 56 ? lm.slice(0, 56) + '…' : lm;
  return 'Чат #' + item.chat_id;
}

function setHint(msg) {
  activeIndex = -1;
  if (!resultsBox) return;
  resultsBox.innerHTML = `<div class="cse-hint" id="chat-search-hint">${escapeHtml(msg)}</div>`;
}

function activateResultElement(el) {
  if (!el) return;
  const chatId = String(el.dataset.chatId || '').trim();
  const messageId = String(el.dataset.messageId || '').trim();
  const dbId = String(el.dataset.id || '').trim();
  closeModal();
  if (!chatId) return;
  if (typeof app.navigateToSearchMessage === 'function') {
    app.navigateToSearchMessage(chatId, {
      messageId: messageId || dbId || null,
      dbId: dbId || null,
    });
  } else if (typeof app.openChat === 'function') {
    app.openChat(chatId, {
      targetMessageId: messageId || dbId || null,
      targetDbId: dbId || null,
    });
  }
}

function updateActiveItemUI() {
  if (!resultsBox) return;
  const items = [...resultsBox.querySelectorAll('.cse-item')];
  items.forEach((el, idx) => {
    const isSel = idx === activeIndex;
    el.classList.toggle('is-selected', isSel);
    if (isSel) {
      el.scrollIntoView({ block: 'nearest' });
    }
  });
}

function renderResults(items, query) {
  activeIndex = -1;
  if (!items || !items.length) {
    setHint('Ничего не найдено');
    applyLiftByResultsCount(0);
    return;
  }
  const html = items.map((item) => {
    const title = escapeHtml(titleFor(item));
    const snippet = highlight(makeSnippet(item.snippet, query), query);
    const who = item.is_from_bot ? 'Ассистент' : 'Вы';
    const chatId = escapeHtml(String(item.chat_id ?? ''));
    const msgId = escapeHtml(String(item.message_id || item.id || ''));
    const dbId = escapeHtml(String(item.id ?? ''));
    return `
      <button type="button" class="cse-item" data-chat-id="${chatId}" data-message-id="${msgId}" data-id="${dbId}">
        <div class="cse-item-title">${title}</div>
        <div class="cse-item-snippet"><span class="cse-who">${who}:</span> ${snippet}</div>
      </button>`;
  }).join('');
  resultsBox.innerHTML = html;

  applyLiftByResultsCount(items.length);

  resultsBox.querySelectorAll('.cse-item').forEach((el) => {
    el.addEventListener('click', () => activateResultElement(el));
  });
}

async function doSearch(query) {
  const reqId = ++currentReqId;
  try {
    const resp = await fetch(`/api/chats/search?q=${encodeURIComponent(query)}&limit=30`, {
      credentials: 'same-origin',
    });
    if (reqId !== currentReqId) return;
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

  if (!q.length) {
    currentReqId++;
    setLiftPx(0);
    setHint(`Введите минимум ${MIN_CHARS} символа для поиска`);
    return;
  }

  if (q.length < MIN_CHARS) {
    return;
  }

  debounceTimer = setTimeout(() => doSearch(q), DEBOUNCE_MS);
}

function openModal() {
  if (!overlay) return;
  overlay.classList.add('active');
  document.body.classList.add('cse-open');
  input.value = '';
  setLiftPx(0);
  setHint(`Введите минимум ${MIN_CHARS} символа для поиска`);
  setTimeout(() => input.focus(), 60);
}

function closeModal() {
  if (!overlay) return;
  overlay.classList.remove('active');
  overlay.classList.add('closing');
  setLiftPx(0);
  document.body.classList.remove('cse-open');
  clearTimeout(debounceTimer);
  currentReqId++;
  activeIndex = -1;
  setTimeout(() => overlay.classList.remove('closing'), 300);
}

if (openBtn) openBtn.addEventListener('click', openModal);
if (closeBtn) closeBtn.addEventListener('click', closeModal);
if (input) input.addEventListener('input', onInput);

if (overlay) {
  overlay.addEventListener('mousedown', (e) => {
    if (e.target === overlay) closeModal();
  });
}

window.addEventListener('resize', () => {
  if (!overlay || !overlay.classList.contains('active')) return;
  applyLiftByResultsCount(lastResultsCount);
});

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
    const items = resultsBox ? [...resultsBox.querySelectorAll('.cse-item')] : [];
    if (e.key === 'ArrowDown' && items.length) {
      e.preventDefault();
      activeIndex = (activeIndex + 1) % items.length;
      updateActiveItemUI();
    } else if (e.key === 'ArrowUp' && items.length) {
      e.preventDefault();
      activeIndex = activeIndex <= 0 ? items.length - 1 : activeIndex - 1;
      updateActiveItemUI();
    } else if (e.key === 'Enter' && items.length) {
      e.preventDefault();
      const target = items[activeIndex >= 0 ? activeIndex : 0];
      if (target) activateResultElement(target);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (input.value) {
        input.value = '';
        setLiftPx(0);
        setHint(`Введите минимум ${MIN_CHARS} символа для поиска`);
      } else {
        closeModal();
      }
    }
  });
}

console.log('Chat search modal loaded.');
