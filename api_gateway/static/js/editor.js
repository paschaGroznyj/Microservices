(() => {
  const CHAT_ID = document.body.dataset.chatId || "";
  let editor = null, monacoRef = null;
  let currentWs = null, currentPath = null, currentContent = "";
  let dirty = false, isBinary = false, saving = false;

  const $ = (id) => document.getElementById(id);
  const treeEl = $("ed-tree");
  const wsSelect = $("ed-ws-select");
  const filenameEl = $("ed-filename");
  const saveBtn = $("ed-save");

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
    currentWs = list[0].workspace_id;
    await loadTree();
  }

  async function loadTree() {
    if (!currentWs) return;
    const data = await apiGet(`/api/editor/tree?workspace_id=${encodeURIComponent(currentWs)}`);
    treeEl.innerHTML = "";
    renderTree(data.tree || [], treeEl);
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
    await loadTree();
  });
  $("ed-refresh").addEventListener("click", loadTree);
  saveBtn.addEventListener("click", saveFile);
  window.addEventListener("beforeunload", (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });

  // ── AI-панель ────────────────────────────────────────
  const aiLog = $("ed-ai-log");
  if (window.marked) marked.setOptions({ breaks: true, gfm: true });

  function renderMd(text) {
    if (window.marked && window.DOMPurify) {
      return DOMPurify.sanitize(marked.parse(text || ""));
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
    if (currentPath && !isBinary) {
      msg = `[Файл: ${currentPath}]\n\`\`\`\n${currentContent.slice(0, 8000)}\n\`\`\`\n\nВопрос: ${text}`;
    }
    aiAppend("user", text);
    ta.value = "";
    showThinking();
    try {
      await fetch("/api/chats/send_message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ chat_id: CHAT_ID, message: msg, message_id: crypto.randomUUID() }),
      });
    } catch (e) {
      clearThinking();
      aiAppend("bot", `Ошибка отправки: ${e.message}`);
    }
  }
  $("ed-ai-send").addEventListener("click", sendAI);
  $("ed-ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendAI(); }
  });
})();
