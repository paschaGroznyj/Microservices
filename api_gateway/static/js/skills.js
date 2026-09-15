(function () {
  function esc(s){return (s||'').replace(/[&<>"']/g, (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}

  // SVG-иконки
  const ICON_HEART_OUTLINE = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>';
  const ICON_HEART_FILLED  = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12 21l-1.45-1.32C5.4 14.99 2 11.9 2 8.1 2 5.42 4.08 3.3 6.75 3.3c1.54 0 3.02.72 4 1.86A5.28 5.28 0 0 1 17.25 3.3C19.92 3.3 22 5.42 22 8.1c0 3.8-3.4 6.89-8.55 11.58L12 21z"/></svg>';
  const ICON_CHEVRON       = '<svg class="chevron" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="m6 9 6 6 6-6"/></svg>';
  const ICON_COPY           = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 8h10v12H8z"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M6 16H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const ICON_TRASH          = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg>';
  const ICON_EDIT           = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>';

  const SkillsPanel = {
    app: null,
    inited: false,
    _debounce: null,
    _likedIds: new Set(),
    _isAdmin: false,
    _adminLoaded: false,
    _selectedIds: new Set(),
    _selectedLimit: 5,
    _sort: 'new',
    _filter: 'all',
    _allSkills: [],
    init(app){
      this.app = app;
      if (this.inited) return;
      this.inited = true;

      // Поиск: лупа раскрывает строку поиска (как у Claude)
      const searchToggle = document.getElementById('skills-search-toggle');
      const searchRow = document.getElementById('skills-search-row');
      const toolbar = document.getElementById('skills-toolbar');
      const q = document.getElementById('skills-query');
      const openSearch = () => {
        this._closeAllMenus();
        toolbar?.classList.add('searching');
        searchRow?.removeAttribute('hidden');
        searchToggle?.setAttribute('aria-expanded','true');
        q?.focus();
      };
      const closeSearch = () => {
        toolbar?.classList.remove('searching');
        searchRow?.setAttribute('hidden','');
        searchToggle?.setAttribute('aria-expanded','false');
        if (q) q.value='';
        this.load();
      };
      searchToggle?.addEventListener('click', () => {
        if (searchRow?.hasAttribute('hidden')) openSearch(); else closeSearch();
      });
      // Крестик внутри поля — выход из режима поиска.
      document.getElementById('skills-search-clear')?.addEventListener('click', () => closeSearch());
      q?.addEventListener('input', () => {
        clearTimeout(this._debounce);
        this._debounce = setTimeout(() => this.load(), 250);
      });
      q?.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') { closeSearch(); }
      });

      // Dropdown-меню фильтра и сортировки
      this._bindMenu('skills-filter-btn', 'skills-filter-menu', 'filter', (val) => { this._filter = val; this._applyFilterSort(); });
      this._bindMenu('skills-sort-btn', 'skills-sort-menu', 'sort', (val) => { this._sort = val; this.load(); });

      // Закрытие меню по клику вне
      document.addEventListener('click', (e) => {
        if (!e.target.closest('.skills-menu-wrap')) this._closeAllMenus();
      });

      // Add — только админ (кнопка показывается в load после _fetchIsAdmin)
      document.getElementById('skills-add-btn')?.addEventListener('click', () => this.openCreate?.());

      this._bindDropToInput();
      this._bindDropToChats();
      this._loadSelected().then(() => this._updateSelectedCounterUi()).catch(() => {});
    },
    _bindMenu(btnId, menuId, dataAttr, onPick){
      const btn = document.getElementById(btnId);
      const menu = document.getElementById(menuId);
      if (!btn || !menu) return;
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = menu.hasAttribute('hidden');
        this._closeAllMenus();
        if (willOpen){ menu.removeAttribute('hidden'); btn.setAttribute('aria-expanded','true'); }
      });
      menu.querySelectorAll('.skills-menu-item').forEach(item => {
        item.addEventListener('click', () => {
          const val = item.dataset[dataAttr];
          menu.querySelectorAll('.skills-menu-item').forEach(i => i.setAttribute('aria-checked', String(i===item)));
          this._closeAllMenus();
          // подсветка активности кнопки (отличной от дефолта)
          const isDefault = (dataAttr==='sort' && val==='new') || (dataAttr==='filter' && val==='all');
          btn.classList.toggle('is-active', !isDefault);
          onPick(val);
        });
      });
    },
    _closeAllMenus(){
      document.querySelectorAll('.skills-menu').forEach(m => m.setAttribute('hidden',''));
      document.querySelectorAll('#skills-filter-btn, #skills-sort-btn').forEach(b => b.setAttribute('aria-expanded','false'));
    },
    open(app){ this.init(app); this.load(); },
    async load(){
      const q = document.getElementById('skills-query')?.value || '';
      const sort = this._sort || 'new';
      let skills = [];
      await this._loadSelected();
      try {
        const res = await fetch(`/api/skills?query=${encodeURIComponent(q)}&sort_by=${encodeURIComponent(sort)}`, {credentials:'include'});
        const data = res.ok ? await res.json() : {skills:[]};
        skills = data.skills || [];
      } catch (e) { skills = []; }
      await this._fetchIsAdmin();
      this._allSkills = skills;
      // Кнопка Add: скиллы публикует агент, создания через UI нет — пока скрыта.
      const addBtn = document.getElementById('skills-add-btn');
      if (addBtn) addBtn.hidden = true;
      this._applyFilterSort();
    },
    _applyFilterSort(){
      let list = (this._allSkills || []).slice();
      if (this._filter === 'liked') list = list.filter(s => this._likedIds.has(s.id));
      else if (this._filter === 'selected') list = list.filter(s => this._selectedIds.has(s.id));
      this.render(list);
      this._updateSelectedCounterUi(list.length);
    },
    render(skills){
      const list = document.getElementById('skills-list');
      const countEl = document.getElementById('skills-count');
      if (!list) return;

      if (countEl) countEl.textContent = `${skills.length} • выбрано ${this._selectedIds.size}/${this._selectedLimit}`;

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
        const selected = this._selectedIds.has(Number(s.id));
        const model = s.model_used ? `<span class="skill-badge skill-badge--model">${esc(s.model_used)}</span>` : '';
        const tools = (s.tools||[]).length
          ? `<span class="skill-badge skill-badge--tools" title="${esc((s.tools||[]).join(', '))}">${esc((s.tools||[]).join(', '))}</span>`
          : '';
        const authorName = s.published_by_name || '';
        const canEditOwn = Number(s.published_by_user_id || 0) === Number(this.app?.currentUser?.user_id || 0);
        const author = authorName
          ? `<div class="skill-author"><svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"/></svg><span>${esc(authorName)}</span></div>`
          : '';
        return `
        <div class="skill-card${selected?' is-selected':''}" draggable="true" data-skill-id="${s.id}" data-skill-body="${esc(s.body)}" data-skill-body-raw="${encodeURIComponent(s.body||'')}" data-skill-theme="${esc(s.theme||'')}" data-skill-tools="${esc((s.tools||[]).join(', '))}" data-skill-tools-csv="${encodeURIComponent((s.tools||[]).join(', '))}">
          <div class="skill-card__head">
            <span class="skill-theme">${esc(s.theme)}</span>
            <div class="skill-head-actions">
              <button class="skill-like${liked?' is-liked':''}" data-like="${s.id}" title="Лайк">
                ${liked ? ICON_HEART_FILLED : ICON_HEART_OUTLINE}
                <span class="skill-like-count">${s.likes_count || 0}</span>
              </button>
              <button class="skill-icon-btn skill-icon-btn--copy" data-copy="${s.id}" title="Скопировать в чат" aria-label="Скопировать в чат">
                ${ICON_COPY}
              </button>
              ${canEditOwn ? `<button class="skill-icon-btn skill-icon-btn--edit" data-edit="${s.id}" title="Редактировать скилл" aria-label="Редактировать скилл">
                ${ICON_EDIT}
              </button>` : ''}
              ${this._isAdmin ? `<button class="skill-icon-btn skill-icon-btn--delete" data-delete="${s.id}" title="Удалить скилл" aria-label="Удалить скилл">
                ${ICON_TRASH}
              </button>` : ''}
            </div>
          </div>
          <div class="skill-meta">${author}${model}</div>
          <button class="skill-toggle" data-toggle="${s.id}">${ICON_CHEVRON}<span class="skill-toggle-label">Читать полностью</span></button>
          <div class="skill-reveal">
            ${tools ? `<div class="skill-tools-row">${tools}</div>` : ''}
            <div class="skill-body">${esc(s.body||'')}</div>
          </div>
</div>`;
      }).join('');

      list.querySelectorAll('[data-toggle]').forEach(b=>b.addEventListener('click',()=>{
        const card = b.closest('.skill-card');
        if (!card) return;
        const open = card.classList.toggle('is-open');
        const label = b.querySelector('.skill-toggle-label');
        if (label) label.textContent = open ? 'Свернуть' : 'Читать полностью';
        if (open) this._renderBodyMd(card);
      }));
      list.querySelectorAll('[data-like]').forEach(b=>b.addEventListener('click',()=>this.like(Number(b.dataset.like))));
      list.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>this.copyToChat(Number(b.dataset.copy))));
      list.querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>this.openEditSkill(Number(b.dataset.edit))));
      list.querySelectorAll('[data-delete]').forEach(b=>b.addEventListener('click',()=>this.deleteSkill(Number(b.dataset.delete))));
      list.querySelectorAll('.skill-card').forEach(card=>{
        card.addEventListener('click', (e) => {
          const interactive = e.target.closest('[data-like],[data-copy],[data-edit],[data-delete],[data-toggle],a,button,input,textarea,select');
          if (interactive) return;
          const id = Number(card.dataset.skillId);
          if (id) this.toggleSelectedSkill(id);
        });
        card.addEventListener('dragstart', (e)=>{
          const txt = this._buildDragText(card);
          this._dragBody = txt;
          e.dataTransfer.setData('text/plain', txt);
          e.dataTransfer.effectAllowed = 'copy';
        });
        card.addEventListener('dragend', ()=>{ this._dragBody = ''; });
      });
    },
    _buildDragText(card){
      const theme = card.dataset.skillTheme || '';
      const tools = card.dataset.skillTools || '';
      const body  = card.dataset.skillBody || '';
      let out = '';
      if (theme) out += `Тема скилла: ${theme}
`;
      out += `Рекомендуемые инструменты: ${tools || 'не указаны'}

`;
      out += `Тело скилла:
${body}`;
      return out;
    },
    _renderBodyMd(card){
      const el = card.querySelector('.skill-body');
      if (!el || el.dataset.mdDone === '1') return;
      const raw = card.dataset.skillBody || '';
      try {
        if (typeof marked !== 'undefined') {
          let html = marked.parse(raw);
          if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);
          el.innerHTML = html;
          el.classList.add('md-content');
          el.dataset.mdDone = '1';
        }
      } catch (e) { /* fallback: остаётся предрендеренный esc-текст */ }
    },
    async _fetchIsAdmin(force = false){
      if (!force && this._adminLoaded) return this._isAdmin;
      try {
        const res = await fetch('/api/auth/permissions', { credentials:'include' });
        if (!res.ok) throw new Error('status ' + res.status);
        const data = await res.json();
        this._isAdmin = !!data?.is_admin;
      } catch (e) {
        this._isAdmin = false;
      } finally {
        this._adminLoaded = true;
      }
      return this._isAdmin;
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
    async openEditSkill(skillId){
      const card = document.querySelector(`.skill-card[data-skill-id="${skillId}"]`);
      if (!card) return;

      const initialTheme = card.dataset.skillTheme || '';
      const initialBody = decodeURIComponent(card.dataset.skillBodyRaw || '');
      const initialTools = decodeURIComponent(card.dataset.skillToolsCsv || '');

      const result = await this._openEditModal({
        theme: initialTheme,
        body: initialBody,
        tools: initialTools,
      });
      if (!result) return;

      try {
        const res = await fetch(`/api/skills/${skillId}`, {
          method: 'PUT',
          credentials: 'include',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify(result),
        });
        if (res.status === 403) {
          this.app?.showToast?.('Редактировать можно только свои скиллы');
          return;
        }
        if (res.status === 404) {
          this.app?.showToast?.('Скилл не найден');
          return;
        }
        if (!res.ok) {
          this.app?.showToast?.('Не удалось обновить скилл');
          return;
        }
        this.app?.showToast?.('Скилл обновлён');
        await this.load();
      } catch (e) {
        this.app?.showToast?.('Ошибка обновления скилла');
      }
    },
    _openEditModal(initial){
      return new Promise((resolve) => {
        const prev = document.getElementById('skill-edit-overlay');
        if (prev) prev.remove();

        const overlay = document.createElement('div');
        overlay.className = 'skill-confirm-overlay';
        overlay.id = 'skill-edit-overlay';
        overlay.innerHTML = `
          <div class="skill-confirm skill-edit" role="dialog" aria-modal="true" aria-labelledby="skill-edit-title">
            <div class="skill-confirm__title" id="skill-edit-title">Редактирование скилла</div>
            <div class="skill-edit__form">
              <input id="skill-edit-theme" class="skill-edit__input" type="text" maxlength="200" placeholder="Тема" value="${esc(initial.theme || '')}">
              <input id="skill-edit-tools" class="skill-edit__input" type="text" maxlength="1000" placeholder="Инструменты через запятую" value="${esc(initial.tools || '')}">
              <textarea id="skill-edit-body" class="skill-edit__textarea" maxlength="10000" placeholder="Тело скилла в Markdown">${esc(initial.body || '')}</textarea>
            </div>
            <div class="skill-confirm__actions">
              <button type="button" class="skill-confirm__btn skill-confirm__btn--cancel" data-act="cancel">Отмена</button>
              <button type="button" class="skill-confirm__btn skill-confirm__btn--danger" data-act="ok">Сохранить</button>
            </div>
          </div>`;

        const close = (val) => {
          overlay.classList.add('closing');
          document.removeEventListener('keydown', onKey);
          setTimeout(() => { overlay.remove(); resolve(val); }, 160);
        };
        const onKey = (e) => {
          if (e.key === 'Escape') close(null);
        };

        overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(null); });
        overlay.addEventListener('click', (e) => {
          const act = e.target.closest('[data-act]')?.dataset?.act;
          if (act === 'cancel') return close(null);
          if (act === 'ok') {
            const theme = overlay.querySelector('#skill-edit-theme')?.value?.trim() || '';
            const toolsCsv = overlay.querySelector('#skill-edit-tools')?.value || '';
            const body = overlay.querySelector('#skill-edit-body')?.value?.trim() || '';
            if (!theme || !body) {
              this.app?.showToast?.('Тема и тело скилла обязательны');
              return;
            }
            const tools = toolsCsv.split(',').map(x => x.trim()).filter(Boolean);
            return close({ theme, body, tools });
          }
        });
        document.addEventListener('keydown', onKey);

        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('active'));
        overlay.querySelector('#skill-edit-theme')?.focus();
      });
    },
    _confirmDelete(skillId){
      // Красивая модалка подтверждения (вместо window.confirm).
      return new Promise((resolve) => {
        const card = document.querySelector(`.skill-card[data-skill-id="${skillId}"]`);
        const theme = card?.dataset?.skillTheme || 'этот скилл';
        const prev = document.getElementById('skill-confirm-overlay');
        if (prev) prev.remove();

        const overlay = document.createElement('div');
        overlay.className = 'skill-confirm-overlay';
        overlay.id = 'skill-confirm-overlay';
        overlay.innerHTML = `
          <div class="skill-confirm" role="dialog" aria-modal="true" aria-labelledby="skill-confirm-title">
            <div class="skill-confirm__icon">
              <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg>
            </div>
            <div class="skill-confirm__title" id="skill-confirm-title">Удалить скилл?</div>
            <div class="skill-confirm__text">“${esc(theme)}” больше не будет показываться в магазине. Сама запись сохраняется в базе.</div>
            <div class="skill-confirm__actions">
              <button type="button" class="skill-confirm__btn skill-confirm__btn--cancel" data-act="cancel">Отмена</button>
              <button type="button" class="skill-confirm__btn skill-confirm__btn--danger" data-act="ok">Удалить</button>
            </div>
          </div>`;

        const close = (val) => {
          overlay.classList.add('closing');
          document.removeEventListener('keydown', onKey);
          setTimeout(() => { overlay.remove(); resolve(val); }, 160);
        };
        const onKey = (e) => {
          if (e.key === 'Escape') close(false);
          else if (e.key === 'Enter') close(true);
        };

        overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(false); });
        overlay.addEventListener('click', (e) => {
          const act = e.target.closest('[data-act]')?.dataset?.act;
          if (act === 'ok') close(true);
          else if (act === 'cancel') close(false);
        });
        document.addEventListener('keydown', onKey);

        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('active'));
        overlay.querySelector('.skill-confirm__btn--danger')?.focus();
      });
    },
    async deleteSkill(skillId){
      const ok = await this._confirmDelete(skillId);
      if (!ok) return;
      try {
        const res = await fetch(`/api/skills/${skillId}`, { method: 'DELETE', credentials:'include' });
        if (res.status === 403) {
          this.app?.showToast?.('Удалять скиллы может только администратор');
          return;
        }
        if (res.status === 404) {
          this.app?.showToast?.('Скилл не найден');
          return;
        }
        if (!res.ok) {
          this.app?.showToast?.('Не удалось удалить скилл');
          return;
        }
        this.app?.showToast?.('Скилл удалён');
        await this.load();
      } catch (e) {
        this.app?.showToast?.('Ошибка удаления скилла');
      }
    },
    async _loadSelected(){
      try {
        const res = await fetch('/api/skills/selected', { credentials:'include' });
        if (!res.ok) return;
        const data = await res.json();
        const ids = Array.isArray(data?.selected_ids) ? data.selected_ids : [];
        this._selectedIds = new Set(ids.map(Number));
      } catch (_) {}
    },
    _updateSelectedCounterUi(totalSkills){
      const nav = document.getElementById('skills-selected-nav-count');
      if (nav) nav.textContent = String(this._selectedIds.size || 0);
      const countEl = document.getElementById('skills-count');
      if (countEl) countEl.textContent = `${totalSkills || 0} • выбрано ${this._selectedIds.size}/${this._selectedLimit}`;
    },
    async toggleSelectedSkill(skillId){
      try {
        const res = await fetch(`/api/skills/${skillId}/select`, {
          method:'POST',
          credentials:'include',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({}),
        });

        if (res.status === 400) {
          this.app?.showToast?.('Можно выбрать не более 5 скиллов');
          await this._loadSelected();
          this._updateSelectedCounterUi(document.querySelectorAll('.skill-card').length);
          this.render(await this._readCurrentSkills());
          return;
        }
        if (res.status === 404) {
          this.app?.showToast?.('Скилл не найден');
          return;
        }
        if (!res.ok) {
          this.app?.showToast?.('Не удалось изменить выбор скилла');
          return;
        }

        const data = await res.json().catch(()=>({}));
        const ids = Array.isArray(data?.selected_ids) ? data.selected_ids : [];
        this._selectedIds = new Set(ids.map(Number));
        await this.load();
      } catch (e) {
        this.app?.showToast?.('Ошибка выбора скилла');
      }
    },
    async _readCurrentSkills(){
      const q = document.getElementById('skills-query')?.value || '';
      const sort = document.getElementById('skills-sort')?.value || 'new';
      try {
        const res = await fetch(`/api/skills?query=${encodeURIComponent(q)}&sort_by=${encodeURIComponent(sort)}`, {credentials:'include'});
        const data = res.ok ? await res.json() : {skills:[]};
        return data.skills || [];
      } catch (_) {
        return [];
      }
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
