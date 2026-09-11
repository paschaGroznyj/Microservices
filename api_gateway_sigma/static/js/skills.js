(function () {
  function esc(s){return (s||'').replace(/[&<>\"']/g, (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'}[m]));}

  const SkillsPanel = {
    app: null,
    inited: false,
    init(app){
      this.app = app;
      if (this.inited) return;
      this.inited = true;
      document.getElementById('skills-refresh')?.addEventListener('click', () => this.load());
      document.getElementById('skills-sort')?.addEventListener('change', () => this.load());
      document.getElementById('skills-create-btn')?.addEventListener('click', () => this.create());
      this._bindDropToInput();
    },
    open(app){ this.init(app); this.load(); },
    async load(){
      const q = document.getElementById('skills-query')?.value || '';
      const sort = document.getElementById('skills-sort')?.value || 'new';
      const res = await fetch(`/api/skills?query=${encodeURIComponent(q)}&sort_by=${encodeURIComponent(sort)}`, {credentials:'include'});
      const data = res.ok ? await res.json() : {skills:[]};
      this.render(data.skills || []);
    },
    render(skills){
      const list = document.getElementById('skills-list');
      if (!list) return;
      if (!skills.length){ list.innerHTML = '<div class="chat-item-skeleton">Скиллы не найдены</div>'; return; }
      list.innerHTML = skills.map(s => `
        <div class="skill-card" draggable="true" data-skill-body="${esc(s.body)}">
          <div class="skill-head"><span class="skill-theme">${esc(s.theme)}</span><span>❤️ ${s.likes_count || 0}</span></div>
          <div class="skill-body">${esc((s.body||'').slice(0,700))}${(s.body||'').length>700?'...':''}</div>
          <div class="skill-tools">Инструменты: ${esc((s.tools||[]).join(', ') || '—')}</div>
          <div class="skill-actions">
            <button class="skills-btn" data-like="${s.id}">Лайк</button>
            <button class="skills-btn" data-copy="${s.id}">Копировать в чат</button>
          </div>
        </div>
      `).join('');
      list.querySelectorAll('[data-like]').forEach(b=>b.addEventListener('click',()=>this.like(Number(b.dataset.like))));
      list.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>this.copyToChat(Number(b.dataset.copy))));
      list.querySelectorAll('.skill-card').forEach(card=>{
        card.addEventListener('dragstart', (e)=>{
          e.dataTransfer.setData('text/plain', card.dataset.skillBody || '');
          e.dataTransfer.effectAllowed = 'copy';
        });
      });
    },
    async create(){
      const theme = document.getElementById('skills-theme')?.value || '';
      const body = document.getElementById('skills-body')?.value || '';
      const toolsRaw = document.getElementById('skills-tools')?.value || '';
      const tools = toolsRaw.split(',').map(s=>s.trim()).filter(Boolean);
      const res = await fetch('/api/skills', {
        method:'POST', credentials:'include', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({theme, body, tools})
      });
      if (!res.ok) {
        this.app?.showToast?.('Не удалось создать скилл');
        return;
      }
      document.getElementById('skills-theme').value='';
      document.getElementById('skills-body').value='';
      document.getElementById('skills-tools').value='';
      this.app?.showToast?.('Скилл опубликован');
      this.load();
    },
    async like(skillId){
      const userId = this.app?.currentUser?.user_id;
      if (!userId) { this.app?.showToast?.('Не найден user_id'); return; }
      const res = await fetch(`/api/skills/${skillId}/like`, {method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({user_id: userId})});
      if (!res.ok) this.app?.showToast?.('Не удалось поставить лайк');
      this.load();
    },
    async copyToChat(skillId){
      const res = await fetch(`/api/skills/${skillId}/prompt`, {credentials:'include'});
      if (!res.ok) return;
      const data = await res.json();
      const txt = data.prompt || '';
      this.app?.switchTab?.('agent');
      await this.app?.renderMessageInput?.();
      this._appendToInput(txt);
      this.app?.showToast?.('Скилл скопирован в чат');
    },
    _appendToInput(text){
      const field = document.getElementById('message-input');
      if (!field) return;
      field.focus();
      if (field.isContentEditable) {
        document.execCommand('insertText', false, text);
      } else {
        field.value = (field.value || '') + text;
      }
      field.dispatchEvent(new Event('input', {bubbles:true}));
    },
    _bindDropToInput(){
      document.addEventListener('dragover', (e)=>{
        if (e.target.closest && e.target.closest('#message-input')) e.preventDefault();
      });
      document.addEventListener('drop', (e)=>{
        const input = e.target.closest && e.target.closest('#message-input');
        if (!input) return;
        const txt = e.dataTransfer?.getData('text/plain') || '';
        if (!txt) return;
        e.preventDefault();
        this._appendToInput(txt);
      });
    }
  };

  window.SkillsPanel = SkillsPanel;
})();
