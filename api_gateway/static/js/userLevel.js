// Уровни пользователя по числу запросов к агенту (stats.agent_requests из /api/profile/me).
// Ничего не хранит: считается на клиенте при каждой загрузке.
(function () {
  const LEVELS = [
    { min: 0,    name: 'Новичок' },
    { min: 50,   name: 'Практик' },
    { min: 200,  name: 'Профи' },
    { min: 1000, name: 'Эксперт' },
    { min: 5000, name: 'Легенда' },
  ];
  const fmt = n => Number(n).toLocaleString('ru-RU');

  function levelOf(count) {
    const n = Math.max(0, Math.floor(Number(count) || 0));
    let i = 0;
    while (i + 1 < LEVELS.length && n >= LEVELS[i + 1].min) i++;
    const cur = LEVELS[i], next = LEVELS[i + 1] || null;
    // полоса совпадает с подписью «n / порог»: доля пути от нуля до следующего уровня
    const pct = next ? Math.min(100, Math.round((n / next.min) * 100)) : 100;
    return { level: i + 1, name: cur.name, count: n, next: next ? next.min : null, nextName: next ? next.name : null, pct };
  }

  function apply(count) {
    if (count === undefined || count === null || isNaN(Number(count))) return null;
    const L = levelOf(count);
    const hint = L.next ? `${L.name} · ${fmt(L.count)} / ${fmt(L.next)} запросов` : `${L.name} · ${fmt(L.count)} запросов`;
    const b = document.getElementById('sb-level');
    if (b) {
      b.textContent = L.level;
      b.dataset.level = L.level;
      b.hidden = false;
      b.closest('.profile-button')?.setAttribute('title', hint);
    }
    const box = document.getElementById('modal-level');
    if (box) {
      box.dataset.level = L.level;
      document.getElementById('modal-level-chip').textContent = `L${L.level} · ${L.name}`;
      document.getElementById('modal-level-next').textContent = L.next
        ? `${fmt(L.count)} / ${fmt(L.next)} до «${L.nextName}»` : `${fmt(L.count)} · максимум`;
      document.getElementById('modal-level-fill').style.width = L.pct + '%';
      box.hidden = false;
    }
    return L;
  }

  async function init() {
    if (!document.getElementById('sb-level')) return;
    try {
      const res = await fetch('/api/profile/me', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'get_profile' }),
      });
      if (!res.ok) return;
      const data = await res.json();
      apply(data?.stats?.agent_requests);
    } catch (_) { /* без уровня, бейдж скрыт */ }
  }

  window.UserLevel = { LEVELS, levelOf, apply };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
