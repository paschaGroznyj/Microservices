/* scrolly.js — движок доклада: тема по главе, прогресс, оглавление, шаги сцен.
   Главы подписываются на событие sc:step (detail: {ch, step}). */
(function () {
  const root = document.documentElement;
  const chapters = [...document.querySelectorAll('.sc-ch')];

  /* ── тема: как theme.js проекта, через data-theme на <html> ── */
  let themeTimer = 0;
  function setTheme(t) {
    if (!t || root.getAttribute('data-theme') === t) return;
    root.classList.add('sc-theming');
    root.setAttribute('data-theme', t);
    window.ChatAnimations?.parseAccent?.();
    document.querySelectorAll('img.js-theme-icon').forEach((img) => {
      const src = img.dataset[t === 'dark' ? 'srcDark' : 'srcLight'];
      if (src) img.src = src;
    });
    clearTimeout(themeTimer);
    themeTimer = setTimeout(() => root.classList.remove('sc-theming'), 700);
  }

  /* ── оглавление ── */
  const toc = document.querySelector('.sc-toc');
  chapters.forEach((ch) => {
    if (!ch.dataset.title) return;
    const a = document.createElement('a');
    a.href = '#' + ch.id;
    a.innerHTML = `<span>${ch.dataset.title}</span><i></i>`;
    toc.appendChild(a);
    ch._toc = a;
  });

  /* ── глава в центре экрана → тема и пункт оглавления ── */
  const chObs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      setTheme(e.target.dataset.theme);
      toc.querySelectorAll('a').forEach((a) => a.classList.toggle('is-on', a === e.target._toc));
    });
  }, { rootMargin: '-50% 0px -50% 0px' });
  chapters.forEach((c) => chObs.observe(c));

  /* ── шаги сцены ── */
  const stepObs = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      const step = e.target;
      const ch = step.closest('.sc-ch');
      ch.querySelectorAll('.sc-step').forEach((s) => s.classList.toggle('is-on', s === step));
      const n = Number(step.dataset.step || 0);
      const stage = ch.querySelector('.sc-stage');
      if (stage) stage.dataset.step = String(n);
      const mc = stage && (stage.querySelector('.sc-mcard') || stage.appendChild(Object.assign(document.createElement('div'), { className: 'sc-mcard' })));
      if (mc) mc.innerHTML = step.querySelector('.sc-step__card')?.innerHTML || '';
      document.dispatchEvent(new CustomEvent('sc:step', { detail: { ch: ch.id, step: n } }));
    });
  }, { rootMargin: '-45% 0px -45% 0px' });
  document.querySelectorAll('.sc-step').forEach((s) => stepObs.observe(s));

  /* ── прогресс ── */
  const bar = document.querySelector('.sc-progress span');
  const onScroll = () => {
    const max = document.documentElement.scrollHeight - innerHeight;
    bar.style.width = (max > 0 ? (scrollY / max) * 100 : 0) + '%';
  };
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* общий хелпер для глав */
  window.Scrolly = {
    on(chId, fn) {
      document.addEventListener('sc:step', (e) => { if (e.detail.ch === chId) fn(e.detail.step); });
    },
  };
})();
