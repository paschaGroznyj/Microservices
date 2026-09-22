/* ============================================================================
 * chess.js — шахматный виджет в ленте чата (CatchPM main)
 * ----------------------------------------------------------------------------
 * Механика (вариант A, согласовано с Павлом):
 *   1. /chess [best] -> gateway ставит Redis-флаг + broadcast {type:"game_start"}.
 *      app.js ловит его в _onWsMessage и зовёт window.ChessWidget.start(...).
 *   2. В ленту монтируется island-плашка (стиль gc-pinned). Клик -> модалка с доской.
 *   3. Ход игрока (drag&drop) -> формируется текст "[CHESS_MOVE] {fen,player_move,engine}"
 *      и отправляется ШТАТНЫМ путём (app.sendMessageText) -> виден user-message.
 *   4. Ответ агента стримится; app.js в _finalizeStream вырезает блок chess_move JSON
 *      и передаёт его сюда через window.ChessWidget.applyServerMove(json).
 *   5. game_stop / checkmate / draw -> window.ChessWidget.stop().
 *
 * Доска и фигуры рисуются здесь. Стиль КЛЕТОК завязан на palette (chess.css,
 * var(--surface)/производная от --accent) и НЕ меняется день/ночь.
 * Движок фиксируется на партию (переключение силы недоступно посреди партии) —
 * показываем текущий движок иконкой, тумблер только визуальный индикатор.
 *
 * Зависит от app.js: window.App (инстанс), метод app.sendMessageText(text).
 * ==========================================================================*/
(function () {
  'use strict';

  // ── SVG фигур (стиль передан Павлом; wK..bP) ──────────────────────────────
  // Ключи: цвет(w|b) + тип(K,Q,R,B,N,P). Заливка/контур наследуют currentColor
  // через CSS-классы .cp-white / .cp-black (в chess.css), поэтому здесь чистые пути.
  const PIECES = {
    K: '<path d="M22.5 11.63V6M20 8h5M22.5 25s4.5-7.5 3-10.5c0 0-1-2.5-3-2.5s-3 2.5-3 2.5c-1.5 3 3 10.5 3 10.5" stroke-linecap="round"/><path d="M11.5 37c5.5 3.5 15.5 3.5 21 0v-7s9-4.5 6-10.5c-4-6.5-13.5-3.5-16 4V27v-3.5c-2.5-7.5-12-10.5-16-4-3 6 6 10.5 6 10.5v7z"/><path d="M11.5 30c5.5-3 15.5-3 21 0m-21 3.5c5.5-3 15.5-3 21 0m-21 3.5c5.5-3 15.5-3 21 0"/>',
    Q: '<path d="M9 26c8.5-1.5 21-1.5 27 0l2.5-12.5L31 25l-.3-14.1-5.2 13.6-3-14.5-3 14.5-5.2-13.6L14 25 6.5 13.5 9 26z"/><path d="M9 26c0 2 1.5 2 2.5 4 1 1.5 1 1 .5 3.5-1.5 1-1.5 2.5-1.5 2.5-1.5 1.5.5 2.5.5 2.5 6.5 1 16.5 1 23 0 0 0 1.5-1 0-2.5 0 0 .5-1.5-1-2.5-.5-2.5-.5-2 .5-3.5 1-2 2.5-2 2.5-4-8.5-1.5-18.5-1.5-27 0z"/><circle cx="6" cy="12" r="2"/><circle cx="14" cy="9" r="2"/><circle cx="22.5" cy="8" r="2"/><circle cx="31" cy="9" r="2"/><circle cx="39" cy="12" r="2"/>',
    R: '<path d="M9 39h27v-3H9v3zM12 36v-4h21v4H12zM11 14V9h4v2h5V9h5v2h5V9h4v5" stroke-linecap="round"/><path d="M34 14l-3 3H14l-3-3"/><path d="M31 17v12.5H14V17" stroke-linecap="round"/><path d="M31 29.5l1.5 2.5h-20l1.5-2.5"/><path d="M11 14h23" fill="none" stroke-linecap="round"/>',
    B: '<g stroke-linecap="round"><path d="M9 36c3.4-1 10.1.4 13.5-2 3.4 2.4 10.1 1 13.5 2 0 0 1.6.5 3 2-.7 1-1.6 1-3 .5-3.4-1-10.1.5-13.5-1-3.4 1.5-10.1 0-13.5 1-1.4.5-2.3.5-3-.5 1.4-2 3-2 3-2z"/><path d="M15 32c2.5 2.5 12.5 2.5 15 0 .5-1.5 0-2 0-2 0-2.5-2.5-4-2.5-4 5.5-1.5 6-11.5-5-15.5-11 4-10.5 14-5 15.5 0 0-2.5 1.5-2.5 4 0 0-.5.5 0 2z"/><path d="M25 8a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z"/></g><path d="M17.5 26h10M15 30h15m-7.5-14.5v5M20 18.5h5" fill="none" stroke-linecap="round"/>',
    N: '<path d="M22 10c10.5 1 16.5 8 16 29H15c0-9 10-6.5 8-21"/><path d="M24 18c.38 2.9-5.5 7.4-8 9-3 2-2.8 4.4-5 4-1.06-1 1.28-2.24 0-2-1 0 .19 1.2-1 2-1 0-4.01 1-4-4 0-2 6-12 6-12s1.9-1.9 2-3.5c-.7-1-.5-3-.5-3s.6-2 2-1c0 0 .74 3.5 2.5 4 2.13-1.5 5-3 5-3"/><path d="M9.5 25.5a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0zM15 15.5a.5 1.5 0 1 1-1 0 .5 1.5 0 0 1 1 0z" stroke="none"/>',
    P: '<path d="M22.5 9c-2.21 0-4 1.79-4 4 0 .89.29 1.71.78 2.38C17.33 16.5 16 18.59 16 21c0 2.03.94 3.84 2.41 5.03-3 1.06-7.41 5.55-7.41 13.47h23c0-7.92-4.41-12.41-7.41-13.47C28.06 24.84 29 23.03 29 21c0-2.41-1.33-4.5-3.28-5.62.49-.67.78-1.49.78-2.38 0-2.21-1.79-4-4-4z"/>',
  };

  const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
  const FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];

  const ENGINE_ICON = {
    // agent — «нейрон/мозг»
    agent: '<svg viewBox="0 0 24 24" fill="none" width="18" height="18"><path d="M12 3a3 3 0 0 0-3 3v.5A2.5 2.5 0 0 0 6.5 9 2.5 2.5 0 0 0 5 11.5 2.5 2.5 0 0 0 6.5 14 2.5 2.5 0 0 0 9 16.5V17a3 3 0 0 0 6 0v-.5a2.5 2.5 0 0 0 2.5-2.5 2.5 2.5 0 0 0 1.5-2.5A2.5 2.5 0 0 0 17.5 9 2.5 2.5 0 0 0 15 6.5V6a3 3 0 0 0-3-3z" stroke="currentColor" stroke-width="1.6"/><path d="M12 7v10M9 10h6M9 14h6" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    // best — «молния» (Stockfish в полную силу)
    best: '<svg viewBox="0 0 24 24" fill="none" width="18" height="18"><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  };
  const ENGINE_LABEL = { agent: 'Агент', best: 'Stockfish' };

  // Модель, форсируемая на время партии (agent-loop с execute_python_code).
  // 'designer' (лёгкая) роняет цикл на инъекции chess-промпта — берём 'coder'.
  const GAME_MODEL = 'coder';
  // Категория тулов, где живёт execute_python_code (нужен, чтобы сходить движком).
  const GAME_TOOL_CATEGORY = 'content';

  class ChessWidget {
    constructor() {
      this.active = false;
      this.mode = null;
      this.engine = 'agent';
      this.playerColor = 'white';   // 'white' | 'black'
      this.fen = START_FEN;
      this.board = null;            // 8x8 массив, [rank0=8..rank7=1][file]
      this.moveNo = 1;
      this.status = 'in_progress';
      this.hasMoved = false;        // сделан ли первый ход (после — движок фиксируется)
      this.selected = null;         // выбранная клетка при клике
      this.pendingFrom = null;      // источник хода в процессе drag
      this.waitingServer = false;   // ждём ответ агента (блок доски)
      this._islandEl = null;
      this._modalEl = null;
      this._boardEl = null;
    }

    // ── Публичный API (вызывается из app.js) ────────────────────────────────
    start({ mode = 'chess', engine = 'agent', player_color = 'white', fen, restore = false } = {}) {
      this.active = true;
      this.mode = mode;
      this.engine = engine === 'best' ? 'best' : 'agent';
      this.playerColor = player_color === 'black' ? 'black' : 'white';
      this.fen = fen || START_FEN;
      this.status = 'in_progress';
      // Номер хода из FEN — при restore партия могла уже идти (6-е поле FEN).
      const p = (this.fen || '').split(' ');
      this.moveNo = (p.length >= 6 && parseInt(p[5], 10)) || 1;
      this.waitingServer = false;
      // hasMoved: при рестарте после F5 определяем по FEN (не стартовая позиция
      // или ход чёрных / номер хода > 1 => партия уже начата, движок зафиксирован).
      this.hasMoved = restore ? (this.fen !== START_FEN) : false;
      this.board = this._parseFen(this.fen);

      // ИГРОВОЙ РЕЖИМ ТРЕБУЕТ execute_python_code (категория "content") и
      // тяжёлую модель — иначе agent-loop падает ("Ошибка внутри цикла агента"
      // при selected_tools:[] и model=designer). Форсим на время партии,
      // запоминая прежний выбор пользователя для отката в stop().
      this._forceGameEnv();

      this._renderIsland();
    }

    stop() {
      this.active = false;
      this._removeModal();
      this._removeIsland();
      // Возвращаем пользовательский выбор тулов/модели, который был до игры.
      this._restoreUserEnv();
    }

    // Форсит набор тулов + модель под игру, сохраняя прежнее состояние.
    _forceGameEnv() {
      try {
        const tp = window.ToolsPanel;
        // Запоминаем, была ли категория content выбрана пользователем.
        this._contentWasForced = tp && typeof tp.ensureCategory === 'function'
          ? tp.ensureCategory(GAME_TOOL_CATEGORY)   // true, если добавили (потом снять)
          : false;
      } catch (_) { this._contentWasForced = false; }
      try {
        const mp = window.ModelPicker;
        // GAME_MODEL — тяжёлая модель для игрового цикла (coder), сохраняем прежнюю.
        this._prevModel = mp && typeof mp.forceModel === 'function'
          ? mp.forceModel(GAME_MODEL)
          : null;
      } catch (_) { this._prevModel = null; }
    }

    _restoreUserEnv() {
      try {
        if (this._contentWasForced && window.ToolsPanel?.removeCategory) {
          window.ToolsPanel.removeCategory(GAME_TOOL_CATEGORY);
        }
      } catch (_) {}
      this._contentWasForced = false;
      try {
        if (this._prevModel && window.ModelPicker?.restoreModel) {
          window.ModelPicker.restoreModel(this._prevModel);
        }
      } catch (_) {}
      this._prevModel = null;
    }

    // Применить ход, вернувшийся от сервера (блок chess_move).
    // json — распарсенный объект контракта agent_apps.
    applyServerMove(json) {
      if (!json || !this.active) return;
      this.waitingServer = false;
      if (json.type === 'chess_error') {
        // Нелегальный ход: откатываем доску к серверному FEN (если есть),
        // иначе просто перерисовываем из текущего (ход не применён).
        this._flashError(json.error || 'illegal_move');
        if (json.fen) { this.fen = json.fen; this.board = this._parseFen(json.fen); }
        this._renderBoard();
        return;
      }
      if (json.fen) {
        this.fen = json.fen;
        this.board = this._parseFen(json.fen);
      }
      if (json.game_status) this.status = json.game_status;
      // Номер хода из FEN (последнее поле).
      const parts = (this.fen || '').split(' ');
      if (parts.length >= 6) this.moveNo = parseInt(parts[5], 10) || this.moveNo;
      this._renderBoard();
      this._renderIsland();  // обновить подпись/статус
      const finished = ['checkmate', 'stalemate', 'draw', 'insufficient_material',
        'seventyfive_moves', 'fivefold_repetition'].includes(this.status);
      if (finished) {
        this._flashStatus(this.status);
        // Плашку оставляем с финальным статусом; закрытие — по /stop или game_stop.
      }
    }

    isActive() { return this.active; }

    // ── ISLAND-плашка (стиль gc-pinned) ─────────────────────────────────────
    _renderIsland() {
      const container = document.querySelector('#view-agent .chat-messages')
        || document.querySelector('.chat-messages:not(.gc-messages)');
      if (!container) return;
      let el = document.getElementById('chess-island');
      if (!el) {
        el = document.createElement('div');
        el.id = 'chess-island';
        el.className = 'chess-island';
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.addEventListener('click', () => this._openModal());
        el.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._openModal(); }
        });
        // Плашка живёт над лентой в том же слое, что gc-pinned.
        const host = container.parentElement || container;
        host.appendChild(el);
        this._islandEl = el;
      }
      const colorTxt = this.playerColor === 'white' ? 'белыми' : 'чёрными';
      const statusTxt = this._statusLabel();
      el.innerHTML =
        '<span class="chess-island-ic">' + ENGINE_ICON[this.engine] + '</span>' +
        '<div class="chess-island-body">' +
        '  <div class="chess-island-title">Шахматы · ' + ENGINE_LABEL[this.engine] + '</div>' +
        '  <div class="chess-island-text">Ты играешь ' + colorTxt +
        (statusTxt ? ' · ' + statusTxt : '') + ' · нажми, чтобы открыть доску</div>' +
        '</div>' +
        '<span class="chess-island-open" aria-hidden="true">' +
        '  <svg viewBox="0 0 24 24" fill="none" width="18" height="18"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
        '</span>';
    }

    _removeIsland() {
      const el = document.getElementById('chess-island');
      if (el) el.remove();
      this._islandEl = null;
    }

    // ── МОДАЛКА с доской ─────────────────────────────────────────────────────
    _openModal() {
      if (this._modalEl) return;
      const overlay = document.createElement('div');
      overlay.className = 'chess-modal-overlay';
      overlay.id = 'chess-modal';
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) this._closeModal();
      });

      const modal = document.createElement('div');
      modal.className = 'chess-modal';

      // Шапка модалки: движок-переключатель + статус + кнопка закрыть
      const header = document.createElement('div');
      header.className = 'chess-modal-header';
      // Тумблер движка активен ТОЛЬКО до первого хода (для новой партии).
      // После первого хода / в процессе — фиксируется (по договорённости с Павлом).
      const toggleEnabled = this._canSwitchEngine();
      header.innerHTML =
        this._engineToggleHtml(toggleEnabled) +
        '<div class="chess-modal-status" id="chess-status">' + (this._statusLabel() || 'В игре') + '</div>' +
        '<button class="chess-modal-close" type="button" aria-label="Закрыть">' +
        '  <svg viewBox="0 0 20 20" fill="none" width="18" height="18"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
        '</button>';
      header.querySelector('.chess-modal-close').addEventListener('click', () => this._closeModal());
      const toggleBtn = header.querySelector('.chess-engine-toggle');
      if (toggleBtn && toggleEnabled) {
        toggleBtn.addEventListener('click', () => this._toggleEngine());
      }

      // Доска
      const boardWrap = document.createElement('div');
      boardWrap.className = 'chess-board-wrap';
      const board = document.createElement('div');
      board.className = 'chess-board';
      board.id = 'chess-board';
      boardWrap.appendChild(board);
      this._boardEl = board;

      // Футер: пара кнопок (сдаться / закрыть окно)
      const footer = document.createElement('div');
      footer.className = 'chess-modal-footer';
      footer.innerHTML =
        '<button class="chess-btn chess-btn--danger" type="button" data-act="resign">' +
        '  <svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M5 21V4M5 4h11l-2 4 2 4H5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
        '  <span>Сдаться</span>' +
        '</button>' +
        '<button class="chess-btn" type="button" data-act="close">' +
        '  <span>Свернуть доску</span>' +
        '</button>';
      footer.querySelector('[data-act="resign"]').addEventListener('click', () => this._resign());
      footer.querySelector('[data-act="close"]').addEventListener('click', () => this._closeModal());

      modal.appendChild(header);
      modal.appendChild(boardWrap);
      modal.appendChild(footer);
      overlay.appendChild(modal);
      document.body.appendChild(overlay);
      this._modalEl = overlay;

      this._renderBoard();
    }

    _closeModal() { this._removeModal(); }

    _removeModal() {
      if (this._modalEl) { this._modalEl.remove(); this._modalEl = null; }
      this._boardEl = null;
    }

    // ── РЕНДЕР ДОСКИ ─────────────────────────────────────────────────────────
    _renderBoard() {
      const board = this._boardEl || document.getElementById('chess-board');
      if (!board) return;
      board.innerHTML = '';
      const flip = this.playerColor === 'black';
      // ranks: сверху вниз. Для белых сверху rank8 (индекс 0). Для чёрных — flip.
      const rankOrder = flip ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];
      const fileOrder = flip ? [7, 6, 5, 4, 3, 2, 1, 0] : [0, 1, 2, 3, 4, 5, 6, 7];

      for (const r of rankOrder) {
        for (const f of fileOrder) {
          const sq = document.createElement('div');
          const isLight = (r + f) % 2 === 0;
          sq.className = 'chess-sq ' + (isLight ? 'chess-sq--light' : 'chess-sq--dark');
          const coord = FILES[f] + (8 - r);   // e.g. 'e2'
          sq.dataset.sq = coord;

          const piece = this.board[r][f];
          if (piece) {
            const isWhite = piece === piece.toUpperCase();
            const type = piece.toUpperCase();
            const span = document.createElement('span');
            span.className = 'chess-piece ' + (isWhite ? 'cp-white' : 'cp-black');
            span.innerHTML = '<svg viewBox="0 0 45 45">' + (PIECES[type] || '') + '</svg>';
            // Тащить можно только свои фигуры и только если не ждём сервер и партия идёт.
            const mine = (this.playerColor === 'white') === isWhite;
            if (mine && !this.waitingServer && this.status === 'in_progress') {
              span.setAttribute('draggable', 'true');
              span.addEventListener('dragstart', (e) => this._onDragStart(e, coord));
            }
            sq.appendChild(span);
          }

          sq.addEventListener('click', () => this._onSquareClick(coord));
          sq.addEventListener('dragover', (e) => { e.preventDefault(); sq.classList.add('chess-sq--over'); });
          sq.addEventListener('dragleave', () => sq.classList.remove('chess-sq--over'));
          sq.addEventListener('drop', (e) => { e.preventDefault(); sq.classList.remove('chess-sq--over'); this._onDrop(coord); });

          if (this.selected === coord) sq.classList.add('chess-sq--sel');
          board.appendChild(sq);
        }
      }
    }

    // ── DRAG & CLICK ─────────────────────────────────────────────────────────
    _onDragStart(e, coord) {
      if (this.waitingServer || this.status !== 'in_progress') { e.preventDefault(); return; }
      this.pendingFrom = coord;
      try { e.dataTransfer.setData('text/plain', coord); e.dataTransfer.effectAllowed = 'move'; } catch (_) {}
    }

    _onDrop(coord) {
      if (!this.pendingFrom) return;
      const from = this.pendingFrom;
      this.pendingFrom = null;
      if (from !== coord) this._commitMove(from, coord);
    }

    _onSquareClick(coord) {
      if (this.waitingServer || this.status !== 'in_progress') return;
      if (this.selected) {
        if (this.selected === coord) { this.selected = null; this._renderBoard(); return; }
        const [r, f] = this._sqToIdx(this.selected);
        const piece = this.board[r][f];
        if (piece) {
          const isWhite = piece === piece.toUpperCase();
          const mine = (this.playerColor === 'white') === isWhite;
          if (mine) { const from = this.selected; this.selected = null; this._commitMove(from, coord); return; }
        }
        this.selected = null; this._renderBoard();
      } else {
        const [r, f] = this._sqToIdx(coord);
        const piece = this.board[r][f];
        if (piece) {
          const isWhite = piece === piece.toUpperCase();
          const mine = (this.playerColor === 'white') === isWhite;
          if (mine) { this.selected = coord; this._renderBoard(); }
        }
      }
    }

    // Сформировать и отправить ход как обычное сообщение с маркером.
    _commitMove(from, to) {
      // Защита от дубля: пока ждём ответ сервера — второй ход не шлём.
      if (this.waitingServer || this.status !== 'in_progress') return;
      // Автопромоушен в ферзя (по договорённости — без модалки выбора фигуры).
      let uci = from + to;
      const [r, f] = this._sqToIdx(from);
      const piece = this.board[r][f];
      if (piece && piece.toUpperCase() === 'P') {
        const toRank = to[1];
        if ((piece === 'P' && toRank === '8') || (piece === 'p' && toRank === '1')) uci += 'q';
      }

      // Оптимистично двигаем фигуру локально (визуальный отклик), ждём подтверждения сервера.
      this.selected = null;
      this.waitingServer = true;
      this.hasMoved = true;   // первый ход сделан -> движок фиксируется
      this._renderBoard();
      this._refreshHeader();  // задизейблить тумблер движка

      const payload = { fen: this.fen, player_move: uci, engine: this.engine };
      const text = '[CHESS_MOVE] ' + JSON.stringify(payload);

      const app = window.App || window.app;
      if (app && typeof app.sendMessageText === 'function') {
        app.sendMessageText(text);
      } else {
        console.error('[chess] App.sendMessageText недоступен');
        this.waitingServer = false;
      }
    }

    // ── ДВИЖОК: переключатель (только до первого хода) ───────────────────────
    // Тумблер в стиле theme-toggle: две SVG-иконки (agent/best), активная —
    // текущий движок. Кликабелен ТОЛЬКО пока не сделан первый ход.
    _canSwitchEngine() {
      return !this.hasMoved && this.status === 'in_progress' && !this.waitingServer;
    }

    _engineToggleHtml(enabled) {
      const cls = 'chess-engine-toggle' + (enabled ? '' : ' chess-engine-toggle--locked');
      const title = enabled
        ? 'Сила соперника (сменить можно до первого хода)'
        : 'Движок зафиксирован на партию';
      const aOn = this.engine === 'agent' ? ' is-active' : '';
      const bOn = this.engine === 'best' ? ' is-active' : '';
      return '<button class="' + cls + '" type="button" title="' + title + '"' +
        (enabled ? '' : ' disabled') + ' aria-label="Переключить движок">' +
        '<span class="chess-eng-opt' + aOn + '" data-eng="agent">' + ENGINE_ICON.agent +
        '<span class="chess-eng-lbl">' + ENGINE_LABEL.agent + '</span></span>' +
        '<span class="chess-eng-opt' + bOn + '" data-eng="best">' + ENGINE_ICON.best +
        '<span class="chess-eng-lbl">' + ENGINE_LABEL.best + '</span></span>' +
        '</button>';
    }

    _toggleEngine() {
      if (!this._canSwitchEngine()) return;
      this.engine = this.engine === 'agent' ? 'best' : 'agent';
      // Персистим выбор на gateway, чтобы после F5 сила соперника сохранилась.
      try {
        fetch('/api/chats/' + encodeURIComponent(this._chatId()) + '/game_engine', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ engine: this.engine }),
        }).catch(() => {});
      } catch (_) {}
      // Перерисовать шапку модалки + плашку.
      this._refreshHeader();
      this._renderIsland();
    }

    _refreshHeader() {
      const header = this._modalEl && this._modalEl.querySelector('.chess-modal-header');
      if (!header) return;
      const old = header.querySelector('.chess-engine-toggle');
      if (!old) return;
      const enabled = this._canSwitchEngine();
      const tmp = document.createElement('div');
      tmp.innerHTML = this._engineToggleHtml(enabled);
      const fresh = tmp.firstChild;
      old.replaceWith(fresh);
      if (enabled) fresh.addEventListener('click', () => this._toggleEngine());
    }

    _chatId() {
      const app = window.App || window.app;
      return (app && app.currentChat) || '';
    }

    // Сдаться: закрываем режим через /stop-эквивалент на gateway.
    _resign() {
      const app = window.App || window.app;
      try {
        if (app && typeof app.sendMessageText === 'function') {
          app.sendMessageText('/stop');
        }
      } catch (_) {}
      this.stop();
    }

    // ── FEN / индексы ────────────────────────────────────────────────────────
    _parseFen(fen) {
      const rows = (fen || START_FEN).split(' ')[0].split('/');
      const board = [];
      for (let r = 0; r < 8; r++) {
        const row = [];
        for (const ch of (rows[r] || '8')) {
          if (/\d/.test(ch)) { for (let k = 0; k < parseInt(ch, 10); k++) row.push(''); }
          else row.push(ch);
        }
        while (row.length < 8) row.push('');
        board.push(row);
      }
      return board;
    }

    // 'e2' -> [rankIdx(0=8..7=1), fileIdx(0=a..7=h)]
    _sqToIdx(coord) {
      const f = FILES.indexOf(coord[0]);
      const rank = parseInt(coord[1], 10);
      const r = 8 - rank;
      return [r, f];
    }

    // ── Статусы / вспомогательное ────────────────────────────────────────────
    _statusLabel() {
      switch (this.status) {
        case 'checkmate': return 'Мат';
        case 'stalemate': return 'Пат';
        case 'draw':
        case 'insufficient_material':
        case 'seventyfive_moves':
        case 'fivefold_repetition': return 'Ничья';
        case 'check': return 'Шах';
        default: return this.waitingServer ? 'Ход соперника…' : '';
      }
    }

    _flashStatus(status) {
      const el = document.getElementById('chess-status');
      if (el) el.textContent = this._statusLabel() || status;
    }

    _flashError(err) {
      const el = document.getElementById('chess-status');
      if (el) {
        el.textContent = 'Нельзя так ходить';
        el.classList.add('chess-status--err');
        setTimeout(() => { el.classList.remove('chess-status--err'); el.textContent = this._statusLabel() || 'В игре'; }, 1600);
      }
    }
  }

  // Экспорт синглтона
  window.ChessWidget = new ChessWidget();
})();
