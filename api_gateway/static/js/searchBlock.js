// static/js/searchBlock.js

import app from './app.js';

console.log("Search module loaded. User:");

const PAPERS = [
  {title:"Conformance Checking Using Automata-Based Alignment",authors:"van Dongen B., Carmona J., De Leoni M.",date:"2025-03-12",tags:[{l:"cs.AI",c:"cs"},{l:"PM",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Inductive Process Mining with Noise Handling for Unstructured Logs",authors:"Leemans S.J.J., Fahland D., van der Aalst W.",date:"2025-02-28",tags:[{l:"cs.SE",c:"cs"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Declarative Process Models and Sparse Event Logs",authors:"Maggi F.M., Di Ciccio C., Montali M.",date:"2025-02-14",tags:[{l:"cs.AI",c:"cs"},{l:"Declare",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Graph-Based Approaches for Object-Centric Process Mining",authors:"Esser S., Fahland D.",date:"2025-01-30",tags:[{l:"cs.DB",c:"cs"},{l:"PM",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Predictive Monitoring with LSTM Networks on Process Data",authors:"Tax N., Verenich I., La Rosa M.",date:"2025-01-18",tags:[{l:"stat.ML",c:"stat"},{l:"PM",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Event Abstraction via Hierarchical Clustering Techniques",authors:"Baier T., Mendling J., Weske M.",date:"2025-01-05",tags:[{l:"cs.AI",c:"cs"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Process Discovery from Low-Quality and Noisy Logs",authors:"Augusto A., Conforti R., Dumas M.",date:"2024-12-22",tags:[{l:"cs.SE",c:"cs"},{l:"PM",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Simulation-Based Conformance Metrics for Business Processes",authors:"Rozinat A., van der Aalst W.M.P.",date:"2024-12-10",tags:[{l:"cs.AI",c:"cs"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Federated Process Mining for Privacy-Preserving Analysis",authors:"Müller M., Zellner L., Rinderle-Ma S.",date:"2024-11-29",tags:[{l:"cs.CR",c:"cs"},{l:"PM",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Explainable AI Techniques Applied to Process Mining Results",authors:"Becker M., Richter F.",date:"2024-11-15",tags:[{l:"cs.AI",c:"cs"},{l:"stat.ML",c:"stat"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Online Process Mining for Streaming Event Data",authors:"van Zelst S.J., van Dongen B.",date:"2024-11-02",tags:[{l:"cs.DB",c:"cs"},{l:"PM",c:"pm"}],link:"https://arxiv.org", abstract: "Привет"},
  {title:"Root Cause Analysis in Deviant Cases Using Process Mining",authors:"Swinnen J., Depaire B., Jans M.",date:"2024-10-20",tags:[{l:"cs.AI",c:"cs"}],link:"https://arxiv.org", abstract: "Привет"},
];


let currentQuery = "";

function tagHtml(tag) {
  return `<span class="tag tag-${tag.c}">${tag.l}</span>`;
}

function renderCards() {
  const sort = document.getElementById("sort-select").value;
  let papers = [...PAPERS];
  if (sort === "date") {
    papers.sort((a, b) => b.date.localeCompare(a.date));
  }
  const grid = document.getElementById("cards-grid");
  grid.innerHTML = papers.map((p, i) => `
    <div class="card" data-index="${i}">
      <div class="card-tags">${p.tags.map(tagHtml).join("")}</div>
      <div class="card-title">${p.title}</div>
      <div class="card-authors">${p.authors}</div>
      <div class="card-date">${p.date}</div>
      <div class="card-footer">
        <a class="card-link" href="${p.link}" target="_blank">arxiv.org ↗</a>
        <button class="card-open">Открыть</button>
      </div>
    </div>
  `).join("");
}

function showSkeleton() {
  const grid = document.getElementById("cards-grid");
  grid.innerHTML = Array(6).fill(`
    <div class="skeleton-card">
      <div class="skel" style="height:14px;width:60%"></div>
      <div class="skel" style="height:12px;width:100%"></div>
      <div class="skel" style="height:12px;width:80%"></div>
      <div class="skel" style="height:10px;width:50%;margin-top:4px"></div>
    </div>
  `).join("");
}

async function doSearch() {

    if (!app.currentUser) {
      alert("Пожалуйста, подождите завершения авторизации или обновите страницу.");
      return;
  }

  const input = document.getElementById('center-input');
  const text = input.value.trim();
  if (!text) return;

  const username = app.currentUser.username
  console.log('DO SEARCH', username, text)
  try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            credentials: 'include',
            body: JSON.stringify({
                username: username,
                query: text
            })
        });

        if (response.ok) {
            input.value = '';
            input.style.height = "auto";

            const sendBtn = document.getElementById('search-btn');
            // sendBtn.classList.remove('active');
            // sendBtn.disabled = true;
        }
    } catch (error) {
        console.error('Error sending message:', error);
    }


  document.getElementById("center-state").classList.add("searched");
  document.getElementById("results-state").classList.add("visible");
  document.getElementById("results-meta").textContent = "загрузка...";

  checkerTab('arxiv');
  showSkeleton();
  setTimeout(() => {
    document.getElementById("results-meta").textContent = PAPERS.length + " статей";
    renderCards();
  }, 900);
}

function sendSearchQuery() {
  const searchBtn = document.getElementById("search-btn");
  const textarea = document.getElementById("center-input")

  if (!textarea || !searchBtn) {
    console.log("Область пустая или кнопки нет");
    return;
  }

  const newTextarea = textarea.cloneNode(true);
  textarea.parentNode.replaceChild(newTextarea, textarea);

  newTextarea.addEventListener('input', () => this.handleInputResize(newTextarea, searchBtn));

  newTextarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          doSearch();
      }
  });

  searchBtn.onclick = () => doSearch();
}

function fillAndSearch(q) {
  document.getElementById("center-input").value = q;
  doSearch();
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
  // Переключаем видимость контента
  ["agent", "arxiv"].forEach(t => {
    const view = document.getElementById("view-" + t);
    const nav = document.getElementById("nav-" + t);
    const pill = document.getElementById("pill-" + t);

    if (view) view.classList.toggle("hidden", t !== tab);
    if (nav) nav.classList.toggle("active", t === tab);
    if (pill) pill.classList.toggle("active", t === tab);
  });

  checkerTab(tab);
  // Обновляем заголовок
  const titleEl = document.getElementById("main-title");
  if (titleEl) {
      titleEl.textContent = tab === "agent" ? "Agent Process Mining" : "Поиск статей — arXiv";
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
    const sortSelect = document.getElementById('sort-select');
    if (sortSelect) {
        sortSelect.addEventListener('change', renderCards);
    }

    // --- Кнопка поиска ---
    const searchBtn = document.getElementById('search-btn');
    if (searchBtn) {
        searchBtn.addEventListener('click', doSearch);
    }

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
            if (e.target.closest(".card-link") || e.target.closest(".card-open")) return;

            const index = card.dataset.index;
            const paper = PAPERS[index];
            if (paper) openCard(paper);
        });
    }
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
          <div class="flip-abstract-label">Аннотация</div>
          <div class="flip-abstract" id="flip-abstract"></div>
          <a class="flip-link" id="flip-link" target="_blank">Открыть на arxiv.org ↗</a>
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

function openCard(paper) {
  // Заполняем данные
  document.getElementById("flip-tags").innerHTML = paper.tags.map(tagHtml).join("");
  document.getElementById("flip-title").textContent = paper.title;
  document.getElementById("flip-authors").textContent = paper.authors;
  document.getElementById("flip-meta").textContent = paper.date;
  document.getElementById("flip-abstract").textContent =
    paper.abstract || "Аннотация недоступна.";
  document.getElementById("flip-link").href = paper.link;

  // Сбрасываем состояние flip-inner на случай повторного открытия
  const inner = document.getElementById("card-flip-inner");
  inner.style.transition = "none";
  inner.style.transform = "rotateY(90deg) translateY(60px)";
  inner.style.opacity = "0";

  // Показываем оверлей
  const overlay = document.getElementById("card-overlay");
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
  if (e.key === "Enter" && document.activeElement.id === "center-input") doSearch();
});

// Инициализируем оверлей
initCardOverlay();
