const overlay = document.getElementById('profile-overlay');
const profileBtn = document.getElementById('profile-btn');
const closeBtn = document.getElementById('profile-close');
const tokenInput = document.getElementById('token-input');
const tokenSaveBtn = document.getElementById('token-save-btn');
const tokenDot = document.getElementById('token-dot');
const tokenStatusText = document.getElementById('token-status-text');
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
  const plan = data?.plan || 'Free';
  const joined = data?.joined || getJoinDateFormatted();
  const stats = data?.stats || {chats: 247, search: 89, saved: 34};

  document.getElementById('modal-avatar').childNodes[0].nodeValue = username[0].toUpperCase();
  document.getElementById('modal-username').textContent = username;
  document.getElementById('modal-name').textContent = name;
  document.getElementById('modal-plan').textContent = plan;
  document.getElementById('modal-joined').textContent = 'с ' + joined;
  modalSubtitle.textContent = '@' + username;

  document.getElementById('stat-chats').textContent = stats.agent_requests;
  document.getElementById('stat-search').textContent = stats.search_requests;
  document.getElementById('stat-saved').textContent = stats.favorites_count;
  console.log('token_bool', stats.token_bool)

  const savedToken = stats.token_bool || '';
  console.log('token', savedToken)
  if (savedToken) {
    tokenInput.value = 'привет_пользователь_pm:)';
    tokenDot.classList.remove('inactive');
    tokenStatusText.textContent = 'активен';
  }
  else {
    tokenInput.value = '';
    tokenDot.classList.add('inactive');
    tokenStatusText.textContent = 'не активен';
  }
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

  if (e.target.closest('#token-save-btn')) {
    const val = tokenInput.value.trim();

    if (!val) { showToast('Введите токен'); return; }
    if (val.length > 50) { showToast('Токен слишком длинный (макс. 50 символов)'); return; }

    try {
        const res = await fetch('/api/profile/token', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ token: val })
        });

        const data = await res.json();

        if (res.ok) {
            localStorage.setItem('scholar_api_token', val);
            tokenDot.classList.remove('inactive');
            tokenStatusText.textContent = 'активен';
            showToast('Токен сохранён');
        } else {
            const errorMsg = data.detail || data.error || 'Неизвестная ошибка';
            showToast(`Ошибка: ${errorMsg}`);
        }
    } catch (err) {
        showToast('Нет соединения с сервером');
    }
}

  if (e.target.closest('#logout-btn')) {
    if (confirm('Выйти из аккаунта?')) {
      localStorage.removeItem('scholar_api_token');
      showToast('Выход...');
      setTimeout(() => { window.location.href = '/logout'; }, 800);
    }
  }
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && overlay.classList.contains('active')) closeProfile();
});