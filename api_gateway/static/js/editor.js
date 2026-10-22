// Monaco-редактор воркспейса. Без исполнения кода — только правка файлов.
(function () {
  const API = {
    tree:   () => fetch('/api/editor/tree').then(r => r.json()),
    read:   (p) => fetch('/api/editor/file?path=' + encodeURIComponent(p)).then(async r => {
      if (!r.ok) throw new Error((await r.json().catch(()=>({}))).detail || r.status);
      return r.json();
    }),
    write:  (p, body) => fetch('/api/editor/file?path=' + encodeURIComponent(p), {
      method: 'PUT', headers: {'Content-Type':'text/plain'}, body
    }),
    create: (path, type) => fetch('/api/editor/create', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path, type})
    }),
    rename: (path, new_path) => fetch('/api/editor/rename', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path, new_path})
    }),
    del:    (path) => fetch('/api/editor/delete', {
      method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path})
    }),
  };

  let editor = null;
  let current = null;   // {path, lang}
  let dirty = false;

  const $tree   = document.getElementById('tree');
  const $empty  = document.getElementById('empty');
  const $save   = document.getElementById('btnSave');
  const $cur    = document.getElementById('curFile');
  const $toast  = document.getElementById('toast');

  function toast(msg, err) {
    $toast.textContent = msg;
    $toast.className = 'toast show' + (err ? ' err' : '');
    setTimeout(() => { $toast.className = 'toast'; }, 2200);
  }

  function setDirty(v) {
    dirty = v;
    $save.disabled = !v || !current;
    $cur.textContent = current ? (current.path + (v ? ' •' : '')) : 'файл не выбран';
    $cur.className = 'fname' + (v ? ' dirty' : '');
  }

  // ───── Дерево ─────
  function iconFor(node) {
    if (node.type === 'dir') return '🗀';
    return '🖹';
  }

  function renderTree(nodes, container) {
    container.innerHTML = '';
    for (const node of nodes) {
      const wrap = document.createElement('div');
      wrap.className = 'node';
      const row = document.createElement('div');
      row.className = 'row';
      row.dataset.path = node.path;
      row.dataset.type = node.type;

      if (node.type === 'dir') {
        const tw = document.createElement('span'); tw.className = 'tw'; tw.textContent = '▸';
        const ic = document.createElement('span'); ic.className = 'ic'; ic.textContent = '🗀';
        const nm = document.createElement('span'); nm.textContent = node.name;
        row.append(tw, ic, nm);
        const kids = document.createElement('div');
        kids.className = 'children'; kids.style.display = 'none';
        renderTree(node.children || [], kids);
        row.addEventListener('click', (e) => {
          e.stopPropagation();
          const open = kids.style.display !== 'none';
          kids.style.display = open ? 'none' : 'block';
          tw.textContent = open ? '▸' : '▾';
        });
        row.addEventListener('contextmenu', (e) => ctxMenu(e, node));
        wrap.append(row, kids);
      } else {
        const tw = document.createElement('span'); tw.className = 'tw';
        const ic = document.createElement('span'); ic.className = 'ic'; ic.textContent = '🖹';
        const nm = document.createElement('span'); nm.textContent = node.name;
        row.append(tw, ic, nm);
        row.addEventListener('click', (e) => { e.stopPropagation(); openFile(node, row); });
        row.addEventListener('contextmenu', (e) => ctxMenu(e, node));
        wrap.append(row);
      }
      container.append(wrap);
    }
  }

  async function reloadTree() {
    try {
      const data = await API.tree();
      renderTree(data.root || [], $tree);
    } catch (e) { toast('Ошибка загрузки дерева', true); }
  }

  // ───── Открытие / сохранение файла ─────
  async function openFile(node, rowEl) {
    if (dirty && !confirm('Есть несохранённые изменения. Открыть другой файл?')) return;
    try {
      const data = await API.read(node.path);
      current = { path: node.path, lang: data.lang };
      $empty.style.display = 'none';
      const model = monaco.editor.createModel(data.content, data.lang);
      editor.setModel(model);
      editor.focus();
      setDirty(false);
      document.querySelectorAll('.row.sel').forEach(r => r.classList.remove('sel'));
      if (rowEl) rowEl.classList.add('sel');
    } catch (e) { toast('Не открыть: ' + e.message, true); }
  }

  async function saveFile() {
    if (!current || !dirty) return;
    try {
      const r = await API.write(current.path, editor.getValue());
      if (!r.ok) { const j = await r.json().catch(()=>({})); throw new Error(j.detail || r.status); }
      setDirty(false);
      toast('Сохранено: ' + current.path);
    } catch (e) { toast('Ошибка сохранения: ' + e.message, true); }
  }

  // ───── Контекстное меню (переименовать / удалить) ─────
  function ctxMenu(e, node) {
    e.preventDefault();
    const action = prompt(
      'Действие с "' + node.path + '":\n  r — переименовать\n  d — удалить\n(введите r или d)'
    );
    if (action === 'r') {
      const np = prompt('Новый путь:', node.path);
      if (np && np !== node.path) API.rename(node.path, np).then(reloadTree);
    } else if (action === 'd') {
      if (confirm('Удалить "' + node.path + '"?')) API.del(node.path).then(() => {
        if (current && current.path === node.path) { current = null; editor.setModel(null); $empty.style.display='flex'; setDirty(false); }
        reloadTree();
      });
    }
  }

  // ───── Тулбар ─────
  document.getElementById('btnSave').addEventListener('click', saveFile);
  document.getElementById('btnReload').addEventListener('click', reloadTree);
  document.getElementById('btnNewFile').addEventListener('click', () => {
    const p = prompt('Путь нового файла (относительно воркспейса):', 'new.txt');
    if (p) API.create(p, 'file').then(reloadTree).catch(()=>toast('Не создать', true));
  });
  document.getElementById('btnNewDir').addEventListener('click', () => {
    const p = prompt('Путь новой папки:', 'folder');
    if (p) API.create(p, 'dir').then(reloadTree).catch(()=>toast('Не создать', true));
  });

  // ───── Monaco bootstrap ─────
  require.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' } });
  require(['vs/editor/editor.main'], function () {
    editor = monaco.editor.create(document.getElementById('monaco'), {
      value: '', language: 'plaintext', theme: 'vs-dark',
      automaticLayout: true, fontSize: 13, minimap: { enabled: true },
      scrollBeyondLastLine: false, tabSize: 2,
    });
    editor.onDidChangeModelContent(() => { if (current && !dirty) setDirty(true); });
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, saveFile);
    reloadTree();
  });

  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveFile(); }
  });
  window.addEventListener('beforeunload', (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ''; }
  });
})();
