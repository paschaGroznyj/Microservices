/**
 * whatsnew-releases.js — модалка релизов «Что нового».
 *
 * ПК (> 720 px): сплит. Слева релизы и их фичи списком, справа демо выбранной
 * фичи (видео/картинка), текст и «Дальше». Мобила: лента-шторка, свежие релизы
 * раскрыты, прошлые свёрнуты в строку.
 *
 * Данные: GET /api/whatsnew/history → { updates: [{ id, version, title, body,
 * created_at, is_published, items: [{ kind, title, body, media }] }] }.
 * media: { type: "video"|"image", webm, mp4, src, poster, thumbhash, w, h }.
 * Медиа берутся напрямую из OBS: обычные <video>/<img> без crossorigin, CORS не нужен.
 * У старых релизов items пуст — показываем body (markdown) как раньше.
 *
 * Загрузка медиа: thumbhash (или пульс-скелетон) → постер → видео. Видео с
 * preload="none" и играет, только пока видно.
 *
 * Грузится лениво из whatsnew.js. API: window.WhatsNewReleases.open(opts) → Promise.
 */
(function () {
  "use strict";

  const MQ = window.matchMedia("(max-width: 720px)");
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
  const KIND = {
    new:     { label: "Новое",       cls: "new" },
    improve: { label: "Улучшено",    cls: "imp" },
    fix:     { label: "Исправления", cls: "fix" },
  };
  const ICON = {
    x:    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
    next: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3l5 5-5 5"/></svg>',
    chev: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>',
    ok:   '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6.2"/><path d="M5.4 8.2l1.8 1.8 3.4-3.6"/></svg>',
    doc:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2.5h5.5L12 5v8.5H4z"/><path d="M9.5 2.5V5H12M6 8h4M6 10.5h3"/></svg>',
    play: '<svg viewBox="0 0 12 12" fill="currentColor"><path d="M3.5 2.2v7.6L9.8 6z"/></svg>',
    pause:'<svg viewBox="0 0 12 12" fill="currentColor"><rect x="2.5" y="2" width="2.4" height="8" rx=".6"/><rect x="7.1" y="2" width="2.4" height="8" rx=".6"/></svg>',
  };

  // ── утилиты ──────────────────────────────────────────────────────────────
  function el(tag, cls, html) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function txt(tag, cls, s) {
    const n = el(tag, cls);
    n.textContent = s == null ? "" : String(s);
    return n;
  }
  function md(s) {
    const r = window.WhatsNew && window.WhatsNew.md;
    if (r) return r(s || "");
    const d = document.createElement("div");
    d.textContent = s || "";
    return "<p>" + d.innerHTML + "</p>";
  }
  function safeUrl(u) {
    return typeof u === "string" && /^https?:\/\//i.test(u) ? u : null;
  }
  function verLabel(v) {
    if (!v) return "Обновление";
    return /^v/i.test(v) ? v : "v" + v;
  }
  function dateLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const o = { day: "numeric", month: "long" };
    if (d.getFullYear() !== new Date().getFullYear()) o.year = "numeric";
    return d.toLocaleDateString("ru-RU", o);
  }
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  function thumbBg(media) {
    const th = media && media.thumbhash;
    try { return th && window.MediaRing ? window.MediaRing.thumbUrl(th) : null; }
    catch (_) { return null; }
  }

  // Релиз → записи для навигации: фичи по одной, исправления одним блоком,
  // у старого релиза без фич — одна запись с markdown.
  function entriesOf(rel) {
    const items = Array.isArray(rel.items) ? rel.items : [];
    if (!items.length) return [{ type: "legacy", rel }];
    const out = [];
    const fixes = [];
    items.forEach((it) => {
      if (it.kind === "fix" && !it.media) fixes.push(it);
      else out.push({ type: "item", rel, item: it });
    });
    if (fixes.length) out.push({ type: "fixes", rel, list: fixes });
    return out;
  }

  // ── медиа ────────────────────────────────────────────────────────────────
  let io = null;
  function observer() {
    if (io) return io;
    io = new IntersectionObserver((es) => es.forEach((e) => {
      const v = e.target.querySelector("video");
      if (!v || e.target.dataset.user === "pause" || REDUCED.matches) return;
      if (e.isIntersecting) v.play().catch(() => {});
      else v.pause();
    }), { threshold: 0.5 });
    return io;
  }

  function mediaNode(media, opts) {
    const box = el("div", "wr-m" + (opts && opts.small ? " wr-m--sm" : ""));
    if (media.w && media.h) box.style.setProperty("--ar", media.w + " / " + media.h);
    const bg = thumbBg(media);
    if (bg) box.style.backgroundImage = 'url("' + bg + '")';
    else box.classList.add("is-sk");
    const poster = safeUrl(media.poster);
    const ready = () => { box.classList.add("is-ld"); box.classList.remove("is-sk"); };
    if (media.type === "image") {
      const src = safeUrl(media.src) || poster;
      if (!src) return box;
      const img = el("img");
      img.alt = "";
      img.decoding = "async";
      img.addEventListener("load", ready, { once: true });
      img.src = src;
      box.appendChild(img);
      return box;
    }
    if (poster) {
      const p = el("img", "wr-m__poster");
      p.alt = "";
      p.addEventListener("load", ready, { once: true });
      p.src = poster;
      box.appendChild(p);
    }
    const v = el("video");
    v.muted = true; v.loop = true; v.playsInline = true; v.preload = "none";
    v.setAttribute("muted", ""); v.setAttribute("playsinline", "");
    [["webm", "video/webm"], ["mp4", "video/mp4"]].forEach(([k, type]) => {
      const u = safeUrl(media[k]);
      if (!u) return;
      const s = el("source"); s.src = u; s.type = type; v.appendChild(s);
    });
    v.addEventListener("playing", () => { ready(); box.classList.add("is-play"); });
    v.addEventListener("pause", () => box.classList.remove("is-play"));
    box.appendChild(v);
    const btn = el("button", "wr-m__pl");
    btn.type = "button";
    const paint = () => {
      const playing = !v.paused;
      btn.innerHTML = playing ? ICON.pause : ICON.play;
      btn.title = playing ? "Пауза" : "Смотреть";
      btn.setAttribute("aria-label", btn.title);
    };
    v.addEventListener("play", paint);
    v.addEventListener("pause", paint);
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (v.paused) { box.dataset.user = ""; v.play().catch(() => {}); }
      else { box.dataset.user = "pause"; v.pause(); }
    });
    paint();
    box.appendChild(btn);
    if (REDUCED.matches) box.classList.add("is-still");
    observer().observe(box);
    return box;
  }

  function stopMedia(root) {
    root.querySelectorAll(".wr-m").forEach((b) => {
      if (io) io.unobserve(b);
      const v = b.querySelector("video");
      if (v) v.pause();
    });
  }

  // ── общие куски разметки ──────────────────────────────────────────────────
  function kindBadge(kind) {
    const k = KIND[kind] || KIND.new;
    return txt("span", "wr-k wr-k--" + k.cls, k.label);
  }
  function relChip(rel) {
    return txt("span", "wr-ver", verLabel(rel.version));
  }
  function draftBadge(rel) {
    return rel.is_published === false ? txt("span", "wr-draft", "Черновик") : null;
  }
  function closeBtn(onClose) {
    const b = el("button", "wr-x", ICON.x);
    b.type = "button"; b.title = "Закрыть"; b.setAttribute("aria-label", "Закрыть");
    b.addEventListener("click", onClose);
    return b;
  }
  function fixList(list) {
    const ul = el("ul", "wr-fx");
    list.forEach((it) => {
      const li = el("li", null, ICON.ok);
      const box = el("div");
      box.appendChild(txt("span", "wr-fx__t", it.title));
      if (it.body) box.appendChild(el("div", "wr-fx__d wr-md", md(it.body)));
      li.appendChild(box);
      ul.appendChild(li);
    });
    return ul;
  }
  // Содержимое одной записи: медиа + бейдж + заголовок + текст.
  function entryBody(e, opts) {
    const wrap = el("div", "wr-e");
    if (e.type === "item") {
      if (e.item.media) wrap.appendChild(mediaNode(e.item.media, opts));
      const b = el("div", "wr-e__b");
      b.appendChild(kindBadge(e.item.kind));
      b.appendChild(txt("div", "wr-ttl", e.item.title));
      if (e.item.body) b.appendChild(el("div", "wr-txt wr-md", md(e.item.body)));
      wrap.appendChild(b);
    } else if (e.type === "fixes") {
      const b = el("div", "wr-e__b");
      b.appendChild(kindBadge("fix"));
      const n = e.list.length;
      b.appendChild(txt("div", "wr-ttl", n + " " + plural(n, "исправление", "исправления", "исправлений")));
      b.appendChild(fixList(e.list));
      wrap.appendChild(b);
    } else {
      const b = el("div", "wr-e__b");
      b.appendChild(txt("div", "wr-ttl", e.rel.title || "Что нового"));
      b.appendChild(el("div", "wr-txt wr-md", md(e.rel.body)));
      wrap.appendChild(b);
    }
    return wrap;
  }

  // ── ПК: сплит ─────────────────────────────────────────────────────────────
  function renderSplit(st) {
    const card = el("div", "wr-card wr-c");
    card.setAttribute("role", "document");
    const left = el("div", "wr-c__l");
    const lh = el("div", "wr-h");
    lh.appendChild(txt("span", "wr-h__t", st.mode === "history" ? "Обновления" : "Что нового"));
    left.appendChild(lh);
    const list = el("div", "wr-c__ls");
    list.setAttribute("role", "listbox");
    left.appendChild(list);

    const right = el("div", "wr-c__r");
    const rh = el("div", "wr-h");
    const rhMeta = el("div", "wr-h__m");
    rh.appendChild(rhMeta);
    rh.appendChild(el("span", "wr-sp"));
    rh.appendChild(closeBtn(st.close));
    right.appendChild(rh);
    const rb = el("div", "wr-c__rb");
    right.appendChild(rb);
    const foot = el("div", "wr-f");
    const cnt = el("span", "wr-f__n");
    const nextBtn = el("button", "wr-btn wr-btn--p");
    nextBtn.type = "button";
    foot.appendChild(cnt);
    foot.appendChild(el("span", "wr-sp"));
    foot.appendChild(nextBtn);
    right.appendChild(foot);

    const rows = [];
    st.releases.forEach((rel) => {
      const head = el("div", "wr-c__rel");
      head.appendChild(relChip(rel));
      head.appendChild(txt("span", "wr-c__date", dateLabel(rel.created_at)));
      const dr = draftBadge(rel);
      if (dr) head.appendChild(dr);
      else if (st.pending.has(rel.id)) head.appendChild(txt("span", "wr-fresh", "Новое"));
      list.appendChild(head);
      st.flat.filter((x) => x.rel === rel).forEach((e) => {
        const row = el("button", "wr-c__i");
        row.type = "button";
        row.setAttribute("role", "option");
        const th = el("span", "wr-th");
        const m = e.type === "item" && e.item.media;
        if (m) {
          const p = safeUrl(m.poster) || (m.type === "image" ? safeUrl(m.src) : null);
          const bg = thumbBg(m);
          th.style.backgroundImage = [p && 'url("' + p + '")', bg && 'url("' + bg + '")'].filter(Boolean).join(",");
        } else {
          th.classList.add("wr-th--ic");
          th.innerHTML = e.type === "fixes" ? ICON.ok : ICON.doc;
        }
        row.appendChild(th);
        const t = el("span", "wr-c__it");
        if (e.type === "item") {
          t.appendChild(txt("b", null, e.item.title));
          t.appendChild(txt("small", null, (KIND[e.item.kind] || KIND.new).label));
        } else if (e.type === "fixes") {
          t.appendChild(txt("b", null, "Исправления"));
          t.appendChild(txt("small", null, e.list.length + " " + plural(e.list.length, "пункт", "пункта", "пунктов")));
        } else {
          t.appendChild(txt("b", null, rel.title || "Что нового"));
          t.appendChild(txt("small", null, "Описание"));
        }
        row.appendChild(t);
        row.addEventListener("click", () => select(st.flat.indexOf(e)));
        list.appendChild(row);
        rows.push(row);
      });
    });

    function select(i) {
      st.idx = Math.max(0, Math.min(i, st.flat.length - 1));
      const e = st.flat[st.idx];
      rows.forEach((r, k) => {
        r.classList.toggle("is-on", k === st.idx);
        r.setAttribute("aria-selected", k === st.idx ? "true" : "false");
      });
      rows[st.idx].scrollIntoView({ block: "nearest" });
      stopMedia(rb);
      rb.innerHTML = "";
      rb.appendChild(entryBody(e));
      rb.scrollTop = 0;
      rhMeta.innerHTML = "";
      rhMeta.appendChild(relChip(e.rel));
      rhMeta.appendChild(txt("span", null, dateLabel(e.rel.created_at)));
      const same = st.flat.filter((x) => x.rel === e.rel);
      const pos = same.indexOf(e);
      cnt.textContent = same.length > 1 ? (pos + 1) + " из " + same.length : "";
      const last = pos === same.length - 1;
      nextBtn.innerHTML = last ? "Понятно" : "Дальше " + ICON.next;
      nextBtn.onclick = last ? st.close : () => select(st.idx + 1);
    }

    st.keys = (ev) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") { ev.preventDefault(); select(st.idx + 1); }
      else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") { ev.preventDefault(); select(st.idx - 1); }
    };
    card.appendChild(left);
    card.appendChild(right);
    select(st.idx);
    return card;
  }

  // ── мобила: лента ─────────────────────────────────────────────────────────
  function renderFeed(st) {
    const card = el("div", "wr-card wr-bf");
    card.setAttribute("role", "document");
    const h = el("div", "wr-h");
    h.appendChild(el("div", "wr-grip"));
    h.appendChild(txt("span", "wr-h__t", st.mode === "history" ? "Обновления" : "Что нового"));
    h.appendChild(el("span", "wr-sp"));
    h.appendChild(closeBtn(st.close));
    card.appendChild(h);
    const sc = el("div", "wr-bf__sc");
    card.appendChild(sc);
    const open = st.idx > 0 ? st.flat[st.idx].rel : null;

    st.releases.forEach((rel, ri) => {
      const sec = el("section", "wr-rel");
      const head = el("button", "wr-rel__h");
      head.type = "button";
      head.appendChild(relChip(rel));
      head.appendChild(txt("b", null, dateLabel(rel.created_at)));
      const dr = draftBadge(rel);
      if (dr) head.appendChild(dr);
      else if (st.pending.has(rel.id)) head.appendChild(txt("span", "wr-fresh", "Новое"));
      head.appendChild(txt("span", "wr-rel__sum", rel.title || ""));
      head.appendChild(el("span", "wr-rel__ch", ICON.chev));
      sec.appendChild(head);
      const body = el("div", "wr-rel__b");
      sec.appendChild(body);
      let filled = false;
      const fill = () => {
        if (filled) return;
        filled = true;
        st.flat.filter((x) => x.rel === rel).forEach((e) => {
          const it = el("div", "wr-it wr-it--" + e.type);
          if (e.type === "fixes") it.classList.add("wr-grp");
          it.appendChild(entryBody(e));
          body.appendChild(it);
        });
      };
      const expanded = open ? rel === open : (ri === 0 || st.pending.has(rel.id));
      const toggle = (on) => {
        if (on) fill();
        sec.classList.toggle("is-open", on);
        head.setAttribute("aria-expanded", on ? "true" : "false");
        if (!on) stopMedia(body);
      };
      head.addEventListener("click", () => toggle(!sec.classList.contains("is-open")));
      toggle(expanded);
      sc.appendChild(sec);
    });
    const stick = () => sc.querySelectorAll(".wr-rel.is-open > .wr-rel__h").forEach((hd) => {
      hd.classList.toggle("is-stuck", hd.getBoundingClientRect().top <= sc.getBoundingClientRect().top + 0.5 && sc.scrollTop > 4);
    });
    sc.addEventListener("scroll", stick, { passive: true });
    st.keys = null;
    return card;
  }

  // ── открытие ─────────────────────────────────────────────────────────────
  let current = null;

  function open(opts) {
    if (current) current.close();
    const updates = (opts && opts.updates || []).slice().sort((a, b) =>
      String(b.created_at || "").localeCompare(String(a.created_at || "")) || (b.id - a.id));
    if (!updates.length) return Promise.resolve();
    const pending = new Set((opts.pendingIds || []).map(Number));
    const flat = [];
    updates.forEach((r) => flat.push(...entriesOf(r)));
    let start = 0;
    if (pending.size) {
      const i = flat.findIndex((e) => pending.has(e.rel.id));
      if (i >= 0) start = i;
    }
    return new Promise((resolve) => {
      const ov = el("div", "wr-ov");
      ov.setAttribute("role", "dialog");
      ov.setAttribute("aria-modal", "true");
      ov.setAttribute("aria-label", "Обновления");
      const st = {
        mode: opts.mode || "pending", releases: updates, pending, flat, idx: start,
        keys: null, close: null,
      };
      let closed = false;
      const onKey = (ev) => {
        if (ev.key === "Escape") { ev.stopPropagation(); st.close(); return; }
        if (st.keys) st.keys(ev);
      };
      const paint = () => {
        stopMedia(ov);
        ov.innerHTML = "";
        ov.appendChild(MQ.matches ? renderFeed(st) : renderSplit(st));
      };
      st.close = () => {
        if (closed) return;
        closed = true;
        current = null;
        document.removeEventListener("keydown", onKey, true);
        MQ.removeEventListener("change", paint);
        stopMedia(ov);
        ov.classList.remove("is-on");
        ov.classList.add("is-closing");
        setTimeout(() => { ov.remove(); resolve(); }, 220);
      };
      ov.addEventListener("click", (e) => { if (e.target === ov) st.close(); });
      document.addEventListener("keydown", onKey, true);
      MQ.addEventListener("change", paint);
      paint();
      document.body.appendChild(ov);
      requestAnimationFrame(() => ov.classList.add("is-on"));
      current = st;
    });
  }

  window.WhatsNewReleases = { open, entriesOf };
})();
