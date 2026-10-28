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
  const treeEl = $("ed-tree");
  const wsSelect = $("ed-ws-select");
  const filenameEl = $("ed-filename");
  const saveBtn = $("ed-save");

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
      o.textContent = w.title || w.workspace_id;
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

  function renderTree(nodes, container) {
    for (const node of nodes) {
      const row = document.createElement("div");
      row.className = `ed-node ${node.type}`;
      row.innerHTML = `<span class="ed-ic">${iconFor(node)}</span>${node.name}`;
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
      minimap: { enabled: true },
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
  // Делегат: любой клик по ссылке в логе открываем в новой вкладке,
  // даже если realtime-рендер markdown не успел проставить target="_blank".
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
  let pendingSelection = null; // {text, startLine, endLine} — контекст из выделения

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

  // Чередуем подсказку в placeholder: про весь файл и про точечный вопрос по ПКМ.
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

  function aiAppend(role, text) {
    const d = document.createElement("div");
    d.className = `ed-ai-msg ${role}`;
    if (role === "bot") {
      d.innerHTML = renderMd(text);
      d.querySelectorAll("pre code").forEach(b => { try { window.hljs && hljs.highlightElement(b); } catch (_) {} });
    } else {
      d.textContent = text;
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

  if (CHAT_ID) {
    try {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/chat/${encodeURIComponent(CHAT_ID)}`);
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.is_from_bot && m.message) { clearThinking(); aiAppend("bot", m.message); }
        } catch (_) {}
      };
    } catch (_) {}
  } else {
    aiAppend("bot", "Чат не привязан — откройте редактор из чата, чтобы спросить агента.");
  }

  async function sendAI() {
    const ta = $("ed-ai-text");
    const text = ta.value.trim();
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
    }
    aiAppend("user", text);
    ta.value = "";
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
          message_id: crypto.randomUUID(),
          agent_model:    window.ModelPicker?.getModel() ?? "coder",
          selected_tools: window.ToolsPanel?.getSelectedCategories() ?? [],
          context_size:   window.ToolsPanel?.getContextSize() ?? "medium",
        }),
      });
    } catch (e) {
      clearThinking();
      aiAppend("bot", `Ошибка отправки: ${e.message}`);
    }
  }
  // Подключаем те же панели выбора модели и инструментов, что в основном чате.
  // tools-panel.js грузится отдельным модулем — ждём появления window.ToolsPanel.
  (function injectPanels(tries = 0) {
    const footer = $("ed-ai")?.querySelector(".ed-ai-footer");
    if (!footer) return;
    if (!window.ToolsPanel || !window.ModelPicker) {
      if (tries < 50) return void setTimeout(() => injectPanels(tries + 1), 100);
      return;
    }
    // inject() ищет .chat-input-footer внутри переданного контейнера.
    // setUser() сам тянет /api/auth/permissions по куке — права берутся оттуда.
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
