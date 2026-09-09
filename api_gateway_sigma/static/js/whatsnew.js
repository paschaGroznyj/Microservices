/**
 * whatsnew.js — Онбординг-демо (для новых юзеров) + What's New (обновления).
 *
 * Механика "показать один раз":
 *   1. При загрузке дёргаем GET /api/whatsnew/pending -> { demo, updates: [...] }.
 *   2. Если demo=true — показываем онбординг-слайдер. По закрытию POST /api/whatsnew/demo/seen.
 *   3. После демо (или сразу, если demo=false) — прокручиваем непросмотренные
 *      обновления по одному. На каждое закрытие POST /api/whatsnew/updates/seen {update_id}.
 *
 * Разметка модалок инжектится этим скриптом в DOM (chat.html трогать не нужно,
 * кроме строки подключения). Стиль опирается на общие CSS-переменные темы
 * (--accent / --surface / --border / --radius-*), файл whatsnew.css.
 *
 * Место под главных героев (нарезка спрайтов) — блок .wn-hero[data-hero-slot]
 * на слайдах онбординга. Пока плейсхолдер, потом врежем картинки.
 */
(function () {
  "use strict";

  const API = {
    pending:     "/api/whatsnew/pending",
    demoSeen:    "/api/whatsnew/demo/seen",
    updateSeen:  "/api/whatsnew/updates/seen",
  };

  // ── Контент онбординг-демо (скелет; наполняется/правится позже) ────────────
  // Каждый слайд: заголовок, текст, и опционально hero-слот под главного героя.
  const ONBOARDING_SLIDES = [
    {
      hero: "guide",           // имя героя-слота (позже -> спрайт нарезки)
      title: "Добро пожаловать в CatchPM",
      text:  "Агентная платформа для Process Mining: сбор, анализ и синтез научной литературы — от парсинга источников до готовых дайджестов.",
    },
    {
      hero: "scout",
      title: "Умный агент под рукой",
      text:  "Задавайте вопросы в чате — агент ищет в интернете, работает с базой знаний, запускает инструменты и возвращает проверяемый результат.",
    },
    {
      hero: "keeper",
      title: "База знаний и дайджесты",
      text:  "Сохраняйте статьи, стройте обзоры и получайте регулярные подборки по вашей теме. Всё структурировано и под контролем.",
    },
  ];

  // ── Утилиты ────────────────────────────────────────────────────────────────
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

  async function apiGet(url) {
    const res = await fetch(url, { method: "GET", headers: { "Accept": "application/json" } });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  async function apiPost(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json().catch(() => ({}));
  }

  // ── Оверлей (общий каркас модалки) ─────────────────────────────────────────
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
    // на след. кадре — активируем анимацию появления
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

  // ── Онбординг-слайдер ──────────────────────────────────────────────────────
  function showOnboarding() {
    return new Promise((resolve) => {
      let idx = 0;
      const total = ONBOARDING_SLIDES.length;

      const heroBox   = h("div", { class: "wn-hero", "data-hero-slot": "" }, []);
      const titleEl   = h("h3", { class: "wn-title" }, []);
      const textEl    = h("p",  { class: "wn-text" }, []);
      const dotsWrap  = h("div", { class: "wn-dots" },
        ONBOARDING_SLIDES.map((_, i) => h("span", { class: "wn-dot", "data-i": String(i) }, [])));

      const skipBtn = h("button", { class: "wn-btn wn-btn-ghost", type: "button" }, ["Пропустить"]);
      const nextBtn = h("button", { class: "wn-btn wn-btn-primary", type: "button" }, ["Далее"]);

      function render() {
        const s = ONBOARDING_SLIDES[idx];
        heroBox.setAttribute("data-hero-slot", s.hero || "");
        // Плейсхолдер под героя (позже заменим спрайтом нарезки)
        heroBox.innerHTML = '<div class="wn-hero-ph" aria-hidden="true">' +
          '<span class="wn-hero-emoji">CatchPM</span></div>';
        titleEl.textContent = s.title;
        textEl.textContent  = s.text;
        dotsWrap.querySelectorAll(".wn-dot").forEach((d, i) =>
          d.classList.toggle("active", i === idx));
        nextBtn.textContent = (idx === total - 1) ? "Начать" : "Далее";
      }

      function finish() {
        closeOverlay();
        resolve();
      }

      nextBtn.addEventListener("click", () => {
        if (idx < total - 1) { idx++; render(); }
        else finish();
      });
      skipBtn.addEventListener("click", finish);

      const card = h("div", { class: "wn-card wn-card-onboarding" }, [
        heroBox,
        h("div", { class: "wn-body" }, [titleEl, textEl, dotsWrap]),
        h("div", { class: "wn-footer" }, [skipBtn, nextBtn]),
      ]);

      render();
      openOverlay(card);
    });
  }

  // ── Модалка одного обновления ──────────────────────────────────────────────
  function showUpdate(update) {
    return new Promise((resolve) => {
      const badge = update.version
        ? h("span", { class: "wn-badge" }, ["v" + update.version])
        : h("span", { class: "wn-badge" }, ["Обновление"]);

      const dateStr = update.created_at
        ? new Date(update.created_at).toLocaleDateString("ru-RU",
            { day: "numeric", month: "long", year: "numeric" })
        : "";

      const closeBtn = h("button", { class: "wn-close", type: "button", "aria-label": "Закрыть" }, []);
      closeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

      const okBtn = h("button", { class: "wn-btn wn-btn-primary", type: "button" }, ["Понятно"]);

      // body — HTML из Б. Санитизируем через DOMPurify, если доступен.
      const rawBody = update.body || "";
      const safeBody = (window.DOMPurify ? window.DOMPurify.sanitize(rawBody) : rawBody);

      const card = h("div", { class: "wn-card wn-card-update" }, [
        h("div", { class: "wn-update-header" }, [
          h("div", { class: "wn-update-head-left" }, [
            badge,
            dateStr ? h("span", { class: "wn-update-date" }, [dateStr]) : null,
          ]),
          closeBtn,
        ]),
        h("h3", { class: "wn-title" }, [update.title || "Что нового"]),
        h("div", { class: "wn-update-body", html: safeBody }, []),
        h("div", { class: "wn-footer" }, [okBtn]),
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
    try {
      data = await apiGet(API.pending);
    } catch (e) {
      // не авторизован / сервис недоступен — молча выходим, не мешаем работе
      return;
    }
    if (!data) return;

    // 1) Демо-онбординг (сначала демо, потом обновления — как договорились)
    if (data.demo) {
      await showOnboarding();
      try { await apiPost(API.demoSeen, {}); } catch (e) { /* не критично */ }
    }

    // 2) Обновления по очереди
    const updates = Array.isArray(data.updates) ? data.updates : [];
    for (const upd of updates) {
      await showUpdate(upd);
      try { await apiPost(API.updateSeen, { update_id: upd.id }); } catch (e) { /* не критично */ }
    }
  }

  // Старт после загрузки DOM
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
