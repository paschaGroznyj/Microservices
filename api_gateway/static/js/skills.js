(function () {
  function esc(s){return (s||'').replace(/[&<>"']/g, (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}

  // SVG-иконки
  const ICON_HEART_OUTLINE = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>';
  const ICON_HEART_FILLED  = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 21l-1.45-1.32C5.4 14.99 2 11.9 2 8.1 2 5.42 4.08 3.3 6.75 3.3c1.54 0 3.02.72 4 1.86A5.28 5.28 0 0 1 17.25 3.3C19.92 3.3 22 5.42 22 8.1c0 3.8-3.4 6.89-8.55 11.58L12 21z"/></svg>';
  const ICON_CHEVRON       = '<svg class="chevron" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="m6 9 6 6 6-6"/></svg>';

  const SkillsPanel = {
    app: null,
    inited: false,
    _debounce: null,
    _likedIds: new Set(),
    init(app){
      this.app = app;
      if (this.inited) return;
      this.inited = true;
      document.getElementById('skills-refresh')?.addEventListener('click', () => this.load());
      document.getElementById('skills-sort')?.addEventListener('change', () => this.load());
      const q = document.getElementById('skills-query');
      q?.addEventListener('input', () => {
        clearTimeout(this._debounce);
        this._debounce = setTimeout(() => this.load(), 250);
      });
      this._bindDropToInput();
      this._bindDropToChats();
    },
    open(app){ this.init(app); this.load(); },
    async load(){
      const q = document.getElementById('skills-query')?.value || '';
      const sort = document.getElementById('skills-sort')?.value || 'new';
      let skills = [];
      try {
        const res = await fetch(`/api/skills?query=${encodeURIComponent(q)}&sort_by=${encodeURIComponent(sort)}`, {credentials:'include'});
        const data = res.ok ? await res.json() : {skills:[]};
        skills = data.skills || [];
      } catch (e) { skills = []; }
      this.render(skills);
    },
    render(skills){
      const list = document.getElementById('skills-list');
      const countEl = document.getElementById('skills-count');
      if (!list) return;

      if (countEl) countEl.textContent = skills.length ? `${skills.length}` : '';

      if (!skills.length){
        list.classList.remove('skills-list-grid');
        list.innerHTML = `
          <div class="skills-empty">
            <svg viewBox="0 0 24 24" width="48" height="48" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" d="M4 7h16M4 12h16M4 17h10"/></svg>
            <div class="skills-empty__title">Пока пусто</div>
            <div class="skills-empty__hint">Скиллы публикует агент в ходе работы. Как только появятся — они отобразятся здесь карточками.</div>
          </div>`;
        return;
      }

      list.innerHTML = skills.map(s => {
        const liked = this._likedIds.has(s.id);
        const model = s.model_used ? `<span class="skill-badge skill-badge--model">${esc(s.model_used)}</span>` : '';
        const tools = (s.tools||[]).length
          ? `<span class="skill-badge">${esc((s.tools||[]).join(', '))}</span>`
          : '';
        const authorName = s.published_by_name || '';
        const author = authorName
          ? `<div class="skill-author"><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"/></svg><span>${esc(authorName)}</span></div>`
          : '';
        return `
        <div class="skill-card" draggable="true" data-skill-id="${s.id}" data-skill-body="${esc(s.body)}">
          <div class="skill-card__head">
            <span class="skill-theme">${esc(s.theme)}</span>
            <button class="skill-like${liked?' is-liked':''}" data-like="${s.id}" title="Лайк">
              ${liked ? ICON_HEART_FILLED : ICON_HEART_OUTLINE}
              <span class="skill-like-count">${s.likes_count || 0}</span>
            </button>
          </div>
          ${author}
          <div class="skill-meta">${model}${tools}</div>
          <button class="skill-toggle" data-toggle="${s.id}">${ICON_CHEVRON}<span class="skill-toggle-label">Читать полностью</span></button>
          <div class="skill-body">${esc(s.body||'')}</div>
          <div class="skill-actions">
            <button class="skills-btn" data-copy="${s.id}">Скопировать в чат</button>
          </div>
        </div>`;
      }).join('');

      list.querySelectorAll('[data-toggle]').forEach(b=>b.addEventListener('click',()=>{
        const card = b.closest('.skill-card');
        if (!card) return;
        const open = card.classList.toggle('is-open');
        const label = b.querySelector('.skill-toggle-label');
        if (label) label.textContent = open ? 'Свернуть' : 'Читать полностью';
      }));
      list.querySelectorAll('[data-like]').forEach(b=>b.addEventListener('click',()=>this.like(Number(b.dataset.like))));
      list.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>this.copyToChat(Number(b.dataset.copy))));
      list.querySelectorAll('.skill-card').forEach(card=>{
        card.addEventListener('dragstart', (e)=>{
          const body = card.dataset.skillBody || '';
          this._dragBody = body;
          e.dataTransfer.setData('text/plain', body);
          e.dataTransfer.effectAllowed = 'copy';
        });
        card.addEventListener('dragend', ()=>{ this._dragBody = ''; });
      });
    },
    async like(skillId){
      const userId = this.app?.currentUser?.user_id;
      if (!userId) { this.app?.showToast?.('Не найден user_id'); return; }
      try {
        const res = await fetch(`/api/skills/${skillId}/like`, {method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body: JSON.stringify({user_id: userId})});
        if (!res.ok) { this.app?.showToast?.('Не удалось поставить лайк'); return; }
        const data = await res.json().catch(()=>({}));
        if (data.liked) this._likedIds.add(skillId); else this._likedIds.delete(skillId);
      } catch (e) { /* ignore */ }
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
    },
    _bindDropToChats(){
      // Перетаскивание карточки скилла на конкретный чат в сайдбаре:
      // чат открывается и текст скилла вставляется в поле ввода.
      const onDragOver = (e)=>{
        const item = e.target.closest && e.target.closest('.chat-item');
        if (!item) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
        item.classList.add('chat-item--drop');
      };
      const onDragLeave = (e)=>{
        const item = e.target.closest && e.target.closest('.chat-item');
        if (item) item.classList.remove('chat-item--drop');
      };
      const onDrop = async (e)=>{
        const item = e.target.closest && e.target.closest('.chat-item');
        if (!item) return;
        e.preventDefault();
        item.classList.remove('chat-item--drop');
        const txt = (e.dataTransfer && e.dataTransfer.getData('text/plain')) || this._dragBody || '';
        if (!txt) return;
        const chatId = Number(item.dataset.chatId);
        if (Number.isFinite(chatId)) {
          await this.app?.openChat?.(chatId);
        }
        this.app?.switchTab?.('agent');
        await this.app?.renderMessageInput?.();
        this._appendToInput(txt);
        this.app?.showToast?.('Скилл добавлен в чат');
      };
      ['chat-list','chat-favorites-list'].forEach(id=>{
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('dragover', onDragOver);
        el.addEventListener('dragleave', onDragLeave);
        el.addEventListener('drop', onDrop);
      });
    },

  };

  window.SkillsPanel = SkillsPanel;
})();
