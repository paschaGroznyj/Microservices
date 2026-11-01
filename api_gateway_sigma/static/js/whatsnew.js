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
 * ГЕРОИ (боковые иллюстрации-компаньоны):
 *   - две фигуры: тёмная и светлая, выглядывают сбоку слайда;
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

  // ── Мини-markdown для тела обновления ───────────────────────────
  // Поддержка подмножества, реально используемого в changelog:
  //   ## / ### заголовки, "- " списки, **bold**, *italic*, `code`, абзацы.
  // Весь текст экранируется через esc() ДО вставки инлайн-токенов,
  // поэтому в разметку попадают только разрешённые теги.
  function wnInline(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    return s;
  }

  function wnRenderMarkdown(md) {
    const lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let inList = false;
    let para = [];
    const flushPara = () => {
      if (para.length) { out.push("<p>" + wnInline(para.join(" ")) + "</p>"); para = []; }
    };
    const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      if (!line.trim()) { flushPara(); closeList(); continue; }
      let m;
      if ((m = line.match(/^###\s+(.*)$/))) {
        flushPara(); closeList();
        out.push("<h4>" + wnInline(m[1]) + "</h4>");
      } else if ((m = line.match(/^##\s+(.*)$/))) {
        flushPara(); closeList();
        out.push("<h3>" + wnInline(m[1]) + "</h3>");
      } else if ((m = line.match(/^[-*]\s+(.*)$/))) {
        flushPara();
        if (!inList) { out.push("<ul>"); inList = true; }
        out.push("<li>" + wnInline(m[1]) + "</li>");
      } else {
        closeList();
        para.push(line.trim());
      }
    }
    flushPara(); closeList();
    return out.join("");
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
        alt: "AuGent герой",
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
        h("span", { class: "wn-hero-cap" }, ["иллюстрация\nздесь"]),
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

  const ICON_GEN_UI = '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<rect x="3" y="3" width="7" height="9" rx="1.5"/>' +
    '<rect x="14" y="3" width="7" height="5" rx="1.5"/>' +
    '<rect x="14" y="12" width="7" height="9" rx="1.5"/>' +
    '<rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>';

  const ICON_EDITOR = '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
    '<polyline points="16 18 22 12 16 6"/>' +
    '<polyline points="8 6 2 12 8 18"/></svg>';

  // Иконки моделей (из tools-panel.js MP_MODELS: coder/designer/universal/gemini).
  const MODEL_ICONS = [
    { label: "GPT", accent: "#10a37f", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M12 3.2 L18.6 7 V15 L12 18.8 L5.4 15 V7 Z"/>' +
      '<path d="M12 3.2 V11 L18.6 15"/><path d="M5.4 7 L12 11 L12 18.8"/></svg>' },
    { label: "Claude", accent: "#d97757", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
      '<line x1="12" y1="4.5" x2="12" y2="19.5"/>' +
      '<line x1="5.8" y1="8" x2="18.2" y2="16"/>' +
      '<line x1="18.2" y1="8" x2="5.8" y2="16"/></svg>' },
    { label: "GLM", accent: "#8b5cf6", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M7 6 H17 L8 14 H17"/><path d="M12 14 V20"/></svg>' },
    { label: "Gemini", accent: "#4285f4", svg:
      '<svg viewBox="0 0 24 24" fill="none" width="24" height="24" stroke="currentColor" ' +
      'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M12 2.5C12 7.8 16.2 12 21.5 12C16.2 12 12 16.2 12 21.5C12 16.2 7.8 12 2.5 12C7.8 12 12 7.8 12 2.5Z"/></svg>' },
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
        alt: "AuGent герои",
      }, []));
    } else {
      box.appendChild(h("div", { class: "wn-hero-ph wn-hero-ph--center" }, [
        h("span", { class: "wn-hero-tag" }, ["[>_]"]),
        h("span", { class: "wn-hero-cap" }, ["иллюстрация здесь"]),
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
          src: IS_SIGMA ? "/static/img/sage_sigma_black.png" : "/static/img/sage_black.png",
          "data-icon-light": IS_SIGMA ? "/static/img/sage_sigma_black.png" : "/static/img/sage_black.png",
          "data-icon-dark": IS_SIGMA ? "/static/img/sage_sigma_white.png" : "/static/img/sage_white.png",
          alt: IS_SIGMA ? "AuGent Sigma icon" : "AuGent icon",
        }, []),
        h("h3", { class: "wn-title",
          html: 'Привет, <span class="username">' + esc(username) + "</span> 👋" }, []),
        h("p", { class: "wn-text center" }, [
          "Это AuGent — рабочее пространство с ИИ-агентом. Ставьте задачи в " +
          "диалоговом окне: агент ищет, анализирует, пишет код и собирает результат.",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Диалог с агентом</b> — задачи текстом, пошаговые рассуждения и проверяемый ответ." }, []),
          h("li", { html: "<b>Чаты</b> — создавайте отдельные диалоги под разные темы, история сохраняется." }, []),
          h("li", { html: "<b>Генерация файлов</b> — презентации, отчёты, документы и изображения в разных форматах." }, []),
          h("li", { html: "<b>Журнал агента</b> — агент запоминает важные факты и артефакты в рамках чата." }, []),
        ]),
      ]),
    });

    // ── Слайд 2. Главная страница: разбор интерфейса ──
    slides.push({
      hero: null,
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Главная страница"]),
        h("p", { class: "wn-text" }, [
          "Слева — сайдбар управления и быстрый доступ ко всем разделам:",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Диалоги и поиск</b> — создавайте чаты, закрепляйте важные и ищите сообщения по всей истории." }, []),
          h("li", { html: "<b>Общий чат</b> — живая лента команды с медиа-коллажами, закрепами и индикатором участников онлайн." }, []),
          h("li", { html: "<b>Сохранение настроек</b> — выбранная модель, инструменты, скилл, тема и открытый чат сохраняются между сессиями." }, []),
          h("li", { html: "<b>Слэш-команды</b> — введите <code>/workspaces</code> или <code>/chat-id</code> в поле ввода для быстрого вызова меню." }, []),
        ]),
        h("span", { class: "wn-note",
          html: "Есть смена <b>дня и ночи</b> и выбор цветовой палитры в профиле. Интерфейс " +
                "полностью адаптирован под мобильные экраны." }, []),
      ]),
    });

    // ── Слайд 3. Sigma vs AuGent ──
    slides.push({
      hero: null,
      node: h("div", {}, [
        h("h3", { class: "wn-title wn-title--sidebar-header" }, ["Два интерфейса: базовый и Sigma"]),
        h("p", { class: "wn-text" }, [
          "Существует две версии интерфейса. Функционал агента в них одинаковый — " +
          "различия только в оболочке и способе входа.",
        ]),
        h("span", { class: "wn-note",
          html: "<b>Базовый AuGent</b> — <code>augent.uva-advanced.ru</code><br>" +
                "Подтягивает настоящее имя из платформы УВА (authentik), интерфейсы " +
                "синхронизируются по имени пользователя. Можно прикладывать файлы " +
                "и изображения, поле ввода без жёсткого лимита." }, []),
        h("span", { class: "wn-note",
          html: "<b>Sigma</b> — <code>augent.cds.uva-advanced.ru</code><br>" +
                "Подтягивает настоящее имя из платформы УВА (authentik), интерфейсы " +
                "синхронизируются по имени пользователя. Нельзя прикладывать файлы " +
                "и изображения, поле ввода ограничено по длине текста. Всё остальное — " +
                "как в базовом." }, []),
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
          // catBlock("Process Mining", "Поиск статей, ревью, дайджесты, PDF"),
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
          h("li", { html: "<b>GPT</b> — модель от OpenAI. Код, работа с инструментами и данными, длинный контекст." }, []),
          h("li", { html: "<b>Claude</b> — модель от Anthropic. Сильна в коде, делает отчёты и презентации. Показывает ход рассуждений." }, []),
          h("li", { html: "<b>GLM</b> — модель от Zhipu AI. Универсальная, показывает ход рассуждений." }, []),
          h("li", { html: "<b>Gemini</b> — модель от Google. Быстрый анализ данных, кода, мультимодальных задач и больших текстов." }, []),
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
          h("li", { html: "<b>Карточки и превью</b> — переключение между красивым Markdown-превью и исходной разметкой." }, []),
          h("li", { html: "<b>Поиск и избранное</b> — сортировка по новизне, популярности и фильтр по вашим лайкам." }, []),
          h("li", { html: "<b>Перетаскивание в чат</b> — перетяните карточку в поле ввода — промт подставится автоматически." }, []),
          h("li", { html: "<b>Сохранение выбора</b> — выбранный скилл отмечается чекбоксом и сохраняется для следующих запросов." }, []),
        ]),
        h("span", { class: "wn-note",
          html: "Собранный скилл можно отредактировать или удалить прямо из карточки в магазине." }, []),
      ]),
    });

    // ── Слайд 7. Динамический UI и медиа ──
    slides.push({
      hero: { side: "right", mode: "peek", asset: "black" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Интерактивный UI и медиа"]),
        iconRow([{ svg: ICON_GEN_UI, label: "Generative UI & Media" }]),
        h("p", { class: "wn-text" }, [
          "Агент встраивает интерактивные виджеты и медиаплееры прямо в поток ответа:",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Диаграммы и KPI</b> — круговые, столбчатые, линейные и точечные графики, а также карточки метрик с выгрузкой в PNG." }, []),
          h("li", { html: "<b>Интерактивные таблицы и опросы</b> — таблицы с поиском и сортировкой, карточки опросов для быстрого выбора варианта." }, []),
          h("li", { html: "<b>Рендер медиафайлов</b> — потоковое видео, адаптивные коллажи с лайтбоксом и матричный аудиоплеер." }, []),
          h("li", { html: "<b>Чипы длинного текста</b> — объёмные вставки сворачиваются в компактные плашки с боковой панелью редактирования." }, []),
        ]),
      ]),
    });

    // ── Слайд 8. Веб-редактор Monaco и воркспейсы ──
    slides.push({
      hero: { side: "left", mode: "peek", asset: "white" },
      node: h("div", {}, [
        h("h3", { class: "wn-title" }, ["Редактор кода и воркспейсы"]),
        iconRow([{ svg: ICON_EDITOR, label: "Monaco Editor" }]),
        h("p", { class: "wn-text" }, [
          "Для работы с кодом, скриптами и собранными артефактами встроен полноценный веб-редактор:",
        ]),
        h("ul", { class: "wn-list" }, [
          h("li", { html: "<b>Редактор Monaco (/editor)</b> — дерево файлов проекта, подсветка синтаксиса, сохранение правок и скачивание по ПКМ." }, []),
          h("li", { html: "<b>Панель агента рядом с кодом</b> — обсуждайте открытый файл с ИИ-ассистентом прямо внутри редактора." }, []),
          h("li", { html: "<b>Команда /workspaces</b> — быстрый переход к рабочим областям текущего чата и закрепление постоянных воркспейсов." }, []),
          h("li", { html: "<b>Интеграция с Telegram и MAX</b> — подключение агента к рабочим группам с настройкой модели, тулов и расписания триггеров." }, []),
        ]),
      ]),
    });

    // ── Слайд 9. Возможности администратора ──
    // Сцена-дуэт снизу: кот в короне/плаще + кот за терминалом "admin mode"
    // (scene_admin.png), статичная, без анимации/drag.
    slides.push({
      hero: null,
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
          h("li", { html: "<b>Генерация медиа</b> — озвучка, видео и музыкальные композиции (работает на стороннем провайдере)." }, []),
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
          src: IS_SIGMA ? "/static/img/sage_sigma_black.png" : "/static/img/sage_black.png",
          "data-icon-light": IS_SIGMA ? "/static/img/sage_sigma_black.png" : "/static/img/sage_black.png",
          "data-icon-dark": IS_SIGMA ? "/static/img/sage_sigma_white.png" : "/static/img/sage_white.png",
          alt: IS_SIGMA ? "AuGent Sigma icon" : "AuGent icon",
        }, []),
        h("h3", { class: "wn-title" }, ["В добрый путь"]),
        h("p", { class: "wn-text center" }, [
          "Всё готово. Откройте новый чат и сформулируйте задачу — " +
          "агент возьмётся за дело. Добро пожаловать в AuGent.",
        ]),
        null,
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
    if (!overlayEl) return Promise.resolve();
    overlayEl.classList.add("closing");
    return new Promise((res) => {
      setTimeout(() => {
        overlayEl.classList.remove("active", "closing");
        overlayEl.innerHTML = "";
        res();
      }, 280);
    });
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

      function finish() { closeOverlay().then(resolve); }

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

    // ── Модалка обновлений с навигацией по истории ──────────────────────────────
    function showUpdates(updates, onSeen) {
      return new Promise((resolve) => {
        // Сортируем от старых к новым (устойчиво к порядку с бэка).
        const list = updates.slice().sort((a, b) => {
          const ta = a.created_at ? Date.parse(a.created_at) : 0;
          const tb = b.created_at ? Date.parse(b.created_at) : 0;
          if (ta !== tb) return ta - tb;
          return (a.id || 0) - (b.id || 0);
        });
        const total = list.length;
        let idx = total - 1;              // стартуем на самом свежем
        const seen = new Set();           // чтобы не слать seen повторно

        const closeBtn = h("button", { class: "wn-close", type: "button", "aria-label": "Закрыть" }, []);
        closeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" ' +
          'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

        const badge   = h("span", { class: "wn-badge" }, []);
        const dateEl  = h("span", { class: "wn-update-date" }, []);
        const topInfo = h("div", { class: "wn-update-head" }, [badge, dateEl]);

        const titleEl = h("h3", { class: "wn-title", style: "text-align:left;" }, []);
        const bodyEl  = h("div", { class: "wn-update-body" }, []);
        const scroll  = h("div", { class: "wn-scroll" }, [titleEl, bodyEl]);

        // Кнопка "Предыдущие" (листать назад), "Дальше" (к более свежим), "Понятно".
        const prevBtn = h("button", { class: "wn-btn wn-btn-secondary", type: "button" }, ["👈Предыдущие"]);
        const fwdBtn  = h("button", { class: "wn-btn wn-btn-ghost", type: "button" }, ["Дальше👉"]);
        const okBtn   = h("button", { class: "wn-btn wn-btn-primary", type: "button" }, ["Понятно"]);

        function markSeen(i) {
          const u = list[i];
          if (!u || seen.has(u.id)) return;
          seen.add(u.id);
          try { onSeen && onSeen(u); } catch (e) { /* не критично */ }
        }

        function render() {
          const u = list[idx];
          const dateStr = u.created_at
            ? new Date(u.created_at).toLocaleDateString("ru-RU",
                { day: "numeric", month: "long", year: "numeric" })
            : "";

          badge.textContent = u.version
            ? (/^v/i.test(u.version) ? u.version : "v" + u.version)
            : "Обновление";
          dateEl.textContent = dateStr;
          dateEl.style.display = dateStr ? "" : "none";

          titleEl.textContent = u.title || "Что нового";
          const htmlBody = wnRenderMarkdown(u.body || "");
          bodyEl.innerHTML = (window.DOMPurify ? window.DOMPurify.sanitize(htmlBody) : htmlBody);

          // Навигация: idx==0 — самое старое, idx==total-1 — самое свежее.
          prevBtn.style.display = idx > 0 ? "" : "none";
          fwdBtn.style.display  = idx < total - 1 ? "" : "none";

          // Счётчик позиции, если обновлений больше одного.
          if (total > 1) {
            prevBtn.textContent = "👈Предыдущие (" + idx + ")";
          }

          markSeen(idx);
          scroll.scrollTop = 0;
          syncThemeIcons(scroll);
        }

        function done() { closeOverlay().then(resolve); }

        prevBtn.addEventListener("click", () => { if (idx > 0) { idx--; render(); } });
        fwdBtn.addEventListener("click",  () => { if (idx < total - 1) { idx++; render(); } });
        okBtn.addEventListener("click", done);
        closeBtn.addEventListener("click", done);

        const card = h("div", { class: "wn-card wn-card-update" }, [
          h("div", { class: "wn-topbar" }, [topInfo, closeBtn]),
          h("div", { class: "wn-stage" }, [scroll]),
          h("div", { class: "wn-footer" }, [
            h("div", { class: "wn-footer-left" }, [prevBtn, fwdBtn]),
            okBtn,
          ]),
        ]);

        render();
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
    if (updates.length) {
      await showUpdates(updates, (upd) => {
        apiPost(API.updateSeen, { update_id: upd.id }).catch(() => {});
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
