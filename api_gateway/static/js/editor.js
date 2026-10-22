(() => {
  const CHAT_ID = document.body.dataset.chatId || "";
  let editor = null, monacoRef = null;
  let currentWs = null, currentPath = null, currentContent = "";

  const $ = (id) => document.getElementById(id);
  const treeEl = $("ed-tree");
  const wsSelect = $("ed-ws-select");
  const filenameEl = $("ed-filename");

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

  function iconFor(node) {
    if (node.type === "dir") return "📁";
    return "📄";
  }

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
          document.querySelectorAll(".ed-node.sel").forEach(n => n.classList.remove("sel"));
          row.classList.add("sel");
          openFile(node.path);
        });
      }
    }
  }

  async function openFile(path) {
    if (!currentWs) return;
    try {
      const data = await apiGet(
        `/api/editor/file?workspace_id=${encodeURIComponent(currentWs)}&path=${encodeURIComponent(path)}`);
      currentPath = path;
      filenameEl.textContent = path + (data.is_binary ? " (бинарный)" : "");
      currentContent = data.is_binary ? "// Бинарный файл — предпросмотр недоступен" : data.content;
      const lang = guessLang(path);
      if (editor) {
        const model = monacoRef.editor.createModel(currentContent, lang);
        editor.setModel(model);
      }
    } catch (e) {
      filenameEl.textContent = `Ошибка: ${e.message}`;
    }
  }

  function guessLang(path) {
    const ext = (path.split(".").pop() || "").toLowerCase();
    const map = { py:"python", js:"javascript", ts:"typescript", json:"json",
      html:"html", css:"css", md:"markdown", sql:"sql", sh:"shell", yml:"yaml",
      yaml:"yaml", xml:"xml", java:"java", c:"c", cpp:"cpp", h:"cpp", go:"go",
      rs:"rust", rb:"ruby", php:"php", ino:"cpp", txt:"plaintext" };
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
    loadWorkspaces();
  });

  // ── UI events ────────────────────────────────────────
  wsSelect.addEventListener("change", async () => {
    currentWs = wsSelect.value || null;
    currentPath = null; filenameEl.textContent = "";
    await loadTree();
  });
  $("ed-refresh").addEventListener("click", loadTree);

  // ── AI-панель (read-only советник) ───────────────────
  const aiLog = $("ed-ai-log");
  function aiAppend(role, text) {
    const d = document.createElement("div");
    d.className = `ed-ai-msg ${role}`;
    d.textContent = text;
    aiLog.appendChild(d);
    aiLog.scrollTop = aiLog.scrollHeight;
    return d;
  }

  if (CHAT_ID) {
    try {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/chat/${encodeURIComponent(CHAT_ID)}`);
      ws.onmessage = (ev) => {
        try {
          const m = JSON.parse(ev.data);
          if (m.is_from_bot && m.message) aiAppend("bot", m.message);
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
    if (currentPath) {
      msg = `[Файл: ${currentPath}]\n\`\`\`\n${currentContent.slice(0, 8000)}\n\`\`\`\n\nВопрос: ${text}`;
    }
    aiAppend("user", text);
    ta.value = "";
    try {
      await fetch("/api/chats/send_message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ chat_id: CHAT_ID, message: msg,
          message_id: crypto.randomUUID() }),
      });
    } catch (e) {
      aiAppend("bot", `Ошибка отправки: ${e.message}`);
    }
  }
  $("ed-ai-send").addEventListener("click", sendAI);
  $("ed-ai-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendAI(); }
  });
})();
