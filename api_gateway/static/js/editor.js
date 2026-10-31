(() => {
  const CHAT_ID = document.body.dataset.chatId || "";
  const URL_WS = (() => {
    const fromData = document.body.dataset.workspaceId || "";
    if (fromData) return fromData;
    try { return new URLSearchParams(location.search).get("workspace_id") || ""; }
    catch (_) { return ""; }
  })();
  let editor = null, monacoRef = null;
  let currentWs = null, currentPath = null, currentContent = "";
  let dirty = false, isBinary = false, saving = false;

  const $ = (id) => document.getElementById(id);
  const layoutEl = $("ed-layout");
  const treeEl = $("ed-tree");
  const wsSelect = $("ed-ws-select");
  const filenameEl = $("ed-filename");
  const saveBtn = $("ed-save");

  // ── Collapse / Expand Left & Right Sidebars + Resizer ──
  const KEY_LEFT_COLLAPSED = "catchpm:ed-sidebar-collapsed";
  const KEY_AI_COLLAPSED   = "catchpm:ed-ai-collapsed";
  const KEY_AI_WIDTH       = "catchpm:ed-ai-width";

  const leftCollapseBtn = $("sidebar-collapse-btn");
  const leftExpandBtn   = $("sidebar-expand-btn");
  const aiCollapseBtn   = $("ai-collapse-btn");
  const aiExpandBtn     = $("ai-expand-btn");
  const aiResizer       = $("ed-ai-resizer");
  const mobileOverlay   = $("ed-mobile-overlay");

  function isMobileViewport() {
    return window.innerWidth <= 768;
  }

  function syncMobileOverlay() {
    if (!layoutEl || !mobileOverlay) return;
    const anyOpen = isMobileViewport() && (
      !layoutEl.classList.contains("sidebar-collapsed") ||
      !layoutEl.classList.contains("ai-collapsed")
    );
    mobileOverlay.classList.toggle("visible", anyOpen);
  }

  function relayoutMonaco() {
    if (!editor) return;
    requestAnimationFrame(() => {
      try {
        editor.updateOptions({ minimap: { enabled: !isMobileViewport() } });
        editor.layout();
      } catch (_) {}
      setTimeout(() => { try { editor.layout(); } catch (_) {} }, 230);
    });
  }

  function applyLeftCollapsed(collapsed, opts = {}) {
    if (!layoutEl) return;
    if (!collapsed && isMobileViewport() && !layoutEl.classList.contains("ai-collapsed")) {
      layoutEl.classList.add("ai-collapsed");
      aiCollapseBtn?.setAttribute("aria-expanded", "false");
      aiExpandBtn?.setAttribute("aria-expanded", "false");
    }
    layoutEl.classList.toggle("sidebar-collapsed", collapsed);
    leftCollapseBtn?.setAttribute("aria-expanded", String(!collapsed));
    leftExpandBtn?.setAttribute("aria-expanded", String(!collapsed));
    if (!opts.skipStorage && !isMobileViewport()) {
      try { localStorage.setItem(KEY_LEFT_COLLAPSED, collapsed ? "1" : "0"); } catch (_) {}
    }
    syncMobileOverlay();
    relayoutMonaco();
  }

  function applyAiCollapsed(collapsed, opts = {}) {
    if (!layoutEl) return;
    if (!collapsed && isMobileViewport() && !layoutEl.classList.contains("sidebar-collapsed")) {
      layoutEl.classList.add("sidebar-collapsed");
      leftCollapseBtn?.setAttribute("aria-expanded", "false");
      leftExpandBtn?.setAttribute("aria-expanded", "false");
    }
    layoutEl.classList.toggle("ai-collapsed", collapsed);
    aiCollapseBtn?.setAttribute("aria-expanded", String(!collapsed));
    aiExpandBtn?.setAttribute("aria-expanded", String(!collapsed));
    if (!opts.skipStorage && !isMobileViewport()) {
      try { localStorage.setItem(KEY_AI_COLLAPSED, collapsed ? "1" : "0"); } catch (_) {}
    }
    syncMobileOverlay();
    relayoutMonaco();
  }

  function applyAiWidth(px) {
    if (!layoutEl) return;
    const minW = 260;
    const maxW = Math.max(minW, Math.min(900, Math.floor(window.innerWidth * 0.7)));
    const clamped = Math.max(minW, Math.min(maxW, Math.round(px)));
    layoutEl.style.setProperty("--ed-ai-width", `${clamped}px`);
    try { localStorage.setItem(KEY_AI_WIDTH, String(clamped)); } catch (_) {}
  }

  // Восстановление состояния панелей (на мобилке по умолчанию обе панели свёрнуты)
  try {
    const savedW = parseInt(localStorage.getItem(KEY_AI_WIDTH) || "", 10);
    if (Number.isFinite(savedW) && savedW >= 260) applyAiWidth(savedW);
    if (isMobileViewport()) {
      applyLeftCollapsed(true, { skipStorage: true });
      applyAiCollapsed(true, { skipStorage: true });
    } else {
      if (localStorage.getItem(KEY_LEFT_COLLAPSED) === "1") applyLeftCollapsed(true, { skipStorage: true });
      if (localStorage.getItem(KEY_AI_COLLAPSED) === "1") applyAiCollapsed(true, { skipStorage: true });
    }
  } catch (_) {}

  leftCollapseBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    applyLeftCollapsed(true);
  });
  leftExpandBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    const isCollapsed = layoutEl?.classList.contains("sidebar-collapsed");
    applyLeftCollapsed(!isCollapsed);
  });
  aiCollapseBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    applyAiCollapsed(true);
  });
  aiExpandBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    const isCollapsed = layoutEl?.classList.contains("ai-collapsed");
    applyAiCollapsed(!isCollapsed);
  });
  mobileOverlay?.addEventListener("click", () => {
    applyLeftCollapsed(true);
    applyAiCollapsed(true);
  });

  let wasMobileViewport = isMobileViewport();
  window.addEventListener("resize", () => {
    const nowMobile = isMobileViewport();
    if (nowMobile !== wasMobileViewport) {
      wasMobileViewport = nowMobile;
      if (nowMobile) {
        applyLeftCollapsed(true, { skipStorage: true });
        applyAiCollapsed(true, { skipStorage: true });
      } else {
        try {
          applyLeftCollapsed(localStorage.getItem(KEY_LEFT_COLLAPSED) === "1", { skipStorage: true });
          applyAiCollapsed(localStorage.getItem(KEY_AI_COLLAPSED) === "1", { skipStorage: true });
        } catch (_) {}
      }
    } else {
      syncMobileOverlay();
      relayoutMonaco();
    }
  });

  if (aiResizer) {
    aiResizer.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      document.body.classList.add("is-resizing");

      const onMove = (ev) => {
        const rawW = window.innerWidth - ev.clientX;
        if (rawW < 140) {
          applyAiCollapsed(true);
          return;
        }
        if (layoutEl.classList.contains("ai-collapsed")) {
          applyAiCollapsed(false);
        }
        applyAiWidth(rawW);
        try { editor?.layout(); } catch (_) {}
      };

      const onUp = () => {
        document.body.classList.remove("is-resizing");
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        relayoutMonaco();
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    });
  }

  // обновить workspace_id в адресной строке без перезагрузки (F5/копия ссылки держат область)
  function syncUrlWs(wsId) {
    try {
      const u = new URL(location.href);
      if (wsId) u.searchParams.set("workspace_id", wsId);
      else u.searchParams.delete("workspace_id");
      history.replaceState(null, "", u);
    } catch (_) {}
  }

  // ── API ──────────────────────────────────────────────
  async function apiGet(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  async function loadWorkspaces() {
    const q = CHAT_ID ? `?chat_id=${encodeURIComponent(CHAT_ID)}` : "";
    const data = await apiGet(`/api/editor/workspaces${q}`);
    const list = data.workspaces || [];
    wsSelect.innerHTML = "";
    if (!list.length) {
      const o = document.createElement("option");
      o.textContent = "— нет областей —";
      o.value = "";
      wsSelect.appendChild(o);
      treeEl.innerHTML = '<div class="ed-node">Пусто</div>';
      return;
    }
    for (const w of list) {
      const o = document.createElement("option");
      o.value = w.workspace_id;
      const baseLabel = w.title || w.workspace_id;
      const ttlSuffix = w.is_permanent ? " · бессрочно" : (w.ttl_text ? ` · ${w.ttl_text}` : "");
      o.textContent = `${baseLabel}${ttlSuffix}`;
      wsSelect.appendChild(o);
    }
    const wanted = URL_WS && list.some(w => w.workspace_id === URL_WS) ? URL_WS : null;
    currentWs = wanted || list[0].workspace_id;
    wsSelect.value = currentWs;
    syncUrlWs(currentWs);
    await loadTree();
  }

  async function loadTree() {
    if (!currentWs) return;
    const data = await apiGet(`/api/editor/tree?workspace_id=${encodeURIComponent(currentWs)}`);
    treeEl.innerHTML = "";
    renderTree(data.tree || [], treeEl);
    startTtl();
  }

  // ── TTL области ──────────────────────────────────────
  const ttlEl = $("ed-ttl");
  const ttlTextEl = $("ed-ttl-text");
  let ttlTimer = null;

  function fmtTtl(sec) {
    if (sec <= 0) return "истекла";
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h > 0) return `${h}ч ${m}м`;
    if (m > 0) return `${m}м`;
    return `${sec}с`;
  }

  async function refreshTtl() {
    if (!currentWs) { ttlEl.hidden = true; return; }
    try {
      const d = await apiGet(`/api/editor/ttl?workspace_id=${encodeURIComponent(currentWs)}`);
      ttlEl.hidden = false;
      if (d.immortal || d.remaining_seconds == null) {
        ttlTextEl.textContent = "бессрочно";
        ttlEl.classList.remove("warn", "crit");
        ttlEl.title = "Область закреплена (git/keep) — иммунитет к TTL";
        return;
      }
      const rem = d.remaining_seconds;
      ttlTextEl.textContent = `живёт ещё ${fmtTtl(rem)}`;
      ttlEl.classList.toggle("crit", rem < 1800);
      ttlEl.classList.toggle("warn", rem >= 1800 && rem < 3600);
      ttlEl.title = `TTL области 10ч, обновляется при активности. Осталось ~${fmtTtl(rem)}`;
    } catch (e) {
      ttlEl.hidden = true;
    }
  }

  function startTtl() {
    if (ttlTimer) clearInterval(ttlTimer);
    refreshTtl();
    ttlTimer = setInterval(refreshTtl, 60000);
  }

  function iconFor(node) { return node.type === "dir" ? "📁" : "📄"; }

  let activeCtxMenu = null;
  function closeTreeContextMenu() {
    if (activeCtxMenu) {
      activeCtxMenu.remove();
      activeCtxMenu = null;
    }
  }

  function downloadFile(path, name) {
    if (!currentWs || !path) return;
    const url = `/api/editor/download?workspace_id=${encodeURIComponent(currentWs)}&path=${encodeURIComponent(path)}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = name || path.split("/").pop() || "file";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function showTreeContextMenu(e, node, rowEl) {
    e.preventDefault();
    e.stopPropagation();
    closeTreeContextMenu();

    const menu = document.createElement("div");
    menu.className = "ed-ctx-menu";
    menu.setAttribute("role", "menu");

    const addItem = (label, onClick) => {
      const item = document.createElement("div");
      item.className = "ed-ctx-item";
      item.setAttribute("role", "menuitem");
      const span = document.createElement("span");
      span.textContent = label;
      item.appendChild(span);
      item.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeTreeContextMenu();
        onClick();
      });
      menu.appendChild(item);
    };

    const addSep = () => {
      const sep = document.createElement("div");
      sep.className = "ed-ctx-sep";
      sep.setAttribute("role", "separator");
      menu.appendChild(sep);
    };

    // Верхний раздел (аналогично встроенному разделу Monaco «Спросить агента о выделенном»)
    addItem("Спросить агента о файле", async () => {
      if (currentPath !== node.path) {
        if (dirty && !confirm("Есть несохранённые изменения. Открыть другой файл?")) return;
        document.querySelectorAll(".ed-node.sel").forEach(n => n.classList.remove("sel"));
        rowEl.classList.add("sel");
        await openFile(node.path);
      }
      if (layoutEl?.classList.contains("ai-collapsed")) {
        applyAiCollapsed(false);
      }
      $("ed-ai-text")?.focus();
    });

    addSep();

    addItem("Открыть", () => {
      if (dirty && !confirm("Есть несохранённые изменения. Открыть другой файл?")) return;
      document.querySelectorAll(".ed-node.sel").forEach(n => n.classList.remove("sel"));
      rowEl.classList.add("sel");
      openFile(node.path);
      if (isMobileViewport()) applyLeftCollapsed(true);
    });

    addItem("Скачать файл", () => {
      downloadFile(node.path, node.name);
    });

    document.body.appendChild(menu);
    activeCtxMenu = menu;

    const rect = menu.getBoundingClientRect();
    const x = Math.min(e.clientX, window.innerWidth - rect.width - 6);
    const y = Math.min(e.clientY, window.innerHeight - rect.height - 6);
    menu.style.left = `${Math.max(4, x)}px`;
    menu.style.top = `${Math.max(4, y)}px`;
  }

  document.addEventListener("mousedown", (e) => {
    if (activeCtxMenu && !activeCtxMenu.contains(e.target)) closeTreeContextMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeTreeContextMenu();
      if (isMobileViewport()) {
        if (!layoutEl?.classList.contains("sidebar-collapsed")) applyLeftCollapsed(true);
        if (!layoutEl?.classList.contains("ai-collapsed")) applyAiCollapsed(true);
      }
    }
  });
  window.addEventListener("blur", closeTreeContextMenu);
  treeEl?.addEventListener("scroll", closeTreeContextMenu);

  function renderTree(nodes, container) {
    for (const node of nodes) {
      const row = document.createElement("div");
      row.className = `ed-node ${node.type}`;
      row.innerHTML = `<span class="ed-ic">${iconFor(node)}</span>`;
      const name = document.createElement("span");
      name.textContent = node.name;
      row.appendChild(name);
      container.appendChild(row);

      if (node.type === "dir") {
        const kids = document.createElement("div");
        kids.className = "ed-children";
        kids.style.display = "none";
        container.appendChild(kids);
        let built = false;
        row.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!built) { renderTree(node.children || [], kids); built = true; }
          kids.style.display = kids.style.display === "none" ? "" : "none";
        });
      } else {
        row.addEventListener("click", (e) => {
          e.stopPropagation();
          if (dirty && !confirm("Есть несохранённые изменения. Открыть другой файл?")) return;
          document.querySelectorAll(".ed-node.sel").forEach(n => n.classList.remove("sel"));
          row.classList.add("sel");
          openFile(node.path);
          if (isMobileViewport()) applyLeftCollapsed(true);
        });
        row.addEventListener("contextmenu", (e) => {
          showTreeContextMenu(e, node, row);
        });
      }
    }
  }

  function setDirty(v) {
    dirty = v;
    saveBtn.disabled = !v || isBinary || saving;
    filenameEl.textContent = (currentPath || "") + (isBinary ? " (бинарный)" : "") + (v ? " •" : "");
  }

  async function openFile(path) {
    if (!currentWs) return;
    try {
      const data = await apiGet(
        `/api/editor/file?workspace_id=${encodeURIComponent(currentWs)}&path=${encodeURIComponent(path)}`);
      currentPath = path;
      isBinary = !!data.is_binary;
      currentContent = isBinary ? "// Бинарный файл — редактирование недоступно" : data.content;
      const lang = guessLang(path);
      if (editor) {
        const model = monacoRef.editor.createModel(currentContent, lang);
        editor.setModel(model);
        editor.updateOptions({ readOnly: isBinary });
        model.onDidChangeContent(() => setDirty(editor.getValue() !== currentContent));
      }
      setDirty(false);
    } catch (e) {
      filenameEl.textContent = `Ошибка: ${e.message}`;
    }
  }

  async function saveFile() {
    if (!editor || !currentWs || !currentPath || isBinary || saving || !dirty) return;
    saving = true; saveBtn.disabled = true;
    const content = editor.getValue();
    try {
      const r = await fetch("/api/editor/file", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ workspace_id: currentWs, path: currentPath, content }),
      });
      if (!r.ok) {
        let detail = r.status;
        try { detail = (await r.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      currentContent = content;
      saving = false;
      setDirty(false);
      refreshTtl();
    } catch (e) {
      saving = false;
      saveBtn.disabled = false;
      filenameEl.textContent = `Ошибка сохранения: ${e.message}`;
    }
  }

  function guessLang(path) {
    const ext = (path.split(".").pop() || "").toLowerCase();
    const map = { py:"python", js:"javascript", ts:"typescript", json:"json",
      html:"html", css:"css", md:"markdown", sql:"sql", sh:"shell", yml:"yaml",
      yaml:"yaml", xml:"xml", java:"java", c:"c", cpp:"cpp", h:"cpp", go:"go",
      rs:"rust", rb:"ruby", php:"php", ino:"cpp", txt:"plaintext",
      dockerfile:"dockerfile", env:"plaintext" };
    return map[ext] || "plaintext";
  }

  // ── Monaco init ──────────────────────────────────────
  require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" } });
  require(["vs/editor/editor.main"], () => {
    monacoRef = window.monaco;
    editor = monacoRef.editor.create($("ed-editor"), {
      value: "// Выберите файл в дереве слева",
      language: "plaintext",
      theme: "vs-dark",
      readOnly: true,
      automaticLayout: true,
      minimap: { enabled: !isMobileViewport() },
    });
    editor.addCommand(monacoRef.KeyMod.CtrlCmd | monacoRef.KeyCode.KeyS, saveFile);

    editor.addAction({
      id: "ed-ask-agent-selection",
      label: "Спросить агента о выделенном",
      contextMenuGroupId: "navigation",
      contextMenuOrder: 0,
      precondition: "editorHasSelection",
      run: (ed) => {
        const sel = ed.getSelection();
        if (!sel || sel.isEmpty()) return;
        const model = ed.getModel();
        const text = model.getValueInRange(sel);
        if (!text.trim()) return;
        pendingSelection = {
          text,
          startLine: sel.startLineNumber,
          endLine: sel.endLineNumber,
        };
        if (layoutEl?.classList.contains("ai-collapsed")) {
          applyAiCollapsed(false);
        }
        updateSelectionHint();
        const ta = $("ed-ai-text");
        ta.focus();
      },
    });

    loadWorkspaces();
  });

  // ── UI events ────────────────────────────────────────
  wsSelect.addEventListener("change", async () => {
    if (dirty && !confirm("Есть несохранённые изменения. Сменить область?")) {
      wsSelect.value = currentWs; return;
    }
    currentWs = wsSelect.value || null;
    currentPath = null; isBinary = false; setDirty(false);
    filenameEl.textContent = "";
    syncUrlWs(currentWs);
    await loadTree();
  });
  $("ed-refresh").addEventListener("click", loadTree);
  saveBtn.addEventListener("click", saveFile);
  window.addEventListener("beforeunload", (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  // ── AI-панель ────────────────────────────────────────
  const aiLog = $("ed-ai-log");
  if (aiLog) {
    aiLog.addEventListener("click", (e) => {
      const a = e.target.closest("a[href]");
      if (!a || !aiLog.contains(a)) return;
      const href = a.getAttribute("href");
      if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;
      e.preventDefault();
      window.open(href, "_blank", "noopener,noreferrer");
    });
  }
  let pendingSelection = null;

  function updateSelectionHint() {
    const ta = $("ed-ai-text");
    let hint = $("ed-ai-selhint");
    if (!hint) {
      hint = document.createElement("div");
      hint.id = "ed-ai-selhint";
      hint.className = "ed-ai-selhint";
      ta.parentNode.insertBefore(hint, ta);
    }
    if (pendingSelection) {
      const { startLine, endLine } = pendingSelection;
      const range = startLine === endLine ? `строка ${startLine}` : `строки ${startLine}–${endLine}`;
      hint.innerHTML = `<span>📎 Вопрос о: ${range}</span><button type="button" id="ed-ai-selclear" title="Сбросить">✕</button>`;
      hint.style.display = "";
      $("ed-ai-selclear").addEventListener("click", () => {
        pendingSelection = null;
        updateSelectionHint();
      });
      ta.placeholder = "Спросить агента о выделенных строках...";
      stopPlaceholderRotation();
    } else {
      hint.style.display = "none";
      hint.innerHTML = "";
      startPlaceholderRotation();
    }
  }

  const PH_VARIANTS = [
    "Спросить агента по открытому файлу…",
    "Выдели участок → ПКМ «Спросить агента о выделенном» для точечного вопроса",
  ];
  let phTimer = null, phIdx = 0;
  function startPlaceholderRotation() {
    const ta = $("ed-ai-text");
    if (!ta) return;
    ta.placeholder = PH_VARIANTS[phIdx];
    if (phTimer) return;
    phTimer = setInterval(() => {
      if (pendingSelection || document.activeElement === ta || ta.value) return;
      phIdx = (phIdx + 1) % PH_VARIANTS.length;
      ta.placeholder = PH_VARIANTS[phIdx];
    }, 3500);
  }
  function stopPlaceholderRotation() {
    if (phTimer) { clearInterval(phTimer); phTimer = null; }
  }
  startPlaceholderRotation();
  if (window.marked) marked.setOptions({ breaks: true, gfm: true });

  function renderMd(text) {
    if (window.marked && window.DOMPurify) {
      const html = DOMPurify.sanitize(marked.parse(text || ""));
      const tmp = document.createElement("div");
      tmp.innerHTML = html;
      tmp.querySelectorAll("a").forEach(a => {
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer");
      });
      return tmp.innerHTML;
    }
    const d = document.createElement("div"); d.textContent = text; return d.innerHTML;
  }

  // ── Client-Side Rendering of Structured Data (Component Mapping) ──
  const POLL_COMPONENTS = new Set(["interactivepoll", "askuserquestion", "poll"]);

  function tryParseComponentJson(raw) {
    if (!raw || typeof raw !== "string") return null;
    const trimmed = raw.trim();
    if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
    try {
      const obj = JSON.parse(trimmed);
      if (!obj || typeof obj !== "object") return null;
      const comp = String(obj.component || obj.type || "").trim().toLowerCase();
      if (POLL_COMPONENTS.has(comp) && Array.isArray(obj.options) && obj.options.length > 0) {
        return { component: "InteractivePoll", data: obj };
      }
    } catch (_) {}
    return null;
  }

  function extractBalancedJsonObjects(text) {
    const results = [];
    let i = 0;
    const n = text.length;
    while (i < n) {
      const start = text.indexOf("{", i);
      if (start === -1) break;
      let depth = 0, inStr = false, esc = false, end = -1;
      for (let j = start; j < n; j++) {
        const ch = text[j];
        if (inStr) {
          if (esc) { esc = false; }
          else if (ch === "\\") { esc = true; }
          else if (ch === "\"") { inStr = false; }
        } else {
          if (ch === "\"") inStr = true;
          else if (ch === "{") depth++;
          else if (ch === "}") {
            depth--;
            if (depth === 0) { end = j; break; }
          }
        }
      }
      if (end !== -1) {
        const candidate = text.slice(start, end + 1);
        const parsed = tryParseComponentJson(candidate);
        if (parsed) {
          results.push({ start, end: end + 1, parsed });
          i = end + 1;
          continue;
        }
      }
      i = start + 1;
    }
    return results;
  }

  function extractStructuredSegments(text) {
    const src = String(text || "");
    const segments = [];
    const fenceRe = /```(?:json)?\s*([\s\S]*?)```/gi;
    let lastIdx = 0;
    let m;

    const pushPlainOrInlineJson = (chunk) => {
      if (!chunk) return;
      const inlineMatches = extractBalancedJsonObjects(chunk);
      if (!inlineMatches.length) {
        if (chunk.trim()) segments.push({ kind: "md", text: chunk });
        return;
      }
      let cur = 0;
      for (const hit of inlineMatches) {
        const before = chunk.slice(cur, hit.start);
        if (before.trim()) segments.push({ kind: "md", text: before });
        segments.push({ kind: "component", component: hit.parsed.component, data: hit.parsed.data });
        cur = hit.end;
      }
      const after = chunk.slice(cur);
      if (after.trim()) segments.push({ kind: "md", text: after });
    };

    while ((m = fenceRe.exec(src)) !== null) {
      const parsed = tryParseComponentJson(m[1]);
      if (parsed) {
        pushPlainOrInlineJson(src.slice(lastIdx, m.index));
        segments.push({ kind: "component", component: parsed.component, data: parsed.data });
        lastIdx = m.index + m[0].length;
      }
    }
    pushPlainOrInlineJson(src.slice(lastIdx));
    return segments;
  }

  let pollSeq = 0;
  function createInteractivePollEl(data, meta = {}) {
    const uid = `_ed_poll_${++pollSeq}_`;
    const botMessageId = String(meta.botMessageId || "").trim();
    const pollIndex = Number(meta.pollIndex ?? 0);
    const savedAnswer = meta.savedAnswer || null;
    const titleText = String(data.title || data.question || "Выберите вариант:");
    const rawOptions = Array.isArray(data.options) ? data.options : [];
    const placeholder = String(data.placeholder || "Something else");
    const skipLabel = String(data.skipLabel || "Skip");
    const sendLabel = String(data.sendLabel || "Send");

    const root = document.createElement("div");
    root.setAttribute("data-ask-user-input-banner", "");
    root.setAttribute("data-cds", "AskUserQuestion");
    root.setAttribute("role", "group");
    root.setAttribute("aria-labelledby", `${uid}label ${uid}cue`);
    root.setAttribute("tabindex", "-1");
    root.className = "ed-poll";
    if (botMessageId) root.dataset.botMessageId = botMessageId;
    root.dataset.pollIndex = String(pollIndex);

    const header = document.createElement("div");
    header.className = "ed-poll-header";

    const titleSpan = document.createElement("span");
    titleSpan.id = `${uid}label`;
    titleSpan.tabIndex = -1;
    titleSpan.className = "ed-poll-title";
    const titleBdi = document.createElement("bdi");
    titleBdi.textContent = titleText;
    titleSpan.appendChild(titleBdi);

    const cueSpan = document.createElement("span");
    cueSpan.id = `${uid}cue`;
    cueSpan.hidden = true;
    cueSpan.textContent = `Choose one of ${rawOptions.length} options.`;

    const minBtn = document.createElement("button");
    minBtn.type = "button";
    minBtn.setAttribute("data-cds", "Button");
    minBtn.setAttribute("data-cds-icon-only", "");
    minBtn.setAttribute("data-cds-ghost", "");
    minBtn.setAttribute("data-size", "xs");
    minBtn.setAttribute("aria-label", "Minimize");
    minBtn.title = "Свернуть / развернуть";
    minBtn.className = "ed-poll-min-btn";
    minBtn.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    minBtn.addEventListener("click", () => {
      root.classList.toggle("is-minimized");
    });

    header.append(titleSpan, cueSpan, minBtn);

    const bodyWrap = document.createElement("div");
    const bodyInner = document.createElement("div");
    bodyInner.className = "ed-poll-body";

    const scrollBox = document.createElement("div");
    scrollBox.className = "ed-poll-scroll";
    scrollBox.setAttribute("data-orientation", "vertical");
    scrollBox.setAttribute("role", "group");

    const listEl = document.createElement("div");
    listEl.className = "ed-poll-list";
    listEl.setAttribute("role", "list");

    const optionButtons = [];
    let answered = false;

    const markLockedUI = (selectedBtn) => {
      answered = true;
      root.classList.add("is-answered");
      optionButtons.forEach((btn) => {
        const isSel = btn === selectedBtn;
        btn.disabled = true;
        btn.classList.toggle("is-selected", isSel);
        btn.setAttribute("aria-pressed", isSel ? "true" : "false");
        btn.setAttribute("aria-disabled", "true");
        if (isSel) {
          const trail = btn.querySelector(".ed-poll-opt-trail");
          if (trail) {
            trail.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.5 8.5l3 3 6-6.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
          }
        }
      });
      if (otherInput) otherInput.disabled = true;
      if (actionBtn) actionBtn.disabled = true;
    };

    const lockAndSubmit = (selectedBtn, answerText, extra = {}) => {
      if (answered) return;
      markLockedUI(selectedBtn);
      if (answerText) {
        const currentBotMsgId = root.dataset.botMessageId
          || root.closest(".ed-ai-msg.bot")?.dataset?.messageId
          || botMessageId
          || "";
        const pollAnswerMeta = currentBotMsgId ? {
          bot_message_id: String(currentBotMsgId),
          poll_index: pollIndex,
          selected_index: extra.selectedIndex ?? null,
          selected_value: answerText,
          is_custom: !!extra.isCustom,
          is_skipped: !!extra.isSkipped,
          title: titleText,
        } : null;
        dispatchAIMessage(answerText, pollAnswerMeta);
      }
    };

    rawOptions.forEach((opt, idx) => {
      const isObj = opt && typeof opt === "object";
      const rawLabel = isObj ? String(opt.label || opt.title || opt.text || opt.value || "") : String(opt);
      const rawValue = isObj ? String(opt.value || rawLabel) : rawLabel;
      const descText = isObj && opt.description ? String(opt.description) : "";
      const numPrefix = `${idx + 1}. `;
      const displayLabel = /^\d+[.)]\s+/.test(rawLabel) ? rawLabel : `${numPrefix}${rawLabel}`;

      const item = document.createElement("div");
      item.setAttribute("role", "listitem");
      item.className = "ed-poll-item";

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ed-poll-opt";
      btn.setAttribute("aria-disabled", "false");
      btn.setAttribute("aria-pressed", "false");
      btn.setAttribute("data-cds-option", String(idx));
      btn.tabIndex = idx === 0 ? 0 : -1;

      const badge = document.createElement("span");
      badge.className = "ed-poll-badge";
      badge.setAttribute("aria-hidden", "true");
      const badgeNum = document.createElement("span");
      badgeNum.textContent = String(idx + 1);
      badge.appendChild(badgeNum);

      const content = document.createElement("span");
      content.className = "ed-poll-opt-content";
      const labelSpan = document.createElement("span");
      labelSpan.className = "ed-poll-opt-label";
      const labelBdi = document.createElement("bdi");
      labelBdi.textContent = displayLabel;
      labelSpan.appendChild(labelBdi);
      content.appendChild(labelSpan);

      if (descText) {
        const descSpan = document.createElement("span");
        descSpan.className = "ed-poll-opt-desc";
        descSpan.textContent = descText;
        content.appendChild(descSpan);
      }

      const trail = document.createElement("span");
      trail.className = "ed-poll-opt-trail";
      trail.setAttribute("aria-hidden", "true");
      trail.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

      btn.dataset.optLabel = rawLabel;
      btn.dataset.optValue = rawValue;
      btn.append(badge, content, trail);

      btn.addEventListener("click", () => {
        lockAndSubmit(btn, rawValue, { selectedIndex: idx, isCustom: false, isSkipped: false });
      });

      btn.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const dir = e.key === "ArrowDown" ? 1 : -1;
          const nextIdx = (idx + dir + optionButtons.length) % optionButtons.length;
          optionButtons.forEach((b, i) => { b.tabIndex = i === nextIdx ? 0 : -1; });
          optionButtons[nextIdx]?.focus();
        }
      });

      const divider = document.createElement("div");
      divider.className = "ed-poll-divider";
      divider.setAttribute("aria-hidden", "true");

      item.append(btn, divider);
      listEl.appendChild(item);
      optionButtons.push(btn);
    });

    const otherWrap = document.createElement("div");
    otherWrap.className = "ed-poll-other";

    const otherDivider = document.createElement("div");
    otherDivider.className = "ed-poll-divider";
    otherDivider.setAttribute("aria-hidden", "true");

    const form = document.createElement("form");
    form.className = "ed-poll-other-form";

    const label = document.createElement("label");
    label.className = "ed-poll-other-label";

    const otherBadge = document.createElement("span");
    otherBadge.className = "ed-poll-badge ed-poll-other-badge";
    otherBadge.setAttribute("aria-hidden", "true");
    otherBadge.innerHTML = `<svg viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M11.2 2.5a1.4 1.4 0 0 1 2 2L5.5 12.2l-2.8.7.7-2.8L11.2 2.5Z" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const otherInput = document.createElement("input");
    otherInput.type = "text";
    otherInput.className = "ed-poll-other-input";
    otherInput.placeholder = placeholder;
    otherInput.setAttribute("aria-label", placeholder);
    otherInput.value = "";

    const actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.setAttribute("data-cds", "Button");
    actionBtn.setAttribute("data-size", "sm");
    actionBtn.className = "ed-poll-action-btn";
    const actionBtnText = document.createElement("span");
    actionBtnText.textContent = skipLabel;
    actionBtn.appendChild(actionBtnText);

    const updateActionBtn = () => {
      const hasVal = otherInput.value.trim().length > 0;
      actionBtnText.textContent = hasVal ? sendLabel : skipLabel;
      actionBtn.classList.toggle("is-primary", hasVal);
    };

    otherInput.addEventListener("input", updateActionBtn);

    const submitOtherOrSkip = () => {
      if (answered) return;
      const customVal = otherInput.value.trim();
      if (customVal) {
        lockAndSubmit(null, customVal, { selectedIndex: null, isCustom: true, isSkipped: false });
      } else {
        lockAndSubmit(
          null,
          "Пропустить опрос — выбери оптимальный вариант на своё усмотрение.",
          { selectedIndex: null, isCustom: false, isSkipped: true }
        );
      }
    };

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitOtherOrSkip();
    });

    actionBtn.addEventListener("click", (e) => {
      e.preventDefault();
      submitOtherOrSkip();
    });

    root._applySavedAnswer = (ans) => {
      if (answered || !ans || typeof ans !== "object") return;
      const selIdx = ans.selected_index != null ? Number(ans.selected_index) : null;
      const selVal = String(ans.selected_value || "").trim();
      if (selIdx != null && optionButtons[selIdx]) {
        markLockedUI(optionButtons[selIdx]);
        return;
      }
      if (!ans.is_custom && !ans.is_skipped && selVal) {
        const matched = optionButtons.find(
          b => b.dataset.optValue === selVal || b.dataset.optLabel === selVal
        );
        if (matched) {
          markLockedUI(matched);
          return;
        }
      }
      if (ans.is_custom && otherInput && selVal) {
        otherInput.value = selVal;
        actionBtnText.textContent = sendLabel;
        actionBtn.classList.add("is-primary");
      }
      markLockedUI(null);
    };

    root._syncWithUserReply = (replyText) => {
      if (answered) return;
      const norm = String(replyText || "").trim();
      if (!norm) return;
      const matched = optionButtons.find(
        b => b.dataset.optValue === norm || b.dataset.optLabel === norm
      );
      if (matched) {
        markLockedUI(matched);
      } else {
        if (otherInput && !norm.startsWith("Пропустить опрос")) {
          otherInput.value = norm;
          actionBtnText.textContent = sendLabel;
          actionBtn.classList.add("is-primary");
        }
        markLockedUI(null);
      }
    };

    if (savedAnswer) {
      root._applySavedAnswer(savedAnswer);
    }

    label.append(otherBadge, otherInput, actionBtn);
    form.appendChild(label);
    otherWrap.append(otherDivider, form);

    scrollBox.append(listEl, otherWrap);
    bodyInner.appendChild(scrollBox);
    bodyWrap.appendChild(bodyInner);
    root.append(header, bodyWrap);

    return root;
  }

  const COMPONENT_MAP = {
    InteractivePoll: createInteractivePollEl,
    AskUserQuestion: createInteractivePollEl,
  };

  function renderBotContent(container, text, meta = {}) {
    container.innerHTML = "";
    const segments = extractStructuredSegments(text);
    if (!segments.length) {
      container.innerHTML = renderMd(text);
      container.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
      return;
    }
    const botMessageId = String(meta.botMessageId || "").trim();
    const pollAnswers = Array.isArray(meta.pollAnswers) ? meta.pollAnswers : [];
    let pollIdx = 0;

    for (const seg of segments) {
      if (seg.kind === "component" && COMPONENT_MAP[seg.component]) {
        const curIdx = pollIdx++;
        const savedAnswer = pollAnswers.find(a => Number(a.poll_index || 0) === curIdx) || null;
        container.appendChild(COMPONENT_MAP[seg.component](seg.data, {
          botMessageId,
          pollIndex: curIdx,
          savedAnswer,
        }));
      } else if (seg.kind === "md" && seg.text) {
        const mdWrap = document.createElement("div");
        mdWrap.className = "ed-ai-md-part";
        mdWrap.innerHTML = renderMd(seg.text);
        mdWrap.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
        container.appendChild(mdWrap);
      }
    }
  }

  function stripEditorPromptEnvelope(rawText) {
    const s = String(rawText || "");
    const idx = s.indexOf("\n\n[UI-компоненты редактора:");
    const cleaned = idx !== -1 ? s.slice(0, idx) : s;
    const qMatch = cleaned.match(/\n\nВопрос:\s*([\s\S]*)$/);
    return qMatch ? qMatch[1].trim() : cleaned.trim();
  }

  function aiAppend(role, text, meta = {}) {
    const d = document.createElement("div");
    d.className = `ed-ai-msg ${role}`;
    if (meta.messageId) d.dataset.messageId = String(meta.messageId);
    if (role === "bot") {
      renderBotContent(d, text, {
        botMessageId: meta.botMessageId || meta.messageId || "",
        pollAnswers: meta.pollAnswers || [],
      });
    } else {
      const cleanUserText = stripEditorPromptEnvelope(text);
      d.textContent = cleanUserText;
      if (meta.pollAnswer && typeof meta.pollAnswer === "object") {
        const bmid = String(meta.pollAnswer.bot_message_id || "");
        const pidx = String(meta.pollAnswer.poll_index ?? 0);
        if (bmid) {
          aiLog.querySelectorAll(`.ed-poll[data-bot-message-id="${bmid}"][data-poll-index="${pidx}"]`).forEach(el => {
            el._applySavedAnswer?.(meta.pollAnswer);
          });
        }
      }
      aiLog.querySelectorAll(".ed-poll:not(.is-answered)").forEach(el => {
        el._syncWithUserReply?.(cleanUserText);
      });
    }
    aiLog.appendChild(d);
    aiLog.scrollTop = aiLog.scrollHeight;
    return d;
  }

  let thinkingEl = null;
  function showThinking() {
    if (thinkingEl) return;
    thinkingEl = document.createElement("div");
    thinkingEl.className = "ed-ai-msg bot ed-ai-thinking";
    thinkingEl.innerHTML = "<span></span><span></span><span></span>";
    aiLog.appendChild(thinkingEl);
    aiLog.scrollTop = aiLog.scrollHeight;
  }
  function clearThinking() { if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; } }

  async function loadEditorChatHistory() {
    if (!CHAT_ID) return;
    try {
      const data = await apiGet(`/api/chats/${encodeURIComponent(CHAT_ID)}?limit=50&offset=0`);
      const msgs = Array.isArray(data?.messages) ? data.messages : [];
      aiLog.innerHTML = "";
      for (const m of msgs) {
        const mid = m.message_id || (m.id != null ? String(m.id) : "");
        if (m.is_from_bot) {
          aiAppend("bot", m.message || "", {
            messageId: mid,
            botMessageId: mid,
            pollAnswers: m.poll_answers || [],
          });
        } else {
          aiAppend("user", m.message || "", {
            messageId: mid,
            pollAnswer: m.poll_answer || null,
          });
        }
      }
    } catch (_) {}
  }

  if (CHAT_ID) {
    loadEditorChatHistory();
    try {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/chat/${encodeURIComponent(CHAT_ID)}`);
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.is_from_bot && m.message) {
            clearThinking();
            const mid = m.message_id || (m.id != null ? String(m.id) : "");
            aiAppend("bot", m.message, {
              messageId: mid,
              botMessageId: mid,
              pollAnswers: m.poll_answers || [],
            });
          } else if (!m.is_from_bot) {
            if (m.poll_answer && typeof m.poll_answer === "object") {
              const bmid = String(m.poll_answer.bot_message_id || "");
              const pidx = String(m.poll_answer.poll_index ?? 0);
              if (bmid) {
                aiLog.querySelectorAll(`.ed-poll[data-bot-message-id="${bmid}"][data-poll-index="${pidx}"]`).forEach(el => {
                  el._applySavedAnswer?.(m.poll_answer);
                });
              }
            }
            if (m.message_id && aiLog.querySelector(`.ed-ai-msg.user[data-message-id="${m.message_id}"]`)) {
              return;
            }
          }
        } catch (_) {}
      };
    } catch (_) {}
  } else {
    aiAppend("bot", "Чат не привязан — откройте редактор из чата, чтобы спросить агента.");
  }

  async function dispatchAIMessage(userVisibleText, pollAnswer = null) {
    const text = String(userVisibleText || "").trim();
    if (!text || !CHAT_ID) return;
    let msg = text;
    const fileUrl = (currentWs && currentPath)
      ? `${location.origin}/editor?chat_id=${encodeURIComponent(CHAT_ID)}`
      : null;
    if (pendingSelection) {
      const { text: selText, startLine, endLine } = pendingSelection;
      const loc = startLine === endLine ? `строка ${startLine}` : `строки ${startLine}–${endLine}`;
      const file = currentPath ? ` ${currentPath}` : "";
      const link = fileUrl ? `\nСсылка на файл в редакторе: ${fileUrl}` : "";
      msg = `[Выделенный фрагмент${file}, ${loc}]\n\`\`\`\n${selText.slice(0, 8000)}\n\`\`\`${link}\n\nВопрос: ${text}`;
    } else if (currentPath && !isBinary) {
      const lines = currentContent ? currentContent.split("\n").length : 0;
      const bytes = currentContent ? new Blob([currentContent]).size : 0;
      const link = fileUrl ? `\nСсылка на файл в редакторе: ${fileUrl}` : "";
      msg = `[Открыт файл: ${currentPath} · воркспейс ${currentWs} · ${lines} строк · ${bytes} байт]${link}\n\nФайл целиком НЕ приложен — при необходимости спроси и я пришлю содержимое или выделю фрагмент.\n\nВопрос: ${text}`;
    } else {
      msg = `${text}`;
    }
    const clientMsgId = `msg-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`;
    aiAppend("user", text, { messageId: clientMsgId, pollAnswer });
    pendingSelection = null;
    updateSelectionHint();
    showThinking();
    try {
      await fetch("/api/chats/send_message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          chat_id: CHAT_ID,
          message: msg,
          message_id: clientMsgId,
          agent_model:    window.ModelPicker?.getModel() ?? "coder",
          selected_tools: window.ToolsPanel?.getSelectedCategories() ?? [],
          context_size:   window.ToolsPanel?.getContextSize() ?? "medium",
          ...(pollAnswer ? { poll_answer: pollAnswer } : {}),
        }),
      });
    } catch (e) {
      clearThinking();
      aiAppend("bot", `Ошибка отправки: ${e.message}`);
    }
  }

  async function sendAI() {
    const ta = $("ed-ai-text");
    const text = ta.value.trim();
    if (!text || !CHAT_ID) return;
    ta.value = "";
    await dispatchAIMessage(text);
  }

  (function injectPanels(tries = 0) {
    const footer = $("ed-ai")?.querySelector(".ed-ai-footer");
    if (!footer) return;
    if (!window.ToolsPanel || !window.ModelPicker) {
      if (tries < 50) return void setTimeout(() => injectPanels(tries + 1), 100);
      return;
    }
    const container = $("ed-ai");
    try { window.ToolsPanel.setUser?.(""); } catch (_) {}
    try { window.ToolsPanel.inject(container); } catch (_) {}
    try { window.ModelPicker.inject(container); } catch (_) {}
  })();

  $("ed-ai-send").addEventListener("click", sendAI);
  $("ed-ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendAI(); }
  });
})();
