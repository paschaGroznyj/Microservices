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
  const ICON_EYE           = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const ICON_CODE          = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true"><path d="M16 18l6-6-6-6M8 6l-6 6 6 6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>';
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


  const I = {
    q:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    sort:'<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M6 4v12M6 16l-2.5-2.5M6 16l2.5-2.5M13 5h5M13 9h4M13 13h2"/></svg>',
    x:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    back:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>',
    more:'<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/></svg>',
    heart:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10Z"/></svg>',
    use:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4"/></svg>',
    tool:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.5-2.5Z"/></svg>',
    pin:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    ok:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    copy:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>',
    eye:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
    code:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m8 7-5 5 5 5M16 7l5 5-5 5"/></svg>',
    edit:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/></svg>',
    del:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
    send:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  };
  /* Раздел «Скиллы» (вариант D): строки в общей рамке + панель скилла справа.
     На ПК список сжимается в левую колонку, на мобиле скилл выезжает экраном. Стили: css/skills-view.css. */
  const tone = id => Math.abs(Number(id) * 2654435761 % 8);
  const ini = t => { const w = String(t || '').replace(/[«»"(),.:]/g, ' ').split(/\s+/).filter(x => /[A-Za-zА-Яа-яЁё]/.test(x)); return ((w[0] || '?')[0] + ((w[1] || '')[0] || '')).toUpperCase(); };
  const av = (s, m = '') => `<span class="skv-av ${m}" data-tone="${tone(s.id)}" aria-hidden="true">${esc(ini(s.theme))}</span>`;
  const MON = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек'];
  const dt = v => { const d = new Date(v); if (isNaN(d)) return ''; return d.getFullYear() === new Date().getFullYear() ? `${d.getDate()} ${MON[d.getMonth()]}` : `${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}`; };
  const plural = (n, a, b, c) => { n = Math.abs(n) % 100; const m = n % 10; return n > 10 && n < 20 ? c : m === 1 ? a : m > 1 && m < 5 ? b : c; };
  const meta = s => {
    const tools = Array.isArray(s.tools) ? s.tools.length : 0;
    return `<span title="Использований">${I.use}${Number(s.uses_count || 0)}</span><span class="skv-dot"></span><span title="Лайков">${I.heart}${Number(s.likes_count || 0)}</span>${tools ? `<span class="skv-dot"></span><span title="Инструментов">${I.tool}${tools}</span>` : ''}${s.created_at ? `<span class="skv-dot"></span><span>${esc(dt(s.created_at))}</span>` : ''}`;
  };
  const isMobile = () => window.matchMedia('(max-width: 600px)').matches;
  const FILTERS = [['all', 'Все'], ['liked', 'Избранные'], ['selected', 'В чате']];
  const SORTS = [['new', 'Сначала новые'], ['popular', 'Сначала популярные'], ['liked', 'Сначала отмеченные']];

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
    _query: '',
    _allSkills: [],
    _activeSkillId: null,
    _activeSkillData: null,
    _isEditingDetail: false,
    _detailTab: 'overview',
    _detailBodyMode: 'preview',
    _openDropdown: null,
    _loadSeq: 0,
    _detailSeq: 0,
    _closeTimer: null,

    init(app){
      this.app = app;
      if (this.inited) return;
      const host = document.getElementById('skills-view');
      if (!host) return;
      this.inited = true;
      this._mount(host);

      host.addEventListener('click', (e) => this._onClick(e));
      host.addEventListener('input', (e) => {
        if (e.target.id !== 'skv-q') return;
        clearTimeout(this._debounce);
        const v = e.target.value;
        this._debounce = setTimeout(() => { this._query = v; this.load(); }, 250);
      });
      host.addEventListener('keydown', (e) => {
        if (e.target.id === 'skv-q' && e.key === 'Escape' && e.target.value) {
          e.stopPropagation();
          e.target.value = '';
          clearTimeout(this._debounce);
          this._query = '';
          this.load();
          return;
        }
        if ((e.key === 'Enter' || e.key === ' ') && e.target.classList?.contains('skv-row')) {
          e.preventDefault();
          this._openRow(Number(e.target.dataset.open));
        }
      });
      host.addEventListener('dragstart', (e) => this._onDragStart(e));
      host.addEventListener('dragend', () => { this._dragSkillId = null; });

      document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape' || this._activeSkillId === null || this._isEditingDetail) return;
        if (document.getElementById('view-skills')?.classList.contains('hidden')) return;
        if (this._openDropdown || document.getElementById('skill-confirm-overlay')) return;
        this.closeSkillDetail(false);
      });
      document.addEventListener('click', (e) => {
        if (!e.target.closest('[data-skill-menu], #skv-sort, .chat-item-dropdown')) this._closeActionDropdown();
      });

      this._bindDropToInput();
      this._bindDropToChats();
      Promise.all([this._loadSelected(), this._loadLiked()]).then(() => this._updateSelectedCounterUi()).catch(() => {});
    },

    _mount(host){
      host.classList.add('skv-on');
      host.innerHTML = `<div class="skv skv-root skv-v-D" id="skv-root"><div class="skv-split">
        <div class="skv-dl"><div class="skv-barw"><div class="skv-bar">
          <label class="skv-q">${I.q}<input type="search" id="skv-q" placeholder="Поиск" autocomplete="off" aria-label="Поиск скиллов"></label>
          <div class="skv-seg" id="skv-seg" role="tablist" aria-label="Фильтр"></div>
          <button type="button" class="skv-ib" id="skv-sort" title="Сортировка" aria-label="Сортировка" aria-haspopup="menu" aria-expanded="false">${I.sort}</button>
        </div></div>
        <div class="skv-sc"><div class="skv-col"><div class="skv-hl" id="skv-hl"></div><div class="skv-grp" id="skv-list"></div></div></div>
        <div class="skv-msf" id="skv-foot"></div></div>
        <div class="skv-md-pane" id="skv-pane" aria-live="polite"></div>
      </div></div>`;
    },

    _onClick(e){
      const t = e.target;
      const menuBtn = t.closest('[data-skill-menu]');
      if (menuBtn) {
        e.stopPropagation();
        const id = Number(menuBtn.dataset.skillMenu);
        const obj = (this._activeSkillId === id && this._activeSkillData) || this._allSkills.find(x => Number(x.id) === id) || { id };
        return this._openSkillDropdown(menuBtn, obj);
      }
      const sortBtn = t.closest('#skv-sort');
      if (sortBtn) { e.stopPropagation(); return this._openSortMenu(sortBtn); }
      const pin = t.closest('[data-pin]');
      if (pin) { e.stopPropagation(); if (!pin.disabled) this.toggleSelectedSkill(Number(pin.dataset.pin)); return; }
      const f = t.closest('[data-f]');
      if (f) { this._filter = f.dataset.f; return this._applyFilterSort(); }
      const lk = t.closest('[data-like]');
      if (lk) return this.like(Number(lk.dataset.like));
      const cp = t.closest('[data-copy]');
      if (cp) return this._copyRaw(cp);
      const tab = t.closest('[data-dtab]');
      if (tab) { this._detailTab = tab.dataset.dtab; return this._renderSkillDetail(this._activeSkillData); }
      const mode = t.closest('[data-mode]');
      if (mode) {
        this._detailBodyMode = mode.dataset.mode === 'raw' ? 'raw' : 'preview';
        document.querySelectorAll('#skv-pane [data-mode]').forEach(b => {
          const on = b.dataset.mode === this._detailBodyMode;
          b.classList.toggle('is-on', on);
          b.setAttribute('aria-pressed', String(on));
        });
        return this._fillBody(this._activeSkillData);
      }
      const ia = t.closest('[data-inline-act]');
      if (ia) {
        if (ia.dataset.inlineAct === 'save') return this._saveEdit(ia);
        this._isEditingDetail = false;
        return this._renderSkillDetail(this._activeSkillData);
      }
      if (t.closest('[data-close]')) {
        if (this._isEditingDetail) this._isEditingDetail = false;
        return this.closeSkillDetail(false);
      }
      const row = t.closest('[data-open]');
      if (row) return this._openRow(Number(row.dataset.open));
    },

    _openRow(id){
      if (!id) return;
      if (id === this._activeSkillId && this._activeSkillData) return;
      if (this._isEditingDetail) {
        this.app?.showToast?.('Сначала сохраните или отмените редактирование');
        return;
      }
      this.openSkillDetail(id);
    },

    _onDragStart(e){
      const row = e.target.closest?.('.skv-row');
      if (!row || this._isEditingDetail) {
        if (e.target.closest?.('#skv-pane') || this._isEditingDetail) { e.preventDefault(); e.stopPropagation(); }
        return;
      }
      const sid = row.dataset.open || '';
      const theme = row.dataset.skillTheme || '';
      this._dragSkillId = sid;
      e.dataTransfer.setData('application/x-catchpm-skill-id', String(sid));
      e.dataTransfer.setData('text/plain', theme ? `Тема скилла: ${theme}` : '');
      e.dataTransfer.effectAllowed = 'copy';
    },

    _closeAllMenus(){ this._closeActionDropdown(); },

    _closeActionDropdown(){
      if (this._openDropdown) {
        this._openDropdown.remove();
        this._openDropdown = null;
      }
      document.querySelectorAll('#skills-view [aria-expanded="true"][data-skill-menu], #skv-sort[aria-expanded="true"]').forEach(b => b.setAttribute('aria-expanded', 'false'));
    },

    _openSortMenu(btn){
      const was = btn.getAttribute('aria-expanded') === 'true';
      this._closeActionDropdown();
      if (was) return;
      const dd = document.createElement('div');
      dd.className = 'chat-item-dropdown skv-sort-dd';
      dd.setAttribute('role', 'menu');
      dd.innerHTML = SORTS.map(([k, t]) => `<button type="button" class="chat-item-dropdown-action${this._sort === k ? ' is-on' : ''}" role="menuitemradio" aria-checked="${this._sort === k}" data-sort="${k}"><span class="chat-item-dropdown-text">${t}</span></button>`).join('');
      document.body.appendChild(dd);
      this._openDropdown = dd;
      window.AnchoredPopup.attach(dd, btn, { onClose: () => { if (this._openDropdown === dd) this._openDropdown = null; } });
      dd.addEventListener('click', (e) => {
        e.stopPropagation();
        const k = e.target.closest('[data-sort]')?.dataset.sort;
        if (!k) return;
        this._closeActionDropdown();
        if (k === this._sort) return;
        this._sort = k;
        this.load();
      });
    },

    open(app){
      this.init(app);
      this.closeSkillDetail(false);
      this.load();
    },

    async load(){
      const q = this._query || '';
      const sort = this._sort || 'new';
      const seq = ++this._loadSeq;
      let skills = [];
      // Первая загрузка: скелетон строк. Повторные (поиск, сортировка) держат прежний список до ответа.
      if (!this._skLoaded && window.Skeleton) {
        const listEl = document.getElementById('skv-list');
        if (listEl && !listEl.querySelector('.skv-row')) listEl.innerHTML = window.Skeleton.skillRows ? window.Skeleton.skillRows() : window.Skeleton.skills();
      }
      await Promise.all([this._loadSelected(), this._loadLiked()]);
      try {
        const res = await fetch(`/api/skills?query=${encodeURIComponent(q)}&sort_by=${encodeURIComponent(sort)}&filter_by=all`, {credentials:'include'});
        const data = res.ok ? await res.json() : {skills:[]};
        skills = data.skills || [];
      } catch (e) { skills = []; }
      await this._fetchIsAdmin();
      if (seq !== this._loadSeq) return;
      for (const s of skills) {
        const sid = Number(s?.id || 0);
        if (sid > 0 && s?.liked_by_me === true) this._likedIds.add(sid);
      }
      this._allSkills = skills;
      this._skLoaded = true;
      if (!q.trim()) {
        const aliveIds = new Set(skills.map(s => Number(s.id)));
        for (const selId of Array.from(this._selectedIds)) {
          if (!aliveIds.has(Number(selId))) this._selectedIds.delete(Number(selId));
        }
      }
      this._applyFilterSort();
    },

    _filtered(){
      let list = (this._allSkills || []).slice();
      if (this._filter === 'liked') list = list.filter(s => this._likedIds.has(Number(s.id)));
      else if (this._filter === 'selected') list = list.filter(s => this._selectedIds.has(Number(s.id)));
      if (this._sort === 'popular') {
        list.sort((a, b) => (Number(b.uses_count || 0) - Number(a.uses_count || 0)) || (Number(b.likes_count || 0) - Number(a.likes_count || 0)));
      } else if (this._sort === 'liked') {
        list.sort((a, b) => (Number(b.likes_count || 0) - Number(a.likes_count || 0)) || (Number(b.uses_count || 0) - Number(a.uses_count || 0)));
      }
      return list;
    },

    _applyFilterSort(){
      this.render(this._filtered());
      this._updateSelectedCounterUi();
    },

    _isFull(){ return this._selectedIds.size >= this._selectedLimit; },

    _renderBar(){
      const seg = document.getElementById('skv-seg');
      if (seg) {
        const all = this._allSkills.length;
        const liked = this._allSkills.filter(s => this._likedIds.has(Number(s.id))).length;
        const n = { all, liked, selected: `${this._selectedIds.size}/${this._selectedLimit}` };
        seg.innerHTML = FILTERS.map(([k, t]) => `<button type="button" role="tab" data-f="${k}" aria-selected="${this._filter === k}" class="${this._filter === k ? 'is-on' : ''}${k === 'selected' && this._isFull() ? ' is-full' : ''}">${t} <b>${n[k]}</b></button>`).join('');
      }
      document.getElementById('skv-sort')?.classList.toggle('is-active', this._sort !== 'new');
    },

    _renderFoot(){
      const f = document.getElementById('skv-foot');
      if (!f) return;
      const full = this._isFull();
      f.classList.toggle('is-full', full);
      f.innerHTML = `<span class="skv-it">${I.ok}в чате <b>${this._selectedIds.size}/${this._selectedLimit}</b></span><span class="skv-dot"></span><span class="skv-it">${full ? 'лимит, отключите лишний' : '＋ подключает скилл к чату'}</span>`;
    },

    _qpHtml(sid){
      const on = this._selectedIds.has(sid);
      const full = !on && this._isFull();
      const L = this._selectedLimit;
      const title = on ? 'Отключить от чата' : full ? `Лимит ${L}/${L}: отключите другой скилл` : 'Подключить к чату';
      return `<button type="button" class="skv-qp${on ? ' is-on' : ''}" data-pin="${sid}" aria-pressed="${on}"${full ? ' disabled' : ''} title="${title}" aria-label="${on ? 'Отключить от чата' : 'Подключить к чату'}">${on ? I.ok : I.pin}</button>`;
    },

    _pinHtml(sid){
      const on = this._selectedIds.has(sid);
      const full = !on && this._isFull();
      return `<button type="button" class="skv-pin${on ? ' is-on' : ''}" data-pin="${sid}" aria-pressed="${on}"${full ? ` disabled title="Лимит ${this._selectedLimit}/${this._selectedLimit}: отключите другой скилл"` : ` title="${on ? 'Отключить от чата' : 'Подключить к чату'}"`}>${on ? I.ok : I.pin}<span>${on ? 'В чате' : 'В чат'}</span></button>`;
    },

    _rowHtml(s){
      const sid = Number(s.id);
      const act = this._activeSkillId === sid;
      return `<div class="skv-row${act ? ' is-act' : ''}" data-open="${sid}" data-skill-theme="${esc(s.theme || '')}" draggable="true" role="button" tabindex="0" aria-current="${act}">${av(s)}<div class="skv-row__b"><div class="skv-row__t" title="${esc(s.theme)}">${esc(s.theme)}</div><div class="skv-row__m">${meta(s)}</div></div>${this._qpHtml(sid)}<button type="button" class="skv-ib" data-skill-menu="${sid}" title="Действия" aria-label="Действия" aria-expanded="false">${I.more}</button></div>`;
    },

    render(skills){
      const list = document.getElementById('skv-list');
      if (!list) return;
      const sc = list.closest('.skv-sc');
      const top = sc ? sc.scrollTop : 0;
      this._renderBar();
      const n = skills.length;
      const hl = document.getElementById('skv-hl');
      if (hl) hl.innerHTML = `${({ liked: 'Избранные', selected: 'В чате' })[this._filter] || 'Библиотека'} <span>${n} ${plural(n, 'скилл', 'скилла', 'скиллов')}</span>`;
      if (!n) {
        const msg = this._query.trim() ? 'Ничего не нашлось'
          : this._filter === 'liked' ? 'В избранном пока пусто'
          : this._filter === 'selected' ? 'К чату не подключено ни одного скилла'
          : 'Скиллы публикует агент в ходе работы. Как только появятся, они будут здесь.';
        list.innerHTML = `<div class="skv-empty">${msg}</div>`;
      } else {
        list.innerHTML = skills.map(s => this._rowHtml(s)).join('');
      }
      this._renderFoot();
      if (sc) sc.scrollTop = top;
    },

    _markActiveRow(){
      document.querySelectorAll('#skv-list .skv-row').forEach(r => {
        const a = Number(r.dataset.open) === this._activeSkillId;
        r.classList.toggle('is-act', a);
        r.setAttribute('aria-current', String(a));
      });
    },

    _setOpen(open){
      const split = document.querySelector('#skv-root .skv-split');
      const pane = document.getElementById('skv-pane');
      clearTimeout(this._closeTimer);
      split?.classList.toggle('is-open', open);
      if (open) {
        if (isMobile()) requestAnimationFrame(() => { if (this._activeSkillId !== null) pane?.classList.add('is-in'); });
        else pane?.classList.add('is-in');
      } else {
        pane?.classList.remove('is-in');
        this._closeTimer = setTimeout(() => { if (this._activeSkillId === null && pane) pane.innerHTML = ''; }, 320);
      }
      this._markActiveRow();
    },

    _paneHead(skill, loading = false){
      const sid = Number(skill?.id || 0);
      const liked = this._likedIds.has(sid);
      const showMode = !loading && this._detailTab === 'overview' && !this._isEditingDetail;
      const mode = this._detailBodyMode === 'raw' ? 'raw' : 'preview';
      const modeSeg = `<div class="skv-seg" role="group" aria-label="Режим просмотра"><button type="button" data-mode="preview" class="${mode === 'preview' ? 'is-on' : ''}" aria-pressed="${mode === 'preview'}" title="Превью" aria-label="Превью">${I.eye}</button><button type="button" data-mode="raw" class="${mode === 'raw' ? 'is-on' : ''}" aria-pressed="${mode === 'raw'}" title="Markdown" aria-label="Markdown">${I.code}</button></div>`;
      const tools = loading ? '' : `${showMode ? modeSeg : ''}<span class="skv-sep skv-hide-m"></span>`
        + `<button type="button" class="skv-ib skv-hide-m" data-copy title="Скопировать Markdown" aria-label="Скопировать Markdown">${I.copy}</button>`
        + `<button type="button" class="skv-ib skv-like${liked ? ' is-on' : ''}" data-like="${sid}" aria-pressed="${liked}" title="${liked ? 'Убрать из избранного' : 'В избранное'}" aria-label="${liked ? 'Убрать из избранного' : 'В избранное'}">${I.heart}</button>`
        + `<button type="button" class="skv-ib" data-skill-menu="${sid}" title="Действия" aria-label="Действия" aria-expanded="false">${I.more}</button>`;
      return `<div class="skv-wh"><button type="button" class="skv-ib skv-only-m" data-close title="Назад" aria-label="Назад">${I.back}</button><span class="skv-ty">SKILL</span><span class="skv-wh__t" title="${esc(skill?.theme || '')}">${esc(skill?.theme || '')}</span><span class="sp"></span>${tools}<button type="button" class="skv-ib skv-hide-m" data-close title="Закрыть" aria-label="Закрыть">${I.x}</button></div>`;
    },

    async openSkillDetail(skillId, opts = {}){
      const sid = Number(skillId);
      if (!sid) return;
      this._closeActionDropdown();
      const pane = document.getElementById('skv-pane');
      if (!pane) return;
      const same = this._activeSkillId === sid && !!this._activeSkillData;
      this._activeSkillId = sid;
      this._isEditingDetail = false;
      if (!same) {
        this._detailTab = 'overview';
        this._activeSkillData = null;
        const summary = this._allSkills.find(x => Number(x.id) === sid) || { id: sid, theme: '' };
        const sk = window.Skeleton ? window.Skeleton.skillDetail() : '';
        pane.innerHTML = this._paneHead(summary, true) + `<div class="skv-wb"><div class="skv-col skv-col--sk">${sk}</div></div>`;
      }
      this._setOpen(true);

      const seq = ++this._detailSeq;
      try {
        const res = await fetch(`/api/skills/${sid}`, { credentials: 'include' });
        if (seq !== this._detailSeq) return;
        if (!res.ok) {
          this.app?.showToast?.('Скилл не найден');
          this.closeSkillDetail(false);
          return;
        }
        const data = await res.json();
        if (seq !== this._detailSeq) return;
        const skill = data.skill || data;
        if (typeof skill?.liked_by_me === 'boolean') {
          if (skill.liked_by_me) this._likedIds.add(sid);
          else this._likedIds.delete(sid);
        }
        this._activeSkillData = skill;
        this._renderSkillDetail(skill);
        if (opts.edit) this._enterDetailEdit(skill);
      } catch (e) {
        if (seq !== this._detailSeq) return;
        this.app?.showToast?.('Ошибка загрузки скилла');
        this.closeSkillDetail(false);
      }
    },

    closeSkillDetail(reloadList = true){
      this._closeActionDropdown();
      this._detailSeq++;
      this._activeSkillId = null;
      this._activeSkillData = null;
      this._isEditingDetail = false;
      this._setOpen(false);
      if (reloadList) this._applyFilterSort();
    },

    _renderSkillDetail(skill){
      const pane = document.getElementById('skv-pane');
      if (!pane || !skill) return;
      const sid = Number(skill.id);
      const tools = Array.isArray(skill.tools) ? skill.tools : [];
      if (!['overview', 'tools', 'info'].includes(this._detailTab)) this._detailTab = 'overview';
      const tab = this._detailTab;
      const author = skill.published_by_name || 'Агент';
      const tabs = [['overview', 'Обзор'], ['tools', 'Инструменты', tools.length], ['info', 'Сведения']];
      const prevScroll = pane.querySelector('.skv-wb')?.scrollTop || 0;
      pane.innerHTML = this._paneHead(skill) + `<div class="skv-wb"><div class="skv-col">
        <div class="skv-wtitle">${av(skill, 'skv-av--l')}<div class="skv-wtitle__b"><h2>${esc(skill.theme || '')}</h2><div class="skv-sline"><span class="skv-it">${esc(author)}</span><span class="skv-dot"></span><span class="skv-it" title="Использований">${I.use}<b>${Number(skill.uses_count || 0)}</b></span><span class="skv-dot"></span><span class="skv-it" title="Лайков">${I.heart}<b>${Number(skill.likes_count || 0)}</b></span><span class="skv-dot"></span><span class="skv-it">${esc(dt(skill.created_at))}</span></div></div>${this._pinHtml(sid)}</div>
        <div class="skv-tabs" role="tablist">${tabs.map(([k, t, n]) => `<button type="button" role="tab" class="skv-tab${tab === k ? ' is-on' : ''}" aria-selected="${tab === k}" data-dtab="${k}">${t}${n ? `<b>${n}</b>` : ''}</button>`).join('')}</div>
        <div class="skv-dbody" id="skill-detail-main">${this._detailBodyHtml(skill, tab)}</div>
      </div></div>`;
      this._fillBody(skill);
      const wb = pane.querySelector('.skv-wb');
      if (wb && prevScroll) wb.scrollTop = prevScroll;
    },

    _detailBodyHtml(skill, tab){
      const tools = Array.isArray(skill.tools) ? skill.tools : [];
      if (tab === 'tools') {
        return tools.length
          ? `<div class="skv-sec">Рекомендуемые инструменты</div><div class="skv-chips">${tools.map(t => `<span class="skv-chip">${I.tool}${esc(t)}</span>`).join('')}</div>`
          : '<p class="skv-sec">Инструменты не указаны</p>';
      }
      if (tab === 'info') {
        const author = skill.published_by_name || 'Агент';
        return `<dl class="skv-kv"><dt>Автор</dt><dd><span class="skv-av skv-av--s" data-tone="${tone(skill.published_by_user_id || 3)}">${esc(ini(author))}</span>${esc(author)}</dd><dt>Модель</dt><dd>${esc(skill.model_used || '—')}</dd><dt>Создан</dt><dd>${esc(formatSkillDate(skill.created_at))}</dd><dt>Использований</dt><dd>${Number(skill.uses_count || 0)}</dd><dt>Лайков</dt><dd>${Number(skill.likes_count || 0)}</dd><dt>ID</dt><dd>#${Number(skill.id)}</dd></dl>`;
      }
      return '<div class="skv-body" id="skv-body"></div>';
    },

    _fillBody(skill){
      const el = document.getElementById('skv-body');
      if (!el || !skill) return;
      const raw = String(skill.body || '');
      if (this._detailBodyMode === 'raw') {
        el.className = 'skv-body';
        el.innerHTML = `<pre class="skv-raw">${esc(raw)}</pre>`;
        return;
      }
      el.className = 'skv-body skv-md';
      try {
        if (typeof marked !== 'undefined') {
          let html = marked.parse(raw);
          if (typeof DOMPurify !== 'undefined') html = DOMPurify.sanitize(html);
          el.innerHTML = html;
        } else {
          el.textContent = raw;
        }
      } catch (e) {
        el.textContent = raw;
      }
    },

    async _copyRaw(btn){
      const raw = String(this._activeSkillData?.body || '');
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(raw);
        } else {
          const ta = document.createElement('textarea');
          ta.value = raw;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          ta.remove();
        }
        btn.innerHTML = I.ok;
        btn.classList.add('is-copied');
        this.app?.showToast?.('Markdown скилла скопирован');
        setTimeout(() => {
          if (btn.isConnected) { btn.innerHTML = I.copy; btn.classList.remove('is-copied'); }
        }, 1400);
      } catch (err) {
        this.app?.showToast?.('Не удалось скопировать');
      }
    },

    _updatePanePin(){
      const sid = this._activeSkillId;
      const old = sid !== null ? document.querySelector(`#skv-pane .skv-wtitle [data-pin="${sid}"]`) : null;
      if (old) old.outerHTML = this._pinHtml(sid);
    },

    _enterDetailEdit(skill){
      const main = document.getElementById('skill-detail-main');
      if (!main || !skill) return;
      this._isEditingDetail = true;
      const theme = skill.theme || '';
      const body = skill.body || '';
      const toolsCsv = Array.isArray(skill.tools) ? skill.tools.join(', ') : '';
      const pane = document.getElementById('skv-pane');
      pane?.querySelector('.skv-tabs')?.setAttribute('hidden', '');
      pane?.querySelector('.skv-wh .skv-seg')?.setAttribute('hidden', '');
      main.innerHTML = `
        <div class="skv-edit" draggable="false">
          <label class="skv-f"><span>Тема</span><input data-inline="theme" type="text" maxlength="200" value="${esc(theme)}"></label>
          <label class="skv-f"><span>Инструменты через запятую</span><input data-inline="tools" type="text" maxlength="1000" value="${esc(toolsCsv)}"></label>
          <label class="skv-f"><span>Тело скилла (Markdown)</span><textarea data-inline="body" maxlength="10000" rows="16">${esc(body)}</textarea></label>
          <div class="skv-edit__act"><button type="button" class="skv-btn" data-inline-act="cancel">Отмена</button><button type="button" class="skv-btn skv-btn--p" data-inline-act="save">Сохранить</button></div>
        </div>`;
      main.querySelector('[data-inline="theme"]')?.focus();
    },

    async _saveEdit(saveBtn){
      const main = document.getElementById('skill-detail-main');
      const sid = Number(this._activeSkillId);
      if (!main || !sid) return;
      const newTheme = main.querySelector('[data-inline="theme"]')?.value?.trim() || '';
      const newToolsCsv = main.querySelector('[data-inline="tools"]')?.value || '';
      const newBody = main.querySelector('[data-inline="body"]')?.value?.trim() || '';
      if (!newTheme || !newBody) {
        this.app?.showToast?.('Тема и тело скилла обязательны');
        return;
      }
      const newTools = newToolsCsv.split(',').map(x => x.trim()).filter(Boolean);
      saveBtn.disabled = true;
      saveBtn.textContent = 'Сохранение…';
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
        this._isEditingDetail = false;
        this.app?.showToast?.('Скилл обновлён');
        await this.load();
        this._activeSkillData = null;
        await this.openSkillDetail(sid);
      } catch (err) {
        this.app?.showToast?.('Ошибка обновления скилла');
      } finally {
        if (saveBtn.isConnected) { saveBtn.disabled = false; saveBtn.textContent = 'Сохранить'; }
      }
    },

    _openSkillDropdown(anchorBtn, skillObj){
      const sid = Number(skillObj?.id || 0);
      if (!sid) return;
      const wasExpanded = anchorBtn.getAttribute('aria-expanded') === 'true';
      this._closeActionDropdown();
      if (wasExpanded) return;

      const liked = this._likedIds.has(sid) || Boolean(skillObj?.liked_by_me);
      const likesCount = Number(skillObj?.likes_count || 0);
      const canEditOwn = Number(skillObj?.published_by_user_id || 0) === Number(this.app?.currentUser?.user_id || 0) || this._isAdmin;
      const selected = this._selectedIds.has(sid);
      const pinBlocked = !selected && this._isFull();

      const dd = document.createElement('div');
      dd.className = 'chat-item-dropdown skill-actions-dropdown';
      dd.innerHTML = `
        <button type="button" class="chat-item-dropdown-action" data-dd-act="pin"${pinBlocked ? ' disabled' : ''}>
          <span class="chat-item-dropdown-icon">${selected ? I.ok : I.pin}</span>
          <span class="chat-item-dropdown-text">${selected ? 'Отключить от чата' : pinBlocked ? `Лимит ${this._selectedLimit}/${this._selectedLimit}` : 'Подключить к чату'}</span>
        </button>
        <button type="button" class="chat-item-dropdown-action" data-dd-act="copy">
          <span class="chat-item-dropdown-icon">${I.send}</span>
          <span class="chat-item-dropdown-text">Вставить в ввод</span>
        </button>
        <button type="button" class="chat-item-dropdown-action${liked ? ' is-liked' : ''}" data-dd-act="like" data-liked="${liked ? 'true' : 'false'}" aria-pressed="${liked ? 'true' : 'false'}">
          <span class="chat-item-dropdown-icon">${liked ? ICON_HEART_FILLED : ICON_HEART_OUTLINE}</span>
          <span class="chat-item-dropdown-text">${liked ? 'Убрать из избранного' : 'В избранное'} (${likesCount})</span>
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
      window.AnchoredPopup.attach(dd, anchorBtn, {
        onClose: () => {
          if (this._openDropdown === dd) this._openDropdown = null;
        }
      });

      dd.addEventListener('click', async (e) => {
        e.stopPropagation();
        const btn = e.target.closest('[data-dd-act]');
        const act = btn?.dataset?.ddAct;
        if (!act || btn.disabled) return;
        this._closeActionDropdown();
        if (act === 'pin') {
          await this.toggleSelectedSkill(sid);
        } else if (act === 'like') {
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
        if (Array.isArray(data.liked_ids)) {
          this._likedIds = new Set(data.liked_ids.map(Number));
        } else if (typeof data.liked === 'boolean') {
          if (data.liked) this._likedIds.add(sid); else this._likedIds.delete(sid);
        }
        const isNowLiked = this._likedIds.has(sid);
        const finalCount = data.likes_count !== undefined ? Number(data.likes_count) : nextCount;
        if (itemInList) {
          itemInList.likes_count = finalCount;
          itemInList.liked_by_me = isNowLiked;
        }
        if (this._activeSkillId === sid && this._activeSkillData) {
          this._activeSkillData.likes_count = finalCount;
          this._activeSkillData.liked_by_me = isNowLiked;
          this._renderSkillDetail(this._activeSkillData);
        }
      } catch (e) {
        if (wasLiked) this._likedIds.add(sid); else this._likedIds.delete(sid);
      }
      if (this._filter === 'liked' && this._activeSkillId === null) {
        await this.load();
      } else {
        this._applyFilterSort();
      }
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
        if (Number(data?.limit) > 0) this._selectedLimit = Number(data.limit);
      } catch (_) {}
    },


    _updateSelectedCounterUi(){
      const nav = document.getElementById('skills-selected-nav-count');
      if (nav) nav.textContent = `${this._selectedIds.size || 0}/${this._selectedLimit}`;
      this._renderBar();
      this._renderFoot();
    },

    async toggleSelectedSkill(skillId, desiredState = null, checkboxEl = null){
      const sid = Number(skillId);
      if (!sid) return;

      const currentlySelected = this._selectedIds.has(sid);
      const nextSelected = desiredState !== null ? Boolean(desiredState) : !currentlySelected;

      if (nextSelected && !currentlySelected && this._selectedIds.size >= this._selectedLimit) {
        if (checkboxEl) checkboxEl.checked = false;
        this.app?.showToast?.(`Можно выбрать не более ${this._selectedLimit} скиллов`);
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
          this.app?.showToast?.(`Можно выбрать не более ${this._selectedLimit} скиллов`);
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

    _syncCheckboxesUi(skillId){
      const sid = Number(skillId);
      document.querySelectorAll('#skv-list [data-pin]').forEach(b => { b.outerHTML = this._qpHtml(Number(b.dataset.pin)); });
      if (this._activeSkillId === sid) this._updatePanePin();
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
        if (this._isEditingDetail) return;
        if (e.target.closest && e.target.closest('#message-input')) e.preventDefault();
      });
      document.addEventListener('drop', async (e)=>{
        if (this._isEditingDetail) return;
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
        if (this._isEditingDetail) return;
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
        if (this._isEditingDetail) return;
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
