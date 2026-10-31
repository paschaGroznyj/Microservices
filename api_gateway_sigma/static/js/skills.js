(function () {
  function esc(s){return String(s||'').replace(/[&<>"']/g, (m) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}

  const ICON_HEART_OUTLINE = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.8 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>';
  const ICON_HEART_FILLED  = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="#e0245e" d="M12 21l-1.45-1.32C5.4 14.99 2 11.9 2 8.1 2 5.42 4.08 3.3 6.75 3.3c1.54 0 3.02.72 4 1.86A5.28 5.28 0 0 1 17.25 3.3C19.92 3.3 22 5.42 22 8.1c0 3.8-3.4 6.89-8.55 11.58L12 21z"/></svg>';
  const ICON_COPY          = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 8h10v12H8z"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M6 16H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const ICON_TRASH         = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg>';
  const ICON_EDIT          = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>';
  const ICON_DOTS          = '<svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14" aria-hidden="true"><circle cx="10" cy="4" r="1.5"/><circle cx="10" cy="10" r="1.5"/><circle cx="10" cy="16" r="1.5"/></svg>';
  const ICON_CHECK         = '<svg viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="M2.2 6.2L4.8 8.8L9.8 3.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_SKILL_DOC     = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true"><path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7l-5-5Z" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 2v5h5M9 13h6M9 17h4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_USER          = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_BACK_ARROW    = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true" style="transform:rotate(180deg);"><path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function formatSkillDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '—';
    const months = ['января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'];
    const nowYear = new Date().getFullYear();
    const yr = d.getFullYear();
    return yr === nowYear
      ? `${d.getDate()} ${months[d.getMonth()]}`
      : `${d.getDate()} ${months[d.getMonth()]} ${yr}`;
  }

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
    _activeSkillId: null,
    _activeSkillData: null,
    _isEditingDetail: false,
    _detailTab: 'overview',
    _openDropdown: null,

    init(app){
      this.app = app;
      if (this.inited) return;
      this.inited = true;

      const searchToggle = document.getElementById('skills-search-toggle');
      const searchClear = document.getElementById('skills-search-clear');
      const toolbar = document.getElementById('skills-toolbar');
      const q = document.getElementById('skills-query');
      const openSearch = () => {
        this._closeAllMenus();
        toolbar?.classList.add('searching');
        searchClear?.removeAttribute('hidden');
        searchToggle?.setAttribute('aria-expanded','true');
        if (q) {
          q.placeholder = 'Поиск по теме или содержанию…';
          q.focus();
        }
      };
      const closeSearch = () => {
        const hadValue = !!(q && q.value);
        toolbar?.classList.remove('searching');
        searchClear?.setAttribute('hidden','');
        searchToggle?.setAttribute('aria-expanded','false');
        if (q) {
          q.value = '';
          q.placeholder = 'Поиск…';
          q.blur();
        }
        if (hadValue) this.load();
      };
      q?.addEventListener('focus', () => openSearch());
      q?.addEventListener('click', () => openSearch());
      searchToggle?.addEventListener('click', () => {
        if (!toolbar?.classList.contains('searching')) {
          openSearch();
        } else {
          q?.focus();
          this.load();
        }
      });
      searchClear?.addEventListener('click', (e) => {
        e.stopPropagation();
        closeSearch();
      });
      q?.addEventListener('input', () => {
        clearTimeout(this._debounce);
        this._debounce = setTimeout(() => this.load(), 250);
      });
      q?.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeSearch();
      });

      this._bindMenu('skills-filter-btn', 'skills-filter-menu', 'filter', (val) => { this._filter = val; this._applyFilterSort(); });
      this._bindMenu('skills-sort-btn', 'skills-sort-menu', 'sort', (val) => { this._sort = val; this.load(); });

      document.addEventListener('click', (e) => {
        if (!e.target.closest('.skills-menu-wrap')) this._closeAllMenus();
        if (!e.target.closest('.skill-menu-btn') && !e.target.closest('.chat-item-dropdown')) {
          this._closeActionDropdown();
        }
      });

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

    _closeActionDropdown(){
      if (this._openDropdown) {
        this._openDropdown.remove();
        this._openDropdown = null;
      }
      document.querySelectorAll('.skill-menu-btn[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    },

    open(app){
      this.init(app);
      this.closeSkillDetail(false);
      this.load();
    },

    async load(){
      const q = document.getElementById('skills-query')?.value || '';
      const sort = this._sort || 'new';
      let skills = [];
      await Promise.all([this._loadSelected(), this._loadLiked()]);
      try {
        const res = await fetch(`/api/skills?query=${encodeURIComponent(q)}&sort_by=${encodeURIComponent(sort)}`, {credentials:'include'});
        const data = res.ok ? await res.json() : {skills:[]};
        skills = data.skills || [];
      } catch (e) { skills = []; }
      await this._fetchIsAdmin();
      this._allSkills = skills;
      if (!q.trim()) {
        const aliveIds = new Set(skills.map(s => Number(s.id)));
        for (const selId of Array.from(this._selectedIds)) {
          if (!aliveIds.has(Number(selId))) {
            this._selectedIds.delete(Number(selId));
          }
        }
      }
      const addBtn = document.getElementById('skills-add-btn');
      if (addBtn) addBtn.hidden = true;
      this._applyFilterSort();
    },

    _applyFilterSort(){
      let list = (this._allSkills || []).slice();
      if (this._filter === 'liked') list = list.filter(s => this._likedIds.has(Number(s.id)));
      else if (this._filter === 'selected') list = list.filter(s => this._selectedIds.has(Number(s.id)));

      if (this._sort === 'popular') {
        list.sort((a, b) => (Number(b.uses_count || 0) - Number(a.uses_count || 0)) || (Number(b.likes_count || 0) - Number(a.likes_count || 0)));
      } else if (this._sort === 'liked') {
        list.sort((a, b) => (Number(b.likes_count || 0) - Number(a.likes_count || 0)) || (Number(b.uses_count || 0) - Number(a.uses_count || 0)));
      }

      this.render(list);
      this._updateSelectedCounterUi(list.length);
    },

    _ensureDetailMount(){
      let detail = document.getElementById('skill-detail-view');
      if (!detail) {
        const host = document.getElementById('skills-view');
        if (host) {
          detail = document.createElement('div');
          detail.className = 'skill-detail';
          detail.id = 'skill-detail-view';
          detail.hidden = true;
          host.appendChild(detail);
        }
      }
      return detail;
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
            <div class="skills-empty__hint">Скиллы публикует агент в ходе работы. Как только появятся — они отобразятся здесь списком.</div>
          </div>`;
        return;
      }

      list.innerHTML = skills.map(s => {
        const sid = Number(s.id);
        const selected = this._selectedIds.has(sid);
        return `
        <div class="skill-card" draggable="true" data-skill-id="${sid}" data-skill-theme="${esc(s.theme||'')}">
          <div class="skill-card__left">
            <label class="skill-select-label" title="Выбрать скилл для чата">
              <input type="checkbox" class="skill-select-cb" data-select-cb="${sid}" ${selected ? 'checked' : ''} />
              <span class="skill-select-box" aria-hidden="true">${ICON_CHECK}</span>
            </label>
            <span class="skill-theme" title="${esc(s.theme)}">${esc(s.theme)}</span>
          </div>
          <div class="skill-card__right">
            <button class="chat-item-menu-btn skill-menu-btn" type="button" data-skill-menu="${sid}" title="Действия" aria-label="Действия" aria-expanded="false">
              ${ICON_DOTS}
            </button>
          </div>
        </div>`;
      }).join('');

      list.querySelectorAll('.skill-select-label').forEach(lbl => {
        lbl.addEventListener('click', (e) => e.stopPropagation());
      });

      list.querySelectorAll('[data-select-cb]').forEach(cb => {
        cb.addEventListener('change', (e) => {
          e.stopPropagation();
          const id = Number(cb.dataset.selectCb);
          if (id) this.toggleSelectedSkill(id, cb.checked, cb);
        });
      });

      list.querySelectorAll('[data-skill-menu]').forEach(btn => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          const id = Number(btn.dataset.skillMenu);
          const summaryObj = (this._allSkills || []).find(x => Number(x.id) === id) || { id };
          this._openSkillDropdown(btn, summaryObj);
        });
      });

      list.querySelectorAll('.skill-card').forEach(card => {
        card.addEventListener('click', (e) => {
          if (e.target.closest('.skill-select-label, .skill-menu-btn, button, input, a')) return;
          const id = Number(card.dataset.skillId);
          if (id) this.openSkillDetail(id);
        });
        card.addEventListener('dragstart', (e) => {
          if (this._activeSkillId !== null || this._isEditingDetail) {
            e.preventDefault();
            return;
          }
          const sid = card.dataset.skillId || '';
          const theme = card.dataset.skillTheme || '';
          this._dragSkillId = sid;
          e.dataTransfer.setData('application/x-catchpm-skill-id', String(sid));
          e.dataTransfer.setData('text/plain', theme ? `Тема скилла: ${theme}` : '');
          e.dataTransfer.effectAllowed = 'copy';
        });
        card.addEventListener('dragend', () => {
          this._dragSkillId = null;
        });
      });
    },

    _openSkillDropdown(anchorBtn, skillObj){
      const sid = Number(skillObj?.id || 0);
      if (!sid) return;
      const wasExpanded = anchorBtn.getAttribute('aria-expanded') === 'true';
      this._closeActionDropdown();
      if (wasExpanded) return;

      anchorBtn.setAttribute('aria-expanded', 'true');
      const liked = this._likedIds.has(sid);
      const likesCount = Number(skillObj?.likes_count || 0);
      const canEditOwn = Number(skillObj?.published_by_user_id || 0) === Number(this.app?.currentUser?.user_id || 0) || this._isAdmin;

      const dd = document.createElement('div');
      dd.className = 'chat-item-dropdown skill-actions-dropdown';
      dd.innerHTML = `
        <button type="button" class="chat-item-dropdown-action" data-dd-act="like">
          <span class="chat-item-dropdown-icon">${liked ? ICON_HEART_FILLED : ICON_HEART_OUTLINE}</span>
          <span class="chat-item-dropdown-text">${liked ? 'Убрать лайк' : 'Лайкнуть'} (${likesCount})</span>
        </button>
        <button type="button" class="chat-item-dropdown-action" data-dd-act="copy">
          <span class="chat-item-dropdown-icon">${ICON_COPY}</span>
          <span class="chat-item-dropdown-text">Скопировать в чат</span>
        </button>
        ${canEditOwn ? `
        <button type="button" class="chat-item-dropdown-action" data-dd-act="edit">
          <span class="chat-item-dropdown-icon">${ICON_EDIT}</span>
          <span class="chat-item-dropdown-text">Редактировать</span>
        </button>` : ''}
        ${this._isAdmin ? `
        <button type="button" class="chat-item-dropdown-delete" data-dd-act="delete">
          <span class="chat-item-dropdown-icon">${ICON_TRASH}</span>
          <span class="chat-item-dropdown-text">Удалить</span>
        </button>` : ''}
      `;

      document.body.appendChild(dd);
      this._openDropdown = dd;

      const rect = anchorBtn.getBoundingClientRect();
      const ddRect = dd.getBoundingClientRect();
      let left = rect.right - ddRect.width;
      let top = rect.bottom + 6;
      if (left < 8) left = 8;
      if (left + ddRect.width > window.innerWidth - 8) left = window.innerWidth - ddRect.width - 8;
      if (top + ddRect.height > window.innerHeight - 8) top = Math.max(8, rect.top - ddRect.height - 6);
      dd.style.left = `${Math.round(left)}px`;
      dd.style.top = `${Math.round(top)}px`;

      dd.addEventListener('click', async (e) => {
        e.stopPropagation();
        const act = e.target.closest('[data-dd-act]')?.dataset?.ddAct;
        if (!act) return;
        this._closeActionDropdown();
        if (act === 'like') {
          await this.like(sid);
        } else if (act === 'copy') {
          await this.copyToChat(sid);
        } else if (act === 'edit') {
          if (this._activeSkillId === sid && this._activeSkillData) {
            this._enterDetailEdit(this._activeSkillData);
          } else {
            await this.openSkillDetail(sid, { edit: true });
          }
        } else if (act === 'delete') {
          await this.deleteSkill(sid);
        }
      });
    },

    async openSkillDetail(skillId, opts = {}){
      const sid = Number(skillId);
      if (!sid) return;
      this._closeActionDropdown();
      const detail = this._ensureDetailMount();
      if (!detail) return;

      const intro = document.querySelector('#skills-view .skills-intro');
      const toolbar = document.getElementById('skills-toolbar');
      const list = document.getElementById('skills-list');
      if (intro) intro.hidden = true;
      if (toolbar) toolbar.hidden = true;
      if (list) list.hidden = true;
      detail.hidden = false;

      this._activeSkillId = sid;
      this._isEditingDetail = false;
      this._detailTab = 'overview';

      detail.innerHTML = `<div class="skills-empty" style="min-height:180px;"><div class="skills-empty__hint">Загрузка скилла…</div></div>`;

      try {
        const res = await fetch(`/api/skills/${sid}`, { credentials: 'include' });
        if (!res.ok) {
          this.app?.showToast?.('Скилл не найден');
          this.closeSkillDetail();
          return;
        }
        const data = await res.json();
        const skill = data.skill || data;
        this._activeSkillData = skill;
        this._renderSkillDetail(skill);
        if (opts.edit) {
          this._enterDetailEdit(skill);
        }
      } catch (e) {
        this.app?.showToast?.('Ошибка загрузки скилла');
        this.closeSkillDetail();
      }
    },

    closeSkillDetail(reloadList = true){
      this._closeActionDropdown();
      this._activeSkillId = null;
      this._activeSkillData = null;
      this._isEditingDetail = false;

      const detail = document.getElementById('skill-detail-view');
      const intro = document.querySelector('#skills-view .skills-intro');
      const toolbar = document.getElementById('skills-toolbar');
      const list = document.getElementById('skills-list');

      if (detail) {
        detail.hidden = true;
        detail.innerHTML = '';
      }
      if (intro) intro.hidden = false;
      if (toolbar) toolbar.hidden = false;
      if (list) list.hidden = false;

      if (reloadList) this._applyFilterSort();
    },

    _renderSkillDetail(skill){
      const detail = this._ensureDetailMount();
      if (!detail || !skill) return;

      const sid = Number(skill.id);
      const selected = this._selectedIds.has(sid);
      const authorName = skill.published_by_name || 'Агент';
      const dateStr = formatSkillDate(skill.created_at);
      const usesCount = Number(skill.uses_count || 0);
      const likesCount = Number(skill.likes_count || 0);
      const modelUsed = skill.model_used || '—';
      const toolsArr = Array.isArray(skill.tools) ? skill.tools : [];

      detail.setAttribute('draggable', 'false');
      detail.innerHTML = `
        <button type="button" class="skill-detail__back" id="skill-detail-back">
          ${ICON_BACK_ARROW}
          <span>Все скиллы</span>
        </button>

        <div class="skill-detail__header">
          <div class="skill-detail__head-left">
            <div class="skill-detail__avatar">${ICON_SKILL_DOC}</div>
            <div class="skill-detail__title-wrap">
              <h2 class="skill-detail__title">${esc(skill.theme || '')}</h2>
              <div class="skill-detail__subtitle">
                <span>от ${esc(authorName)}</span>
                <span>·</span>
                <span>${esc(dateStr)}</span>
                <span>·</span>
                <span>использований: ${usesCount}</span>
              </div>
            </div>
          </div>

          <div class="skill-detail__head-right">
            <label class="skill-select-label" title="Выбрать скилл для чата">
              <input type="checkbox" class="skill-select-cb" id="skill-detail-select-cb" ${selected ? 'checked' : ''} />
              <span class="skill-select-box" aria-hidden="true">${ICON_CHECK}</span>
            </label>
            <button class="chat-item-menu-btn skill-menu-btn" type="button" id="skill-detail-menu-btn" title="Действия" aria-label="Действия" aria-expanded="false">
              ${ICON_DOTS}
            </button>
          </div>
        </div>

        <div class="skill-detail__tabs">
          <button type="button" class="skill-detail__tab ${this._detailTab === 'overview' ? 'is-active' : ''}" data-detail-tab="overview">Обзор</button>
          ${toolsArr.length ? `<button type="button" class="skill-detail__tab ${this._detailTab === 'tools' ? 'is-active' : ''}" data-detail-tab="tools">Инструменты <span class="skill-detail__tab-count">· ${toolsArr.length}</span></button>` : ''}
        </div>

        <div class="skill-detail__grid">
          <div class="skill-detail__main" id="skill-detail-main">
            ${this._detailTab === 'tools' && toolsArr.length ? `
              <div class="skill-detail__section-label">Рекомендуемые инструменты</div>
              <div class="skill-detail__body reasoning-body" draggable="false">
                <div class="skill-detail__tools-list">
                  ${toolsArr.map(t => `<span class="skill-badge">${esc(t)}</span>`).join('')}
                </div>
              </div>
            ` : `
              <div class="skill-detail__section-label">Содержимое</div>
              <div class="skill-detail__body reasoning-body md-content" id="skill-detail-body" draggable="false"></div>
            `}
          </div>

          <aside class="skill-detail__card">
            <div class="skill-detail__meta-group">
              <div class="skill-detail__meta-label">Кем собран</div>
              <div class="skill-detail__author-row">
                <span class="skill-detail__author-icon">${ICON_USER}</span>
                <span>${esc(authorName)}</span>
              </div>
            </div>
            <div class="skill-detail__meta-group">
              <div class="skill-detail__meta-label">Модель</div>
              <div class="skill-detail__meta-val">${esc(modelUsed)}</div>
            </div>
            <div class="skill-detail__meta-group">
              <div class="skill-detail__meta-label">Дата создания</div>
              <div class="skill-detail__meta-val">${esc(dateStr)}</div>
            </div>
            <div class="skill-detail__meta-group">
              <div class="skill-detail__meta-label">Использований</div>
              <div class="skill-detail__meta-val">${usesCount}</div>
            </div>
            <div class="skill-detail__meta-group">
              <div class="skill-detail__meta-label">Лайков</div>
              <div class="skill-detail__meta-val">${likesCount}</div>
            </div>
          </aside>
        </div>
      `;

      const bodyEl = document.getElementById('skill-detail-body');
      if (bodyEl) {
        const raw = skill.body || '';
        try {
          if (typeof marked !== 'undefined') {
            let html = marked.parse(raw);
            if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);
            bodyEl.innerHTML = html;
          } else {
            bodyEl.textContent = raw;
          }
        } catch (e) {
          bodyEl.textContent = raw;
        }
      }

      detail.addEventListener('dragstart', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });

      document.getElementById('skill-detail-back')?.addEventListener('click', () => {
        this.closeSkillDetail(true);
      });

      const detailCb = document.getElementById('skill-detail-select-cb');
      detailCb?.addEventListener('change', (e) => {
        e.stopPropagation();
        this.toggleSelectedSkill(sid, detailCb.checked, detailCb);
      });

      const menuBtn = document.getElementById('skill-detail-menu-btn');
      menuBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        this._openSkillDropdown(menuBtn, skill);
      });

      detail.querySelectorAll('[data-detail-tab]').forEach(tabBtn => {
        tabBtn.addEventListener('click', () => {
          this._detailTab = tabBtn.dataset.detailTab || 'overview';
          this._renderSkillDetail(this._activeSkillData || skill);
        });
      });
    },

    _enterDetailEdit(skill){
      const mainCol = document.getElementById('skill-detail-main');
      if (!mainCol || !skill) return;
      this._isEditingDetail = true;

      const sid = Number(skill.id);
      const theme = skill.theme || '';
      const body = skill.body || '';
      const toolsCsv = Array.isArray(skill.tools) ? skill.tools.join(', ') : '';

      mainCol.innerHTML = `
        <div class="skill-detail__section-label">Редактирование скилла</div>
        <div class="skill-edit-box reasoning-body" draggable="false">
          <label class="skill-inline-edit__label">Тема</label>
          <input class="skill-inline-edit__input" data-inline="theme" type="text" maxlength="200" value="${esc(theme)}" draggable="false">
          <label class="skill-inline-edit__label">Инструменты через запятую</label>
          <input class="skill-inline-edit__input" data-inline="tools" type="text" maxlength="1000" value="${esc(toolsCsv)}" draggable="false">
          <label class="skill-inline-edit__label">Тело скилла (Markdown)</label>
          <textarea class="skill-inline-edit__textarea" data-inline="body" maxlength="10000" draggable="false">${esc(body)}</textarea>
          <div class="skill-inline-edit__actions">
            <button type="button" class="skill-inline-edit__btn skill-inline-edit__btn--cancel" data-inline-act="cancel">Отмена</button>
            <button type="button" class="skill-inline-edit__btn skill-inline-edit__btn--save" data-inline-act="save">Сохранить</button>
          </div>
        </div>
      `;

      mainCol.addEventListener('dragstart', (e) => {
        e.preventDefault();
        e.stopPropagation();
      });

      mainCol.querySelector('[data-inline-act="cancel"]')?.addEventListener('click', (e) => {
        e.stopPropagation();
        this._isEditingDetail = false;
        this._renderSkillDetail(this._activeSkillData || skill);
      });

      mainCol.querySelector('[data-inline-act="save"]')?.addEventListener('click', async (e) => {
        e.stopPropagation();
        const newTheme = mainCol.querySelector('[data-inline="theme"]')?.value?.trim() || '';
        const newToolsCsv = mainCol.querySelector('[data-inline="tools"]')?.value || '';
        const newBody = mainCol.querySelector('[data-inline="body"]')?.value?.trim() || '';
        if (!newTheme || !newBody) {
          this.app?.showToast?.('Тема и тело скилла обязательны');
          return;
        }
        const newTools = newToolsCsv.split(',').map(x => x.trim()).filter(Boolean);
        const saveBtn = mainCol.querySelector('[data-inline-act="save"]');
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Сохранение…'; }
        try {
          const res = await fetch(`/api/skills/${sid}`, {
            method: 'PUT',
            credentials: 'include',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ theme: newTheme, body: newBody, tools: newTools }),
          });
          if (res.status === 403) { this.app?.showToast?.('Редактировать можно только свои скиллы'); return; }
          if (res.status === 404) { this.app?.showToast?.('Скилл не найден'); return; }
          if (!res.ok) { this.app?.showToast?.('Не удалось обновить скилл'); return; }
          const updated = await res.json().catch(() => ({}));
          this._isEditingDetail = false;
          this.app?.showToast?.('Скилл обновлён');
          await this.load();
          await this.openSkillDetail(sid);
        } catch (err) {
          this.app?.showToast?.('Ошибка обновления скилла');
        } finally {
          if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Сохранить'; }
        }
      });

      mainCol.querySelector('[data-inline="theme"]')?.focus();
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
      const sid = Number(skillId);
      const userId = this.app?.currentUser?.user_id;
      if (!userId) { this.app?.showToast?.('Не найден user_id'); return; }
      const wasLiked = this._likedIds.has(sid);
      const nextLiked = !wasLiked;
      const itemInList = (this._allSkills || []).find(x => Number(x.id) === sid);
      const curCount = Number(itemInList?.likes_count ?? this._activeSkillData?.likes_count ?? 0);
      const nextCount = Math.max(0, curCount + (nextLiked ? 1 : -1));
      if (nextLiked) this._likedIds.add(sid); else this._likedIds.delete(sid);

      try {
        const res = await fetch(`/api/skills/${sid}/like`, {
          method:'POST',
          credentials:'include',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            user_id: userId,
            liked: nextLiked,
            liked_ids: Array.from(this._likedIds),
            likes_count: nextCount,
          })
        });
        if (!res.ok) {
          if (wasLiked) this._likedIds.add(sid); else this._likedIds.delete(sid);
          this.app?.showToast?.('Не удалось поставить лайк');
          return;
        }
        const data = await res.json().catch(()=>({}));
        if (typeof data.liked === 'boolean') {
          if (data.liked) this._likedIds.add(sid); else this._likedIds.delete(sid);
        }
        const finalCount = data.likes_count !== undefined ? Number(data.likes_count) : nextCount;
        if (itemInList) itemInList.likes_count = finalCount;
        if (this._activeSkillId === sid && this._activeSkillData) {
          this._activeSkillData.likes_count = finalCount;
          this._renderSkillDetail(this._activeSkillData);
        }
      } catch (e) {
        if (wasLiked) this._likedIds.add(sid); else this._likedIds.delete(sid);
      }
      this._applyFilterSort();
    },

    async copyToChat(skillId){
      const res = await fetch(`/api/skills/${skillId}/prompt`, {credentials:'include'});
      if (!res.ok) return;
      const data = await res.json();
      const txt = data.prompt || '';
      this.app?.switchTab?.('agent');
      await this.app?.renderMessageInput?.();
      this._setSkillInInput(txt);
      this.app?.showToast?.('Скилл скопирован в чат');
    },

    _confirmDelete(skillId){
      return new Promise((resolve) => {
        const prev = document.getElementById('skill-confirm-overlay');
        if (prev) prev.remove();

        const overlay = document.createElement('div');
        overlay.className = 'skill-confirm-overlay';
        overlay.id = 'skill-confirm-overlay';
        overlay.innerHTML = `
          <div class="skill-confirm" role="dialog" aria-modal="true" aria-labelledby="skill-confirm-title">
            <div class="skill-confirm__icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="28" height="28"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m5 5v6m4-6v6"/></svg>
            </div>
            <div class="skill-confirm__title" id="skill-confirm-title">Удалить скилл?</div>
            <div class="skill-confirm__text">Скилл #${esc(String(skillId))} будет скрыт из библиотеки. Это действие доступно только администратору.</div>
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
        this._selectedIds.delete(Number(skillId));
        this._updateSelectedCounterUi();
        this.app?.showToast?.('Скилл удалён');
        if (this._activeSkillId === Number(skillId)) {
          this.closeSkillDetail(false);
        }
        await this.load();
      } catch (e) {
        this.app?.showToast?.('Ошибка удаления скилла');
      }
    },

    
    async _loadLiked(){
      try {
        const res = await fetch('/api/skills/liked', { credentials:'include' });
        if (!res.ok) return;
        const data = await res.json();
        const ids = Array.isArray(data?.liked_ids) ? data.liked_ids : [];
        this._likedIds = new Set(ids.map(Number));
      } catch (_) {}
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
      if (nav) nav.textContent = `${this._selectedIds.size || 0}/${this._selectedLimit}`;
      const countEl = document.getElementById('skills-count');
      const total = typeof totalSkills === 'number' ? totalSkills : (this._allSkills?.length || 0);
      if (countEl) countEl.textContent = `${total} • выбрано ${this._selectedIds.size}/${this._selectedLimit}`;
    },

    async toggleSelectedSkill(skillId, desiredState = null, checkboxEl = null){
      const sid = Number(skillId);
      if (!sid) return;

      const currentlySelected = this._selectedIds.has(sid);
      const nextSelected = desiredState !== null ? Boolean(desiredState) : !currentlySelected;

      if (nextSelected && !currentlySelected && this._selectedIds.size >= this._selectedLimit) {
        if (checkboxEl) checkboxEl.checked = false;
        this.app?.showToast?.('Можно выбрать не более 5 скиллов');
        return;
      }

      if (nextSelected) this._selectedIds.add(sid);
      else this._selectedIds.delete(sid);

      this._syncCheckboxesUi(sid, nextSelected);
      this._updateSelectedCounterUi();

      try {
        const res = await fetch(`/api/skills/${sid}/select`, {
          method:'POST',
          credentials:'include',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            selected: nextSelected,
            selected_ids: Array.from(this._selectedIds),
          }),
        });

        if (res.status === 400) {
          this.app?.showToast?.('Можно выбрать не более 5 скиллов');
          await Promise.all([this._loadSelected(), this._loadLiked()]);
          this._syncCheckboxesUi(sid, this._selectedIds.has(sid));
          this._updateSelectedCounterUi();
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
        if (Array.isArray(data?.selected_ids)) {
          this._selectedIds = new Set(data.selected_ids.map(Number));
        }
        this._syncCheckboxesUi(sid, this._selectedIds.has(sid));
        this._updateSelectedCounterUi();
        if (this._filter === 'selected' && this._activeSkillId === null) {
          this._applyFilterSort();
        }
      } catch (e) {
        this.app?.showToast?.('Ошибка выбора скилла');
      }
    },

    _syncCheckboxesUi(skillId, isChecked){
      const sid = Number(skillId);
      document.querySelectorAll(`.skill-select-cb[data-select-cb="${sid}"]`).forEach(cb => {
        cb.checked = isChecked;
      });
      if (this._activeSkillId === sid) {
        const detailCb = document.getElementById('skill-detail-select-cb');
        if (detailCb) detailCb.checked = isChecked;
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

    _setSkillInInput(text){
      const field = document.getElementById('message-input');
      if (!field) return;
      const S = '\u2063[skill]\u2063';
      const E = '\u2063[/skill]\u2063';
      const block = S + text + E;
      const read = () => field.isContentEditable ? (field.innerText || '') : (field.value || '');
      const write = (v) => {
        if (field.isContentEditable) field.textContent = v;
        else field.value = v;
      };
      const cur = read();
      const si = cur.indexOf(S);
      const ei = cur.indexOf(E);
      let next;
      if (si !== -1 && ei !== -1 && ei > si) {
        next = cur.slice(0, si) + block + cur.slice(ei + E.length);
      } else {
        const sep = cur && !cur.endsWith('\n') ? '\n' : '';
        next = cur + sep + block;
      }
      field.focus();
      write(next);
      field.dispatchEvent(new Event('input', {bubbles:true}));
    },

    _bindDropToInput(){
      document.addEventListener('dragover', (e)=>{
        if (this._activeSkillId !== null || this._isEditingDetail) return;
        if (e.target.closest && e.target.closest('#message-input')) e.preventDefault();
      });
      document.addEventListener('drop', async (e)=>{
        if (this._activeSkillId !== null || this._isEditingDetail) return;
        const input = e.target.closest && e.target.closest('#message-input');
        if (!input) return;
        const sid = e.dataTransfer?.getData('application/x-catchpm-skill-id') || this._dragSkillId;
        if (sid) {
          e.preventDefault();
          await this.copyToChat(Number(sid));
          return;
        }
        const txt = e.dataTransfer?.getData('text/plain') || '';
        if (!txt) return;
        e.preventDefault();
        this._appendToInput(txt);
      });
    },

    _bindDropToChats(){
      const onDragOver = (e)=>{
        if (this._activeSkillId !== null || this._isEditingDetail) return;
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
        if (this._activeSkillId !== null || this._isEditingDetail) return;
        const item = e.target.closest && e.target.closest('.chat-item');
        if (!item) return;
        e.preventDefault();
        item.classList.remove('chat-item--drop');
        const chatId = item.dataset.chatId;
        const sid = e.dataTransfer?.getData('application/x-catchpm-skill-id') || this._dragSkillId;
        if (!chatId || !sid) return;
        try {
          const res = await fetch(`/api/skills/${Number(sid)}/prompt`, {credentials:'include'});
          if (!res.ok) return;
          const data = await res.json();
          const txt = data.prompt || '';
          this.app?.switchTab?.('agent');
          await this.app?.openChat?.(chatId);
          this._setSkillInInput(txt);
          this.app?.showToast?.('Скилл добавлен в поле ввода чата');
        } catch (err) {
          console.error('drop skill to chat failed:', err);
        }
      };
      document.addEventListener('dragover', onDragOver);
      document.addEventListener('dragleave', onDragLeave);
      document.addEventListener('drop', onDrop);
    }
  };
  window.SkillsPanel = SkillsPanel;
})();
