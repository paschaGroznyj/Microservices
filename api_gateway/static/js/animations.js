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

    stopPlaceholderGraph() {
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
          this.stopPlaceholderGraph();
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

    flattenWaveIndicator() {
      const ind = document.querySelector('#agent-response-placeholder .wave-indicator');
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
        dpr = Math.min(window.devicePixelRatio || 1, 2);
        W = Math.max(1, r.width);
        H = Math.max(1, r.height);
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
      this.stopMobileEdgeGraph();
      const cv = edgeEl?.querySelector('.gc-mobile-edge-graph');
      if (!cv) return;
      const W = 5;
      const H = Math.max(320, window.innerHeight || 640);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      cv.width = W * dpr;
      cv.height = H * dpr;
      cv.style.width = W + 'px';
      cv.style.height = H + 'px';

      const g = cv.getContext('2d');
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);

      const COLS = this.parseAccent();
      const N = 18;
      const L = 72;
      const nodes = Array.from({ length: N }, (_, i) => ({
        x: 1 + Math.random() * (W - 2),
        y: (i / Math.max(1, N - 1)) * H,
        vx: (Math.random() - 0.5) * 0.18,
        vy: (Math.random() - 0.5) * 1.15 || 0.65,
        r: 1.2 + (i % 2) * 0.5,
        c: COLS[i % 3],
      }));

      let t = 0;
      const frame = () => {
        if (!edgeEl.isConnected || !edgeEl.classList.contains('gc-edge-active')) {
          this.stopMobileEdgeGraph();
          return;
        }
        t += 0.055;
        g.clearRect(0, 0, W, H);

        const sweepY = ((Math.sin(t) + 1) * 0.5) * H;
        const grad = g.createLinearGradient(0, Math.max(0, sweepY - H * 0.35), 0, Math.min(H, sweepY + H * 0.35));
        grad.addColorStop(0, `rgba(${COLS[0]}, 0.08)`);
        grad.addColorStop(0.5, `rgba(${COLS[1]}, 0.72)`);
        grad.addColorStop(1, `rgba(${COLS[2]}, 0.08)`);
        g.fillStyle = grad;
        g.fillRect(0, 0, W, H);

        for (let i = 0; i < N; i++) {
          const n = nodes[i];
          n.x += n.vx;
          n.y += n.vy;
          if (n.x < 1 || n.x > W - 1) n.vx = -n.vx;
          if (n.y < 0) n.y = H;
          if (n.y > H) n.y = 0;
        }

        for (let i = 0; i < N; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < N; j++) {
            const b = nodes[j];
            const d = Math.abs(a.y - b.y);
            if (d < L) {
              const alpha = (1 - d / L) * 0.65;
              g.strokeStyle = `rgba(${a.c}, ${alpha})`;
              g.lineWidth = 1.1;
              g.beginPath();
              g.moveTo(a.x, a.y);
              g.lineTo(b.x, b.y);
              g.stroke();
            }
          }
        }

        for (let i = 0; i < N; i++) {
          const n = nodes[i];
          g.fillStyle = `rgba(${n.c}, 0.95)`;
          g.beginPath();
          g.arc(n.x, n.y, n.r, 0, Math.PI * 2);
          g.fill();
        }

        this._edgeGraphRAF = requestAnimationFrame(frame);
      };
      this._edgeGraphRAF = requestAnimationFrame(frame);
    },
  };

  window.ChatAnimations = ChatAnimations;
})();
