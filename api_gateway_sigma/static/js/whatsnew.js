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
  // Один PNG на обе темы: light и dark указывают на один и тот же файл.
  // Для героев с боковым "выглядыванием" (peek) поза зависит от стороны слайда:
  // поля peekLeft/peekRight — картинки под соответствующий край.
  const HERO_BASE = "/static/img/onboarding/heroes/";
  const HERO_ASSETS = {
    // Дуэт: обнимаются (центр/full) — под слайды 2, 5 и финал.
    duo:      { light: HERO_BASE + "duo_hug.png",  dark: HERO_BASE + "duo_hug.png" },
    // Оба выглядывают из-за края.
    duo_peek: { light: HERO_BASE + "duo_peek.png", dark: HERO_BASE + "duo_peek.png" },
    // Чёрный кот: idle + peek с обеих сторон.
    black: {
      light:     HERO_BASE + "hero_black_idle.png",
      dark:      HERO_BASE + "hero_black_idle.png",
      peekLeft:  HERO_BASE + "hero_black_peek_l.png",
      peekRight: HERO_BASE + "hero_black_peek_r.png",
    },
    // Белый кот: idle + peek с обеих сторон.
    white: {
      light:     HERO_BASE + "hero_white_idle.png",
      dark:      HERO_BASE + "hero_white_idle.png",
      peekLeft:  HERO_BASE + "hero_white_peek_l.png",
      peekRight: HERO_BASE + "hero_white_peek_r.png",
    },
    // Сцена Sigma: отдельный обновлённый ассет.
    sigma_scene: { light: HERO_BASE + "scene_sigma_v2.png", dark: HERO_BASE + "scene_sigma_v2.png" },
    // Сцена админа: кот в короне/плаще + кот за терминалом, "admin mode".
    admin_scene: { light: HERO_BASE + "scene_admin.png", dark: HERO_BASE + "scene_admin.png" },
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

      const rawName = (d.name || "").trim();
      const firstFromName = (rawName && rawName !== "Unknown")
        ? rawName.split(/\s+/)[0]
        : "";
      const username = (d.username || "").trim();

      // Два сценария в Sigma:
      // 1) authentik: показываем имя из X-Authentik-Name, если оно валидно;
      // 2) login-page (internal): показываем username.
      if (d.source === "authentik") return firstFromName || username || "друг";
      return username || firstFromName || "друг";
    } catch (e) { return "друг"; }
  }

  // ── Drag героя (мышь + тач) ───────────────────────────────────────────────
  // axis="y" — боковые коты (peek), таскаются по вертикали вплоть до крестика
  //            закрытия сверху (широкий диапазон вверх).
  // axis="x" — duo снизу, таскается влево/вправо.
  // Смещение хранится в CSS-переменных --wn-hero-dy / --wn-hero-dx на <img>;
  // базовый transform задаётся классами в CSS через
  // translate(..., var(--wn-hero-dx,0), var(--wn-hero-dy,0)).
  // ВАЖНО: анимация въезда играет ОДИН раз при первом показе слайда. Класс
  // wn-hero-dragging насовсем гасит animation после первого взаимодействия,
  // чтобы кот не «прыгал» назад в анимацию при отпускании.
  const HERO_DRAG_Y_MIN = -240;  // вверх (px) — дотянуть до крестика закрытия
  const HERO_DRAG_Y_MAX = 60;    // вниз (px)
  const HERO_DRAG_X_MIN = -140;  // влево (px)
  const HERO_DRAG_X_MAX = 140;   // вправо (px)
  function makeHeroDraggable(img, axis) {
    const vertical = axis !== "x";
    const prop = vertical ? "--wn-hero-dy" : "--wn-hero-dx";
    const lo = vertical ? HERO_DRAG_Y_MIN : HERO_DRAG_X_MIN;
    const hi = vertical ? HERO_DRAG_Y_MAX : HERO_DRAG_X_MAX;
    let start = 0, base = 0, dragging = false;
    const getV = () => parseFloat(img.style.getPropertyValue(prop)) || 0;
    const setV = (v) => {
      const clamped = Math.max(lo, Math.min(hi, v));
      img.style.setProperty(prop, clamped + "px");
    };
    function onDown(e) {
      dragging = true;
      const pt = e.touches ? e.touches[0] : e;
      start = vertical ? pt.clientY : pt.clientX;
      base = getV();
      // Гасим анимацию НАСОВСЕМ: после первого касания кот больше не въезжает.
      img.classList.add("wn-hero-dragging");
      window.addEventListener("mousemove", onMove, { passive: false });
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("mouseup", onUp);
      window.addEventListener("touchend", onUp);
    }
    function onMove(e) {
      if (!dragging) return;
      const pt = e.touches ? e.touches[0] : e;
      const cur = vertical ? pt.clientY : pt.clientX;
      setV(base + (cur - start));
      if (e.cancelable) e.preventDefault();   // не скроллить страницу пальцем
    }
    function onUp() {
      dragging = false;
      // Класс wn-hero-dragging НЕ снимаем — анимация должна остаться выключенной,
      // позиция фиксируется на месте отпускания.
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("touchend", onUp);
    }
    img.addEventListener("mousedown", onDown);
    img.addEventListener("touchstart", onDown, { passive: true });
  }

  // ── Рендер hero-слота (картинка кота или плейсхолдер) ─────────────────────
  function heroSlot(side, mode, assetKey) {
    // side: "left" | "right"; mode: "full" | "peek" | "bottom"; assetKey: ключ HERO_ASSETS
    const cls = "wn-hero wn-hero--" + side + " wn-hero--" + (mode || "full");
    const box = h("div", { class: cls, "data-hero-slot": side, "data-hero-asset": assetKey || "" }, []);
    const asset = assetKey ? HERO_ASSETS[assetKey] : null;

    // Для peek-режима у одиночных котов берём позу под нужный край, если есть.
    let srcLight = asset && asset.light;
    let srcDark  = asset && (asset.dark || asset.light);
    if (asset && mode === "peek") {
      const peek = side === "left" ? asset.peekLeft : asset.peekRight;
      if (peek) { srcLight = peek; srcDark = peek; }
    }

    if (srcLight) {
      const img = h("img", {
        class: "wn-hero-img js-theme-icon",
        src: srcLight,
        "data-icon-light": srcLight,
        "data-icon-dark": srcDark || srcLight,
        alt: "CatchPM герой",
      }, []);
      box.appendChild(img);
      // Боковые коты (peek) — таскаются по вертикали (до крестика).
      // Duo снизу (bottom) и сцены (scene) — таскаются влево/вправо.
      if (mode === "peek" || mode === "bottom" || mode === "scene") {
        box.style.pointerEvents = "auto";
        const axis = mode === "peek" ? "y" : "x";
        if (axis === "x") {
          img.classList.add("wn-hero-drag", "wn-hero-drag--x");
        } else {
          img.classList.add("wn-hero-drag");
        }
        makeHeroDraggable(img, axis);
      }
    } else {
      // Плейсхолдер с табличкой [>_]
      box.appendChild(h("div", { class: "wn-hero-ph" }, [
        h("span", { class: "wn-hero-tag" }, ["[>_]"]),
        h("span", { class: "wn-hero-cap" }, ["кот-герой\nздесь"]),
      ]));
    }
    return box;
  }

  // ── Ряд иконок (для слайдов «Инструменты» / «Выбор модели») ──────────────
  // items: [{ svg, label, accent? }]. Рисует горизонтальную галерею.
  function iconRow(items) {
    return h("div", { class: "wn-iconrow" },
      items.map((it) => {
        const chip = h("div", { class: "wn-iconchip" }, []);
        if (it.accent) chip.style.setProperty("--wn-chip-accent", it.accent);
        chip.appendChild(h("span", { class: "wn-iconchip-ic", html: it.svg }, []));
        if (it.label) chip.appendChild(h("span", { class: "wn-iconchip-lb" }, [it.label]));
        return chip;
      }));
  }

  // SVG иконки, синхронные с реальными кнопками интерфейса.
  const ICON_TOOLS_TRIGGER = '<svg viewBox="0 0 24 24" fill="none" width="24" height="24">' +
    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77' +
    'a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91' +
    'a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round"/></svg>';

  const ICON_SKILLS = '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
    'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M13 2L4.5 12.5a.6.6 0 0 0 .46 1H11l-1 8.5L18.5 11.5a.6.6 0 0 0-.46-1H12l1-8.5z"/></svg>';

  // Иконки моделей (из tools-panel.js MP_MODELS: coder/designer/universal).
  const MODEL_ICONS = [
    { label: "Кодер", accent: "#6366f1", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<polyline points="7 8 3 12 7 16"/><polyline points="17 8 21 12 17 16"/>' +
      '<line x1="14" y1="4" x2="10" y2="20"/></svg>' },
    { label: "Дизайнер", accent: "#ec4899", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M12 2a10 10 0 0 1 0 20c-1.657 0-3-1.343-3-3 0-.828.336-1.578.879-2.121' +
      'A3 3 0 0 0 8 14.5c0-1.657 1.343-3 3-3"/>' +
      '<circle cx="8" cy="9" r="1.2" fill="currentColor" stroke="none"/>' +
      '<circle cx="12" cy="6" r="1.2" fill="currentColor" stroke="none"/>' +
      '<circle cx="16" cy="9" r="1.2" fill="currentColor" stroke="none"/></svg>' },
    { label: "Универсал", accent: "#10b981", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/>' +
      '<line x1="4" y1="18" x2="20" y2="18"/>' +
      '<circle cx="9" cy="6" r="2" fill="var(--surface)" stroke="currentColor"/>' +
      '<circle cx="15" cy="12" r="2" fill="var(--surface)" stroke="currentColor"/>' +
      '<circle cx="9" cy="18" r="2" fill="var(--surface)" stroke="currentColor"/></svg>' },
  ];

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
          src: IS_SIGMA ? "/static/img/icon_sigma_black.png" : "/static/img/icon_black.png",
          "data-icon-light": IS_SIGMA ? "/static/img/icon_sigma_black.png" : "/static/img/icon_black.png",
          "data-icon-dark": IS_SIGMA ? "/static/img/icon_sigma_white.png" : "/static/img/icon_white.png",
          alt: IS_SIGMA ? "CatchPM Sigma icon" : "CatchPM icon",
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
    // Два кота выглядывают снизу по центру (duo_peek, режим bottom).
    slides.push({
      hero: { side: "center", mode: "bottom", asset: "duo_peek" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Главная страница"]),
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
    // Герой + картинка перегенерятся отдельно (один герой + два экрана ПК).
    // Пока картинки нет — wn-shot с onerror скрывается, слайд не ломается.
    slides.push({
      hero: { side: "center", mode: "scene", asset: "sigma_scene" },
      node: h("div", {}, [
        h("h3", { class: "wn-title wn-title--sidebar-header" }, ["Два интерфейса: базовый и Sigma"]),
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
        iconRow([{ svg: ICON_TOOLS_TRIGGER, label: "Инструменты" }]),
        h("p", { class: "wn-text" }, [
          "Эта кнопка в поле ввода открывает набор возможностей, " +
          "сгруппированных по категориям. Включайте только то, что нужно под задачу:",
        ]),
        h("div", { class: "wn-cats" }, [
          catBlock("Process Mining", "Поиск статей, ревью, дайджесты, PDF"),
          catBlock("Интернет", "Поиск в сети и парсинг страниц по ссылке"),
          catBlock("Контент и файлы", "Генерация PPTX, DOCX, Excel, PNG — презентации, отчёты, изображения по промту"),
          catBlock("Бортовой журнал агента", "Долгосрочная память и точки сессий, архивация записей, управление размером контекста"),
          catBlock("Администрирование", "БД, кодовая база, библиотека скриптов (доступно только команде разработки)"),
        ]),
      ]),
    });

    // ── Слайд 5. Выбор модели ──
    slides.push({
      hero: { side: "left", mode: "peek", asset: "black" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Выбор модели"]),
        iconRow(MODEL_ICONS),
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

            // ── Слайд 6. Магазин скиллов ──
    slides.push({
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Магазин скиллов"]),
        iconRow([{ svg: ICON_SKILLS, label: "Скиллы" }]),
        h("p", { class: "wn-text" }, [
          "Скилл — это готовый промт-сценарий под типовую задачу. В магазине можно " +
          "найти чужой или собрать свой и применить в один клик:",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Карточки скиллов</b> — каждый с темой, описанием и привязкой к инструментам." }, []),
          h("li", { html: "<b>Поиск и лайки</b> — сортировка по новизне и популярности, отмечайте полезные." }, []),
          h("li", { html: "<b>Перетаскивание в чат</b> — перетяните карточку в поле ввода — промт подставится автоматически." }, []),
        ]),
        h("span", { class: "wn-note",
          html: "Собранный скилл можно отредактировать или удалить — тело скилла пишется в Markdown и " +
                "красиво рендерится при раскрытии карточки." }, []),
      ]),
    });

    // ── Слайд 7. Возможности администратора ──
    // Сцена-дуэт снизу: кот в короне/плаще + кот за терминалом "admin mode"
    // (scene_admin.png), статичная, без анимации/drag.
    slides.push({
      hero: { side: "center", mode: "scene", asset: "admin_scene" },
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

    // ── Слайд 8. Финал ──
    // Без боковых героев — одна картинка с котами по центру под текстом.
    slides.push({
      hero: null,
      node: h("div", {}, [
        h("img", {
          class: "wn-app-icon js-theme-icon",
          src: IS_SIGMA ? "/static/img/icon_sigma_black.png" : "/static/img/icon_black.png",
          "data-icon-light": IS_SIGMA ? "/static/img/icon_sigma_black.png" : "/static/img/icon_black.png",
          "data-icon-dark": IS_SIGMA ? "/static/img/icon_sigma_white.png" : "/static/img/icon_white.png",
          alt: IS_SIGMA ? "CatchPM Sigma icon" : "CatchPM icon",
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

        // Сцена: [heroLeft?] [scroll] [heroRight?]  либо  [scroll + heroBottom(overlay)]
        stage.innerHTML = "";
        const hero = s.hero;
        const isCenter = hero && hero.side === "center";
        stage.classList.toggle("wn-stage--center-hero", !!isCenter);
        // Для сцены-дуэта (scene) резервируем место внизу под картинку,
        // чтобы текст заканчивался выше и картинка его не перекрывала.
        stage.classList.toggle("wn-stage--scene-hero", !!(hero && hero.mode === "scene"));

        if (hero && hero.side === "left") stage.appendChild(heroSlot("left", hero.mode, hero.asset));

        const scroll = h("div", { class: "wn-scroll" }, [s.node]);
        stage.appendChild(scroll);

        if (hero && hero.side === "right") stage.appendChild(heroSlot("right", hero.mode, hero.asset));

        // Центральный герой снизу — оверлеем поверх сцены (не влияет на flex-поток).
        if (isCenter) stage.appendChild(heroSlot("center", hero.mode, hero.asset));

        // Кнопки
        backBtn.disabled = idx === 0;
        backBtn.setAttribute("aria-disabled", backBtn.disabled ? "true" : "false");
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
