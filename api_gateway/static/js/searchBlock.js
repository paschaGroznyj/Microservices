// static/js/searchBlock.js

import app from './app.js';

console.log("Search module loaded. User:");

const TAG_PALETTE = [
  { bg: "#e8f0f8", text: "#1a3a52" },  // синий (accent)
  { bg: "#dff0e8", text: "#1a6644" },  // зелёный
  { bg: "#ede9fb", text: "#3c2f8a" },  // фиолетовый
  { bg: "#fef3e2", text: "#8a5a00" },  // жёлтый
  { bg: "#fde8e8", text: "#8a1a1a" },  // красный
  { bg: "#e8f8f5", text: "#0e5c4a" },  // бирюзовый
];

function normalizeArticle(p) {
  return {
    article_id: p.article_id,
    title: p.title,
    abstract: p.abstract || "",
    date: p.date || p.date_sub || "",
    link: p.link || p.url_article || "",
    source: p.source || "arxiv",
    external_id: p.external_id || "",
    // authors — строка или массив
    authors: Array.isArray(p.authors)
      ? p.authors
      : typeof p.authors === "string"
        ? JSON.parse(p.authors)
        : [],
    // tags — строка или массив
    tags: Array.isArray(p.tags)
      ? p.tags
      : typeof p.tags === "string"
        ? JSON.parse(p.tags)
        : [],
    // сохраняем перевод если уже есть
    translated_abstract: p.translated_abstract || null,
    is_favorite: p.is_favorite || false,
  };
}

function tagColor(str) {
  // Простой хэш строки → индекс палитры
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return TAG_PALETTE[Math.abs(hash) % TAG_PALETTE.length];
}

function tagHtml(tag) {
  const color = tagColor(tag);
  return `<span class="tag" style="background:${color.bg};color:${color.text}">${tag}</span>`;
}


// Хранилище избранного: article_id → paper
const favorites = new Map();
let currentPapers = [];
let relevantArticleIds = new Set(); // Список релевантных статей в сокета

function renderCards() {
  console.log('ВЫЗОВ RENDERcards');
  // const sort = document.getElementById("sort-select").value;
  let papers = [...currentPapers];
  // if (sort === "date") {
  //   papers.sort((a, b) => b.date.localeCompare(a.date));
  // }
  const grid = document.getElementById("cards-grid");
  grid.innerHTML = papers.map((p, i) =>{

      // Если Set пустой (поиск еще не закончен или сброс), считаем все релевантными
    const isRelevant = relevantArticleIds.size === 0 || relevantArticleIds.has(p.article_id);
    // console.log('Множество', relevantArticleIds, 'id_card', p.article_id)
    // Классы карточки: базовый + условие нерелевантности
    const cardClass = isRelevant ? 'card' : 'card irrelevant-card';

    // Стиль звезды: если нерелевантно, можно сделать её серой или скрыть,
    // но обычно оставляют возможность добавить в избранное.
    // Здесь оставим логику избранного как есть, но визуально карточка изменится.
    const isFav = favorites.has(p.article_id);
    const starClass = isFav ? 'card-star active' : 'card-star';
    const starFill = isFav ? 'currentColor' : 'none';

      return`
    <div class="${cardClass}" data-index="${i}" data-article-id="${p.article_id}">
      <div class="card-header-row">
        <div class="card-tags">${p.tags.map(t => tagHtml(t)).join("")}</div>
        <button class="${starClass}" data-article-id="${p.article_id}" data-index="${i}">
          <svg viewBox="0 0 24 24" width="16" height="16">
            <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"
              stroke="currentColor" stroke-width="1.5" fill="${starFill}" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </button>
      </div>
      <div class="card-favorites"></div>
      <div class="card-title">${p.title}</div>
      <div class="card-authors">${Array.isArray(p.authors) ? p.authors.join(", ") : p.authors}</div>
      <div class="card-date">${p.date_sub}</div>
      <div class="card-footer">
          <a class="card-link" href="${p.link}" target="_blank">${sourceLinkLabel(p.source)}</a>
           ${sourceTag(p.source)}
        </div>
    </div>
  `}).join("");

}

function sourceTag(source) {
  const sources = {
    arxiv:   { label: "arXiv",   cls: "tag-source-arxiv"   },
    scholar: { label: "Scholar", cls: "tag-source-scholar" },
  };
  const s = sources[source] || { label: source || "unknown", cls: "tag-source-arxiv" };
  return `<span class="tag tag-source ${s.cls}">${s.label}</span>`;
}

function sourceLinkLabel(source) {
  const labels = {
    arxiv:   "arxiv.org ↗",
    scholar: "scholar ↗",
  };
  return labels[source] || "открыть ↗";
}

function showSkeleton() {
  const grid = document.getElementById("cards-grid");
  grid.classList.remove('hidden')
  grid.innerHTML = Array(6).fill(`
    <div class="skeleton-card">
      <div class="skel" style="height:14px;width:60%"></div>
      <div class="skel" style="height:12px;width:100%"></div>
      <div class="skel" style="height:12px;width:80%"></div>
      <div class="skel" style="height:10px;width:50%;margin-top:4px"></div>
    </div>
  `).join("");
}

function hideSkeleton(){
    const grid = document.getElementById("cards-grid");
      grid.classList.add('hidden');
}

// Загрузка избранных статей с бэка
async function loadFavorites() {
  try {
    const response = await fetch('/api/favorites/select', {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include"
        // Не обязательно отправлять юзернейм, тянется из куки
    });

    if (!response.ok) return;

    const data = await response.json();
    console.log('Данные с бэка для избранного', data)
    // Заполняем Map — article_id → paper
    data.articles.forEach(p => {
      favorites.set(String(p.article_id), p);
    });

    updateCount();
    syncStarsWithFavorites();

  } catch (error) {
    console.error('Ошибка загрузки избранного:', error);
  }
}

async function doSearch(isDefault = false, keyword = null) {
  if (!app.currentUser) {
    alert("Пожалуйста, подождите завершения авторизации или обновите страницу.");
    return;
  }

  const input = document.getElementById('center-input');
  const text = input.value.trim();
  if (!isDefault && !text) return;

  relevantArticleIds.clear(); // Обновляем множество релевантных статей
  const username = app.currentUser.username;
  const url = isDefault ? '/api/search/default' : '/api/search';
  const body = isDefault ? { username, key_word: keyword } : { username, query: text };
  console.log(url)
  document.getElementById("center-state").classList.add("searched");
  document.getElementById("results-state").classList.add("visible");
  document.getElementById("results-meta").classList.remove("hidden");
  document.getElementById("results-meta").textContent = "загрузка...";
  document.getElementById("basket-btn").classList.remove("hidden");
  checkerTab('arxiv');
  showSkeleton();

  try {

    const [searchResponse] = await Promise.all([
    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body)
    }),
    // loadFavorites()  // параллельно обновляем избранное
  ]);
    if (!searchResponse.ok) {
      if (searchResponse.status === 502) {
        showToast({
          title: "Сервис недоступен",
          text: "Не удалось подключиться к сервису парсеров. Попробуйте позже.",
          type: "error"
        });
        showRateLimitModal(120);
      }
      document.getElementById("results-meta").textContent = "Ошибка загрузки";
      return;
    }

    const data = await searchResponse.json();
    if (data.type === 'agent') {
        hideSkeleton();
        document.getElementById("results-meta").textContent = "";
        showToast({
                title: "Нерелевантный запрос",
                text: `${data.message}`,
                type: "agent",
                duration: 10000
            });
        return;
    }
    if (data.type === 'arxiv_error') {
        hideSkeleton();
        document.getElementById("results-meta").textContent = "";
        showToast({
                title: "Проблема с arxiv",
                text: `${data.message}`,
                type: "arxiv",
                duration: 10000
            });
        return;
    }
    console.log('Данные с бэка после парсинга DoSearch', data.payload.articles)
    // currentPapers = data.articles.map(normalizeArticle);
    currentPapers = data.payload.articles;

    document.getElementById("results-meta").textContent = currentPapers.length + ' ' + pluralArticles(currentPapers.length);
    renderCards();
    syncStarsWithFavorites()

  } catch (error) {
    console.error('Error:', error);
    showToast({
      title: "Ошибка сети",
      text: "Проверьте подключение к интернету.",
      type: "error"
    });
    document.getElementById("results-meta").textContent = "Ошибка загрузки";
  }
}

let userSocket = null;
// Работа с сокетом, агент отправляет сообщение с релевантными статьями
function initUserSocket(username) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const WS_HOST = window.location.host; // на gateway

    userSocket = new WebSocket(
        `${protocol}//${WS_HOST}/ws/user/${username}`
        );
    console.log('Открыли сокет для поисковика статей')
    userSocket.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.type === 'embeddings_ready') {
            console.log('РЕЛЕВАНТНЫЕ СТАТЬИ', data)
            relevantArticleIds = new Set(data.article_ids.map(Number));
            console.info('Значение переменной множества', relevantArticleIds)
            showToast({
                title: "Анализ готов",
                text: `Найдено ${data.count} релевантных статей. Показать?`,
                type: "success",
                duration: 10000
            });
            renderCards();
        }
        // if (data.type === 'agent') {
        //     showToast({
        //         title: "Не релевантный запрос",
        //         text: `${data.message}`,
        //         type: "agent",
        //         duration: 10000
        //     });
        // }
        if (data.type === 'error_parser') {
            showToast({
                title: "Ошибка парсера",
                text: "Не удалось получить статьи. Попробуйте позже.",
                type: "error",
                duration: 10000
            });
        }
        if (data.type === 'translate') {
            showToast({
                title: "Ошибка перевода",
                text: "Не удалось перевести аннотацию. Попробуйте позже.",
                type: "error",
                duration: 10000
            });
        }
        if (data.type === 'parser') {
            console.log('Прилет предупреждения для токенов', data.type, data.message)
            showToast({
                title: data.title,
                text: data.message,
                type: "warning",
                duration: 10000
            });
        }

        if (data.type === 'feedback_new') {
            console.log('Данные по Вебсокету на фидбэк', data.feedback)
            onNewFeedback(data.feedback);
        }
            // // Возвращаем UI в исходное состояние
            // document.getElementById("results-meta").textContent = "Ошибка загрузки";
        }


    userSocket.onclose = () => {
        // Переподключаемся через 3 сек
        setTimeout(() => initUserSocket(username), 3000);
    };
}

function showRateLimitModal(seconds = 120) {
  // Создаём модалку если её нет
  let modal = document.getElementById("rate-limit-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "rate-limit-modal";
    modal.innerHTML = `
      <div class="rate-limit-backdrop"></div>
      <div class="rate-limit-card">
        <div class="rate-limit-icon">⏳</div>
        <div class="rate-limit-title">arXiv просит подождать</div>
        <div class="rate-limit-text">
          Сервис arXiv ограничивает частоту запросов.<br>
          Повторная попытка через:
        </div>
        <div class="rate-limit-timer" id="rate-limit-timer">${seconds}</div>
        <div class="rate-limit-sub">секунд</div>
        <button class="rate-limit-close" id="rate-limit-close">Закрыть</button>
      </div>
    `;
    document.body.appendChild(modal);

    document.getElementById("rate-limit-close").addEventListener("click", () => {
      clearInterval(modal._interval);
      modal.classList.remove("active");
    });
  }

  modal.classList.add("active");

  // Запускаем таймер
  let remaining = seconds;
  const timerEl = document.getElementById("rate-limit-timer");
  timerEl.textContent = remaining;

  clearInterval(modal._interval);
  modal._interval = setInterval(() => {
    remaining--;
    timerEl.textContent = remaining;

    if (remaining <= 0) {
      clearInterval(modal._interval);
      modal.classList.remove("active");
      // Автоматически повторяем поиск
      doSearch();
    }
  }, 1000);
}

function fillAndSearch(q) {
  document.getElementById("center-input").value = q;
  console.log('ЗНАЧЕНИЕ', q)
  doSearch(true, q)
}

function checkerTab(tab){
    // Обновляем резульаты поиска
  const resultSearch = document.getElementById("results-state");
  const centerState = document.getElementById("center-state");

  if (resultSearch && centerState) {
      if (tab === "arxiv") {
          if (centerState.classList.contains("searched")) {
              resultSearch.classList.remove("hidden");
          } else {
              resultSearch.classList.add("hidden");
          }
      } else {
          resultSearch.classList.add("hidden");
      }
  }
}

function switchTab(tab) {
  const TABS = ["agent", "arxiv", "graph"];

  TABS.forEach(t => {
    // view-блок
    const view = document.getElementById("view-" + t);
    if (view) view.classList.toggle("hidden", t !== tab);

    // nav-item в сайдбаре (data-tab="arxiv" etc.)
    const navItems = document.querySelectorAll(`.nav-item[data-tab="${t}"]`);
    navItems.forEach(el => el.classList.toggle("active", t === tab));

    // pill в хедере (data-tab="arxiv" etc.)
    const pills = document.querySelectorAll(`.pill[data-tab="${t}"]`);
    pills.forEach(el => el.classList.toggle("active", t === tab));
  });

  checkerTab(tab);

  // Обновляем заголовок в шапке
  const titleEl = document.getElementById("main-title");
  if (titleEl) {
    const titles = {
      "agent": "Agent Process Mining",
      "arxiv": "Поиск статей",
      "graph":  "Граф знаний",
      "skills": "Магазин скиллов",
    };
    titleEl.textContent = titles[tab] || "CatchPM";
  }
}

// 2. Инициализация событий (вызывается один раз при загрузке)
document.addEventListener('DOMContentLoaded', () => {


    document.addEventListener('click', (e) => {
        // Ищем ближайшего родителя (или сам элемент) с атрибутом data-tab
        const target = e.target.closest('[data-tab]');

        // Если кликнули не по элементу с data-tab — выходим
        if (!target) return;

        const tabName = target.getAttribute('data-tab');
        // app.currentTab = tabName;
        console.log('Переключение таба:', tabName);

        // Вызываем функцию переключения
        switchTab(tabName);
    });

    // --- Кнопки подсказок (Hint Chips) ---
    const hintChips = document.querySelectorAll('.hint-chip');
    hintChips.forEach(chip => {
        chip.addEventListener('click', () => {
            const query = chip.getAttribute('data-query');
            if (query) fillAndSearch(query);
        });
    });

    // --- Сортировка ---
    // const sortSelect = document.getElementById('sort-select');
    // if (sortSelect) {
    //     sortSelect.addEventListener('change', renderCards);
    // }

    const searchBtn = document.getElementById('search-btn');
    if (searchBtn) {
        searchBtn.addEventListener('click', () => doSearch());
    }

    const waitForUser = setInterval(() => {
    if (app.currentUser) {
      clearInterval(waitForUser);
      loadFavorites(); // Один раз грузим корзину
      initUserSocket(app.currentUser.username); // Один раз открываем сокет для релевантных статей
      }
    }, 100);

    document.addEventListener('click', (e) => {
    if (e.target.closest('#hide-toast')) {
        hideToast();
    }
    });
    // Открытие корзины
    document.addEventListener('click', (e) => {
    if (e.target.closest('#basket-btn')) {
        openBasket();
    }
    });
    // Закрытие корзины
    document.addEventListener('click', (e) => {
      const removeBtn = e.target.closest('[data-remove-id]');
      if (removeBtn) {
        const id = removeBtn.dataset.removeId;
        removeFromBasket(id);
        return;
      }

      if (e.target.closest('#close-basket')) {
        closeBasket();
      }
    });

    // --- Enter в поле ввода ---
    const centerInput = document.getElementById('center-input');
    if (centerInput) {
        centerInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault(); // Чтобы не было переноса строки, если это textarea
                doSearch();
            }
        });
    }

    // Поворот карточки
    const cardsGrid = document.getElementById("cards-grid");
    if (cardsGrid) {
        cardsGrid.addEventListener("click", (e) => {
            const card = e.target.closest(".card");
            if (!card) return;

            // Не открываем флип если кликнули на ссылку или кнопку внутри карточки
            if (e.target.closest(".card-link") || e.target.closest(".card-open") || e.target.closest(".card-star")) return;

            const index = card.dataset.index;
            const paper = currentPapers[index];
            if (paper) openCard(paper);
        });
    }

      // Кнопка избранного
      cardsGrid.addEventListener("click", (e) => {
      // Звёздочка
      const starBtn = e.target.closest(".card-star");
      console.log('Тап по звездочке')
      if (starBtn) {
        toggleFavorite(starBtn);
        return;
      }

      // Флип карточки
      const card = e.target.closest(".card");
      if (!card) return;
      if (e.target.closest(".card-link") || e.target.closest(".card-open")) return;
      const paper = currentPapers[card.dataset.index];
      if (paper) openCard(paper);
        });


    // Взаимодействие с корзиной
    document.getElementById("basket-overlay").addEventListener("click", (e) => {
        if (e.target === document.getElementById("basket-overlay")) closeBasket();
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeBasket();
    });
});


// Создаём оверлей один раз при загрузке
function initCardOverlay() {
  const overlay = document.createElement("div");
  overlay.className = "card-overlay";
  overlay.id = "card-overlay";
  overlay.innerHTML = `
    <div class="card-flip-wrap">
      <div class="card-flip-inner" id="card-flip-inner">

        <!-- Лицевая сторона -->
        <div class="card-face back" id="flip-front">
          <button class="card-close" data-action="close">✕</button>
          <div class="flip-tags" id="flip-tags"></div>
          <div class="flip-title" id="flip-title"></div>
          <div class="flip-authors" id="flip-authors"></div>
          <div class="flip-meta" id="flip-meta"></div>
        </div>

        <!-- Задняя сторона -->
        <div class="card-face front">
          <button class="card-close" data-action="close">✕</button>
          <div class="flip-abstract-label">
            Аннотация
          </div>
          <div class="flip-abstract" id="flip-abstract"></div>
          <div class="flip-footer">
            <a class="flip-link" id="flip-link" target="_blank">Открыть на arxiv.org ↗</a>
          </div>
        </div>
      </div>
    </div>
  `;

  // Клик по затемнению — закрыть
  overlay.addEventListener("click", (e) => {
    if (e.target.closest("[data-action='close']") || e.target === overlay) {
      closeCard();
    }
  });

  document.body.appendChild(overlay);
}

function renderMarkdown(text) {
  if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
    return text;
  }
  return DOMPurify.sanitize(marked.parse(text || ""));
}

function openCard(paper) {
  // Заполняем данные
  document.getElementById("flip-tags").innerHTML = paper.tags.map(tagHtml).join("");
  document.getElementById("flip-title").textContent = paper.title;
  document.getElementById("flip-authors").textContent = paper.authors;
  document.getElementById("flip-meta").textContent = paper.date;

  // Рендерим аннотацию через Markdown
  const abstractEl = document.getElementById("flip-abstract");
  const abstractText = paper.translated_abstract || paper.abstract || "Аннотация недоступна.";
  abstractEl.innerHTML = renderMarkdown(abstractText);

  document.getElementById("flip-link").href = paper.link;
  document.getElementById("flip-link").textContent = 'Ссылка на ' + sourceLinkLabel(paper.source);

  // Сбрасываем состояние flip-inner на случай повторного открытия
  const inner = document.getElementById("card-flip-inner");
  inner.style.transition = "none";
  inner.style.transform = "rotateY(90deg) translateY(60px)";
  inner.style.opacity = "0";

  // Показываем оверлей
  const overlay = document.getElementById("card-overlay");
  overlay.dataset.articleId = paper.article_id;
  overlay.classList.remove("closing");
  overlay.classList.add("active");

  // Через один кадр запускаем анимацию влёта
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      inner.style.transition = "";
      inner.style.transform = "";
      inner.style.opacity = "";
    });
  });
}

// Подсветка звездочек, чтобы было совпадение с корзиной
function syncStarsWithFavorites() {
  document.querySelectorAll('.card-star').forEach(btn => {
    const id = String(btn.dataset.articleId);
    const isFav = favorites.has(id);
    btn.classList.toggle('active', isFav);
    btn.querySelector('path').setAttribute('fill', isFav ? 'currentColor' : 'none');
  });
}

// ── Анимация звёздочки летящей к корзине ─────────────────
function animateStar(fromEl, toEl, adding) {
  const fromRect = fromEl.getBoundingClientRect();
  const toRect   = toEl.getBoundingClientRect();

  const fly = document.createElement("div");
  fly.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" fill="currentColor"/></svg>`;
  fly.style.cssText = `
    position: fixed;
    left: ${fromRect.left + fromRect.width/2 - 7}px;
    top:  ${fromRect.top  + fromRect.height/2 - 7}px;
    pointer-events: none;
    z-index: 9999;
    color: ${adding ? '#f5c518' : '#ccc'};
    transition: left 0.5s cubic-bezier(0.25,0.46,0.45,0.94),
                top  0.5s cubic-bezier(0.25,0.46,0.45,0.94),
                opacity 0.5s, transform 0.5s;
  `;
  document.body.appendChild(fly);

  requestAnimationFrame(() => requestAnimationFrame(() => {
    fly.style.left    = `${toRect.left + toRect.width/2 - 7}px`;
    fly.style.top     = `${toRect.top  + toRect.height/2 - 7}px`;
    fly.style.opacity = "0";
    fly.style.transform = "scale(0.3) rotate(30deg)";
  }));

  setTimeout(() => fly.remove(), 550);
}

// ── Обновление счётчика ───────────────────────────────────
function updateCount() {
  const n = favorites.size;
  const el = document.getElementById("basket-count");
  el.textContent = n;
  el.classList.toggle("visible", n > 0);
  if (n > 0) {
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }
  document.getElementById("basket-panel-count").textContent = n + " " + pluralArticles(n);
}

function pluralArticles(n) {
  if (n % 10 === 1 && n % 100 !== 11) return "статья";
  if ([2,3,4].includes(n%10) && ![12,13,14].includes(n%100)) return "статьи";
  return "статей";
}


async function toggleFavorite(btn) {
    console.log(btn)
  const articleId = btn.dataset.articleId;  // ID для БД и Map
  const index = btn.dataset.index;           // индекс для массива
  console.log(btn.dataset)
  const paper = currentPapers[index];

  const isNowFavorite = !btn.classList.contains("active");
  const username = app.currentUser.username
  const adding = !favorites.has(articleId);
  console.log('Статья и ее id', paper, articleId, index)
  // Сразу обновляем UI — не ждём ответа сервера
  btn.classList.toggle("active");

  // Анимируем звёздочку к/от корзины
  const basketBtn = document.getElementById("basket-btn");
  animateStar(btn, basketBtn, adding);

  if (adding) {
    favorites.set(articleId, paper);
    btn.classList.add("active");
    btn.querySelector("path").setAttribute("fill", "currentColor");
    // Пульс на кнопке корзины
    basketBtn.classList.remove("basket-pulse");
    void basketBtn.offsetWidth;
    basketBtn.classList.add("basket-pulse");
    setTimeout(() => basketBtn.classList.remove("basket-pulse"), 400);
  } else {
    favorites.delete(articleId);
    btn.classList.remove("active");
    btn.querySelector("path").setAttribute("fill", "none");
  }

  updateCount();
  refreshBasketCards();

  try {
    await fetch('/api/favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({
        username: username,
        article_id: parseInt(articleId),
        is_favorite: adding
      })
    });
  } catch (error) {
    // Если запрос упал — откатываем UI обратно
    btn.classList.toggle("active");
    showToast({ title: "Ошибка", text: "Не удалось сохранить.", type: "error" });
  }
}

function openBasket() {
  refreshBasketCards();
  const overlay = document.getElementById("basket-overlay");
  overlay.classList.remove("closing");
  overlay.classList.add("active");
}

function closeBasket() {
  const overlay = document.getElementById("basket-overlay");
  overlay.classList.add("closing");
  setTimeout(() => overlay.classList.remove("active","closing"), 350);
}

// ── Рендер карточек в корзине ────────────────────────────
function refreshBasketCards() {
  const container = document.getElementById("basket-cards");
  const empty     = document.getElementById("basket-empty");
  const items     = [...favorites.values()];

  empty.style.display = items.length ? "none" : "flex";
  console.log('ITEMS', items)
  container.innerHTML = items.map(p => `
    <div class="basket-card" id="bcard-${p.article_id}">
      <button class="basket-card-remove" data-remove-id="${p.article_id}">✕</button>
      <div class="basket-card-title">${p.title}</div>
      <div class="basket-card-authors">${p.authors.join(", ")}</div>
      <div class="basket-card-date">${p.date}</div>
    </div>
  `).join("");
}

function removeFromBasket(id) {
  const card = document.getElementById("bcard-" + id);
  if (card) {
    card.classList.add("removing");
    setTimeout(() => {
      favorites.delete(id);
      updateCount();
      refreshBasketCards();
      // Обновляем звёздочку на карточке в сетке
      const starBtn = document.querySelector(`.card-star[data-article-id="${id}"]`);
      if (starBtn) {
        starBtn.classList.remove("active");
        starBtn.querySelector("path").setAttribute("fill","none");
      }
    }, 300);
  }

  fetch('/api/favorites', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    credentials: 'include',
    body: JSON.stringify({ username: app.currentUser.username, article_id: parseInt(id), is_favorite: false })
  }).catch(console.error);
}

function showToast({ title, text, type = "error", duration = 4000 }) {
  // Убираем предыдущий если есть
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  const safeText = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\n/g, "<br>");
  const icons = {
    error:   `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="#ef4444" stroke-width="1.5"/><path d="M8 5v3.5M8 10.5v.5" stroke="#ef4444" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    success: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="#22c55e" stroke-width="1.5"/><path d="M5 8l2 2 4-4" stroke="#22c55e" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    warning: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2L14 13H2L8 2Z" stroke="#f59e0b" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6v3M8 10.5v.5" stroke="#f59e0b" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    agent:   `<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="3" y="4" width="10" height="9" rx="2" stroke="#6b7280" stroke-width="1.5"/><circle cx="6" cy="8" r="1" fill="#6b7280"/><circle cx="10" cy="8" r="1" fill="#6b7280"/><path d="M8 4V2" stroke="#6b7280" stroke-width="1.5" stroke-linecap="round"/><circle cx="8" cy="1.5" r="0.5" fill="#6b7280"/><path d="M5 13v1M11 13v1" stroke="#6b7280" stroke-width="1.5" stroke-linecap="round"/></svg>`,
    arxiv: `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#3b82f6" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="8" cy="8" r="1.5" fill="#3b82f6"/>
  <ellipse cx="8" cy="8" rx="6" ry="2.5" transform="rotate(0 8 8)"/>
  <ellipse cx="8" cy="8" rx="6" ry="2.5" transform="rotate(60 8 8)"/>
  <ellipse cx="8" cy="8" rx="6" ry="2.5" transform="rotate(120 8 8)"/>
</svg>`

  };

  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  // toast.id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
  toast.innerHTML = `
    <div class="toast-icon">${icons[type]}</div>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      <div class="toast-text">${safeText}</div>
    </div>
    <button class="toast-close" id="hide-toast">✕</button>
  `;

  container.appendChild(toast);

  // Кнопка закрытия
  toast.querySelector(".toast-close").addEventListener("click", () => {
    removeSingleToast(toast);
  });

  requestAnimationFrame(() => {
    requestAnimationFrame(() => toast.classList.add("visible"));
  });

  toast._timer = setTimeout(() => removeSingleToast(toast), duration);
}

function removeSingleToast(toast) {
  clearTimeout(toast._timer);
  toast.classList.remove("visible");
  setTimeout(() => toast.remove(), 350);
}

// Оставь hideToast для совместимости если где-то используется
function hideToast() {
  const toasts = document.querySelectorAll(".toast");
  toasts.forEach(t => removeSingleToast(t));
}

function closeCard() {
  const overlay = document.getElementById("card-overlay");
  overlay.classList.add("closing");

  // Ждём конца анимации и скрываем
  setTimeout(() => {
    overlay.classList.remove("active", "closing");
  }, 400);
}

// Закрытие по Escape
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeCard();
});

// Инициализируем оверлей
await initCardOverlay();
