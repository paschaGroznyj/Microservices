const overlay = document.getElementById('profile-overlay');
const profileBtn = document.getElementById('profile-btn');
const closeBtn = document.getElementById('profile-close');
const modalSubtitle = document.getElementById('modal-subtitle');
const toast = document.getElementById('toast-token');

let isLoading = false;

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2200);
}

async function fetchProfileData() {
  if (isLoading) return;
  isLoading = true;
  modalSubtitle.innerHTML = '<span style="display:flex;align-items:center;gap:5px;"><span class="spinner"></span> Загружается...</span>';

  try {
    const res = await fetch('/api/profile/me', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ action: 'get_profile' })
    });
    if (!res.ok) throw new Error('not ok');
    const data = await res.json();
    console.log(data)
    applyProfileData(data);
  } catch {
    modalSubtitle.textContent = 'локальные данные';
    applyProfileData(null);
  } finally {
    isLoading = false;
  }
}

const MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
};

const getJoinDateFormatted = (dateStringOrObject) => {
    const date = dateStringOrObject ? new Date(dateStringOrObject) : new Date();

    // Проверка на валидность даты
    if (isNaN(date.getTime())) return '';

    const monthIndex = date.getMonth() + 1; // getMonth() возвращает 0-11
    const year = date.getFullYear();

    const monthName = MONTHS_RU[monthIndex] || 'месяц';

    return `${monthName} ${year}`;
};

function applyProfileData(data) {
  const username = data?.username || 'pascha';
  const name = data?.name || 'Павел';
  // const plan = data?.plan || 'Free';
  const joined = data?.joined || getJoinDateFormatted();
  const stats = data?.stats || {chats: 247, search: 89, saved: 34};

  document.getElementById('modal-avatar').childNodes[0].nodeValue = username[0].toUpperCase();
  document.getElementById('modal-username').textContent = username;
  document.getElementById('modal-name').textContent = name;
  // document.getElementById('modal-plan').textContent = plan;
  document.getElementById('modal-joined').textContent = 'с ' + joined;
  modalSubtitle.textContent = '@' + username;

  document.getElementById('stat-chats').textContent = stats.agent_requests;
  document.getElementById('stat-saved').textContent = stats.favorites_count;
  // Рендер тулзов
  const tools = data?.stats?.tools || [];
  renderToolBars(tools);
}

function renderToolBars(tools) {
  const barList = document.querySelector(".bar-list");
  if (!barList || !tools.length) return;

  // Максимум для нормализации ширины баров
  const maxCount = Math.max(...tools.map(t => t.count));

  // Цвета чередуем — синий/зелёный
  const colors = ["", "green"];

  barList.innerHTML = tools.map((t, i) => {
    const widthPct = Math.round((t.count / maxCount) * 100);
    const colorClass = colors[i % 2];
    return `
      <div class="bar-item">
        <span class="bar-item-label">${t.tool}</span>
        <div class="bar-track">
          <div class="bar-fill ${colorClass}" style="width:${widthPct}%"></div>
        </div>
        <span class="bar-count">${t.count}</span>
      </div>
    `;
  }).join("");
}

function openProfile() {
  overlay.classList.add('active');
  document.body.style.overflow = 'hidden';
  fetchProfileData();
}

function closeProfile() {
  overlay.classList.add('closing');
  setTimeout(() => {
    overlay.classList.remove('active', 'closing');
    document.body.style.overflow = '';
  }, 300);
}

document.addEventListener('click', async (e) => {
  if (e.target.closest('#profile-btn')) openProfile();
  if (e.target.closest('#profile-close')) closeProfile();
  if (e.target === overlay) closeProfile();

  if (e.target.closest('#logout-btn')) {
    if (confirm('Выйти из приложения?')) {
      localStorage.removeItem('scholar_api_token');
      showToast('Выход...');
      // Выход делаем через бэкенд: POST /logout-web удаляет cookie с токеном
      // и сам решает, куда редиректить — на /login-page для внутренних
      // (парольных) юзеров или на uva для authentik-юзеров (SSO-выход).
      // Хардкод window.location на uva убран: он не чистил cookie и всегда
      // вёл в магазин приложений, даже когда заголовков authentik нет.
      setTimeout(() => {
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = '/logout-web';
          document.body.appendChild(form);
          form.submit();
      }, 800);
    }
  }

});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && overlay.classList.contains('active')) closeProfile();
});