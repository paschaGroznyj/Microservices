/**
 * whatsnew.js — Онбординг-демо (7 слайдов) + What's New (обновления).
 *
 * Механика "показать один раз":
 *   1. GET /api/whatsnew/pending -> { demo, updates: [...] }.
 *   2. Если demo=true — показываем онбординг-слайдер (7 слайдов).
 *      По завершении POST /api/whatsnew/demo/seen.
 *   3. Далее — непросмотренные обновления по одному.
 *      На каждое закрытие POST /api/whatsnew/updates/seen {update_id}.
 *
 * Разметку инжектит скрипт (chat.html не раздуваем).
 * Стиль — общие переменные темы (whatsnew.css).
 *
 * ГЕРОИ (коты с табличкой [>_] на груди):
 *   - два героя: чёрный кот и белый кот, обнимаются как друганы;
 *   - слоты .wn-hero (левый / правый), внутри <img class="wn-hero-img">;
 *   - модификаторы .wn-hero--full (целиком) / .wn-hero--peek (выглядывает);
 *   - картинки со сменой темы: data-hero-light / data-hero-dark (js-theme-icon
 *     переключает src через theme.js).
 *   Пока картинок нет — рисуем плейсхолдер с табличкой [>_].
 *   Путь картинок задаётся в HERO_ASSETS ниже (когда сгенерируем PNG).
 */
(function () {
  "use strict";

  const API = {
    pending:    "/api/whatsnew/pending",
    demoSeen:   "/api/whatsnew/demo/seen",
    updateSeen: "/api/whatsnew/updates/seen",
  };

  // ── Ассеты героев (заполним, когда сгенерируем PNG) ──────────────────────
  // Формат: { light: "/static/img/heroes/xxx.png", dark: "/static/img/heroes/xxx_night.png" }
  // Пока null => показываем плейсхолдер с табличкой [>_].
  const HERO_ASSETS = {
    duo:      null,   // оба кота обнимаются, показывают на интерфейс
    duo_peek: null,   // оба выглядывают из-за края
    black:    null,   // чёрный кот
    white:    null,   // белый кот
  };

  // Определяем базовый интерфейс это или Sigma (по хосту).
  const IS_SIGMA = /(?:^|\.)cds\./i.test(location.hostname) || /sigma/i.test(location.hostname);

  // ── Утилиты ──────────────────────────────────────────────────────────────
  function h(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === "class") el.className = attrs[k];
        else if (k === "html") el.innerHTML = attrs[k];
        else el.setAttribute(k, attrs[k]);
      }
    }
    (children || []).forEach((c) => {
      if (c == null) return;
      el.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return el;
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  async function apiGet(url) {
    const res = await fetch(url, { method: "GET", headers: { "Accept": "application/json" }, credentials: "include" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }
  async function apiPost(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json().catch(() => ({}));
  }

  // Достаём имя пользователя (как в app.js: name -> username -> "друг")
  async function fetchUsername() {
    try {
      const res = await fetch("/api/auth/me", { credentials: "include" });
      if (!res.ok) return "друг";
      const d = await res.json();
      const first = (d.name && String(d.name).split(" ")[0]) || d.username || "друг";
      return first;
    } catch (e) { return "друг"; }
  }

  // ── Рендер hero-слота (картинка кота или плейсхолдер) ─────────────────────
  function heroSlot(side, mode, assetKey) {
    // side: "left" | "right"; mode: "full" | "peek"; assetKey: ключ HERO_ASSETS
    const cls = "wn-hero wn-hero--" + side + " wn-hero--" + (mode || "full");
    const box = h("div", { class: cls, "data-hero-slot": side }, []);
    const asset = assetKey ? HERO_ASSETS[assetKey] : null;

    if (asset && asset.light) {
      const img = h("img", {
        class: "wn-hero-img js-theme-icon",
        src: asset.light,
        "data-icon-light": asset.light,
        "data-icon-dark": asset.dark || asset.light,
        alt: "CatchPM герой",
      }, []);
      box.appendChild(img);
    } else {
      // Плейсхолдер с табличкой [>_]
      box.appendChild(h("div", { class: "wn-hero-ph" }, [
        h("span", { class: "wn-hero-tag" }, ["[>_]"]),
        h("span", { class: "wn-hero-cap" }, ["кот-герой\nздесь"]),
      ]));
    }
    return box;
  }

  // Центральная картинка героев (под основным текстом, напр. на финале).
  function heroCenter(assetKey) {
    const asset = assetKey ? HERO_ASSETS[assetKey] : null;
    const box = h("div", { class: "wn-hero-center" }, []);
    if (asset && asset.light) {
      box.appendChild(h("img", {
        class: "wn-hero-center-img js-theme-icon",
        src: asset.light,
        "data-icon-light": asset.light,
        "data-icon-dark": asset.dark || asset.light,
        alt: "CatchPM герои",
      }, []));
    } else {
      box.appendChild(h("div", { class: "wn-hero-ph wn-hero-ph--center" }, [
        h("span", { class: "wn-hero-tag" }, ["[>_]"]),
        h("span", { class: "wn-hero-cap" }, ["коты-герои здесь"]),
      ]));
    }
    return box;
  }

  // Блок категории инструментов (как строка в панели тулов: название + описание)
  function catBlock(label, desc) {
    return h("div", { class: "wn-cat" }, [
      h("span", { class: "wn-cat-label" }, [label]),
      h("span", { class: "wn-cat-desc" }, [desc]),
    ]);
  }

  // ── Контент слайдов ───────────────────────────────────────────────────────
  // Каждый слайд возвращает { hero: {side,mode,asset}|null, node: HTMLElement }
  function buildSlides(username) {
    const slides = [];

    // ── Слайд 1. Приветствие + иконка приложения ──
    slides.push({
      hero: null,
      node: h("div", {}, [
        h("img", {
          class: "wn-app-icon js-theme-icon",
          src: "/static/img/icon_black.png",
          "data-icon-light": "/static/img/icon_black.png",
          "data-icon-dark": "/static/img/icon_white.png",
          alt: "CatchPM icon",
        }, []),
        h("h3", { class: "wn-title",
          html: 'Привет, <span class="username">' + esc(username) + "</span> 👋" }, []),
        h("p", { class: "wn-text center" }, [
          "Это CatchPM — рабочее пространство с ИИ-агентом. Ставьте задачи в " +
          "диалоговом окне: агент ищет, анализирует, пишет код и собирает результат.",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Диалог с агентом</b> — задачи текстом, пошаговые рассуждения и проверяемый ответ." }, []),
          h("li", { html: "<b>Чаты</b> — создавайте отдельные диалоги под разные темы, история сохраняется." }, []),
          h("li", { html: "<b>Генерация файлов</b> — презентации, отчёты, документы и изображения в разных форматах." }, []),
          h("li", { html: "<b>Журнал агента</b> — агент запоминает важные факты и артефакты в рамках чата." }, []),
          h("li", { html: "<b>Process Mining</b> — дайджесты и поиск по научным статьям." }, []),
        ]),
      ]),
    });

    // ── Слайд 2. Главная страница: разбор интерфейса ──
    slides.push({
      hero: { side: "right", mode: "full", asset: "duo" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Главная страница"]),
        h("img", { class: "wn-shot", src: "/static/img/onboarding/main_screen.png",
          alt: "Главная страница CatchPM",
          onerror: "this.style.display='none'" }, []),
        h("p", { class: "wn-text" }, [
          "Слева — сайдбар управления. Здесь всё, чтобы держать работу под рукой:",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Создание чатов</b> — новая кнопка запускает свежий диалог." }, []),
          h("li", { html: "<b>Хранение диалогов</b> — все ваши чаты списком, можно вернуться к любому." }, []),
          h("li", { html: "<b>Кнопка профиля</b> — настройки пользователя внизу сайдбара." }, []),
          h("li", { html: "<b>Поиск по диалогам</b> — рядом с профилем, ищет по истории сообщений." }, []),
          h("li", { html: "<b>Поле ввода</b> — по центру, отсюда стартует новый запрос." }, []),
          h("li", { html: "<b>Раздел графа</b> — визуализация базы знаний платформы." }, []),
        ]),
        h("span", { class: "wn-note",
          html: "Есть смена <b>дня и ночи</b> — переключатель темы вверху. Интерфейс " +
                "полностью адаптирован под мобильные экраны." }, []),
      ]),
    });

    // ── Слайд 3. Sigma vs CatchPM ──
    slides.push({
      hero: { side: "left", mode: "peek", asset: "black" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Два интерфейса: базовый и Sigma"]),
        h("p", { class: "wn-text" }, [
          "Существует две версии интерфейса. Функционал агента в них одинаковый — " +
          "различия только в оболочке и способе входа.",
        ]),
        h("span", { class: "wn-note",
          html: "<b>Базовый CatchPM</b> — <code>catchpm.uva-advanced.ru</code><br>" +
                "Подтягивает настоящее имя из платформы УВА (authentik), можно " +
                "прикладывать файлы и изображения, поле ввода без жёсткого лимита." }, []),
        h("span", { class: "wn-note",
          html: "<b>Sigma</b> — <code>catchpm.cds.uva-advanced.ru</code><br>" +
                "Имя пользователя из УВА не подтягивается. Нельзя прикладывать файлы " +
                "и изображения, поле ввода ограничено по длине текста. Всё остальное — " +
                "как в базовом." }, []),
        h("span", { class: "wn-note",
          html: "Если вы зашли через страницу аутентификации или как гость — вы на " +
                (IS_SIGMA ? "<b>Sigma</b> (сейчас вы здесь)." : "<b>Sigma</b>.") }, []),
      ]),
    });

    // ── Слайд 4. Раздел выбора тулов ──
    slides.push({
      hero: { side: "right", mode: "peek", asset: "white" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Инструменты агента"]),
        h("img", { class: "wn-shot", src: "/static/img/onboarding/tools_panel.png",
          alt: "Панель инструментов",
          onerror: "this.style.display='none'" }, []),
        h("p", { class: "wn-text" }, [
          "Кнопка инструментов в поле ввода открывает набор возможностей, " +
          "сгруппированных по категориям. Включайте только то, что нужно под задачу:",
        ]),
        h("div", { class: "wn-cats" }, [
          catBlock("Process Mining", "Поиск статей, ревью, дайджесты, PDF"),
          catBlock("Интернет", "Поиск в сети и парсинг страниц по ссылке"),
          catBlock("Параллелизм", "Разбиение сложных задач: удалённые области (песочница/фабрика) для запуска скриптов (~1 час), встроенный браузер для сложных страниц"),
          catBlock("Контент и файлы", "Генерация PPTX, DOCX, Excel, PNG — презентации, отчёты, изображения по промту"),
          catBlock("Сессии и память", "Журнал агента: память переданных артефактов, архивация записей, управление размером контекста"),
          catBlock("Администрирование", "БД, кодовая база, библиотека скриптов (доступно только команде разработки)"),
        ]),
      ]),
    });

    // ── Слайд 5. Выбор модели ──
    slides.push({
      hero: { side: "left", mode: "full", asset: "duo" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Выбор модели"]),
        h("img", { class: "wn-shot", src: "/static/img/onboarding/model_picker.png",
          alt: "Выбор модели",
          onerror: "this.style.display='none'" }, []),
        h("p", { class: "wn-text" }, [
          "В поле ввода рядом с инструментами — переключатель модели. Под задачу " +
          "выбирайте подходящую:",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Кодер</b> — сбор данных, написание скриптов .py, работа с памятью." }, []),
          h("li", { html: "<b>Дизайнер</b> — отчёты и презентации: красиво оформляет, легко читаемо. В базовом интерфейсе показывает ход рассуждений." }, []),
          h("li", { html: "<b>Универсал</b> — лёгкий запасной вариант, если обе модели недоступны." }, []),
        ]),
        h("span", { class: "wn-note",
          html: "Доступность моделей видно прямо в этом же переключателе. Если агент " +
                "выдаёт ошибки — загляните в список моделей: там отмечено, какие " +
                "сейчас недоступны на стороне cloud.ru (провайдер иногда временно недоступен)." }, []),
      ]),
    });

    // ── Слайд 6. Возможности администратора ──
    slides.push({
      hero: { side: "right", mode: "peek", asset: "black" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Режим администратора"]),
        h("p", { class: "wn-text" }, [
          "У главного разработчика есть отдельный набор возможностей — он обкатывает " +
          "новые функции перед тем, как отдать их пользователям.",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Внедрение и обкатка</b> новых функций до релиза." }, []),
          h("li", { html: "<b>Запросы к базе данных</b> напрямую." }, []),
          h("li", { html: "<b>Поднятие приложений</b> и работа с инфраструктурой." }, []),
        ]),
        h("span", { class: "wn-note",
          html: "Обычным пользователям эти инструменты недоступны — по техническим " +
                "вопросам обращайтесь к команде разработки." }, []),
      ]),
    });

    // ── Слайд 7. Финал ──
    // Без боковых героев — одна картинка с котами по центру под текстом.
    slides.push({
      hero: null,
      node: h("div", {}, [
        h("img", {
          class: "wn-app-icon js-theme-icon",
          src: "/static/img/icon_black.png",
          "data-icon-light": "/static/img/icon_black.png",
          "data-icon-dark": "/static/img/icon_white.png",
          alt: "CatchPM icon",
        }, []),
        h("h3", { class: "wn-title" }, ["Погнали!"]),
        h("p", { class: "wn-text center" }, [
          "Всё готово. Открывайте новый чат, ставьте задачу — и поехали. " +
          "Добро пожаловать в CatchPM 🚀",
        ]),
        heroCenter("duo"),
      ]),
    });

    return slides;
  }

  // ── Оверлей ────────────────────────────────────────────────────────────────
  let overlayEl = null;
  function ensureOverlay() {
    if (overlayEl) return overlayEl;
    overlayEl = h("div", { class: "wn-overlay", id: "whatsnew-overlay",
      role: "dialog", "aria-modal": "true" }, []);
    document.body.appendChild(overlayEl);
    return overlayEl;
  }
  function openOverlay(cardEl) {
    const ov = ensureOverlay();
    ov.innerHTML = "";
    ov.appendChild(cardEl);
    requestAnimationFrame(() => ov.classList.add("active"));
  }
  function closeOverlay() {
    if (!overlayEl) return;
    overlayEl.classList.add("closing");
    setTimeout(() => {
      overlayEl.classList.remove("active", "closing");
      overlayEl.innerHTML = "";
    }, 280);
  }

  // Синхронизируем src героев/иконок с текущей темой (theme.js делает это на
  // toggle, но при первом рендере модалки нужно выставить руками).
  function syncThemeIcons(scope) {
    const theme = document.documentElement.getAttribute("data-theme") || "light";
    (scope || document).querySelectorAll(".js-theme-icon").forEach((node) => {
      const l = node.getAttribute("data-icon-light");
      const d = node.getAttribute("data-icon-dark");
      if (l && d) node.src = theme === "dark" ? d : l;
    });
  }

  // ── Онбординг-слайдер ──────────────────────────────────────────────────────
  async function showOnboarding() {
    const username = await fetchUsername();
    const slides = buildSlides(username);
    const total = slides.length;

    return new Promise((resolve) => {
      let idx = 0;

      // Прогресс-сегменты
      const progress = h("div", { class: "wn-progress" },
        slides.map(() => h("div", { class: "wn-progress-seg" }, [])));

      const closeBtn = h("button", { class: "wn-close", type: "button", "aria-label": "Пропустить" }, []);
      closeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

      const stage = h("div", { class: "wn-stage" }, []);

      const backBtn = h("button", { class: "wn-btn wn-btn-secondary", type: "button" }, ["Назад"]);
      const skipBtn = h("button", { class: "wn-btn wn-btn-ghost", type: "button" }, ["Пропустить"]);
      const nextBtn = h("button", { class: "wn-btn wn-btn-primary", type: "button" }, ["Далее"]);

      function render() {
        const s = slides[idx];

        // Прогресс
        progress.querySelectorAll(".wn-progress-seg").forEach((seg, i) =>
          seg.classList.toggle("filled", i <= idx));

        // Сцена: [heroLeft?] [scroll] [heroRight?]
        stage.innerHTML = "";
        const hero = s.hero;
        if (hero && hero.side === "left") stage.appendChild(heroSlot("left", hero.mode, hero.asset));

        const scroll = h("div", { class: "wn-scroll" }, [s.node]);
        stage.appendChild(scroll);

        if (hero && hero.side === "right") stage.appendChild(heroSlot("right", hero.mode, hero.asset));

        // Кнопки
        backBtn.style.visibility = idx === 0 ? "hidden" : "visible";
        nextBtn.textContent = (idx === total - 1) ? "Поехали" : "Далее";
        skipBtn.style.display = (idx === total - 1) ? "none" : "";

        scroll.scrollTop = 0;
        syncThemeIcons(stage);
      }

      function finish() { closeOverlay(); resolve(); }

      nextBtn.addEventListener("click", () => {
        if (idx < total - 1) { idx++; render(); } else finish();
      });
      backBtn.addEventListener("click", () => { if (idx > 0) { idx--; render(); } });
      skipBtn.addEventListener("click", finish);
      closeBtn.addEventListener("click", finish);

      const card = h("div", { class: "wn-card wn-card-onboarding" }, [
        h("div", { class: "wn-topbar" }, [progress, closeBtn]),
        stage,
        h("div", { class: "wn-footer" }, [
          h("div", { class: "wn-footer-left" }, [skipBtn]),
          h("div", { class: "wn-footer-left" }, [backBtn, nextBtn]),
        ]),
      ]);

      render();
      openOverlay(card);
      syncThemeIcons(card);
    });
  }

  // ── Модалка одного обновления ──────────────────────────────────────────────
  function showUpdate(update) {
    return new Promise((resolve) => {
      const dateStr = update.created_at
        ? new Date(update.created_at).toLocaleDateString("ru-RU",
            { day: "numeric", month: "long", year: "numeric" })
        : "";

      const closeBtn = h("button", { class: "wn-close", type: "button", "aria-label": "Закрыть" }, []);
      closeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

      const okBtn = h("button", { class: "wn-btn wn-btn-primary", type: "button" }, ["Понятно"]);

      const rawBody = update.body || "";
      const safeBody = (window.DOMPurify ? window.DOMPurify.sanitize(rawBody) : rawBody);

      const card = h("div", { class: "wn-card wn-card-update" }, [
        h("div", { class: "wn-topbar" }, [
          h("div", { class: "wn-progress", style: "flex:1;align-items:center;gap:8px;" }, [
            h("span", { class: "wn-badge" }, [update.version ? "v" + update.version : "Обновление"]),
            dateStr ? h("span", { class: "wn-update-date" }, [dateStr]) : null,
          ]),
          closeBtn,
        ]),
        h("div", { class: "wn-stage" }, [
          h("div", { class: "wn-scroll" }, [
            h("h3", { class: "wn-title", style: "text-align:left;" }, [update.title || "Что нового"]),
            h("div", { class: "wn-update-body", html: safeBody }, []),
          ]),
        ]),
        h("div", { class: "wn-footer" }, [
          h("span", {}, []),
          okBtn,
        ]),
      ]);

      function done() { closeOverlay(); resolve(); }
      closeBtn.addEventListener("click", done);
      okBtn.addEventListener("click", done);

      openOverlay(card);
    });
  }

  // ── Оркестрация ────────────────────────────────────────────────────────────
  async function run() {
    let data;
    try { data = await apiGet(API.pending); }
    catch (e) { return; }   // не авторизован / сервис недоступен — молча выходим
    if (!data) return;

    if (data.demo) {
      await showOnboarding();
      try { await apiPost(API.demoSeen, {}); } catch (e) { /* не критично */ }
    }

    const updates = Array.isArray(data.updates) ? data.updates : [];
    for (const upd of updates) {
      await showUpdate(upd);
      try { await apiPost(API.updateSeen, { update_id: upd.id }); } catch (e) { /* не критично */ }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
