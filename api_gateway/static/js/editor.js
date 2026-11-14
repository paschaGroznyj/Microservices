(() => {
  const CHAT_ID = document.body.dataset.chatId || "";
  const URL_WS = (() => {
    const fromData = document.body.dataset.workspaceId || "";
    if (fromData) return fromData;
    try { return new URLSearchParams(location.search).get("workspace_id") || ""; }
    catch (_) { return ""; }
  })();
  // ?path= — файл, который открыть сразу (кнопки карточки «Изменённые файлы»)
  const URL_PATH = (() => {
    try { return (new URLSearchParams(location.search).get("path") || "").replace(/^\/+/, ""); }
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
  // Sigma: редактор только для просмотра (gateway отвечает 403 на PUT /api/editor/file)
  const READONLY = document.body.dataset.readonly === "1";
  // Как AI-панель получает ответы агента: "ws" (main) или "sse" (Sigma, без WebSocket)
  const CHAT_TRANSPORT = document.body.dataset.chatTransport === "sse" ? "sse" : "ws";
  if (READONLY && saveBtn) saveBtn.hidden = true;

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

  // открытый файл тоже держим в адресе: F5 вернёт его, а не старый ?path=
  function syncUrlPath(path) {
    try {
      const u = new URL(location.href);
      if (path) u.searchParams.set("path", path);
      else u.searchParams.delete("path");
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
      o.textContent = baseLabel;
      o.title = `${baseLabel} (${w.workspace_id})`;
      wsSelect.appendChild(o);
    }
    const wanted = URL_WS && list.some(w => w.workspace_id === URL_WS) ? URL_WS : null;
    currentWs = wanted || list[0].workspace_id;
    wsSelect.value = currentWs;
    syncUrlWs(currentWs);
    syncWsTitle();
    // путь из ссылки относится только к запрошенной области
    const wantPath = wanted && URL_PATH ? URL_PATH : "";
    if (!wantPath) syncUrlPath(null);
    const parts = wantPath.split("/");
    for (let i = 1; i < parts.length; i++) openDirs.add(parts.slice(0, i).join("/"));
    await activateWs();
    if (wantPath) await openFile(wantPath);
  }

  function syncWsTitle() {
    const o = wsSelect.selectedOptions && wsSelect.selectedOptions[0];
    wsSelect.title = o ? (o.title || o.textContent) : "";
  }

  // Переключение области: дерево + TTL + live-подписка
  async function activateWs() {
    await loadTree();
    startTtl();
    startWatch();
  }

  async function loadTree() {
    if (!currentWs) return;
    const data = await apiGet(`/api/editor/tree?workspace_id=${encodeURIComponent(currentWs)}`);
    const scroll = treeEl.scrollTop;
    treeEl.innerHTML = "";
    renderTree(data.tree || [], treeEl);
    treeEl.scrollTop = scroll;
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
        ttlTextEl.textContent = "∞";
        ttlEl.classList.remove("warn", "crit");
        ttlEl.classList.add("immortal");
        ttlEl.title = "Область закреплена (git / keep-маркер), TTL не действует";
        return;
      }
      ttlEl.classList.remove("immortal");
      const rem = d.remaining_seconds;
      ttlTextEl.textContent = fmtTtl(rem);
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

  // ── Live-синхронизация файлов (SSE: agent_sandbox → editor_gw → браузер) ──
  const liveEl = $("ed-live");
  const diskBanner = $("ed-disk-banner");
  const diskText = $("ed-disk-text");
  let es = null, esWs = null, esRetry = null, esFails = 0, esHadReady = false;
  let treeTimer = null, ttlBump = null, fileRev = 0;

  function setLive(state, title) {
    if (!liveEl) return;
    liveEl.dataset.state = state;
    liveEl.title = title;
  }
  function flashLive() {
    if (!liveEl) return;
    liveEl.classList.add("flash");
    setTimeout(() => liveEl.classList.remove("flash"), 400);
  }
  function stopWatch() {
    if (esRetry) { clearTimeout(esRetry); esRetry = null; }
    if (es) { es.close(); es = null; }
    esWs = null;
  }
  function startWatch() {
    stopWatch();
    esFails = 0; esHadReady = false;
    if (!currentWs || !window.EventSource) {
      setLive("off", window.EventSource ? "Нет области" : "Браузер не поддерживает live-обновление");
      return;
    }
    connectWatch(currentWs);
  }
  function connectWatch(ws) {
    esWs = ws;
    setLive("connecting", "Подключаюсь к live-обновлению…");
    const src = new EventSource(`/api/editor/watch?workspace_id=${encodeURIComponent(ws)}`);
    es = src;
    src.addEventListener("ready", (ev) => {
      if (src !== es) return;
      let d = {}; try { d = JSON.parse(ev.data); } catch (_) {}
      const reconnect = esHadReady || esFails > 0;
      esFails = 0; esHadReady = true;
      setLive("live", "Live: правки агента приходят автоматически. Нажмите, чтобы перечитать с диска");
      // после обрыва могли пропустить события — пересинхронизируемся
      if (reconnect) onFsChange({ tree_changed: true, modified: currentPath ? [currentPath] : [] });
    });
    src.addEventListener("change", (ev) => {
      if (src !== es) return;
      let d; try { d = JSON.parse(ev.data); } catch (_) { return; }
      onFsChange(d);
    });
    src.addEventListener("ttl", () => {
      if (src !== es) return;
      flashLive();
      refreshTtl();
    });
    src.addEventListener("gone", () => {
      if (src !== es) return;
      stopWatch();
      setLive("off", "Область удалена в песочнице");
    });
    src.onerror = () => {
      if (src !== es) return;
      if (src.readyState === EventSource.CLOSED) {
        // сервер ответил ошибкой (403/404/502) — переподключаемся сами с backoff
        src.close(); es = null;
        esFails++;
        if (esFails > 8) { setLive("off", "Live-обновление недоступно. Нажмите, чтобы переподключиться"); return; }
        const delay = Math.min(30000, 1000 * 2 ** (esFails - 1));
        setLive("connecting", `Нет связи, повтор через ${Math.round(delay / 1000)} с`);
        esRetry = setTimeout(() => { if (esWs === ws && currentWs === ws) connectWatch(ws); }, delay);
      } else {
        esFails++;
        setLive("connecting", "Связь прервалась, переподключаюсь…");
      }
    };
  }

  function onFsChange(d) {
    flashLive();
    const touched = new Set([...(d.created || []), ...(d.modified || [])]);
    if (d.tree_changed || d.truncated) {
      clearTimeout(treeTimer);
      treeTimer = setTimeout(() => loadTree().catch(() => {}), 250);
    }
    if (currentPath) {
      if ((d.deleted || []).includes(currentPath)) showDiskBanner("deleted");
      else if (touched.has(currentPath) || d.truncated) syncOpenFileFromDisk();
    }
    // активность агента продлевает TTL — обновим чип без ожидания минутного таймера
    clearTimeout(ttlBump);
    ttlBump = setTimeout(refreshTtl, 1500);
  }

  async function syncOpenFileFromDisk() {
    const ws = currentWs, path = currentPath;
    if (!ws || !path) return;
    let data;
    try {
      data = await apiGet(`/api/editor/file?workspace_id=${encodeURIComponent(ws)}&path=${encodeURIComponent(path)}`);
    } catch (_) { return; }
    if (ws !== currentWs || path !== currentPath) return;
    if (isBinary || data.is_binary) {
      fileRev++;
      if (previewOn) schedulePreview();
      return;
    }
    const disk = data.content;
    if (disk === currentContent) { hideDiskBanner(); return; } // в т.ч. эхо нашего сохранения
    if (editor && editor.getValue() === disk) {                // правки совпали с диском
      currentContent = disk; setDirty(false); hideDiskBanner(); return;
    }
    if (dirty) { pendingDisk = disk; showDiskBanner("modified"); return; }
    applyDiskContent(disk);
  }

  let pendingDisk = null;
  function applyDiskContent(text) {
    currentContent = text;
    if (editor) {
      const view = editor.saveViewState();
      editor.getModel().setValue(text);   // колбэк onDidChangeContent выставит dirty=false
      if (view) editor.restoreViewState(view);
    }
    setDirty(false);
    hideDiskBanner();
    if (previewOn) schedulePreview();
  }
  function showDiskBanner(kind) {
    if (!diskBanner) return;
    diskBanner.dataset.kind = kind;
    const name = (currentPath || "").split("/").pop();
    if (kind === "deleted") {
      diskText.textContent = `«${name}» удалён на диске. Сохранение создаст файл заново.`;
      $("ed-disk-reload").hidden = true;
      $("ed-disk-keep").textContent = "Понятно";
    } else {
      diskText.textContent = `«${name}» изменён на диске. У вас есть несохранённые правки: загрузить версию с диска (правки пропадут) или оставить свою?`;
      $("ed-disk-reload").hidden = false;
      $("ed-disk-keep").textContent = "Оставить мою версию";
    }
    diskBanner.hidden = false;
  }
  function hideDiskBanner() {
    pendingDisk = null;
    if (diskBanner) diskBanner.hidden = true;
  }
  $("ed-disk-reload")?.addEventListener("click", () => {
    if (pendingDisk != null) applyDiskContent(pendingDisk);
    else hideDiskBanner();
  });
  $("ed-disk-keep")?.addEventListener("click", () => {
    // своя версия остаётся в редакторе; «Сохранить» перезапишет диск
    if (diskBanner) diskBanner.hidden = true;
  });
  function manualResync() {
    if (!currentWs) return;
    loadTree().catch(() => {});
    if (currentPath) syncOpenFileFromDisk();
    startWatch();
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && diskBanner && !diskBanner.hidden) diskBanner.hidden = true;
  });
  liveEl?.addEventListener("click", manualResync);
  liveEl?.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); manualResync(); } });
  window.addEventListener("pagehide", stopWatch);

  const CHEV = '<svg class="ed-chev" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M4.5 3l3 3-3 3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const IC_DIR = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M2 4.5A1.2 1.2 0 0 1 3.2 3.3h2.6l1.3 1.4h5.7A1.2 1.2 0 0 1 14 5.9v5.9a1.2 1.2 0 0 1-1.2 1.2H3.2A1.2 1.2 0 0 1 2 11.8z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  const IC_FILE = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M4 2.5h5l3 3v8H4z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M9 2.5v3h3" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  const IC_IMG = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><rect x="2.5" y="3" width="11" height="10" rx="1.5" stroke="currentColor" stroke-width="1.2"/><circle cx="6" cy="6.5" r="1.1" fill="currentColor"/><path d="M3 11.5l3.2-3 2.3 2 1.8-1.5 2.7 2.5" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/></svg>';
  const IC_CODE = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M6 5L3 8l3 3M10 5l3 3-3 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const CODE_EXT = new Set(["py","js","ts","tsx","jsx","html","htm","css","json","sh","sql","go","rs","java","c","cpp","h","rb","php","yml","yaml","xml","vue"]);
  function iconFor(node) {
    if (node.type === "dir") return CHEV + IC_DIR;
    const e = String(node.name || "").split(".").pop().toLowerCase();
    const ic = ["png","jpg","jpeg","gif","webp","svg","bmp","ico","avif"].includes(e) ? IC_IMG : (CODE_EXT.has(e) ? IC_CODE : IC_FILE);
    return '<span class="ed-chev-sp"></span>' + ic;
  }

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

  const openDirs = new Set();
  function renderTree(nodes, container) {
    for (const node of nodes) {
      const row = document.createElement("div");
      row.dataset.path = node.path;
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
        if (openDirs.has(node.path)) {
          renderTree(node.children || [], kids); built = true;
          kids.style.display = ""; row.classList.add("open");
        }
        row.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!built) { renderTree(node.children || [], kids); built = true; }
          kids.style.display = kids.style.display === "none" ? "" : "none";
          const isOpen = kids.style.display !== "none";
          row.classList.toggle("open", isOpen);
          if (isOpen) openDirs.add(node.path); else openDirs.delete(node.path);
        });
      } else {
        if (node.path === currentPath) row.classList.add("sel");
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
    dirty = READONLY ? false : v;
    saveBtn.disabled = READONLY || !dirty || isBinary || saving;
    const roMark = READONLY && currentPath && !isBinary ? " (только чтение)" : "";
    filenameEl.textContent = (currentPath || "") + (isBinary ? " (бинарный)" : "") + roMark + (dirty ? " •" : "");
  }

  // подсветить файл в дереве и докрутить к нему (открытие по ?path=)
  function markTreeSel(path) {
    let row = null;
    for (const n of treeEl.querySelectorAll(".ed-node.file")) { if (n.dataset.path === path) { row = n; break; } }
    if (!row) return;
    treeEl.querySelectorAll(".ed-node.sel").forEach(n => { if (n !== row) n.classList.remove("sel"); });
    row.classList.add("sel");
    try { row.scrollIntoView({ block: "nearest" }); } catch (_) {}
  }

  async function openFile(path) {
    if (!currentWs) return;
    try {
      const data = await apiGet(
        `/api/editor/file?workspace_id=${encodeURIComponent(currentWs)}&path=${encodeURIComponent(path)}`);
      currentPath = path;
      hideDiskBanner();
      isBinary = !!data.is_binary;
      currentContent = isBinary ? "// Бинарный файл — редактирование недоступно" : data.content;
      const lang = guessLang(path);
      if (editor) {
        const model = monacoRef.editor.createModel(currentContent, lang);
        const prevModel = editor.getModel();
        editor.setModel(model);
        if (prevModel && prevModel !== model) prevModel.dispose();
        editor.updateOptions({ readOnly: READONLY || isBinary });
        model.onDidChangeContent(() => {
          setDirty(editor.getValue() !== currentContent);
          if (previewOn) schedulePreview();
        });
      }
      setDirty(false);
      onFileOpened();
      syncUrlPath(path);
      markTreeSel(path);
    } catch (e) {
      filenameEl.textContent = `Ошибка: ${e.message}`;
    }
  }

  async function saveFile() {
    if (READONLY || !editor || !currentWs || !currentPath || isBinary || saving || !dirty) return;
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

  // ── Monaco: локальная копия + тема из токенов приложения ──
  const MONACO_BASE = "/static/js/vendor/monaco/vs";
  require.config({ paths: { vs: MONACO_BASE } });
  // Воркеры Monaco грузим напрямую с того же origin, без data:-обёртки.
  // data:-воркер имеет opaque origin: его importScripts уходит без SameSite-cookie,
  // forward-auth отвечает редиректом на логин, и воркер падает с NetworkError.
  // workerMain.js сам вычисляет baseUrl ("../../../" от своего URL) = /static/js/vendor/monaco/.
  window.MonacoEnvironment = {
    getWorkerUrl() {
      return `${location.origin}/static/js/vendor/monaco/vs/base/worker/workerMain.js`;
    },
  };

  function cssVar(name, fallback = "") {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }
  const _colorCtx = document.createElement("canvas").getContext("2d");
  function toHex(color, fallback = "#000000") {
    try {
      _colorCtx.fillStyle = fallback;
      _colorCtx.fillStyle = color || fallback;
      const v = _colorCtx.fillStyle;
      return /^#[0-9a-f]{6}$/i.test(v) ? v : fallback;
    } catch (_) { return fallback; }
  }
  function isDarkTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark";
  }
  function applyMonacoTheme() {
    if (!monacoRef) return;
    const dark = isDarkTheme();
    const bg = toHex(cssVar("--bg"), dark ? "#0d0d0d" : "#faf9f7");
    const surface = toHex(cssVar("--surface"), dark ? "#141414" : "#ffffff");
    const surface2 = toHex(cssVar("--surface2"), dark ? "#1a1a1a" : "#f3f1ee");
    const border = toHex(cssVar("--border"), dark ? "#2a2a2a" : "#e0dbd4");
    const text = toHex(cssVar("--text-primary"), dark ? "#e8e4df" : "#1a1612");
    const secondary = toHex(cssVar("--text-secondary"), dark ? "#8a8580" : "#5c5650");
    const muted = toHex(cssVar("--text-muted"), dark ? "#555555" : "#9e9890");
    const accent = toHex(cssVar("--accent"), dark ? "#cc785c" : "#8b4513");
    const h = (c) => c.slice(1);
    monacoRef.editor.defineTheme("augent", {
      base: dark ? "vs-dark" : "vs",
      inherit: true,
      rules: [
        { token: "comment", foreground: dark ? "7d776f" : h(muted), fontStyle: "italic" },
        { token: "keyword", foreground: h(accent) },
        { token: "string", foreground: dark ? "a8c69f" : "3f7d3a" },
        { token: "number", foreground: dark ? "d7a96b" : "a35a00" },
        { token: "type", foreground: dark ? "8fb8de" : "2c5d8f" },
        { token: "tag", foreground: h(accent) },
        { token: "attribute.name", foreground: dark ? "8fb8de" : "2c5d8f" },
      ],
      colors: {
        "editor.background": bg,
        "editor.foreground": text,
        "editorGutter.background": bg,
        "minimap.background": bg,
        "editorLineNumber.foreground": muted,
        "editorLineNumber.activeForeground": secondary,
        "editorCursor.foreground": accent,
        "editor.selectionBackground": accent + "40",
        "editor.inactiveSelectionBackground": accent + "22",
        "editor.selectionHighlightBackground": accent + "1c",
        "editor.lineHighlightBackground": surface2 + (dark ? "99" : "cc"),
        "editor.lineHighlightBorder": "#00000000",
        "editorIndentGuide.background1": border,
        "editorIndentGuide.activeBackground1": muted,
        "editorWhitespace.foreground": border,
        "editorBracketMatch.background": accent + "18",
        "editorBracketMatch.border": accent + "88",
        "editorWidget.background": surface,
        "editorWidget.border": border,
        "editorSuggestWidget.background": surface,
        "editorSuggestWidget.border": border,
        "editorSuggestWidget.selectedBackground": surface2,
        "editorHoverWidget.background": surface,
        "editorHoverWidget.border": border,
        "editorStickyScroll.background": bg,
        "menu.background": surface,
        "menu.foreground": text,
        "menu.selectionBackground": surface2,
        "menu.selectionForeground": text,
        "menu.separatorBackground": border,
        "menu.border": border,
        "input.background": surface,
        "input.border": border,
        "focusBorder": accent + "88",
        "scrollbarSlider.background": muted + "44",
        "scrollbarSlider.hoverBackground": muted + "77",
        "scrollbarSlider.activeBackground": muted + "99",
      },
    });
    monacoRef.editor.setTheme("augent");
  }
  new MutationObserver(() => {
    applyMonacoTheme();
    onThemeChangedPreview();
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  function bootMonaco(cb) {
    // русская локализация Monaco должна загрузиться раньше editor.main
    require(["vs/nls.messages.ru"], () => require(["vs/editor/editor.main"], cb),
            () => require(["vs/editor/editor.main"], cb));
  }
  bootMonaco(() => {
    monacoRef = window.monaco;
    applyMonacoTheme();
    editor = monacoRef.editor.create($("ed-editor"), {
      value: isMobileViewport() ? "// Откройте файл: кнопка с папкой в шапке" : "// Выберите файл в дереве слева (Ctrl+B — показать/скрыть)",
      overviewRulerBorder: false,
      language: "plaintext",
      theme: "augent",
      fontFamily: cssVar("--font-mono", "monospace"),
      fontSize: 13,
      lineHeight: 20,
      padding: { top: 10, bottom: 10 },
      smoothScrolling: true,
      cursorSmoothCaretAnimation: "on",
      bracketPairColorization: { enabled: true },
      guides: { bracketPairs: true, indentation: true },
      stickyScroll: { enabled: true },
      scrollBeyondLastLine: false,
      renderLineHighlight: "all",
      scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
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
          startCol: sel.startColumn,
          endCol: sel.endColumn,
          path: currentPath,
          ws: currentWs,
        };
        if (layoutEl?.classList.contains("ai-collapsed")) {
          applyAiCollapsed(false);
        }
        updateSelectionHint();
        const ta = $("ed-ai-text");
        ta.focus();
      },
    });

    const runCmdOnEditor = (cmdName) => {
      const sel = editor.getSelection();
      const model = editor.getModel();
      if (sel && !sel.isEmpty()) {
        pendingSelection = { text: model.getValueInRange(sel), startLine: sel.startLineNumber, endLine: sel.endLineNumber, startCol: sel.startColumn, endCol: sel.endColumn, path: currentPath, ws: currentWs };
        updateSelectionHint();
      }
      if (layoutEl?.classList.contains("ai-collapsed")) applyAiCollapsed(false);
      runSlashCommand(cmdName, "");
    };
    [["tests", "Агент: написать тесты"], ["explain", "Агент: объяснить код"],
     ["review", "Агент: код-ревью"], ["refactor", "Агент: рефакторинг"], ["docs", "Агент: добавить документацию"]]
      .forEach(([name, label], i) => {
        editor.addAction({ id: `ed-cmd-${name}`, label, contextMenuGroupId: "navigation", contextMenuOrder: 0.1 + i * 0.01, run: () => runCmdOnEditor(name) });
      });

    loadWorkspaces();
  });

  // ── Превью файлов (HTML / Markdown / SVG / картинки / CSV) ──
  const workareaEl = $("ed-workarea");
  const previewEl = $("ed-preview");
  const previewBtn = $("ed-preview-toggle");
  const KEY_PREVIEW = "catchpm:ed-preview";
  const IMG_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "avif"]);
  let previewOn = false, previewTimer = null, previewBlobUrl = null;

  function fileExt(p) {
    const n = String(p || "").split("/").pop().toLowerCase();
    return n.includes(".") ? n.split(".").pop() : n;
  }
  function previewKind(p) {
    if (!p) return null;
    const e = fileExt(p);
    if (e === "html" || e === "htm") return "html";
    if (e === "md" || e === "markdown") return "md";
    if (e === "svg") return "svg";
    if (IMG_EXT.has(e)) return "image";
    if (e === "csv" || e === "tsv") return "csv";
    return null;
  }
  function revokePreviewBlob() {
    if (previewBlobUrl) { try { URL.revokeObjectURL(previewBlobUrl); } catch (_) {} previewBlobUrl = null; }
  }
  function currentText() { return editor && !isBinary ? editor.getValue() : currentContent; }
  function rawFileUrl() {
    return `/api/editor/download?workspace_id=${encodeURIComponent(currentWs || "")}&path=${encodeURIComponent(currentPath || "")}&v=${fileRev}`;
  }
  function parseDelimited(text, delim, maxRows) {
    const rows = []; let row = [], cell = "", q = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (q) {
        if (ch === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
        else cell += ch;
      } else if (ch === '"') q = true;
      else if (ch === delim) { row.push(cell); cell = ""; }
      else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); cell = ""; rows.push(row); row = [];
        if (rows.length >= maxRows) return { rows, truncated: true };
      } else cell += ch;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return { rows, truncated: false };
  }
  function renderPreview() {
    if (!previewOn || !currentPath || !previewEl) return;
    const kind = previewKind(currentPath);
    revokePreviewBlob();
    if (kind === "html") {
      let frame = previewEl.querySelector("iframe.ed-preview-frame");
      if (!frame) {
        previewEl.innerHTML = "";
        frame = document.createElement("iframe");
        frame.className = "ed-preview-frame";
        // без allow-same-origin: скрипты страницы изолированы от cookie и API приложения
        frame.setAttribute("sandbox", "allow-scripts allow-forms allow-modals allow-popups");
        frame.setAttribute("referrerpolicy", "no-referrer");
        frame.title = "Превью HTML";
        previewEl.appendChild(frame);
      }
      frame.srcdoc = currentText();
    } else if (kind === "md") {
      const st = previewEl.scrollTop;
      const wrap = document.createElement("div");
      wrap.className = "ed-preview-md";
      wrap.innerHTML = renderMd(currentText());
      wrap.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
      previewEl.replaceChildren(wrap);
      previewEl.scrollTop = st;
    } else if (kind === "svg" || kind === "image") {
      const box = document.createElement("div");
      box.className = "ed-preview-img";
      const img = document.createElement("img");
      img.alt = currentPath;
      if (kind === "svg" && !isBinary) {
        previewBlobUrl = URL.createObjectURL(new Blob([currentText()], { type: "image/svg+xml" }));
        img.src = previewBlobUrl;
      } else {
        img.src = rawFileUrl();
      }
      img.onerror = () => { box.innerHTML = '<div class="ed-preview-empty">Не удалось показать изображение</div>'; };
      box.appendChild(img);
      previewEl.replaceChildren(box);
    } else if (kind === "csv") {
      const delim = fileExt(currentPath) === "tsv" ? "\t" : (currentText().split("\n", 1)[0].includes(";") && !currentText().split("\n", 1)[0].includes(",") ? ";" : ",");
      const { rows, truncated } = parseDelimited(currentText(), delim, 501);
      const wrap = document.createElement("div");
      wrap.className = "ed-preview-table";
      const table = document.createElement("table");
      rows.forEach((r, i) => {
        const tr = document.createElement("tr");
        r.forEach(c => { const td = document.createElement(i === 0 ? "th" : "td"); td.textContent = c; td.title = c; tr.appendChild(td); });
        table.appendChild(tr);
      });
      wrap.appendChild(table);
      const note = document.createElement("div");
      note.className = "ed-preview-note";
      note.textContent = truncated ? "Показаны первые 500 строк" : `Строк: ${Math.max(0, rows.length - 1)}`;
      previewEl.replaceChildren(note, wrap);
    }
  }
  function schedulePreview() {
    if (previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, 250);
  }
  function onThemeChangedPreview() { if (previewOn) schedulePreview(); }
  function setPreview(on) {
    if (!previewEl || !workareaEl || !previewBtn) return;
    previewOn = !!on && !!previewKind(currentPath);
    previewEl.hidden = !previewOn;
    workareaEl.classList.toggle("preview-on", previewOn);
    workareaEl.classList.toggle("preview-only", previewOn && isBinary);
    previewBtn.classList.toggle("is-active", previewOn);
    previewBtn.setAttribute("aria-pressed", String(previewOn));
    if (previewOn) renderPreview(); else { previewEl.replaceChildren(); revokePreviewBlob(); }
    relayoutMonaco();
  }
  function onFileOpened() {
    const kind = previewKind(currentPath);
    if (previewBtn) previewBtn.hidden = !kind;
    if (!kind) { setPreview(false); return; }
    let want = false;
    if (isBinary) want = kind === "image";
    else { try { want = localStorage.getItem(KEY_PREVIEW) === "1"; } catch (_) {} }
    setPreview(want);
  }
  previewBtn?.addEventListener("click", () => {
    setPreview(!previewOn);
    if (!isBinary) { try { localStorage.setItem(KEY_PREVIEW, previewOn ? "1" : "0"); } catch (_) {} }
  });

  // ── UI events ────────────────────────────────────────
  wsSelect.addEventListener("change", async () => {
    if (dirty && !confirm("Есть несохранённые изменения. Сменить область?")) {
      wsSelect.value = currentWs; return;
    }
    currentWs = wsSelect.value || null;
    currentPath = null; isBinary = false; setDirty(false);
    filenameEl.textContent = "";
    openDirs.clear();
    hideDiskBanner();
    onFileOpened();
    syncUrlWs(currentWs);
    syncUrlPath(null);
    syncWsTitle();
    await activateWs();
  });
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
      const fname = String(pendingSelection.path || currentPath || "").split("/").pop();
      hint.innerHTML = `<span></span><button type="button" id="ed-ai-selclear" title="Сбросить">✕</button>`;
      hint.firstElementChild.textContent = `📎 Вопрос о: ${fname ? fname + " · " : ""}${range}`;
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
      if (window.GenerativeUI && typeof window.GenerativeUI.tryParseComponentJson === "function") {
        const genParsed = window.GenerativeUI.tryParseComponentJson(trimmed);
        if (genParsed) return genParsed;
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
    root.setAttribute("data-gen-component", "InteractivePoll");
    root.setAttribute("data-gen-state", "rendered");
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

  const renderGenUiEl = (data) => (window.GenerativeUI ? window.GenerativeUI.createBlockEl(data) : document.createElement("div"));
  const COMPONENT_MAP = {
    InteractivePoll: createInteractivePollEl,
    AskUserQuestion: createInteractivePollEl,
    Chart: renderGenUiEl,
    MetricCards: renderGenUiEl,
    DataTable: renderGenUiEl,
  };

  function renderBotContent(container, text, meta = {}) {
    if (!container) return;
    const isStreaming = !!meta.isStreaming;
    const segments = window.GenerativeUI && typeof window.GenerativeUI.extractStructuredSegments === 'function'
      ? window.GenerativeUI.extractStructuredSegments(text, tryParseComponentJson, { isStreaming })
      : extractStructuredSegments(text);

    const hasSpecial = segments.some(s => s.kind === 'component' || s.kind === 'skeleton');
    const hadStructuredNodes = !!container.querySelector(':scope > [data-seg-kind]');

    if (!hasSpecial && !hadStructuredNodes && !isStreaming) {
      container.innerHTML = renderMd(text);
      container.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
      return;
    }

    const botMessageId = String(meta.botMessageId || "").trim();
    const pollAnswers = Array.isArray(meta.pollAnswers) ? meta.pollAnswers : [];
    let pollIdx = 0;

    const existingNodes = [...container.querySelectorAll(':scope > [data-seg-kind]')];
    [...container.childNodes].forEach(node => {
      if (node.nodeType === Node.TEXT_NODE) {
        node.remove();
      } else if (node.nodeType === Node.ELEMENT_NODE && !node.hasAttribute('data-seg-kind')) {
        node.remove();
      }
    });

    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      const curNode = existingNodes[i] || null;

      if (seg.kind === 'md') {
        if (curNode && curNode.dataset.segKind === 'md') {
          if (curNode._rawMd !== seg.text) {
            curNode.innerHTML = renderMd(seg.text);
            curNode._rawMd = seg.text;
            curNode.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
          }
        } else {
          const mdBlock = document.createElement('div');
          mdBlock.className = 'ed-ai-md-part';
          mdBlock.dataset.segKind = 'md';
          mdBlock._rawMd = seg.text;
          mdBlock.innerHTML = renderMd(seg.text);
          mdBlock.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
          if (curNode) container.replaceChild(mdBlock, curNode);
          else container.appendChild(mdBlock);
        }
      } else if (seg.kind === 'skeleton') {
        if (curNode && curNode.dataset.segKind === 'skeleton' && window.GenerativeUI && window.GenerativeUI.updateSkeletonEl) {
          window.GenerativeUI.updateSkeletonEl(curNode, seg);
        } else {
          const skelEl = window.GenerativeUI ? window.GenerativeUI.createSkeletonEl(seg) : document.createElement('div');
          skelEl.dataset.segKind = 'skeleton';
          if (curNode) container.replaceChild(skelEl, curNode);
          else container.appendChild(skelEl);
        }
      } else if (seg.kind === 'component' && COMPONENT_MAP[seg.component]) {
        const curIdx = pollIdx++;
        const savedAnswer = pollAnswers.find(a => Number(a.poll_index || 0) === curIdx) || null;
        const segKey = seg.component + ':' + (seg.rawKey || JSON.stringify(seg.data));

        if (curNode && curNode.dataset.segKind === 'component' && curNode._segKey === segKey) {
          if (seg.component === 'InteractivePoll' || seg.component === 'AskUserQuestion' || seg.component === 'poll') {
            if (botMessageId) curNode.dataset.botMessageId = botMessageId;
            if (savedAnswer && typeof curNode._applySavedAnswer === 'function') {
              curNode._applySavedAnswer(savedAnswer);
            }
          }
        } else {
          const compEl = COMPONENT_MAP[seg.component](seg.data, {
            botMessageId,
            pollIndex: curIdx,
            savedAnswer,
          });
          compEl.dataset.segKind = 'component';
          compEl._segKey = segKey;
          if (curNode && curNode.dataset.segKind === 'skeleton') {
            compEl.classList.add('gen-ui-blk--appear');
          }
          if (curNode) container.replaceChild(compEl, curNode);
          else container.appendChild(compEl);
        }
      }
    }
    for (let i = segments.length; i < existingNodes.length; i++) {
      existingNodes[i].remove();
    }
  }

  async function streamRenderBotContent(container, rawText, meta = {}) {
    const full = String(rawText || "");
    let accumulated = "";
    let cursor = 0;
    const totalLen = full.length;
    const BASE_STEP = 14;
    const DELAY = 20;
    while (cursor < totalLen) {
      const rem = full.slice(cursor);
      const step = (window.GenerativeUI && typeof window.GenerativeUI.computeStreamStep === "function")
        ? window.GenerativeUI.computeStreamStep(accumulated, rem, BASE_STEP)
        : BASE_STEP;
      accumulated += rem.slice(0, step || 1);
      cursor += (step || 1);
      renderBotContent(container, accumulated, { ...meta, isStreaming: cursor < totalLen });
      aiLog.scrollTop = aiLog.scrollHeight;
      await new Promise(r => setTimeout(r, DELAY));
    }
    renderBotContent(container, full, { ...meta, isStreaming: false });
    aiLog.scrollTop = aiLog.scrollHeight;
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
      if (meta.animate) {
        streamRenderBotContent(d, text, {
          botMessageId: meta.botMessageId || meta.messageId || "",
          pollAnswers: meta.pollAnswers || [],
        });
      } else {
        renderBotContent(d, text, {
          botMessageId: meta.botMessageId || meta.messageId || "",
          pollAnswers: meta.pollAnswers || [],
          isStreaming: !!meta.isStreaming,
        });
      }
    } else {
      const cleanUserText = stripEditorPromptEnvelope(text);
      const pollRoot = meta.into || aiLog;
      d.textContent = cleanUserText;
      if (meta.pollAnswer && typeof meta.pollAnswer === "object") {
        const bmid = String(meta.pollAnswer.bot_message_id || "");
        const pidx = String(meta.pollAnswer.poll_index ?? 0);
        if (bmid) {
          pollRoot.querySelectorAll(`.ed-poll[data-bot-message-id="${bmid}"][data-poll-index="${pidx}"]`).forEach(el => {
            el._applySavedAnswer?.(meta.pollAnswer);
          });
        }
      }
      pollRoot.querySelectorAll(".ed-poll:not(.is-answered)").forEach(el => {
        el._syncWithUserReply?.(cleanUserText);
      });
    }
    if (!meta.into) aiLog.querySelector(":scope > .ed-ai-empty")?.remove();
    (meta.into || aiLog).appendChild(d);
    if (!meta.noScroll) aiLog.scrollTop = aiLog.scrollHeight;
    return d;
  }

  let thinkingEl = null;
  function showThinking() {
    if (thinkingEl) return;
    thinkingEl = document.createElement("div");
    thinkingEl.className = "ed-ai-msg bot ed-ai-thinking";
    thinkingEl.innerHTML = "<span></span><span></span><span></span>";
    aiLog.querySelector(":scope > .ed-ai-empty")?.remove();
    aiLog.appendChild(thinkingEl);
    aiLog.scrollTop = aiLog.scrollHeight;
  }
  function clearThinking() { if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; } }

  // ── История чата агента: первая страница + дозагрузка вверх (как в основном чате) ──
  const HISTORY_PAGE = 50;
  const hist = { loaded: 0, oldestId: null, hasMore: false, loading: false };

  function historyTopEl() {
    let el = aiLog.querySelector(":scope > .ed-ai-history-top");
    if (!el) {
      el = document.createElement("div");
      el.className = "ed-ai-history-top";
      el.dataset.state = "";
      aiLog.prepend(el);
    }
    return el;
  }
  function setHistoryState(state, text = "") {
    const el = historyTopEl();
    el.dataset.state = state;
    el.textContent = text;
  }
  function appendHistoryMessage(m, into = null) {
    const mid = m.message_id || (m.id != null ? String(m.id) : "");
    if (m.is_from_bot) {
      aiAppend("bot", m.message || "", { messageId: mid, botMessageId: mid, pollAnswers: m.poll_answers || [], into, noScroll: true });
    } else {
      aiAppend("user", m.message || "", { messageId: mid, pollAnswer: m.poll_answer || null, into, noScroll: true });
    }
  }
  function renderEmptyState() {
    const box = document.createElement("div");
    box.className = "ed-ai-empty";
    box.innerHTML = '<div class="ed-ai-empty-title">Агент рядом с кодом</div>' +
      '<div>Спросите про открытый файл, выделите фрагмент и нажмите ПКМ, или наберите <b>/</b> для быстрых команд.</div>' +
      '<div class="ed-ai-empty-cmds"></div>';
    const cmds = box.querySelector(".ed-ai-empty-cmds");
    ["tests", "explain", "review", "summary"].forEach(name => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "ed-ai-chip";
      b.textContent = `/${name}`;
      b.addEventListener("click", () => {
        const ta = $("ed-ai-text");
        ta.value = `/${name}`;
        ta.focus();
        ta.dispatchEvent(new Event("input"));
      });
      cmds.appendChild(b);
    });
    aiLog.appendChild(box);
  }
  function fetchHistoryPage(query) {
    return apiGet(`/api/chats/${encodeURIComponent(CHAT_ID)}/messages?${query}`);
  }
  async function loadEditorChatHistory() {
    if (!CHAT_ID || !aiLog) return;
    hist.loading = true;
    aiLog.innerHTML = "";
    setHistoryState("loading", "Загрузка истории…");
    try {
      const data = await fetchHistoryPage(`limit=${HISTORY_PAGE}&offset=0`);
      const msgs = Array.isArray(data?.messages) ? data.messages : [];
      for (const m of msgs) appendHistoryMessage(m);
      hist.loaded = msgs.length;
      hist.oldestId = msgs.length && msgs[0].id != null ? msgs[0].id : null;
      hist.hasMore = !!data?.has_more;
      setHistoryState(hist.hasMore ? "more" : "", hist.hasMore ? "Прокрутите вверх, чтобы загрузить ранние сообщения" : "");
      if (!msgs.length) renderEmptyState();
      aiLog.scrollTop = aiLog.scrollHeight;
    } catch (_) {
      setHistoryState("error", "Не удалось загрузить историю");
    } finally {
      hist.loading = false;
    }
  }
  async function loadOlderHistory() {
    if (!CHAT_ID || hist.loading || !hist.hasMore) return;
    hist.loading = true;
    setHistoryState("loading", "Загрузка…");
    const prevHeight = aiLog.scrollHeight, prevTop = aiLog.scrollTop;
    try {
      const q = hist.oldestId != null
        ? `limit=${HISTORY_PAGE}&before_id=${encodeURIComponent(hist.oldestId)}`
        : `limit=${HISTORY_PAGE}&offset=${hist.loaded}`;
      const data = await fetchHistoryPage(q);
      const older = Array.isArray(data?.messages) ? data.messages : [];
      if (!older.length) {
        hist.hasMore = false;
      } else {
        const staging = document.createElement("div");
        older.forEach(m => appendHistoryMessage(m, staging));
        const frag = document.createDocumentFragment();
        while (staging.firstChild) frag.appendChild(staging.firstChild);
        historyTopEl().after(frag);
        hist.loaded += older.length;
        if (older[0]?.id != null) hist.oldestId = older[0].id;
        hist.hasMore = !!data.has_more;
        aiLog.scrollTop = prevTop + (aiLog.scrollHeight - prevHeight);
      }
      setHistoryState(hist.hasMore ? "more" : "end", hist.hasMore ? "Прокрутите вверх, чтобы загрузить ещё" : "Начало диалога");
    } catch (_) {
      setHistoryState("error", "Не удалось загрузить, прокрутите ещё раз");
    } finally {
      hist.loading = false;
    }
  }
  if (aiLog) {
    let histTicking = false;
    aiLog.addEventListener("scroll", () => {
      if (histTicking) return;
      histTicking = true;
      requestAnimationFrame(() => {
        histTicking = false;
        if (aiLog.scrollTop < 120) loadOlderHistory();
      });
    }, { passive: true });
  }

  if (CHAT_ID) {
    loadEditorChatHistory();
    // Входящее сообщение чата (из WS main или из SSE-финала Sigma)
    const handleIncoming = (m) => {
      if (!m || typeof m !== "object") return;
      if (m.is_from_bot && m.message) {
        clearThinking();
        const mid = m.message_id || (m.id != null ? String(m.id) : "");
        aiAppend("bot", m.message, {
          messageId: mid,
          botMessageId: mid,
          pollAnswers: m.poll_answers || [],
          animate: true,
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
    };
    if (CHAT_TRANSPORT === "ws") {
      try {
        const proto = location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${proto}://${location.host}/ws/chat/${encodeURIComponent(CHAT_ID)}`);
        ws.onmessage = (ev) => {
          try { handleIncoming(JSON.parse(ev.data)); } catch (_) {}
        };
      } catch (_) {}
    } else {
      // Sigma: WebSocket нет. После отправки держим SSE /api/chats/{id}/stream до done/idle.
      let sigmaEs = null;
      window.__edWatchSigmaRun = () => {
        if (!window.EventSource) return;
        if (sigmaEs) { try { sigmaEs.close(); } catch (_) {} }
        const es = new EventSource(`/api/chats/${encodeURIComponent(CHAT_ID)}/stream`, { withCredentials: true });
        sigmaEs = es;
        const finish = () => { try { es.close(); } catch (_) {} if (sigmaEs === es) sigmaEs = null; };
        es.addEventListener("done", (ev) => {
          finish();
          let st = null;
          try { st = JSON.parse(ev.data); } catch (_) {}
          const msg = st && st.message;
          if (msg && typeof msg === "object") handleIncoming({ is_from_bot: true, ...msg });
          else clearThinking();
        });
        es.addEventListener("idle", () => { finish(); clearThinking(); });
        es.addEventListener("timeout", () => { finish(); window.__edWatchSigmaRun(); });
        es.onerror = () => { if (es.readyState === EventSource.CLOSED) finish(); };
      };
    }
  } else {
    aiAppend("bot", "Чат не привязан — откройте редактор из чата, чтобы спросить агента.");
  }

  // Метаданные контекста для агента: воркспейс (id + название), позиция выделения, язык, правки.
  function wsLabel(ws) {
    if (!ws) return "—";
    const o = Array.from(wsSelect.options || []).find(x => x.value === ws);
    const name = o ? String(o.textContent || "").trim() : "";
    return name && name !== ws ? `${ws} («${name}»)` : ws;
  }
  function editorLink(ws) {
    if (!CHAT_ID) return null;
    const u = new URL("/editor", location.origin);
    u.searchParams.set("chat_id", CHAT_ID);
    if (ws) u.searchParams.set("workspace_id", ws);
    return u.toString();
  }
  function selectionHeader(sel, maxChars) {
    const path = sel.path || currentPath || "";
    const ws = sel.ws || currentWs || "";
    const lineLoc = sel.startLine === sel.endLine ? `строка ${sel.startLine}` : `строки ${sel.startLine}–${sel.endLine}`;
    const pos = (sel.startCol && sel.endCol) ? ` (${sel.startLine}:${sel.startCol} → ${sel.endLine}:${sel.endCol})` : "";
    const nLines = sel.endLine - sel.startLine + 1;
    const total = path && path === currentPath ? String(currentText() || "").split("\n").length : 0;
    const lang = path ? guessLang(path) : "";
    const parts = [
      `файл ${path || "—"}`,
      `воркспейс ${wsLabel(ws)}`,
      `${lineLoc}${pos}`,
      `выделено ${nLines} стр. · ${sel.text.length} симв.`,
    ];
    if (lang) parts.push(`язык ${lang}`);
    if (total) parts.push(`в файле ${total} строк`);
    if (dirty && path === currentPath) parts.push("есть несохранённые правки");
    const cut = sel.text.length > maxChars ? `\n(показаны первые ${maxChars} символов из ${sel.text.length})` : "";
    return { head: `[Выделенный фрагмент · ${parts.join(" · ")}]`, cut, lang };
  }

  async function dispatchAIMessage(userVisibleText, pollAnswer = null, opts = {}) {
    const text = String(userVisibleText || "").trim();
    if (!text || !CHAT_ID) return;
    let msg = text;
    const fileUrl = (currentWs && currentPath) ? editorLink(currentWs) : null;
    if (pendingSelection) {
      const sel = pendingSelection;
      const { head, cut, lang } = selectionHeader(sel, 8000);
      const selUrl = editorLink(sel.ws || currentWs) || fileUrl;
      const link = selUrl ? `\nСсылка на файл в редакторе: ${selUrl}` : "";
      msg = `${head}\n${codeFence(sel.text.slice(0, 8000), lang)}${cut}${link}\n\nВопрос: ${text}`;
    } else if (currentPath && !isBinary) {
      const lines = currentContent ? currentContent.split("\n").length : 0;
      const bytes = currentContent ? new Blob([currentContent]).size : 0;
      const link = fileUrl ? `\nСсылка на файл в редакторе: ${fileUrl}` : "";
      msg = `[Открыт файл: ${currentPath} · воркспейс ${wsLabel(currentWs)} · ${lines} строк · ${bytes} байт]${link}\n\nФайл целиком НЕ приложен — при необходимости спроси и я пришлю содержимое или выделю фрагмент.\n\nВопрос: ${text}`;
    } else {
      msg = `${text}`;
    }
    if (opts.command) msg = buildCommandMessage(opts.command, opts.extra || "", fileUrl);
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
          timezone_name:  (Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow"),
          tz_offset_minutes: new Date().getTimezoneOffset(),
          ...(pollAnswer ? { poll_answer: pollAnswer } : {}),
        }),
      });
      if (CHAT_TRANSPORT === "sse" && typeof window.__edWatchSigmaRun === "function") window.__edWatchSigmaRun();
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
    autosizeAiInput();
    closeSlash();
    const m = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text);
    if (m && findSlashCmd(m[1])) { await runSlashCommand(m[1], (m[2] || "").trim()); return; }
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
    try { window.ModelPicker.loadModels?.(); } catch (_) {}
    try { window.ToolsPanel.inject(container); } catch (_) {}
    try { window.ModelPicker.inject(container); } catch (_) {}
  })();


  // ── Slash-команды агента (/tests, /explain, …) ──
  const SLASH_ICON = {
    tests: '<path d="M7.5 3.5h5M8.5 3.5v4.2L4.6 14.4A1.4 1.4 0 0 0 5.8 16.5h8.4a1.4 1.4 0 0 0 1.2-2.1L11.5 7.7V3.5" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M6.5 12h7" stroke="currentColor" stroke-width="1.5"/>',
    explain: '<circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.5"/><path d="M8 8a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.3v.4M10 13.8v.1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    review: '<path d="M3 10s2.6-5 7-5 7 5 7 5-2.6 5-7 5-7-5-7-5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="10" cy="10" r="2" stroke="currentColor" stroke-width="1.5"/>',
    refactor: '<path d="M4 7h10l-2.5-2.5M16 13H6l2.5 2.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
    docs: '<path d="M6 3.5h5.5L15 7v9.5H6z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8.5 10.5h4M8.5 13h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    summary: '<path d="M4.5 5.5h11M4.5 10h11M4.5 14.5h6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
    describe: '<path d="M4 15.5l1-3.5 7.5-7.5 2.5 2.5L7.5 14.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>',
    fix: '<path d="M12.5 4a3.5 3.5 0 0 0-3.3 4.6L4.5 13.3a1.5 1.5 0 0 0 2.1 2.1l4.7-4.7A3.5 3.5 0 0 0 16 7.5l-2 2-2-.5-.5-2 2-2z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>',
  };
  const SLASH_CMDS = [
    { name: "tests", desc: "Написать тесты", prompt: "Напиши unit-тесты для этого кода. Используй тестовый фреймворк, принятый в проекте (для Python по умолчанию pytest). Покрой основные сценарии, граничные случаи и ошибки. Верни готовый файл тестов и коротко перечисли, что покрыто." },
    { name: "explain", desc: "Объяснить код", prompt: "Объясни, что делает этот код: назначение, ключевые шаги, входы и выходы, неочевидные места. Коротко и по делу." },
    { name: "review", desc: "Код-ревью", prompt: "Сделай код-ревью: баги, уязвимости, гонки, утечки ресурсов, проблемы читаемости. Для каждого пункта укажи строку и предложи исправление. Отсортируй по важности." },
    { name: "fix", desc: "Найти и исправить ошибки", prompt: "Найди вероятные ошибки в этом коде и исправь их. Покажи исправленный фрагмент и объясни причину каждой ошибки." },
    { name: "refactor", desc: "Рефакторинг", prompt: "Предложи рефакторинг без изменения поведения: упростить, убрать дублирование, улучшить имена. Покажи итоговый код и перечисли изменения." },
    { name: "docs", desc: "Docstring и комментарии", prompt: "Добавь документацию: docstring или JSDoc для функций и классов, комментарии в неочевидных местах. Логику не меняй. Верни код с документацией." },
    { name: "summary", desc: "Саммари файла", prompt: "Сделай краткое саммари: назначение файла, основные сущности (классы, функции, роуты) по одной строке, внешние зависимости и что важно знать перед правкой." },
    { name: "describe", desc: "Описание для README или PR", prompt: "Напиши описание этого кода для README или PR в Markdown: что делает, как использовать, пример вызова или запуска." },
  ];
  const SLASH_MAX_CHARS = 60000;

  function findSlashCmd(name) {
    return SLASH_CMDS.find(c => c.name === String(name || "").toLowerCase()) || null;
  }
  function codeFence(body, lang) {
    const fence = body.includes("```") ? "````" : "```";
    return `${fence}${lang || ""}\n${body}\n${fence}`;
  }
  function buildCommandMessage(cmd, extra, fileUrl) {
    const link = fileUrl ? `\nСсылка на файл в редакторе: ${fileUrl}` : "";
    const lang = currentPath ? guessLang(currentPath) : "";
    let ctx;
    if (pendingSelection) {
      const { head, cut, lang: selLang } = selectionHeader(pendingSelection, SLASH_MAX_CHARS);
      ctx = `${head}\n${codeFence(pendingSelection.text.slice(0, SLASH_MAX_CHARS), selLang || lang)}${cut}`;
    } else if (currentPath && !isBinary) {
      const body = currentText() || "";
      const lines = body ? body.split("\n").length : 0;
      const cut = body.length > SLASH_MAX_CHARS ? `\n(показаны первые ${SLASH_MAX_CHARS} символов из ${body.length})` : "";
      ctx = `[Файл: ${currentPath} · воркспейс ${wsLabel(currentWs)} · ${lines} строк]\n${codeFence(body.slice(0, SLASH_MAX_CHARS), lang)}${cut}`;
    } else {
      ctx = "[Файл в редакторе не открыт]";
    }
    const extraLine = extra ? `\nДополнительно от пользователя: ${extra}` : "";
    return `[Команда редактора /${cmd.name}]\n${cmd.prompt}${extraLine}\n\n${ctx}${link}\n\nВопрос: /${cmd.name}${extra ? " " + extra : ""}`;
  }
  async function runSlashCommand(name, extra = "") {
    const cmd = findSlashCmd(name);
    if (!cmd || !CHAT_ID) return;
    const visible = `/${cmd.name}${extra ? " " + extra : ""}`;
    await dispatchAIMessage(visible, null, { command: cmd, extra });
  }

  const aiTa = $("ed-ai-text");
  const aiInputArea = $("ed-ai")?.querySelector(".chat-input-area");
  let slashMatches = [], slashIdx = 0;

  function autosizeAiInput() {
    if (!aiTa) return;
    aiTa.style.height = "auto";
    aiTa.style.height = `${Math.min(aiTa.scrollHeight, 200)}px`;
  }
  function closeSlash() {
    aiInputArea?.querySelector(".slash-cmd-popover")?.remove();
    slashMatches = [];
    slashIdx = 0;
  }
  function slashContextLabel() {
    if (pendingSelection) {
      const { startLine, endLine } = pendingSelection;
      return startLine === endLine ? `Контекст: строка ${startLine}` : `Контекст: строки ${startLine}–${endLine}`;
    }
    if (currentPath && !isBinary) return `Контекст: ${currentPath.split("/").pop()}`;
    return "Файл не открыт";
  }
  function renderSlash() {
    if (!aiInputArea) return;
    let pop = aiInputArea.querySelector(".slash-cmd-popover");
    if (!pop) {
      pop = document.createElement("div");
      pop.className = "slash-cmd-popover ed-slash-popover";
      pop.setAttribute("role", "menu");
      aiInputArea.appendChild(pop);
    }
    const items = slashMatches.map((c, i) => {
      const hl = i === slashIdx ? ' data-highlighted=""' : "";
      return `<div role="menuitem" class="slash-cmd-item" data-idx="${i}"${hl}>` +
        `<div class="slash-cmd-icon"><svg viewBox="0 0 20 20" width="16" height="16" fill="none">${SLASH_ICON[c.name] || ""}</svg></div>` +
        `<span class="slash-cmd-name">/${c.name}</span><span class="ed-slash-desc">${c.desc}</span></div>`;
    }).join("");
    pop.innerHTML = `<div class="slash-cmd-scroll"><div class="slash-cmd-list">${items}</div></div>` +
      `<div class="ed-slash-ctx"><span></span><span>Enter запустить · Tab дописать</span></div>`;
    pop.querySelector(".ed-slash-ctx span").textContent = slashContextLabel();
    pop.querySelectorAll(".slash-cmd-item").forEach(el => {
      el.addEventListener("mousedown", (e) => e.preventDefault());
      el.addEventListener("mouseenter", () => {
        slashIdx = Number(el.dataset.idx) || 0;
        pop.querySelectorAll(".slash-cmd-item").forEach((x, i) => x.toggleAttribute("data-highlighted", i === slashIdx));
      });
      el.addEventListener("click", () => pickSlash(Number(el.dataset.idx) || 0, true));
    });
    pop.querySelector("[data-highlighted]")?.scrollIntoView({ block: "nearest" });
  }
  function syncSlash() {
    const m = /^\/(\S*)$/.exec(aiTa?.value || "");
    if (!m) { closeSlash(); return; }
    const q = m[1].toLowerCase();
    slashMatches = SLASH_CMDS.filter(c => c.name.startsWith(q) || c.desc.toLowerCase().includes(q));
    if (!slashMatches.length) { closeSlash(); return; }
    if (slashIdx >= slashMatches.length) slashIdx = 0;
    renderSlash();
  }
  function pickSlash(idx, run) {
    const cmd = slashMatches[idx];
    if (!cmd) return;
    closeSlash();
    if (run) {
      aiTa.value = "";
      autosizeAiInput();
      runSlashCommand(cmd.name, "");
    } else {
      aiTa.value = `/${cmd.name} `;
      aiTa.focus();
      autosizeAiInput();
    }
  }

  $("ed-ai-send").addEventListener("click", sendAI);
  aiTa?.addEventListener("input", () => { autosizeAiInput(); syncSlash(); });
  aiTa?.addEventListener("blur", () => setTimeout(closeSlash, 120));
  aiTa?.addEventListener("keydown", (e) => {
    const popOpen = slashMatches.length > 0;
    if (popOpen) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const d = e.key === "ArrowDown" ? 1 : -1;
        slashIdx = (slashIdx + d + slashMatches.length) % slashMatches.length;
        renderSlash();
        return;
      }
      if (e.key === "Tab") { e.preventDefault(); pickSlash(slashIdx, false); return; }
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); pickSlash(slashIdx, true); return; }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeSlash(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendAI(); }
  });

  // Горячие клавиши редактора
  document.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    if (e.shiftKey && e.code === "KeyA") {
      e.preventDefault();
      const opening = layoutEl?.classList.contains("ai-collapsed");
      applyAiCollapsed(!opening);
      if (opening) setTimeout(() => aiTa?.focus(), 60);
    } else if (e.shiftKey && e.code === "KeyV" && e.target !== aiTa && previewBtn && !previewBtn.hidden) {
      e.preventDefault();
      previewBtn.click();
    } else if (!e.shiftKey && e.code === "KeyB") {
      e.preventDefault();
      applyLeftCollapsed(!layoutEl?.classList.contains("sidebar-collapsed"));
    }
  }, true);

})();
