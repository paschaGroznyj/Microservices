(function () {
  var PAL = ['#539af8', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#6250d6'];
  var FONT = 'Inter,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif';
  var _measureCtx = null;

  function measureTextPx(str, fontSize, fontWeight) {
    var s = String(str == null ? '' : str);
    if (!s) return 0;
    var fs = Number(fontSize) || 13;
    var fw = fontWeight || 400;
    var approx = 0;
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if ('ШЩЖМЮЫФДmMwW@%'.indexOf(ch) !== -1) approx += fs * 0.78;
      else if ('іїjlI1.,:;|!()[] '.indexOf(ch) !== -1) approx += fs * 0.36;
      else if (ch >= 'А' && ch <= 'Я') approx += fs * 0.68;
      else approx += fs * 0.59;
    }
    try {
      if (!_measureCtx && typeof document !== 'undefined') {
        var cv = document.createElement('canvas');
        _measureCtx = cv.getContext('2d');
      }
      if (_measureCtx) {
        _measureCtx.font = fw + ' ' + fs + 'px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif';
        var m = _measureCtx.measureText(s).width * 1.06;
        return Math.max(m, approx);
      }
    } catch (_) {}
    return approx;
  }

  function truncText(str, maxPx, fontSize, fontWeight) {
    var s = String(str == null ? '' : str).trim();
    if (!s) return '';
    if (measureTextPx(s, fontSize, fontWeight) <= maxPx) return s;
    var ell = '...';
    var ellW = measureTextPx(ell, fontSize, fontWeight);
    var target = Math.max(12, maxPx - ellW);
    var lo = 1, hi = s.length, best = 1;
    while (lo <= hi) {
      var mid = (lo + hi) >> 1;
      var sub = s.slice(0, mid).replace(/[\s.,;:\-–—/]+$/, '');
      if (measureTextPx(sub, fontSize, fontWeight) <= target) {
        best = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return s.slice(0, best).replace(/[\s.,;:\-–—/]+$/, '') + ell;
  }
  var built = {};
  var blockSeq = 0;

  var STATUS = {
    success: 'Готово',
    processing: 'Обработка',
    error: 'Ошибка',
    warning: 'Внимание',
    danger: 'Ошибка'
  };

  var ICONS = {
    'files': 'M14 3v4a1 1 0 0 0 1 1h4M17 21h-10a2 2 0 0 1-2-2v-14a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z',
    'database': 'M4 6a8 3 0 1 0 16 0a8 3 0 1 0-16 0M4 6v6a8 3 0 0 0 16 0v-6M4 12v6a8 3 0 0 0 16 0v-6',
    'alert-triangle': 'M12 9v4M10.363 3.591l-8.106 13.534a1.914 1.914 0 0 0 1.636 2.871h16.214a1.914 1.914 0 0 0 1.636-2.871l-8.106-13.534a1.914 1.914 0 0 0-3.274 0zM12 16h.01',
    'users': 'M9 7m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0M3 21v-2a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v2M16 3.13a4 4 0 0 1 0 7.75M21 21v-2a4 4 0 0 0 -3 -3.85',
    'clock': 'M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0M12 7v5l3 3',
    'check': 'M5 12l5 5l10 -10',
    'activity': 'M3 12h4l3 8l4 -16l3 8h4',
    'trending-up': 'M3 17l6 -6l4 4l8 -8M14 7h7v7',
    'chart': 'M3 12m0 1a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1zM9 8m0 1a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1zM15 4m0 1a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1z',
    'folder': 'M5 4h4l3 3h7a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2v-11a2 2 0 0 1 2 -2',
    'shield': 'M12 3a12 12 0 0 0 8.5 3a12 12 0 0 1 -8.5 15a12 12 0 0 1 -8.5 -15a12 12 0 0 0 8.5 -3',
    'zap': 'M13 3l0 7l6 0l-8 11l0 -7l-6 0l8 -11',
    'default': 'M12 12m-8 0a8 8 0 1 0 16 0a8 8 0 1 0-16 0'
  };

  var DL = 'M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2M7 11l5 5l5-5M12 4v12';
  var CP = 'M8 8m0 2a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2zM16 8v-2a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function fmt(n) {
    var num = Number(n);
    if (Number.isNaN(num)) return String(n == null ? '' : n);
    return num.toLocaleString('ru-RU');
  }

  function isDarkTheme(el) {
    if (el && el.closest && el.closest('.ed-ai, [data-theme="dark"], .dark')) return true;
    var rootTheme = document.documentElement.getAttribute('data-theme');
    if (rootTheme === 'dark') return true;
    if (rootTheme === 'light') return false;
    if (document.body && document.body.getAttribute('data-theme') === 'dark') return true;
    return false;
  }

  function C(el) {
    var dark = isDarkTheme(el);
    if (dark) {
      return {
        dark: true,
        t1: 'rgba(255,255,255,0.93)',
        t2: 'rgba(255,255,255,0.57)',
        tm: 'rgba(255,255,255,0.40)',
        grid: 'rgba(255,255,255,0.14)',
        ring: 'rgba(255,255,255,0.08)',
        alpha1: 'rgba(255,255,255,0.055)',
        alpha2: 'rgba(255,255,255,0.09)',
        fillSec: 'rgba(255,255,255,0.15)',
        bg: '#303030',
        status: {
          success: { bg: '#233a1c', fg: '#9fd68a' },
          warning: { bg: '#3f3314', fg: '#f0cf74' },
          processing: { bg: '#3f3314', fg: '#f0cf74' },
          danger: { bg: '#442020', fg: '#f19a9a' },
          error: { bg: '#442020', fg: '#f19a9a' }
        }
      };
    }
    return {
      dark: false,
      t1: '#1a1612',
      t2: '#5c5650',
      tm: '#9e9890',
      grid: 'rgba(26,22,18,0.14)',
      ring: 'rgba(26,22,18,0.10)',
      alpha1: 'rgba(26,22,18,0.045)',
      alpha2: 'rgba(26,22,18,0.085)',
      fillSec: 'rgba(26,22,18,0.07)',
      bg: '#ffffff',
      status: {
        success: { bg: '#e3f2dc', fg: '#2b6a14' },
        warning: { bg: '#fbefc9', fg: '#7a5600' },
        processing: { bg: '#fbefc9', fg: '#7a5600' },
        danger: { bg: '#fbe1e1', fg: '#a42a2b' },
        error: { bg: '#fbe1e1', fg: '#a42a2b' }
      }
    };
  }

  function ticks(max) {
    max = Number(max) || 1;
    if (max <= 0) max = 1;
    var raw = max / 4;
    var p = Math.pow(10, Math.floor(Math.log10(raw)));
    var m = raw / p;
    var s = (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
    var n = Math.ceil(max / s);
    var t = [];
    for (var i = 0; i <= n; i++) t.push(+(i * s).toFixed(6));
    return { t: t, max: n * s };
  }

  function legendSvg(items, c, y) {
    var x = 52;
    var maxItemW = Math.max(80, Math.floor(520 / Math.max(1, items.length)) - 28);
    return items.map(function (it) {
      var shortLbl = truncText(it.label, maxItemW, 12, 400);
      var tipAttr = shortLbl !== String(it.label || '') ? ' data-tip="' + esc(it.label) + '"' : '';
      var g = '<rect x="' + x + '" y="' + (y - 9) + '" width="10" height="10" rx="2" fill="' + it.color + '"/>' +
        '<text x="' + (x + 15) + '" y="' + y + '" font-size="12" fill="' + c.t2 + '"' + tipAttr + '>' + esc(shortLbl) + '</text>';
      x += Math.ceil(measureTextPx(shortLbl, 12, 400)) + 32;
      return g;
    }).join('');
  }

  function pie(spec, c) {
    var W = 600, H = 340, cx = 170, cy = 170, r = 115, cfg = spec.config || {};
    var d = Array.isArray(spec.data) ? spec.data : [];
    var tot = d.reduce(function (s, x) { return s + (Number(x.value) || 0); }, 0) || 1;
    var a0 = -Math.PI / 2, o = '';
    d.forEach(function (it, i) {
      var val = Number(it.value) || 0;
      var f = val / tot, a1 = a0 + f * Math.PI * 2;
      var x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      var x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      var large = f > .5 ? 1 : 0, pc = Math.round(f * 100);
      if (f >= 0.9999) {
        o += '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="' + PAL[i % PAL.length] + '" stroke="' + c.bg + '" stroke-width="2" data-tip="' + esc(it.label) + '\n' + fmt(val) + ' (' + pc + '%)"/>';
      } else if (f > 0) {
        o += '<path d="M' + cx + ',' + cy + ' L' + x0 + ',' + y0 + ' A' + r + ',' + r + ' 0 ' + large + ' 1 ' + x1 + ',' + y1 + ' Z" fill="' + PAL[i % PAL.length] + '" stroke="' + c.bg + '" stroke-width="2" stroke-linejoin="round" data-tip="' + esc(it.label) + '\n' + fmt(val) + ' (' + pc + '%)"/>';
      }
      if (cfg.showPercentage && pc >= 8) {
        var am = (a0 + a1) / 2;
        o += '<text x="' + (cx + r * .62 * Math.cos(am)) + '" y="' + (cy + r * .62 * Math.sin(am) + 4) + '" text-anchor="middle" font-size="13" font-weight="500" fill="#fff" pointer-events="none">' + pc + '%</text>';
      }
      a0 = a1;
    });
    if (cfg.showLegend !== false) {
      var step = d.length > 6 ? Math.floor(240 / d.length) : 34;
      var startY = d.length > 4 ? Math.max(40, Math.round(170 - ((d.length - 1) * step) / 2)) : 130;
      var lblX = 368, valEndX = 582, minGap = 14;
      d.forEach(function (it, i) {
        var val = Number(it.value) || 0;
        var pc = Math.round((val / tot) * 100);
        var valStr = fmt(val) + ' · ' + pc + '%';
        var valW = measureTextPx(valStr, 13, 400);
        var maxLblPx = Math.max(60, valEndX - lblX - valW - minGap);
        var fullLbl = String(it.label == null ? '' : it.label);
        var shortLbl = truncText(fullLbl, maxLblPx, 13, 400);
        var tipAttr = ' data-tip="' + esc(fullLbl) + '\n' + fmt(val) + ' (' + pc + '%)"';
        var y = startY + i * step;
        o += '<rect x="350" y="' + (y - 5) + '" width="10" height="10" rx="2" fill="' + PAL[i % PAL.length] + '"' + tipAttr + '/>';
        o += '<text x="' + lblX + '" y="' + (y + 4) + '" font-size="13" fill="' + c.t1 + '"' + tipAttr + '>' + esc(shortLbl) + '</text>';
        o += '<text x="' + valEndX + '" y="' + (y + 4) + '" font-size="13" text-anchor="end" fill="' + c.t2 + '">' + esc(valStr) + '</text>';
      });
    }
    return { w: W, h: H, inner: o };
  }

  function cart(spec, c) {
    var W = 600, H = 340, cfg = spec.config || {}, type = String(spec.type || 'bar').toLowerCase();
    var xa = spec.xAxis || {}, ya = spec.yAxis || {};
    var rows = Array.isArray(spec.data) ? spec.data : [];
    var S = Array.isArray(spec.series) && spec.series.length ? spec.series : [];
    if (!S.length && rows.length) {
      var first = rows[0] || {};
      var keys = Object.keys(first).filter(function (k) {
        return k !== xa.key && k !== 'id' && typeof first[k] === 'number';
      });
      if (type === 'scatter' && keys.length >= 2) {
        S = [{ key: 'items', label: spec.title || 'Данные', xKey: xa.key || keys[0], yKey: ya.key || keys[1] }];
      } else {
        S = keys.map(function (k) { return { key: k, label: k }; });
      }
    }
    var leg = S.length > 1 && cfg.showLegend !== false;
    var m = { l: ya.label ? 68 : 52, r: 22, t: leg ? 40 : 18, b: xa.label ? 56 : 36 };
    var pw = W - m.l - m.r, ph = H - m.t - m.b, o = '', unitY = ya.unit || '', unitX = xa.unit || '';
    var xk = (S[0] && S[0].xKey) || xa.key;
    var yk = (S[0] && S[0].yKey) || ya.key || (S[0] && S[0].key);
    var max = 0;
    rows.forEach(function (row) {
      S.forEach(function (s) {
        var v = Number(type === 'scatter' ? row[s.yKey || yk] : row[s.key]) || 0;
        if (v > max) max = v;
      });
    });
    if (ya.max != null) max = Number(ya.max);
    var ty = ticks(max), ymax = (ya.max != null ? Number(ya.max) : ty.max) || 1;
    function Y(v) { return m.t + ph - ((Number(v) || 0) / ymax) * ph; }
    if (leg) {
      o += legendSvg(S.map(function (s, i) { return { label: s.label || s.key, color: PAL[i % PAL.length] }; }), c, 18);
    }
    (ya.max != null ? ticks(ya.max).t.filter(function (t) { return t <= ya.max; }) : ty.t).forEach(function (t) {
      if (cfg.showGrid !== false || t === 0) {
        o += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + Y(t) + '" y2="' + Y(t) + '" stroke="' + c.grid + '" stroke-dasharray="' + (t === 0 ? '0' : '3 3') + '"/>';
      }
      o += '<text x="' + (m.l - 8) + '" y="' + (Y(t) + 4) + '" text-anchor="end" font-size="12" fill="' + c.tm + '">' + fmt(t) + (unitY === '%' ? '%' : '') + '</text>';
    });
    if (ya.label) {
      o += '<text transform="translate(18,' + (m.t + ph / 2) + ') rotate(-90)" text-anchor="middle" font-size="12" fill="' + c.t2 + '">' + esc(ya.label + (unitY && unitY !== '%' ? ', ' + unitY : '')) + '</text>';
    }
    if (xa.label) {
      o += '<text x="' + (m.l + pw / 2) + '" y="' + (H - 10) + '" text-anchor="middle" font-size="12" fill="' + c.t2 + '">' + esc(xa.label + (unitX ? ', ' + unitX : '')) + '</text>';
    }

    if (type === 'scatter') {
      var mx = 0;
      rows.forEach(function (r) { var vx = Number(r[xk]) || 0; if (vx > mx) mx = vx; });
      var tx = ticks(mx);
      function X(v) { return m.l + ((Number(v) || 0) / (tx.max || 1)) * pw; }
      tx.t.forEach(function (t) {
        o += '<text x="' + X(t) + '" y="' + (m.t + ph + 18) + '" text-anchor="middle" font-size="12" fill="' + c.tm + '">' + fmt(t) + '</text>';
      });
      if (cfg.showTrendLine && rows.length >= 2) {
        var nPts = rows.length, sx = 0, sy = 0, sxy = 0, sxx = 0;
        rows.forEach(function (r) {
          var vx = Number(r[xk]) || 0, vy = Number(r[yk]) || 0;
          sx += vx; sy += vy; sxy += vx * vy; sxx += vx * vx;
        });
        var denom = nPts * sxx - sx * sx;
        if (Math.abs(denom) > 1e-9) {
          var slope = (nPts * sxy - sx * sy) / denom;
          var inter = (sy - slope * sx) / nPts;
          var xMin = 0, xMax = tx.max;
          var y0 = Math.max(0, Math.min(ymax, slope * xMin + inter));
          var y1 = Math.max(0, Math.min(ymax, slope * xMax + inter));
          o += '<line x1="' + X(xMin) + '" y1="' + Y(y0) + '" x2="' + X(xMax) + '" y2="' + Y(y1) + '" stroke="' + PAL[1] + '" stroke-width="1.5" stroke-dasharray="5 4" opacity="0.75"/>';
        }
      }
      rows.forEach(function (r) {
        var tipTitle = r.id || r.label || r.name || '';
        var tipBody = fmt(r[xk]) + (unitX ? ' ' + esc(unitX) : '') + ', ' + fmt(r[yk]) + (unitY ? ' ' + esc(unitY) : '');
        var tipText = tipTitle ? (esc(tipTitle) + '\n' + tipBody) : tipBody;
        o += '<circle cx="' + X(r[xk]) + '" cy="' + Y(r[yk]) + '" r="6" fill="' + PAL[0] + '" stroke="' + c.bg + '" stroke-width="2" data-tip="' + tipText + '"/>';
      });
      return { w: W, h: H, inner: o };
    }

    var n = Math.max(1, rows.length), band = pw / n;
    var xKey = xa.key || Object.keys(rows[0] || {})[0] || 'label';
    rows.forEach(function (r, i) {
      var lab = String(r[xKey] != null ? r[xKey] : '');
      var shortXLbl = truncText(lab, Math.max(36, band - 8), 12, 400);
      var xTip = shortXLbl !== lab ? ' data-tip="' + esc(lab) + '"' : '';
      o += '<text x="' + (m.l + band * (i + .5)) + '" y="' + (m.t + ph + 18) + '" text-anchor="middle" font-size="12" fill="' + c.tm + '"' + xTip + '>' + esc(shortXLbl) + '</text>';
    });
    if (type === 'bar') {
      var sCount = Math.max(1, S.length);
      var groupW = Math.min(band * .68, sCount * 48);
      var bw = Math.min(groupW / sCount - (sCount > 1 ? 3 : 0), 48);
      S.forEach(function (s, si) {
        rows.forEach(function (r, i) {
          var v = Number(r[s.key]) || 0;
          var h = Math.max(0, (v / ymax) * ph);
          var groupStart = m.l + band * (i + .5) - (sCount * bw + (sCount - 1) * 3) / 2;
          var x = sCount === 1 ? (m.l + band * (i + .5) - bw / 2) : (groupStart + si * (bw + 3));
          var y = m.t + ph - h, rr = Math.min(4, h), col = PAL[si % PAL.length];
          o += '<path d="M' + x + ',' + (m.t + ph) + ' V' + (y + rr) + ' Q' + x + ',' + y + ' ' + (x + rr) + ',' + y + ' H' + (x + bw - rr) + ' Q' + (x + bw) + ',' + y + ' ' + (x + bw) + ',' + (y + rr) + ' V' + (m.t + ph) + ' Z" fill="' + col + '" data-tip="' + esc(r[xKey]) + '\n' + esc(s.label || s.key) + ': ' + fmt(v) + '"/>';
          if (cfg.showValues) {
            o += '<text x="' + (x + bw / 2) + '" y="' + (y - 6) + '" text-anchor="middle" font-size="12" font-weight="500" fill="' + c.t1 + '">' + fmt(v) + '</text>';
          }
        });
      });
      return { w: W, h: H, inner: o };
    }

    S.forEach(function (s, si) {
      var col = PAL[si % PAL.length];
      var pts = rows.map(function (r, i) { return [m.l + band * (i + .5), Y(r[s.key]), r]; });
      if (!pts.length) return;
      o += '<path d="' + pts.map(function (p, i) { return (i ? 'L' : 'M') + p[0] + ',' + p[1]; }).join('') + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"' + (si > 0 ? ' stroke-dasharray="6 4"' : '') + '/>';
      if (cfg.showDots !== false) {
        pts.forEach(function (p) {
          o += '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="4.5" fill="' + col + '" stroke="' + c.bg + '" stroke-width="2" data-tip="' + esc(p[2][xKey]) + '\n' + esc(s.label || s.key) + ': ' + fmt(p[2][s.key]) + esc(unitY === '%' ? '%' : '') + '"/>';
        });
      }
    });
    return { w: W, h: H, inner: o };
  }

  function chart(spec, el) {
    var c = C(el);
    return String(spec.type || '').toLowerCase() === 'pie' ? pie(spec, c) : cart(spec, c);
  }

  function slug(t) {
    return String(t || 'block').toLowerCase().replace(/[^a-zа-яё0-9]+/gi, '-').replace(/^-|-$/g, '') || 'block';
  }

  function ensureOverlays() {
    var tip = document.getElementById('gen-ui-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'gen-ui-tip';
      tip.setAttribute('data-export-ignore', '');
      document.body.appendChild(tip);
    }
    var toastEl = document.getElementById('gen-ui-toast');
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'gen-ui-toast';
      toastEl.setAttribute('data-export-ignore', '');
      document.body.appendChild(toastEl);
    }
    return { tip: tip, toast: toastEl };
  }

  function toast(t) {
    var ov = ensureOverlays();
    var el = ov.toast;
    el.textContent = t;
    el.classList.add('on');
    clearTimeout(toast.t);
    toast.t = setTimeout(function () { el.classList.remove('on'); }, 1800);
  }

  function save(blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }

  function wrapLines(text, maxChars) {
    var raw = String(text || '').trim();
    if (!raw) return [];
    var words = raw.split(/\s+/);
    var lines = [];
    var cur = '';
    for (var i = 0; i < words.length; i++) {
      var w = words[i];
      if (!cur) {
        cur = w;
      } else if ((cur + ' ' + w).length <= maxChars) {
        cur += ' ' + w;
      } else {
        lines.push(cur);
        cur = w;
      }
    }
    if (cur) lines.push(cur);
    return lines;
  }

  function buildHeaderSvg(spec, c, P, W) {
    var titleLines = wrapLines(spec.title || '', Math.floor((W - P * 2) / 8.8));
    var descLines = spec.description ? wrapLines(spec.description, Math.floor((W - P * 2) / 7.2)) : [];
    var y = P + 16;
    var svg = '';
    titleLines.forEach(function (ln, idx) {
      svg += '<text x="' + P + '" y="' + y + '" font-size="16" font-weight="600" fill="' + c.t1 + '">' + esc(ln) + '</text>';
      y += idx === titleLines.length - 1 ? 6 : 21;
    });
    if (descLines.length) {
      y += 14;
      descLines.forEach(function (ln, idx) {
        svg += '<text x="' + P + '" y="' + y + '" font-size="13.5" fill="' + c.t2 + '">' + esc(ln) + '</text>';
        y += idx === descLines.length - 1 ? 6 : 18;
      });
    }
    return { svg: svg, height: y + 10 };
  }

  function wrapCardSvg(W, H, c, innerSvg) {
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" font-family="' + FONT + '">' +
      '<rect width="' + W + '" height="' + H + '" rx="20" fill="' + c.bg + '"/>' +
      '<rect x="0.5" y="0.5" width="' + (W - 1) + '" height="' + (H - 1) + '" rx="19.5" fill="none" stroke="' + c.ring + '" stroke-width="1"/>' +
      innerSvg +
      '</svg>';
  }

  function buildFullSvgString(spec, idx, sec) {
    var comp = String(spec.component || 'Chart');
    var c = C(sec);
    var P = 24;

    if (comp === 'MetricCards') {
      var W = 680;
      var head = buildHeaderSvg(spec, c, P, W);
      var items = Array.isArray(spec.items) ? spec.items : [];
      var cols = Math.min(3, Math.max(1, (spec.layout && Number(spec.layout.columns)) || Math.min(3, items.length || 1)));
      var gap = 10;
      var cardW = Math.floor((W - P * 2 - gap * (cols - 1)) / cols);
      var rowsCount = Math.ceil(items.length / cols);
      var curY = head.height + 4;
      var cardsSvg = '';

      for (var r = 0; r < rowsCount; r++) {
        var rowItems = items.slice(r * cols, (r + 1) * cols);
        var maxDescLines = 0;
        rowItems.forEach(function (it) {
          var dl = it.description ? wrapLines(it.description, Math.floor((cardW - 28) / 6.6)).length : 0;
          if (dl > maxDescLines) maxDescLines = dl;
        });
        var cardH = 84 + (maxDescLines > 0 ? maxDescLines * 16 + 4 : 0);

        rowItems.forEach(function (it, ci) {
          var cx = P + ci * (cardW + gap);
          var cy = curY;
          var stKey = String(it.status || '').toLowerCase();
          var stTone = c.status[stKey] || null;
          var cardFill = c.alpha1;
          var valFill = c.t1;
          var lblFill = stTone ? stTone.fg : c.t2;

          cardsSvg += '<rect x="' + cx + '" y="' + cy + '" width="' + cardW + '" height="' + cardH + '" rx="12" fill="' + cardFill + '"/>';
          var iconPath = ICONS[it.icon] || ICONS['default'];
          cardsSvg += '<g transform="translate(' + (cx + 14) + ',' + (cy + 14) + ') scale(0.6667)" fill="none" stroke="' + lblFill + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="' + iconPath + '"/></g>';
          cardsSvg += '<text x="' + (cx + 36) + '" y="' + (cy + 26) + '" font-size="13" fill="' + lblFill + '">' + esc(truncText(it.label || '', cardW - 48, 13, 400)) + '</text>';
          cardsSvg += '<text x="' + (cx + 14) + '" y="' + (cy + 58) + '" font-size="24" font-weight="500" fill="' + valFill + '">' + esc(fmt(it.value)) + '</text>';

          if (it.description) {
            var dLines = wrapLines(it.description, Math.floor((cardW - 28) / 6.6));
            dLines.forEach(function (dln, di) {
              cardsSvg += '<text x="' + (cx + 14) + '" y="' + (cy + 78 + di * 16) + '" font-size="12" fill="' + c.tm + '">' + esc(dln) + '</text>';
            });
          }
        });
        curY += cardH + gap;
      }

      var totalH = Math.max(140, curY - gap + P);
      return wrapCardSvg(W, totalH, c, head.svg + cardsSvg);
    }

    if (comp === 'DataTable') {
      var W = 680;
      var head = buildHeaderSvg(spec, c, P, W);
      var cols = Array.isArray(spec.columns) ? spec.columns : [];
      var rawData = Array.isArray(spec.data) ? spec.data : [];
      if (!cols.length && rawData.length) {
        cols = Object.keys(rawData[0]).map(function (k) { return { key: k, label: k, type: typeof rawData[0][k] === 'number' ? 'number' : 'text' }; });
      }
      var colCount = Math.max(1, cols.length);
      var tableW = W - P * 2;
      var colW = tableW / colCount;
      var y = head.height + 6;
      var tSvg = '';

      tSvg += '<line x1="' + P + '" y1="' + y + '" x2="' + (W - P) + '" y2="' + y + '" stroke="' + c.alpha2 + '" stroke-width="1"/>';
      if ((spec.config || {}).showHeader !== false) {
        cols.forEach(function (col, ci) {
          var isRight = col.align === 'right' || col.type === 'number';
          var tx = isRight ? (P + (ci + 1) * colW - 10) : (P + ci * colW + 10);
          var anchor = isRight ? 'end' : 'start';
          var labelTxt = (col.label || col.key || '') + (col.unit ? ', ' + col.unit : '');
          tSvg += '<text x="' + tx + '" y="' + (y + 24) + '" text-anchor="' + anchor + '" font-size="12.5" font-weight="600" fill="' + c.t2 + '">' + esc(labelTxt) + '</text>';
        });
        y += 36;
        tSvg += '<line x1="' + P + '" y1="' + y + '" x2="' + (W - P) + '" y2="' + y + '" stroke="' + c.alpha2 + '" stroke-width="1"/>';
      }

      var visRows = rawData.slice(0, 20);
      visRows.forEach(function (row) {
        var rowH = 38;
        cols.forEach(function (col, ci) {
          var val = row[col.key];
          var isRight = col.align === 'right' || col.type === 'number';
          var tx = isRight ? (P + (ci + 1) * colW - 10) : (P + ci * colW + 10);
          var anchor = isRight ? 'end' : 'start';

          if (col.type === 'badge' || col.type === 'status') {
            var txt = col.type === 'status' ? (STATUS[val] || String(val || '')) : String(val || '');
            var stTone = col.type === 'status' ? c.status[String(val || '').toLowerCase()] : null;
            var pBg = stTone ? stTone.bg : c.fillSec;
            var pFg = stTone ? stTone.fg : c.t1;
            var pw = Math.max(36, txt.length * 7.4 + 16);
            var px = isRight ? (tx - pw) : tx;
            tSvg += '<rect x="' + px + '" y="' + (y + 9) + '" width="' + pw + '" height="20" rx="6" fill="' + pBg + '"/>';
            tSvg += '<text x="' + (px + pw / 2) + '" y="' + (y + 23) + '" text-anchor="middle" font-size="11.5" font-weight="500" fill="' + pFg + '">' + esc(txt) + '</text>';
          } else {
            var disp = col.type === 'number' ? fmt(val) : truncText(String(val == null ? '' : val), colW - 18, 13.5, 400);
            tSvg += '<text x="' + tx + '" y="' + (y + 24) + '" text-anchor="' + anchor + '" font-size="13.5" fill="' + c.t1 + '">' + esc(disp) + '</text>';
          }
        });
        y += rowH;
        tSvg += '<line x1="' + P + '" y1="' + y + '" x2="' + (W - P) + '" y2="' + y + '" stroke="' + c.alpha2 + '" stroke-width="1"/>';
      });

      var totalH = y + P;
      return wrapCardSvg(W, totalH, c, head.svg + tSvg);
    }

    var ch = chart(spec, sec);
    built[idx] = ch;
    var W = ch.w + P * 2;
    var head = buildHeaderSvg(spec, c, P, W);
    var HH = head.height;
    var H = ch.h + HH + P;
    return wrapCardSvg(W, H, c, head.svg + '<g transform="translate(' + P + ',' + HH + ')">' + ch.inner + '</g>');
  }

  function nextFrame() {
    return new Promise(function (resolve) {
      if (typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(function () { setTimeout(resolve, 0); });
      } else {
        setTimeout(resolve, 0);
      }
    });
  }

  function svgToCanvasBlob(svgStr, scale) {
    scale = scale || 2;
    return nextFrame().then(function () {
      return new Promise(function (resolve, reject) {
        var parser = new DOMParser();
        var doc = parser.parseFromString(svgStr, 'image/svg+xml');
        var svgEl = doc.documentElement;
        var w = Number(svgEl.getAttribute('width')) || 648;
        var h = Number(svgEl.getAttribute('height')) || 420;
        var blob = new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' });
        var url = URL.createObjectURL(blob);
        var img = new Image();
        img.decoding = 'async';

        function drawToBlob() {
          try {
            var cv = document.createElement('canvas');
            cv.width = w * scale;
            cv.height = h * scale;
            var ctx = cv.getContext('2d');
            ctx.scale(scale, scale);
            ctx.drawImage(img, 0, 0, w, h);
            URL.revokeObjectURL(url);
            cv.toBlob(function (b) {
              if (b) resolve(b);
              else reject(new Error('toBlob failed'));
            }, 'image/png');
          } catch (err) {
            URL.revokeObjectURL(url);
            reject(err);
          }
        }

        img.onload = function () {
          if (typeof img.decode === 'function') {
            img.decode().then(drawToBlob, drawToBlob);
          } else {
            drawToBlob();
          }
        };
        img.onerror = function (e) {
          URL.revokeObjectURL(url);
          reject(e);
        };
        img.src = url;
      });
    });
  }

  function snapBlockToBlob(sec, spec, idx) {
    var svgStr = buildFullSvgString(spec, idx, sec);
    return svgToCanvasBlob(svgStr, 2);
  }

  function withButtonBusy(btn, fn) {
    if (!btn) return fn();
    if (btn.dataset.busy === '1') return Promise.resolve();
    btn.dataset.busy = '1';
    btn.classList.add('is-busy');
    return Promise.resolve().then(fn).finally(function () {
      delete btn.dataset.busy;
      btn.classList.remove('is-busy');
    });
  }

  function exportPng(sec, spec, idx, btn) {
    return withButtonBusy(btn, function () {
      return snapBlockToBlob(sec, spec, idx).then(function (b) {
        save(b, slug(spec.title) + '.png');
        toast('PNG сохранён');
      }).catch(function () {
        toast('Не удалось собрать изображение');
      });
    });
  }

  function exportCopy(sec, spec, idx, btn) {
    if (!window.ClipboardItem || !navigator.clipboard || !navigator.clipboard.write) {
      toast('Копирование недоступно в этом браузере');
      return;
    }
    return withButtonBusy(btn, function () {
      var blobPromise = snapBlockToBlob(sec, spec, idx);
      try {
        var item = new ClipboardItem({ 'image/png': blobPromise });
        return navigator.clipboard.write([item]).then(function () {
          toast('Изображение скопировано');
        }).catch(function () {
          return blobPromise.then(function (b) {
            return navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]);
          }).then(function () {
            toast('Изображение скопировано');
          });
        });
      } catch (_) {
        return blobPromise.then(function (b) {
          return navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]);
        }).then(function () {
          toast('Изображение скопировано');
        });
      }
    }).catch(function () {
      toast('Браузер запретил копирование');
    });
  }

  function exportSvg(spec, idx, sec, btn) {
    return withButtonBusy(btn, function () {
      return nextFrame().then(function () {
        var s = buildFullSvgString(spec, idx, sec);
        save(new Blob([s], { type: 'image/svg+xml' }), slug(spec.title) + '.svg');
        toast('SVG сохранён');
      });
    });
  }

  function icon(p) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + p + '"/></svg>';
  }

  function metric(spec) {
    var items = Array.isArray(spec.items) ? spec.items : [];
    var cols = (spec.layout && Number(spec.layout.columns)) || 0;
    var styleAttr = cols > 0 ? ' style="grid-template-columns:repeat(auto-fit,minmax(' + Math.max(140, Math.floor(540 / cols)) + 'px,1fr))"' : '';
    return '<div class="mc"' + styleAttr + '>' + items.map(function (it) {
      return '<div class="mc-card ' + esc(it.status || '') + '">' +
        '<div class="mc-label">' + icon(ICONS[it.icon] || ICONS['default']) + '<span>' + esc(it.label) + '</span></div>' +
        '<div class="mc-val">' + fmt(it.value) + '</div>' +
        (it.description ? '<div class="mc-desc">' + esc(it.description) + '</div>' : '') +
        '</div>';
    }).join('') + '</div>';
  }

  function table(spec, body) {
    var cfg = spec.config || {};
    var cols = Array.isArray(spec.columns) ? spec.columns : [];
    var rawData = Array.isArray(spec.data) ? spec.data : [];
    if (!cols.length && rawData.length) {
      cols = Object.keys(rawData[0]).map(function (k) {
        return { key: k, label: k, type: typeof rawData[0][k] === 'number' ? 'number' : 'text', sortable: true };
      });
    }
    var pg = cfg.pagination && cfg.pagination.enabled;
    var ps = pg ? (Number(cfg.pagination.pageSize) || 10) : 1e9;
    var st = { k: null, d: 1, q: '', p: 0 };

    body.innerHTML = '<div class="tb-wrap">' +
      (cfg.searchable ? '<input class="tb-search" type="search" placeholder="Поиск по таблице" data-export-ignore>' : '') +
      '<div class="tb-scroll"><table class="tb"><thead></thead><tbody></tbody></table></div>' +
      '<div class="pager" data-export-ignore></div></div>';

    var thead = body.querySelector('thead');
    var tbody = body.querySelector('tbody');
    var pager = body.querySelector('.pager');

    function cell(col, v) {
      var al = col.align === 'right' ? ' r' : '';
      if (col.type === 'badge') return '<td><span class="pill">' + esc(v) + '</span></td>';
      if (col.type === 'status') return '<td><span class="pill ' + esc(v) + '">' + esc(STATUS[v] || v) + '</span></td>';
      if (col.type === 'number') return '<td class="r' + al + '">' + fmt(v) + '</td>';
      return '<td class="' + al.trim() + '">' + esc(v) + '</td>';
    }

    function draw() {
      if (cfg.showHeader !== false) {
        thead.innerHTML = '<tr>' + cols.map(function (c) {
          var sortable = cfg.sortable !== false && c.sortable !== false;
          var arrow = st.k === c.key ? (st.d > 0 ? ' ↑' : ' ↓') : '';
          var cls = [sortable ? 'sort' : '', c.align === 'right' ? 'r' : ''].filter(Boolean).join(' ');
          return '<th class="' + cls + '" data-k="' + esc(c.key) + '">' + esc(c.label + (c.unit ? ', ' + c.unit : '')) + arrow + '</th>';
        }).join('') + '</tr>';
      } else {
        thead.innerHTML = '';
      }

      var rows = rawData.filter(function (r) {
        if (!st.q) return true;
        return Object.keys(r).some(function (k) {
          return String(r[k] == null ? '' : r[k]).toLowerCase().indexOf(st.q) > -1;
        });
      });

      if (st.k) {
        rows = rows.slice().sort(function (a, b) {
          var va = a[st.k], vb = b[st.k];
          if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * st.d;
          return (String(va) > String(vb) ? 1 : String(va) < String(vb) ? -1 : 0) * st.d;
        });
      }

      var pages = Math.max(1, Math.ceil(rows.length / ps));
      if (st.p >= pages) st.p = pages - 1;
      var vis = rows.slice(st.p * ps, st.p * ps + ps);

      tbody.innerHTML = vis.length
        ? vis.map(function (r) {
            return '<tr>' + cols.map(function (c) { return cell(c, r[c.key]); }).join('') + '</tr>';
          }).join('')
        : '<tr><td class="empty" colspan="' + Math.max(1, cols.length) + '">' + esc(cfg.emptyMessage || 'Данные отсутствуют') + '</td></tr>';

      pager.innerHTML = pg && pages > 1
        ? '<button class="btn" type="button" data-a="prev">Назад</button>' + (st.p + 1) + ' / ' + pages + '<button class="btn" type="button" data-a="next">Вперёд</button>'
        : '';

      thead.querySelectorAll('th.sort').forEach(function (h) {
        h.onclick = function () {
          var k = h.dataset.k;
          st.d = st.k === k ? -st.d : 1;
          st.k = k;
          draw();
        };
      });

      pager.querySelectorAll('button').forEach(function (b) {
        b.onclick = function () {
          st.p = Math.max(0, Math.min(pages - 1, st.p + (b.dataset.a === 'next' ? 1 : -1)));
          draw();
        };
      });
    }

    var q = body.querySelector('.tb-search');
    if (q) {
      q.oninput = function () {
        st.q = q.value.toLowerCase();
        st.p = 0;
        draw();
      };
    }
    draw();
  }

  function createGenerativeBlockEl(spec) {
    if (!spec || typeof spec !== 'object') return null;
    ensureOverlays();

    var idx = ++blockSeq;
    var comp = String(spec.component || '');
    var isChart = comp === 'Chart';

    var sec = document.createElement('section');
    sec.className = 'gen-ui-blk blk';
    sec.dataset.genIdx = String(idx);
    sec.dataset.genComponent = comp;
    sec._genSpec = spec;

    sec.innerHTML =
      '<div class="blk-head">' +
        '<div class="blk-titles">' +
          '<div class="blk-title">' + esc(spec.title || '') + '</div>' +
          (spec.description ? '<p class="blk-desc">' + esc(spec.description) + '</p>' : '') +
        '</div>' +
        '<div class="blk-actions" data-export-ignore>' +
          '<button class="btn" type="button" data-a="png" title="Скачать PNG">' + icon(DL) + 'PNG</button>' +
          '<button class="btn" type="button" data-a="svg"' + (isChart ? '' : ' hidden') + ' title="Скачать SVG">' + icon(DL) + 'SVG</button>' +
          '<button class="btn" type="button" data-a="copy" title="Скопировать изображение">' + icon(CP) + '</button>' +
        '</div>' +
      '</div>' +
      '<div class="blk-body"></div>';

    var body = sec.querySelector('.blk-body');
    if (isChart) {
      var ch = chart(spec, sec);
      built[idx] = ch;
      body.innerHTML = '<svg viewBox="0 0 ' + ch.w + ' ' + ch.h + '" role="img" aria-label="' + esc(spec.title || 'Chart') + '" font-family="' + FONT + '">' + ch.inner + '</svg>';
    } else if (comp === 'MetricCards') {
      body.innerHTML = metric(spec);
    } else if (comp === 'DataTable') {
      table(spec, body);
    }

    sec.querySelectorAll('.blk-actions .btn').forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        var a = b.dataset.a;
        if (a === 'png') exportPng(sec, spec, idx, b);
        else if (a === 'svg') exportSvg(spec, idx, sec, b);
        else exportCopy(sec, spec, idx, b);
      };
    });

    return sec;
  }

  function refreshAllCharts() {
    document.querySelectorAll('.gen-ui-blk[data-gen-component="Chart"]').forEach(function (sec) {
      var spec = sec._genSpec;
      var idx = Number(sec.dataset.genIdx || 0);
      var body = sec.querySelector('.blk-body');
      if (!spec || !body) return;
      var ch = chart(spec, sec);
      if (idx) built[idx] = ch;
      body.innerHTML = '<svg viewBox="0 0 ' + ch.w + ' ' + ch.h + '" role="img" aria-label="' + esc(spec.title || 'Chart') + '" font-family="' + FONT + '">' + ch.inner + '</svg>';
    });
  }

  function tryParseComponentJson(raw) {
    if (!raw || typeof raw !== 'string') return null;
    var trimmed = raw.trim();
    if (!trimmed.startsWith('{') || !trimmed.endsWith('}')) return null;
    try {
      var obj = JSON.parse(trimmed);
      if (!obj || typeof obj !== 'object') return null;
      var comp = String(obj.component || '').trim();
      var compLower = comp.toLowerCase();

      if (compLower === 'chart' || (!comp && ['pie', 'bar', 'line', 'scatter'].indexOf(String(obj.type || '').toLowerCase()) > -1 && Array.isArray(obj.data))) {
        var chartType = String(obj.type || 'bar').toLowerCase();
        if (['pie', 'bar', 'line', 'scatter'].indexOf(chartType) > -1 && Array.isArray(obj.data) && obj.data.length > 0) {
          return { component: 'Chart', data: Object.assign({}, obj, { component: 'Chart', type: chartType }) };
        }
      }

      if (compLower === 'metriccards' || compLower === 'metric_cards' || compLower === 'metrics') {
        var items = Array.isArray(obj.items) ? obj.items : (Array.isArray(obj.data) ? obj.data : null);
        if (items && items.length > 0) {
          return { component: 'MetricCards', data: Object.assign({}, obj, { component: 'MetricCards', items: items }) };
        }
      }

      if (compLower === 'datatable' || compLower === 'data_table' || compLower === 'table') {
        var rows = Array.isArray(obj.data) ? obj.data : (Array.isArray(obj.rows) ? obj.rows : null);
        if (rows && (Array.isArray(obj.columns) || rows.length > 0)) {
          return { component: 'DataTable', data: Object.assign({}, obj, { component: 'DataTable', data: rows }) };
        }
      }
    } catch (_) {}
    return null;
  }

  function isGenerativeComponent(name) {
    return name === 'Chart' || name === 'MetricCards' || name === 'DataTable';
  }

  document.addEventListener('mousemove', function (e) {
    var t = e.target && e.target.closest && e.target.closest('.gen-ui-blk [data-tip]');
    var tip = document.getElementById('gen-ui-tip');
    if (!t) {
      if (tip) {
        tip.classList.remove('on');
        tip.style.opacity = '0';
      }
      return;
    }
    var ov = ensureOverlays();
    ov.tip.textContent = t.getAttribute('data-tip');
    ov.tip.classList.add('on');
    ov.tip.style.opacity = '1';
    var w = ov.tip.offsetWidth || 180;
    var h = ov.tip.offsetHeight || 42;
    var left = Math.min(e.clientX + 14, window.innerWidth - w - 8);
    var top = Math.min(e.clientY + 14, window.innerHeight - h - 8);
    ov.tip.style.left = Math.max(8, left) + 'px';
    ov.tip.style.top = Math.max(8, top) + 'px';
  });

  if (typeof MutationObserver !== 'undefined') {
    var obs = new MutationObserver(function () {
      requestAnimationFrame(refreshAllCharts);
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] });
  }


  var STATUS_LABELS = {
    'InteractivePoll': 'Подготавливаю варианты ответа…',
    'pie': 'Строю круговую диаграмму…',
    'bar': 'Строю столбчатую диаграмму…',
    'line': 'Строю график динамики…',
    'scatter': 'Строю диаграмму рассеяния…',
    'chart': 'Строю диаграмму…',
    'MetricCards': 'Рассчитываю показатели…',
    'DataTable': 'Формирую таблицу…'
  };

  function detectStreamingComponent(rawPartial) {
    if (!rawPartial || typeof rawPartial !== 'string') return null;
    var compMatch = rawPartial.match(/"component"\s*:\s*"([^"\r\n]*)/i);
    if (!compMatch) return null;
    var compRaw = String(compMatch[1] || '').trim().toLowerCase();
    if (!compRaw) return null;

    if (compRaw === 'interactivepoll' || compRaw === 'askuserquestion' || compRaw === 'poll' ||
        'interactivepoll'.indexOf(compRaw) === 0 || 'askuserquestion'.indexOf(compRaw) === 0) {
      return { component: 'InteractivePoll', subType: 'poll', label: STATUS_LABELS['InteractivePoll'] };
    }

    if (compRaw === 'chart' || 'chart'.indexOf(compRaw) === 0) {
      var typeMatch = rawPartial.match(/"type"\s*:\s*"([^"\r\n]*)/i);
      var tRaw = typeMatch ? String(typeMatch[1] || '').trim().toLowerCase() : '';
      if (tRaw === 'pie' || (tRaw && 'pie'.indexOf(tRaw) === 0)) return { component: 'Chart', subType: 'pie', label: STATUS_LABELS['pie'] };
      if (tRaw === 'bar' || (tRaw && 'bar'.indexOf(tRaw) === 0)) return { component: 'Chart', subType: 'bar', label: STATUS_LABELS['bar'] };
      if (tRaw === 'line' || (tRaw && 'line'.indexOf(tRaw) === 0)) return { component: 'Chart', subType: 'line', label: STATUS_LABELS['line'] };
      if (tRaw === 'scatter' || (tRaw && 'scatter'.indexOf(tRaw) === 0)) return { component: 'Chart', subType: 'scatter', label: STATUS_LABELS['scatter'] };
      return { component: 'Chart', subType: 'chart', label: STATUS_LABELS['chart'] };
    }

    if (compRaw === 'metriccards' || compRaw === 'metric_cards' || compRaw === 'metrics' || 'metriccards'.indexOf(compRaw) === 0) {
      return { component: 'MetricCards', subType: 'metrics', label: STATUS_LABELS['MetricCards'] };
    }

    if (compRaw === 'datatable' || compRaw === 'data_table' || compRaw === 'table' || 'datatable'.indexOf(compRaw) === 0) {
      return { component: 'DataTable', subType: 'table', label: STATUS_LABELS['DataTable'] };
    }

    return null;
  }

  function getSkeletonIcon(subType, component) {
    if (subType === 'pie') return '<svg viewBox="0 0 24 24"><path d="M12 3a9 9 0 1 0 9 9h-9v-9z"/><path d="M15 3.5a9 9 0 0 1 5.5 5.5h-5.5v-5.5z"/></svg>';
    if (subType === 'bar' || component === 'Chart') return '<svg viewBox="0 0 24 24"><path d="M3 12m0 1a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v6a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1zM9 8m0 1a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v10a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1zM15 4m0 1a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v14a1 1 0 0 1 -1 1h-4a1 1 0 0 1 -1 -1z"/></svg>';
    if (subType === 'line') return '<svg viewBox="0 0 24 24"><path d="M3 17l6 -6l4 4l8 -8M14 7h7v7"/></svg>';
    if (subType === 'scatter') return '<svg viewBox="0 0 24 24"><path d="M4 4v16h16 M8 14h.01 M12 10h.01 M16 13h.01 M17 7h.01 M9 8h.01" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    if (component === 'MetricCards') return '<svg viewBox="0 0 24 24"><path d="M3 12h4l3 8l4 -16l3 8h4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    if (component === 'DataTable') return '<svg viewBox="0 0 24 24"><path d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6z M4 10h16 M10 10v10"/></svg>';
    if (component === 'InteractivePoll') return '<svg viewBox="0 0 24 24"><path d="M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    return '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/></svg>';
  }

  function createSkeletonEl(spec) {
    var sec = document.createElement('section');
    sec.className = 'gen-ui-blk gen-ui-blk--skeleton';
    sec.setAttribute('data-gen-component', spec.component || 'Chart');
    sec.setAttribute('data-gen-type', spec.subType || '');
    sec.setAttribute('data-gen-state', 'generating');
    sec.innerHTML =
      '<div class="gen-ui-skeleton-body">' +
        '<span class="gen-ui-skeleton-icon" aria-hidden="true">' + getSkeletonIcon(spec.subType, spec.component) + '</span>' +
        '<span class="gen-ui-skeleton-text">' + esc(spec.label || 'Подготовка…') + '</span>' +
      '</div>';
    return sec;
  }

  function updateSkeletonEl(el, spec) {
    if (!el || !spec) return;
    var curSub = el.getAttribute('data-gen-type') || '';
    if (curSub !== spec.subType || el.getAttribute('data-gen-component') !== spec.component) {
      el.setAttribute('data-gen-component', spec.component || '');
      el.setAttribute('data-gen-type', spec.subType || '');
      var ic = el.querySelector('.gen-ui-skeleton-icon');
      if (ic) ic.innerHTML = getSkeletonIcon(spec.subType, spec.component);
    }
    var txt = el.querySelector('.gen-ui-skeleton-text');
    if (txt && txt.textContent !== spec.label) {
      txt.textContent = spec.label || 'Подготовка…';
    }
  }

  function extractBalancedJsonObjects(text, tryParseFn) {
    var results = [];
    var i = 0;
    var n = text.length;
    while (i < n) {
      var start = text.indexOf('{', i);
      if (start === -1) break;
      var depth = 0, inStr = false, esc = false, end = -1;
      for (var j = start; j < n; j++) {
        var ch = text[j];
        if (inStr) {
          if (esc) { esc = false; }
          else if (ch === '\\') { esc = true; }
          else if (ch === '"') { inStr = false; }
        } else {
          if (ch === '"') inStr = true;
          else if (ch === '{') depth++;
          else if (ch === '}') {
            depth--;
            if (depth === 0) { end = j; break; }
          }
        }
      }
      if (end !== -1) {
        var candidate = text.slice(start, end + 1);
        var parsed = tryParseFn ? tryParseFn(candidate) : tryParseComponentJson(candidate);
        if (parsed) {
          results.push({ start: start, end: end + 1, parsed: parsed });
          i = end + 1;
          continue;
        }
      }
      i = start + 1;
    }
    return results;
  }

  function extractStructuredSegments(text, tryParseFn, options) {
    var src = String(text || '');
    var isStreaming = !!(options && options.isStreaming);
    var segments = [];
    var fenceRe = /```(?:json)?\s*([\s\S]*?)```/gi;
    var lastIdx = 0;
    var m;

    var pushPlainOrInlineJson = function(chunk, isTail) {
      if (!chunk) return;
      var inlineMatches = extractBalancedJsonObjects(chunk, tryParseFn);
      if (!inlineMatches.length) {
        if (isTail) {
          var braceIdx = chunk.lastIndexOf('{');
          if (braceIdx !== -1) {
            var tailBrace = chunk.slice(braceIdx);
            var detInline = detectStreamingComponent(tailBrace);
            if (detInline) {
              var beforeBrace = chunk.slice(0, braceIdx);
              if (beforeBrace.trim()) segments.push({ kind: 'md', text: beforeBrace });
              segments.push({ kind: 'skeleton', component: detInline.component, subType: detInline.subType, label: detInline.label });
              return;
            }
          }
        }
        if (chunk.trim()) segments.push({ kind: 'md', text: chunk });
        return;
      }
      var cur = 0;
      for (var k = 0; k < inlineMatches.length; k++) {
        var hit = inlineMatches[k];
        var before = chunk.slice(cur, hit.start);
        if (before.trim()) segments.push({ kind: 'md', text: before });
        segments.push({ kind: 'component', component: hit.parsed.component, data: hit.parsed.data, rawKey: chunk.slice(hit.start, hit.end) });
        cur = hit.end;
      }
      var after = chunk.slice(cur);
      if (after.trim()) {
        if (isTail) {
          var bIdx = after.lastIndexOf('{');
          if (bIdx !== -1) {
            var detAfter = detectStreamingComponent(after.slice(bIdx));
            if (detAfter) {
              var bef = after.slice(0, bIdx);
              if (bef.trim()) segments.push({ kind: 'md', text: bef });
              segments.push({ kind: 'skeleton', component: detAfter.component, subType: detAfter.subType, label: detAfter.label });
              return;
            }
          }
        }
        segments.push({ kind: 'md', text: after });
      }
    };

    while ((m = fenceRe.exec(src)) !== null) {
      var parsed = tryParseFn ? tryParseFn(m[1]) : tryParseComponentJson(m[1]);
      if (parsed) {
        pushPlainOrInlineJson(src.slice(lastIdx, m.index), false);
        segments.push({ kind: 'component', component: parsed.component, data: parsed.data, rawKey: m[1].trim() });
        lastIdx = m.index + m[0].length;
      }
    }

    var tail = src.slice(lastIdx);
    if (!isStreaming) {
      pushPlainOrInlineJson(tail, false);
      return segments;
    }

    var openFenceMatch = /```(json)?(?:\s+|$)([\s\S]*)$/i.exec(tail);
    if (openFenceMatch) {
      var beforeOpen = tail.slice(0, openFenceMatch.index);
      pushPlainOrInlineJson(beforeOpen, false);

      var isExplicit = Boolean(openFenceMatch[1]);
      var partialBody = openFenceMatch[2] || '';
      var detected = detectStreamingComponent(partialBody);

      if (detected) {
        segments.push({ kind: 'skeleton', component: detected.component, subType: detected.subType, label: detected.label });
      } else if (isExplicit && partialBody.trim().length < 60 && /^\{\s*(?:"(?:c(?:o(?:m(?:p(?:o(?:n(?:e(?:n(?:t)?)?)?)?)?)?)?)?)?"?\s*:?\s*"?)?$/i.test(partialBody.trim())) {
        // Удерживаем сырой префикс до появления "component"
      } else {
        segments.push({ kind: 'md', text: tail.slice(openFenceMatch.index) });
      }
    } else {
      var rawEndMatch = /(\n|^)\s*`{1,3}(?:j(?:s(?:o(?:n)?)?)?)?$/i.exec(tail);
      if (rawEndMatch) {
        var beforeRawEnd = tail.slice(0, rawEndMatch.index);
        pushPlainOrInlineJson(beforeRawEnd, false);
      } else {
        pushPlainOrInlineJson(tail, true);
      }
    }

    return segments;
  }

  function computeStreamStep(shownText, bufferText, baseStep) {
    var shown = String(shownText || '');
    var buf = String(bufferText || '');
    if (!buf.length) return 0;

    var lastClosedEnd = 0;
    var fenceRe = /```(?:json)?\s*[\s\S]*?```/gi;
    var m;
    while ((m = fenceRe.exec(shown)) !== null) {
      lastClosedEnd = m.index + m[0].length;
    }

    var tailShown = shown.slice(lastClosedEnd);
    var openMatch = /```(?:json)?(?:\s+|$)([\s\S]*)$/i.exec(tailShown);

    if (openMatch) {
      var closeIdxInBuf = buf.indexOf('```');
      if (closeIdxInBuf !== -1) {
        var alreadyInFence = openMatch[0].length;
        var remainingToClose = closeIdxInBuf + 3;
        var totalFenceLen = alreadyInFence + remainingToClose;
        var hasComponent = /"component"\s*:/i.test(openMatch[1]);
        if (!hasComponent) {
          var compIntroMatch = /("type"\s*:\s*"[^"]*"|"component"\s*:\s*"[^"]*")/i.exec(buf);
          if (compIntroMatch && compIntroMatch.index + compIntroMatch[0].length < remainingToClose && compIntroMatch.index < 120) {
            return Math.min(compIntroMatch.index + compIntroMatch[0].length + 1, remainingToClose);
          }
        }
        var targetFrames = 42;
        var fenceStep = Math.max(4, Math.ceil(totalFenceLen / targetFrames));
        return Math.min(fenceStep, remainingToClose);
      }
      return baseStep;
    }

    var nextFenceIdx = buf.indexOf('```');
    if (nextFenceIdx > 0 && nextFenceIdx <= baseStep) {
      return nextFenceIdx;
    }
    if (nextFenceIdx === 0) {
      var compIntroMatch2 = /("type"\s*:\s*"[^"]*"|"component"\s*:\s*"[^"]*")/i.exec(buf);
      var nextClose = buf.indexOf('```', 3);
      if (nextClose !== -1 && compIntroMatch2 && compIntroMatch2.index < nextClose && compIntroMatch2.index < 220) {
        return Math.min(compIntroMatch2.index + compIntroMatch2[0].length + 1, nextClose + 3);
      }
    }

    return baseStep;
  }

  window.GenerativeUI = {
    createBlockEl: createGenerativeBlockEl,
    createBlock: createGenerativeBlockEl,
    tryParseComponentJson: tryParseComponentJson,
    tryParse: tryParseComponentJson,
    isGenerativeComponent: isGenerativeComponent,
    isComponent: isGenerativeComponent,
    detectStreamingComponent: detectStreamingComponent,
    createSkeletonEl: createSkeletonEl,
    updateSkeletonEl: updateSkeletonEl,
    extractStructuredSegments: extractStructuredSegments,
    computeStreamStep: computeStreamStep,
    refreshCharts: refreshAllCharts
  };
})();
