/* ============================================================
   ANIMATIONS MODULE (window.ChatAnimations)
   Вынесенная логика canvas- и UI-анимаций:
   - стартовый экран (.chat-start-screen, #start-graph, .start-header)
   - плейсхолдер ответа агента (#agent-response-placeholder, .placeholder-graph)
   - уведомление «Общий чат» (.gc-nav-graph и мобильная 5px-полоса .gc-mobile-edge-graph)
============================================================ */
(function () {
  const ChatAnimations = {
    _placeholderRAF: null,
    _placeholderCleanup: null,
    _placeholderOwner: null,
    _startGraphRAF: null,
    _startGraphCleanup: null,
    _navGraphRAF: null,
    _edgeGraphRAF: null,

    parseAccent() {
      const cs = getComputedStyle(document.documentElement);
      let v = cs.getPropertyValue('--accent').trim() || '#6366f1';
      let r, gg, b;
      if (v.startsWith('#')) {
        if (v.length === 4) v = '#' + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
        r = parseInt(v.slice(1, 3), 16);
        gg = parseInt(v.slice(3, 5), 16);
        b = parseInt(v.slice(5, 7), 16);
      } else {
        const mm = v.match(/\d+/g) || [99, 102, 241];
        r = +mm[0];
        gg = +mm[1];
        b = +mm[2];
      }
      const clamp = (x) => Math.max(0, Math.min(255, Math.round(x)));
      const shade = (k) => `${clamp(r * k)},${clamp(gg * k)},${clamp(b * k)}`;
      return [`${r},${gg},${b}`, shade(1.18), shade(0.82)];
    },

    waveIndicatorSVG() {
      return '<div class="wave-indicator">'
           + '<canvas class="placeholder-graph" aria-hidden="true"></canvas>'
           + '</div>';
    },

    // owner — узел плейсхолдера, который просит остановку. Если анимацию уже
    // перехватил новый плейсхолдер (другой чат/ход), чужой стоп игнорируется.
    stopPlaceholderGraph(owner = null) {
      if (owner && this._placeholderOwner && owner !== this._placeholderOwner) return;
      this._placeholderOwner = null;
      if (this._placeholderRAF) {
        cancelAnimationFrame(this._placeholderRAF);
        this._placeholderRAF = null;
      }
      if (this._placeholderCleanup) {
        this._placeholderCleanup();
        this._placeholderCleanup = null;
      }
    },

    initPlaceholderGraph(placeholder) {
      this.stopPlaceholderGraph();
      const cv = placeholder?.querySelector('.placeholder-graph');
      if (!cv) return;
      this._placeholderOwner = placeholder;

      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const W = 116, H = 28, L = 40;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = W * dpr;
      cv.height = H * dpr;
      cv.style.width = W + 'px';
      cv.style.height = H + 'px';

      const g = cv.getContext('2d');
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);

      let COLS = this.parseAccent();
      const N = 11;
      const nodes = Array.from({ length: N }, (_, i) => ({
        x: 8 + (i / Math.max(1, N - 1)) * (W - 16) + (Math.random() - 0.5) * 6,
        y: 5 + Math.random() * (H - 10),
        vx: (Math.random() - 0.5) * 0.42 || 0.22,
        vy: (Math.random() - 0.5) * 0.34 || 0.18,
        r: 1.5 + (i % 3) * 0.55,
        c: COLS[i % 3],
      }));

      const m = { x: 0, y: 0, active: false };
      const onMove = (e) => { m.x = e.clientX; m.y = e.clientY; m.active = true; };
      const onLeave = () => { m.active = false; };
      document.addEventListener('pointermove', onMove, { passive: true });
      document.addEventListener('pointerleave', onLeave);
      this._placeholderCleanup = () => {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerleave', onLeave);
      };

      let frames = 0;
      const vk = reduce ? 0.55 : 1;
      const frame = () => {
        if (!placeholder.isConnected) {
          this.stopPlaceholderGraph(placeholder);
          return;
        }
        if ((frames & 63) === 0) {
          COLS = this.parseAccent();
          for (let i = 0; i < nodes.length; i++) nodes[i].c = COLS[i % 3];
        }
        frames++;

        g.clearRect(0, 0, W, H);
        const t = frames * 0.045;
        let cx = W * 0.5 + Math.sin(t) * (W * 0.38);
        let cy = H * 0.5 + Math.cos(t * 0.8) * (H * 0.26);
        if (m.active) {
          const rect = cv.getBoundingClientRect();
          const mx = m.x - rect.left;
          const my = m.y - rect.top;
          if (mx >= -40 && mx <= W + 40 && my >= -40 && my <= H + 40) {
            cx = mx;
            cy = my;
          }
        }

        const pad = 3.5;
        for (const n of nodes) {
          n.x += n.vx * vk;
          n.y += n.vy * vk;
          if (n.x < pad) { n.x = pad; n.vx = Math.abs(n.vx); }
          if (n.x > W - pad) { n.x = W - pad; n.vx = -Math.abs(n.vx); }
          if (n.y < pad) { n.y = pad; n.vy = Math.abs(n.vy); }
          if (n.y > H - pad) { n.y = H - pad; n.vy = -Math.abs(n.vy); }
        }

        const GR = 46;
        for (let i = 0; i < nodes.length; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j];
            const d = Math.hypot(a.x - b.x, a.y - b.y);
            if (d < L) {
              const near = Math.max(0, 1 - Math.hypot((a.x + b.x) / 2 - cx, (a.y + b.y) / 2 - cy) / GR);
              const alpha = (1 - d / L) * (0.28 + near * 0.55);
              g.strokeStyle = `rgba(${a.c}, ${alpha})`;
              g.lineWidth = 1 + near * 0.85;
              g.beginPath();
              g.moveTo(a.x, a.y);
              g.lineTo(b.x, b.y);
              g.stroke();
            }
          }
        }
        for (const n of nodes) {
          const near = Math.max(0, 1 - Math.hypot(n.x - cx, n.y - cy) / GR);
          g.fillStyle = `rgba(${n.c}, ${0.58 + near * 0.42})`;
          g.beginPath();
          g.arc(n.x, n.y, n.r + near * 1.15, 0, Math.PI * 2);
          g.fill();
        }

        this._placeholderRAF = requestAnimationFrame(frame);
      };
      this._placeholderRAF = requestAnimationFrame(frame);
    },

    flattenWaveIndicator(placeholder = null) {
      const root = placeholder || document.getElementById('agent-response-placeholder');
      const ind = root?.querySelector('.wave-indicator');
      if (!ind) return;
      ind.classList.add('wave-flatten');
    },

    animateStartHeader(container) {
      const header = container?.querySelector('.start-header');
      if (!header) return;

      const wrap = (el) => {
        el.childNodes.forEach((node) => {
          if (node.nodeType === Node.TEXT_NODE) {
            const frag = document.createDocumentFragment();
            [...node.textContent].forEach((ch) => {
              const s = document.createElement('span');
              s.className = 'letter';
              s.textContent = ch === ' ' ? '\u00A0' : ch;
              frag.appendChild(s);
            });
            node.replaceWith(frag);
          } else if (node.nodeType === Node.ELEMENT_NODE) {
            wrap(node);
          }
        });
      };

      header.querySelectorAll('.start-greeting, .start-title').forEach(wrap);

      let i = 0;
      header.querySelectorAll('.letter').forEach((s) => {
        s.style.transitionDelay = `${i * 28}ms`;
        i++;
      });

      requestAnimationFrame(() => {
        requestAnimationFrame(() => header.classList.add('animate'));
      });
    },

    stopStartGraph() {
      if (this._startGraphRAF) {
        cancelAnimationFrame(this._startGraphRAF);
        this._startGraphRAF = null;
      }
      if (this._startGraphCleanup) {
        this._startGraphCleanup();
        this._startGraphCleanup = null;
      }
    },

    initStartGraph(container) {
      this.stopStartGraph();

      const screen = container?.querySelector('.chat-start-screen');
      const cv = container?.querySelector('#start-graph');
      const body = container?.querySelector('.start-body');
      if (!screen || !cv || !body) return;

      requestAnimationFrame(() => screen.classList.add('bg-ready'));

      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const interactive = window.matchMedia('(hover: hover) and (pointer: fine)').matches && !reduce;

      const CONFIG = {
        nodes: 64,
        linkDist: 132,
        cursorRadius: 150,
        cursorForce: 0.9,
        glowRadius: 180,
        cursorMode: 'repel',
        cursorLerp: 0.12,
        graphParallax: 14,
        tilt: 2.5,
      };

      const g = cv.getContext('2d');
      let W = 0, H = 0, dpr = 1, nodes = [];
      const m = { x: 0, y: 0, sx: 0, sy: 0, active: false, seeded: false };

      let COLS = this.parseAccent();

      function resize() {
        const r = screen.getBoundingClientRect();
        if (r.width < 10 || r.height < 10) return;
        const prevW = W;
        const prevH = H;
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        W = Math.round(r.width);
        H = Math.round(r.height);
        cv.width = W * dpr;
        cv.height = H * dpr;
        cv.style.width = W + 'px';
        cv.style.height = H + 'px';
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        const target = Math.max(24, Math.min(90, Math.round((W * H) / (1440 * 900) * CONFIG.nodes)));
        if (!nodes.length) {
          nodes = Array.from({ length: target }, (_, i) => ({
            x: Math.random() * W,
            y: Math.random() * H,
            vx: (Math.random() - 0.5) * 0.35,
            vy: (Math.random() - 0.5) * 0.35,
            r: 1.4 + Math.random() * 1.4,
            c: COLS[i % 3],
          }));
        } else if (prevW > 10 && prevH > 10 && (prevW !== W || prevH !== H)) {
          const sx = W / prevW;
          const sy = H / prevH;
          for (const n of nodes) {
            n.x *= sx;
            n.y *= sy;
          }
        }
      }
      resize();

      const ro = new ResizeObserver(resize);
      ro.observe(screen);

      const onMove = (e) => {
        m.x = e.clientX;
        m.y = e.clientY;
        if (!m.seeded) { m.sx = m.x; m.sy = m.y; m.seeded = true; }
        m.active = true;
      };
      const onLeave = () => {
        m.active = false;
        body.style.setProperty('--rx', '0deg');
        body.style.setProperty('--ry', '0deg');
      };
      document.addEventListener('pointermove', onMove, { passive: true });
      document.addEventListener('pointerleave', onLeave);

      this._startGraphCleanup = () => {
        ro.disconnect();
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerleave', onLeave);
      };

      const drawGraph = () => {
        g.clearRect(0, 0, W, H);
        const L = CONFIG.linkDist, RR = CONFIG.cursorRadius, GR = CONFIG.glowRadius;
        const rect = screen.getBoundingClientRect();
        const cx = m.sx - rect.left, cy = m.sy - rect.top;
        const live = m.active;

        for (const n of nodes) {
          if (!reduce) {
            n.x += n.vx;
            n.y += n.vy;
            if (n.x < -20) n.x = W + 20;
            if (n.x > W + 20) n.x = -20;
            if (n.y < -20) n.y = H + 20;
            if (n.y > H + 20) n.y = -20;
            if (live) {
              const dx = n.x - cx, dy = n.y - cy, dist = Math.hypot(dx, dy);
              if (dist < RR && dist > 0.1) {
                const f = (1 - dist / RR) * CONFIG.cursorForce;
                n.x += (dx / dist) * f;
                n.y += (dy / dist) * f;
              }
            }
          }
        }

        for (let i = 0; i < nodes.length; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j];
            const d = Math.hypot(a.x - b.x, a.y - b.y);
            if (d < L) {
              const near = live ? Math.max(0, 1 - Math.hypot((a.x + b.x) / 2 - cx, (a.y + b.y) / 2 - cy) / GR) : 0;
              const alpha = (1 - d / L) * (0.26 + near * 0.5);
              g.strokeStyle = `rgba(${a.c}, ${alpha})`;
              g.lineWidth = 1 + near * 0.8;
              g.beginPath();
              g.moveTo(a.x, a.y);
              g.lineTo(b.x, b.y);
              g.stroke();
            }
          }
        }
        for (const n of nodes) {
          const near = live ? Math.max(0, 1 - Math.hypot(n.x - cx, n.y - cy) / GR) : 0;
          g.fillStyle = `rgba(${n.c}, ${0.55 + near * 0.45})`;
          g.beginPath();
          g.arc(n.x, n.y, n.r + near * 1.5, 0, Math.PI * 2);
          g.fill();
        }
      };

      let frames = 0;
      const frame = () => {
        if (!screen.isConnected) {
          this.stopStartGraph();
          return;
        }
        if (screen.offsetParent === null) {
          this._startGraphRAF = requestAnimationFrame(frame);
          return;
        }
        if (W < 10 || H < 10) {
          resize();
          if (W < 10 || H < 10) {
            this._startGraphRAF = requestAnimationFrame(frame);
            return;
          }
        }
        if (m.seeded) {
          m.sx += (m.x - m.sx) * CONFIG.cursorLerp;
          m.sy += (m.y - m.sy) * CONFIG.cursorLerp;
        }

        if (interactive && m.active) {
          const nx = (m.sx / window.innerWidth - 0.5) * 2;
          const ny = (m.sy / window.innerHeight - 0.5) * 2;
          const gp = CONFIG.graphParallax;
          cv.style.transform = `translate3d(${(-nx * gp).toFixed(1)}px, ${(-ny * gp).toFixed(1)}px, 0)`;

          const rect = body.getBoundingClientRect();
          const bx = (m.sx - (rect.left + rect.width / 2)) / (rect.width / 2);
          const by = (m.sy - (rect.top + rect.height / 2)) / (rect.height / 2);
          body.style.setProperty('--ry', (Math.max(-1, Math.min(1, bx)) * CONFIG.tilt).toFixed(2) + 'deg');
          body.style.setProperty('--rx', (-Math.max(-1, Math.min(1, by)) * CONFIG.tilt).toFixed(2) + 'deg');
          body.style.setProperty('--mx', ((m.sx - rect.left) / rect.width * 100).toFixed(1) + '%');
          body.style.setProperty('--my', ((m.sy - rect.top) / rect.height * 100).toFixed(1) + '%');
        }

        if ((frames++ & 63) === 0) {
          COLS = this.parseAccent();
          for (let i = 0; i < nodes.length; i++) nodes[i].c = COLS[i % 3];
        }
        drawGraph();
        this._startGraphRAF = requestAnimationFrame(frame);
      };
      this._startGraphRAF = requestAnimationFrame(frame);
    },

    stopNavGraph() {
      if (this._navGraphRAF) {
        cancelAnimationFrame(this._navGraphRAF);
        this._navGraphRAF = null;
      }
    },

    startNavGraph(navEl) {
      this.stopNavGraph();
      const cv = navEl?.querySelector('.gc-nav-graph');
      if (!cv) return;
      const rect = navEl.getBoundingClientRect();
      const W = Math.max(160, Math.round(rect.width || 220));
      const H = Math.max(32, Math.round(rect.height || 38));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = W * dpr;
      cv.height = H * dpr;
      cv.style.width = W + 'px';
      cv.style.height = H + 'px';

      const g = cv.getContext('2d');
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);

      const COLS = this.parseAccent();
      const N = 14;
      const L = 58;
      const nodes = Array.from({ length: N }, (_, i) => ({
        x: 6 + (i / Math.max(1, N - 1)) * (W - 12) + (Math.random() - 0.5) * 8,
        y: 4 + Math.random() * (H - 8),
        vx: (Math.random() - 0.5) * 0.52 || 0.26,
        vy: (Math.random() - 0.5) * 0.38 || 0.2,
        r: 1.4 + (i % 3) * 0.55,
        c: COLS[i % 3],
      }));

      let t = 0;
      const frame = () => {
        if (!navEl.isConnected || !navEl.classList.contains('gc-nav-notify')) {
          this.stopNavGraph();
          return;
        }
        t += 0.045;
        g.clearRect(0, 0, W, H);

        const sweepX = ((Math.sin(t) + 1) * 0.5) * W;
        const grad = g.createRadialGradient(sweepX, H * 0.5, 2, sweepX, H * 0.5, W * 0.55);
        grad.addColorStop(0, `rgba(${COLS[0]}, 0.22)`);
        grad.addColorStop(1, `rgba(${COLS[0]}, 0)`);
        g.fillStyle = grad;
        g.fillRect(0, 0, W, H);

        for (let i = 0; i < N; i++) {
          const n = nodes[i];
          n.x += n.vx;
          n.y += n.vy;
          if (n.x < 4 || n.x > W - 4) n.vx = -n.vx;
          if (n.y < 4 || n.y > H - 4) n.vy = -n.vy;
        }

        for (let i = 0; i < N; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < N; j++) {
            const b = nodes[j];
            const d = Math.hypot(a.x - b.x, a.y - b.y);
            if (d < L) {
              const alpha = (1 - d / L) * 0.52;
              g.strokeStyle = `rgba(${a.c}, ${alpha})`;
              g.lineWidth = 1.05;
              g.beginPath();
              g.moveTo(a.x, a.y);
              g.lineTo(b.x, b.y);
              g.stroke();
            }
          }
        }

        for (let i = 0; i < N; i++) {
          const n = nodes[i];
          g.fillStyle = `rgba(${n.c}, 0.88)`;
          g.beginPath();
          g.arc(n.x, n.y, n.r, 0, Math.PI * 2);
          g.fill();
        }

        this._navGraphRAF = requestAnimationFrame(frame);
      };
      this._navGraphRAF = requestAnimationFrame(frame);
    },

    stopMobileEdgeGraph() {
      if (this._edgeGraphRAF) {
        cancelAnimationFrame(this._edgeGraphRAF);
        this._edgeGraphRAF = null;
      }
    },

    startMobileEdgeGraph(edgeEl) {
      if (!edgeEl) return;
      edgeEl.classList.add('gc-edge-active');
    },

    stopMobileEdgeGraph() {
      const edgeEl = document.getElementById('gc-mobile-edge-notify');
      if (edgeEl) edgeEl.classList.remove('gc-edge-active', 'bg-ready');
    },
    /* ══════════════════════════════════════════════════════════════════════
       АУДИО-ПЛАШКИ В СТИЛЕ ГРАФА (03 Dot matrix — .fa-chip--audio.v-matrix)
    ══════════════════════════════════════════════════════════════════════ */
    _audioStates: [],
    _audioLoopRunning: false,
    _audioCtx: null,

    _PLAYABLE_AUDIO_EXTS: new Set([
      'mp3', 'mpga', 'wav', 'wave', 'ogg', 'oga', 'opus',
      'm4a', 'aac', 'flac', 'weba', 'caf', 'aif', 'aiff'
    ]),

    isPlayableAudioFile(fileOrName, mimeType) {
      let name = '';
      let ext = '';
      let mime = String(mimeType || '').toLowerCase();
      if (typeof fileOrName === 'string') {
        name = fileOrName;
      } else if (fileOrName && typeof fileOrName === 'object') {
        if (fileOrName.kind === 'audio' || fileOrName.is_audio) return true;
        name = fileOrName.name || fileOrName.file_name || '';
        ext = String(fileOrName.ext || '').toLowerCase();
        if (!mime && fileOrName.mime_type) mime = String(fileOrName.mime_type).toLowerCase();
      }
      if (!ext && name && name.includes('.')) {
        ext = name.split('.').pop().toLowerCase();
      }
      if (mime.startsWith('audio/')) return true;
      if (this._PLAYABLE_AUDIO_EXTS.has(ext)) return true;
      return false;
    },

    _escAttr(s) {
      return String(s || '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    },

    _getAudioIcons() {
      return {
        play: '<svg class="fa-glyph ic-play" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6.2 4.9a1.3 1.3 0 0 1 1.98-1.1l10.2 6.35a1.3 1.3 0 0 1 0 2.2L8.18 18.7a1.3 1.3 0 0 1-1.98-1.1V4.9Z" fill="currentColor" fill-opacity="0.28" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path><path d="M19.8 8.2a5.2 5.2 0 0 1 0 7.6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path></svg>',
        pause: '<svg class="fa-glyph ic-pause" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="6.25" y="4.75" width="4" height="14.5" rx="2" fill="currentColor" fill-opacity="0.28" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></rect><rect x="13.75" y="4.75" width="4" height="14.5" rx="2" fill="currentColor" fill-opacity="0.28" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></rect></svg>',
        dl: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></svg>'
      };
    },

    renderAudioChipHtml({ name = 'audio.mp3', url = '#', dlUrl = '' } = {}) {
      const safeName = this._escAttr(name);
      const safeUrl = this._escAttr(url);
      const safeDl = this._escAttr(dlUrl || url);
      const ic = this._getAudioIcons();
      return (
        `<div class="fa-chip fa-chip--gray fa-chip--bot fa-chip--audio v-matrix" data-audio-url="${safeUrl}" data-audio-name="${safeName}" style="--lvl: 0;">` +
          `<button class="fa-chip__icon fa-audio-play" type="button" aria-label="Воспроизвести"><svg class="fa-glyph ic-play" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7.4 5.2c0-1.15 1.26-1.85 2.24-1.24l10.1 6.3c.93.58.93 1.9 0 2.48l-10.1 6.3c-.98.61-2.24-.09-2.24-1.24V5.2Z" fill="currentColor"/></svg><svg class="fa-glyph ic-pause" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="6.2" y="5" width="4.2" height="14" rx="2.1" fill="currentColor"/><rect x="13.6" y="5" width="4.2" height="14" rx="2.1" fill="currentColor"/></svg></button>` +
          `<div class="fa-audio-body">` +
            `<div class="fa-audio-head">` +
              `<span class="fa-chip__name" title="${safeName}">${safeName}</span>` +
              `<span class="fa-audio-time"><b class="cur">0:00</b><span class="fa-audio-dur"> / 0:00</span></span>` +
            `</div>` +
            `<canvas class="fa-audio-viz" role="slider" tabindex="0" aria-label="Перемотка: ${safeName}" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0" aria-valuetext="0:00 из 0:00"></canvas>` +
          `</div>` +
          `<a class="fa-chip__download" href="${safeDl}" download="${safeName}" aria-label="Скачать ${safeName}">${ic.dl}</a>` +
        `</div>`
      );
    },

    createAudioChip({ name = 'audio.mp3', url = '#', dlUrl = '', onContextMenu = null } = {}) {
      const holder = document.createElement('div');
      holder.innerHTML = this.renderAudioChipHtml({ name, url, dlUrl });
      const chip = holder.firstElementChild;
      if (chip && typeof onContextMenu === 'function') {
        chip.addEventListener('contextmenu', onContextMenu);
      }
      if (chip) this.bindAudioChip(chip);
      return chip;
    },

    bindAudioChips(root) {
      const scope = root || document;
      scope.querySelectorAll('.fa-chip--audio:not([data-audio-bound="1"])').forEach(chip => {
        this.bindAudioChip(chip);
      });
    },

    _seedFromStr(str) {
      let h = 2166136261 >>> 0;
      const s = String(str || 'audio');
      for (let i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 16777619);
      }
      return ((h >>> 0) % 9000) / 1000 + 1.1;
    },

    _rng(seed) {
      let a = Math.floor(seed * 100000) >>> 0;
      return () => {
        a |= 0;
        a = (a + 0x6D2B79F5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
      };
    },

    _shape(n, seed) {
      const r = this._rng(seed);
      const p1 = r() * 6, p2 = r() * 6, p3 = r() * 6;
      const out = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const v = 0.5 + 0.5 * Math.sin(i * 0.35 + p1) * Math.sin(i * 0.11 + p2) + 0.25 * Math.sin(i * 0.9 + p3);
        out[i] = Math.min(1, Math.max(0.15, 0.15 + Math.abs(v) * 0.85));
      }
      return out;
    },

    _synthSpectrum(s, t, out) {
      const N = out.length;
      const sd = s.seed;
      const bps = 112 / 60;
      const beat = Math.pow(Math.max(0, Math.sin(t * Math.PI * bps)), 6);
      const phrase = 0.6 + 0.4 * Math.sin(t * 0.9 + sd);
      for (let i = 0; i < N; i++) {
        const f = i / (N - 1);
        const env = 1 - 0.62 * Math.pow(f, 0.6);
        const w1 = 0.5 + 0.5 * Math.sin(t * (3 + i * 0.55) + sd * (i + 1));
        const w2 = 0.5 + 0.5 * Math.sin(t * (7.3 + i * 0.21) + i * 1.7);
        const val = (env * (0.34 * w1 + 0.3 * w2) * phrase + beat * (1 - f) * 0.6) * 1.25;
        out[i] = Math.min(1, Math.max(0, val));
      }
    },

    _fmtAudioSec(sec) {
      const v = Number.isFinite(sec) && sec > 0 ? Math.max(0, Math.floor(sec)) : 0;
      return Math.floor(v / 60) + ':' + String(v % 60).padStart(2, '0');
    },

    _readAudioThemeColors() {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      const cols = this.parseAccent();
      const acc = Array.isArray(cols) ? cols[0] : String(cols || '99,102,241');
      const soft = isDark ? '162, 157, 192' : '107, 103, 133';
      return { acc, soft };
    },

    bindAudioChip(chip) {
      if (!chip || chip.dataset.audioBound === '1') return;
      chip.dataset.audioBound = '1';

      const url = chip.dataset.audioUrl || chip.querySelector('.fa-chip__download')?.getAttribute('href') || '';
      const name = chip.dataset.audioName || chip.querySelector('.fa-chip__name')?.textContent || 'audio';
      const viz = chip.querySelector('.fa-audio-viz');
      const playBtn = chip.querySelector('.fa-audio-play');
      const curEl = chip.querySelector('.cur');
      const durEl = chip.querySelector('.fa-audio-dur');
      if (!viz || !playBtn || !curEl) return;

      const N = 32;
      const seed = this._seedFromStr(name);
      const audio = new Audio();
      audio.preload = 'metadata';

      const s = {
        chip,
        name,
        url,
        seed,
        N,
        dur: 0,
        t: 0,
        playing: false,
        lvl: 0,
        scrub: false,
        hoverX: null,
        lastSec: -1,
        level: new Float32Array(N),
        target: new Float32Array(N),
        peak: new Float32Array(64),
        idle: this._shape(N, seed),
        freqBuf: new Uint8Array(N),
        audio,
        analyser: null,
        sourceConnected: false,
        w: 0,
        h: 0,
        ctx: viz.getContext('2d'),
        el: { chip, viz, play: playBtn, cur: curEl, dur: durEl }
      };

      const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

      const syncDuration = () => {
        let d = audio.duration;
        if (!Number.isFinite(d) || d <= 0) {
          if (audio.seekable && audio.seekable.length > 0) {
            const end = audio.seekable.end(audio.seekable.length - 1);
            if (Number.isFinite(end) && end > 0) d = end;
          }
        }
        if (Number.isFinite(d) && d > 0) {
          s.dur = d;
          if (s.el.dur) s.el.dur.textContent = ' / ' + this._fmtAudioSec(s.dur);
          s.el.viz.setAttribute('aria-valuemax', Math.floor(s.dur));
          updateTime(true);
        }
      };

      const updateTime = (force) => {
        const sec = Math.floor(s.t);
        if (force || sec !== s.lastSec) {
          s.lastSec = sec;
          const curStr = this._fmtAudioSec(s.t);
          const durStr = this._fmtAudioSec(s.dur);
          s.el.cur.textContent = curStr;
          if (s.el.dur) s.el.dur.textContent = ' / ' + durStr;
          s.el.viz.setAttribute('aria-valuenow', sec);
          s.el.viz.setAttribute('aria-valuetext', curStr + ' из ' + durStr);
        }
      };

      const connectAnalyser = () => {
        if (s.sourceConnected) return;
        try {
          const AudioCtx = window.AudioContext || window.webkitAudioContext;
          if (!AudioCtx) return;
          if (!this._audioCtx) this._audioCtx = new AudioCtx();
          if (this._audioCtx.state === 'suspended') {
            this._audioCtx.resume().catch(() => {});
          }
          const srcNode = this._audioCtx.createMediaElementSource(audio);
          const analyser = this._audioCtx.createAnalyser();
          analyser.fftSize = 64;
          analyser.smoothingTimeConstant = 0.72;
          srcNode.connect(analyser);
          analyser.connect(this._audioCtx.destination);
          s.analyser = analyser;
          s.freqBuf = new Uint8Array(analyser.frequencyBinCount);
          s.sourceConnected = true;
        } catch (_) {
          s.sourceConnected = true;
        }
      };

      const setPlaying = (val) => {
        if (val) {
          this._audioStates.forEach(o => {
            if (o !== s && o.playing) o.setPlaying(false);
          });
          connectAnalyser();
          if (this._audioCtx && this._audioCtx.state === 'suspended') {
            this._audioCtx.resume().catch(() => {});
          }
          if (s.dur > 0 && s.t >= s.dur - 0.05) {
            s.t = 0;
            try { audio.currentTime = 0; } catch (_) {}
          }
          s.playing = true;
          chip.classList.add('is-playing');
          playBtn.setAttribute('aria-label', 'Пауза');
          if (audio.src) {
            const p = audio.play();
            if (p && typeof p.catch === 'function') {
              p.catch((err) => {
                if (err && err.name === 'NotAllowedError') {
                  s.playing = false;
                  chip.classList.remove('is-playing');
                  playBtn.setAttribute('aria-label', 'Воспроизвести');
                }
              });
            }
          }
        } else {
          s.playing = false;
          chip.classList.remove('is-playing');
          playBtn.setAttribute('aria-label', 'Воспроизвести');
          try { audio.pause(); } catch (_) {}
        }
      };
      s.setPlaying = setPlaying;
      s.updateTime = updateTime;

      const seek = (targetSec) => {
        const maxDur = s.dur > 0 ? s.dur : (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0);
        const clamped = maxDur > 0 ? clamp(targetSec, 0, maxDur) : Math.max(0, targetSec);
        s.t = clamped;
        try {
          if (maxDur > 0) audio.currentTime = clamped;
        } catch (_) {}
        updateTime(true);
      };

      audio.addEventListener('loadedmetadata', syncDuration);
      audio.addEventListener('durationchange', syncDuration);
      audio.addEventListener('canplay', syncDuration);
      if (url && url !== '#') {
        audio.src = url;
      }
      if (audio.readyState >= 1) {
        syncDuration();
      }
      chip._audioState = s;
      audio.addEventListener('timeupdate', () => {
        if (!s.scrub && audio.getAttribute('src')) {
          s.t = audio.currentTime || 0;
          if (s.t > s.dur) {
            s.dur = s.t;
            if (s.el.dur) s.el.dur.textContent = ' / ' + this._fmtAudioSec(s.dur);
          }
          updateTime(false);
        }
      });
      audio.addEventListener('ended', () => {
        if (s.t > 0 && (!s.dur || s.t > s.dur)) s.dur = s.t;
        s.t = 0;
        setPlaying(false);
        try { audio.currentTime = 0; } catch (_) {}
        updateTime(true);
      });
      audio.addEventListener('pause', () => {
        if (s.playing && !audio.ended && !s.scrub) {
          s.playing = false;
          chip.classList.remove('is-playing');
          playBtn.setAttribute('aria-label', 'Воспроизвести');
        }
      });

      const syncCanvasSize = () => {
        const cw = viz.clientWidth || viz.getBoundingClientRect().width;
        const ch = viz.clientHeight || viz.getBoundingClientRect().height;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        if (cw > 0 && ch > 0 && (Math.abs(cw - s.w) > 0.5 || Math.abs(ch - s.h) > 0.5 || s.dpr !== dpr)) {
          s.w = cw;
          s.h = ch;
          s.dpr = dpr;
          viz.width = Math.max(1, Math.round(cw * dpr));
          viz.height = Math.max(1, Math.round(ch * dpr));
          s.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }
      };
      s.syncCanvasSize = syncCanvasSize;

      const ro = new ResizeObserver(syncCanvasSize);
      ro.observe(viz);
      s._ro = ro;

      playBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        setPlaying(!s.playing);
      });

      const dlLink = chip.querySelector('.fa-chip__download');
      if (dlLink) {
        dlLink.addEventListener('click', (e) => {
          e.stopPropagation();
        });
      }

      const fracFrom = (e) => {
        const r = viz.getBoundingClientRect();
        return clamp((e.clientX - r.left) / Math.max(1, r.width), 0, 1);
      };

      const localXFrom = (e) => {
        syncCanvasSize();
        const w = s.w || viz.clientWidth || 1;
        return fracFrom(e) * w;
      };

      viz.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        s.scrub = true;
        s.hoverX = localXFrom(e);
        try { viz.setPointerCapture(e.pointerId); } catch (_) {}
        if (s.dur > 0) seek(fracFrom(e) * s.dur);
      });

      viz.addEventListener('pointermove', (e) => {
        s.hoverX = localXFrom(e);
        if (s.scrub && s.dur > 0) seek(fracFrom(e) * s.dur);
      });

      const endScrub = () => { s.scrub = false; };
      viz.addEventListener('pointerup', endScrub);
      viz.addEventListener('pointercancel', endScrub);
      viz.addEventListener('pointerleave', () => { if (!s.scrub) s.hoverX = null; });
      viz.addEventListener('keydown', (e) => {
        const k = 5;
        if (e.key === 'ArrowRight') { seek(s.t + k); e.preventDefault(); }
        else if (e.key === 'ArrowLeft') { seek(s.t - k); e.preventDefault(); }
        else if (e.key === 'Home') { seek(0); e.preventDefault(); }
        else if (e.key === 'End' && s.dur > 0) { seek(s.dur); e.preventDefault(); }
        else if (e.key === ' ') { setPlaying(!s.playing); e.preventDefault(); }
      });

      updateTime(true);
      this._audioStates.push(s);
      this._ensureAudioLoop();
    },

    _drawAudioMatrix(ctx, s, w, h, dt, C) {
      const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
      const rgba = (c, a) => `rgba(${c}, ${a})`;
      const lv = (i, n) => s.level[Math.min(s.N - 1, Math.floor((i + 0.5) * s.N / n))];

      const mh = h - 6;
      const cols = Math.max(8, Math.floor(w / 8));
      const rows = 5;
      const cw = w / cols;
      const ch = mh / rows;
      const r = Math.min(cw, ch) * 0.2 + 0.5;
      const p = s.dur > 0 ? clamp(s.t / s.dur, 0, 1) : 0;

      for (let c = 0; c < cols; c++) {
        const l = clamp(lv(c, cols), 0, 1);
        const lit = Math.round(l * rows);
        const played = (c + 0.5) / cols <= p;
        s.peak[c] = Math.max(lit, (s.peak[c] || 0) - dt * 5);

        for (let k = 0; k < rows; k++) {
          const x = cw * (c + 0.5);
          const y = mh - ch * (k + 0.5);
          ctx.beginPath();
          ctx.arc(x, y, r, 0, Math.PI * 2);
          if (k < lit) {
            ctx.fillStyle = played ? rgba(C.acc, 0.55 + 0.45 * (k / rows)) : rgba(C.soft, 0.75);
          } else {
            ctx.fillStyle = rgba(C.soft, 0.16);
          }
          ctx.fill();
        }

        const pk = Math.round(s.peak[c]) - 1;
        if (pk >= lit && pk >= 0) {
          ctx.beginPath();
          ctx.arc(cw * (c + 0.5), mh - ch * (pk + 0.5), r * 1.05, 0, Math.PI * 2);
          ctx.fillStyle = rgba(C.acc, 1);
          ctx.fill();
        }
      }

      const x0 = 1;
      const x1 = w - 1;
      const px = x0 + p * (x1 - x0);
      ctx.lineWidth = 1.5;
      ctx.lineCap = 'round';
      ctx.strokeStyle = rgba(C.soft, 0.25);
      ctx.beginPath();
      ctx.moveTo(x0, h - 2);
      ctx.lineTo(x1, h - 2);
      ctx.stroke();

      ctx.strokeStyle = rgba(C.acc, 0.9);
      ctx.beginPath();
      ctx.moveTo(x0, h - 2);
      ctx.lineTo(px, h - 2);
      ctx.stroke();
      ctx.lineCap = 'butt';

      if (s.hoverX != null && s.dur > 0) {
        const hx = clamp(s.hoverX, 0, w);
        ctx.save();
        ctx.strokeStyle = rgba(C.acc, 0.5);
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 3]);
        ctx.beginPath();
        ctx.moveTo(hx, 0);
        ctx.lineTo(hx, h);
        ctx.stroke();
        ctx.restore();

        const tm = this._fmtAudioSec(clamp(hx / Math.max(1, w), 0, 1) * s.dur);
        ctx.save();
        ctx.font = '600 9.5px Inter, system-ui, sans-serif';
        const tw = ctx.measureText(tm).width + 8;
        const tx = clamp(hx - tw / 2, 0, w - tw);
        ctx.fillStyle = rgba(C.acc, 0.92);
        ctx.beginPath();
        if (typeof ctx.roundRect === 'function') {
          ctx.roundRect(tx, 0, tw, 13, 6.5);
        } else {
          ctx.rect(tx, 0, tw, 13);
        }
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(tm, tx + tw / 2, 6.8);
        ctx.restore();
      }
    },

    _ensureAudioLoop() {
      if (this._audioLoopRunning) return;
      this._audioLoopRunning = true;
      let prev = performance.now();
      const attack = 28;
      const release = 7;

      const step = (now) => {
        const dt = Math.min(0.05, (now - prev) / 1000);
        prev = now;
        const up = 1 - Math.exp(-dt * attack);
        const down = 1 - Math.exp(-dt * release);
        const C = this._readAudioThemeColors();

        for (let idx = this._audioStates.length - 1; idx >= 0; idx--) {
          const s = this._audioStates[idx];
          if (s.chip.isConnected) {
            s._wasConnected = true;
          } else if (s._wasConnected) {
            if (s.playing) {
              try { s.audio.pause(); } catch (_) {}
            }
            if (s._ro) {
              try { s._ro.disconnect(); } catch (_) {}
            }
            this._audioStates.splice(idx, 1);
            continue;
          } else {
            continue;
          }

          if (s.playing && !s.scrub) {
            if (!s.audio.paused) {
              s.t = s.audio.currentTime || 0;
              if (Number.isFinite(s.audio.duration) && s.audio.duration > 0) {
                s.dur = s.audio.duration;
              } else if (s.t > s.dur) {
                s.dur = s.t + 1;
              }
            } else {
              s.t += dt;
            }
          }

          if (s.playing) {
            let hasRealFreq = false;
            if (s.analyser) {
              s.analyser.getByteFrequencyData(s.freqBuf);
              let sum = 0;
              for (let i = 0; i < s.N; i++) {
                const v = (s.freqBuf[i] || 0) / 255;
                s.target[i] = v;
                sum += v;
              }
              hasRealFreq = sum > 0.02;
            }
            if (!hasRealFreq) {
              this._synthSpectrum(s, s.t || (now / 1000), s.target);
            }
          } else {
            for (let i = 0; i < s.N; i++) {
              s.target[i] = s.idle[i] * 0.42;
            }
          }

          let bass = 0;
          for (let i = 0; i < s.N; i++) {
            const k = s.target[i] > s.level[i] ? up : down;
            s.level[i] += (s.target[i] - s.level[i]) * k;
            if (i < 10) bass += s.level[i];
          }
          s.lvl = Math.min(1, Math.max(0, (bass / 10) * 1.5));
          s.chip.style.setProperty('--lvl', s.lvl.toFixed(3));
          s.updateTime(false);

          if (typeof s.syncCanvasSize === 'function') s.syncCanvasSize();
          if (s.w > 0 && s.h > 0) {
            s.ctx.clearRect(0, 0, s.w, s.h);
            this._drawAudioMatrix(s.ctx, s, s.w, s.h, dt, C);
          }
        }

        if (this._audioStates.length > 0) {
          requestAnimationFrame(step);
        } else {
          this._audioLoopRunning = false;
        }
      };

      requestAnimationFrame(step);
    },

  };

  window.ChatAnimations = ChatAnimations;
})();
