(function () {
  const root      = document.documentElement;
  const btn       = document.getElementById('theme-toggle');
  const hljsLight = document.getElementById('hljs-theme-light');
  const hljsDark  = document.getElementById('hljs-theme-dark');

  // Восстановить тему из localStorage при загрузке
  const saved = localStorage.getItem('theme') || 'light';
  applyTheme(saved);

  btn && btn.addEventListener('click', function () {
    const current = root.getAttribute('data-theme') || 'light';
    applyTheme(current === 'light' ? 'dark' : 'light');
  });

  function applyTheme(theme) {
    root.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);

    if (hljsLight)  hljsLight.disabled  = (theme === 'dark');
    if (hljsDark)   hljsDark.disabled   = (theme === 'light');
  }
})();
