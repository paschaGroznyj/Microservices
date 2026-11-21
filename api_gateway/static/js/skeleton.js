// Скелетоны загрузки (вариант «пульс»). Стили: css/skeleton.css.
// Ширины фиксированы, чтобы скелетон выглядел одинаково при каждом показе.
(function () {
  'use strict';
  const W = [72, 58, 84, 46, 66, 78, 52, 88, 62, 70, 48, 80];
  const bar = (w, i, cls = '') => `<span class="sk${cls ? ' ' + cls : ''}" style="${w ? `width:${w}%;` : ''}--i:${i}"></span>`;
  const wrap = (cls, inner, label) =>
    `<div class="sk-wrap ${cls}" role="status" aria-busy="true" aria-label="${label}">${inner}</div>`;
  const lines = (ws, start = 0) => ws.map((w, k) => (w ? bar(w, start + k) : '<span class="sk-gap"></span>')).join('');

  const Skeleton = {
    // Список чатов в сайдбаре
    chats(n = 8) {
      let s = '';
      for (let i = 0; i < n; i++) s += `<div class="sk-row"><span class="sk sk-av" style="--i:${i}"></span>${bar(W[i % W.length], i)}</div>`;
      return wrap('sk-chats', s, 'Загрузка чатов');
    },
    // Лента: пузыри пользователя справа, ответы агента строками, плитки файлов
    messages(opts = {}) {
      let i = 0;
      const user = (w) => `<div class="sk-u">${bar(w, i++)}</div>`;
      const bot = (ws, files) => {
        let s = ws.map((w) => bar(w, i++)).join('');
        if (files) s += `<div class="sk-files"><span class="sk" style="--i:${i++}"></span><span class="sk" style="width:180px;--i:${i++}"></span></div>`;
        return `<div class="sk-b">${s}</div>`;
      };
      const body = user(42) + bot([92, 86, 64]) + user(30) + bot([88, 95, 78, 40], !opts.compact) + user(48) + bot([94, 82, 90, 58]);
      return wrap('sk-msgs' + (opts.compact ? ' sk-msgs--compact' : ''), body, 'Загрузка сообщений');
    },
    // Превью PDF: страница A4 с абзацами
    pdf() {
      const s = bar(55, 0, 'sk-h') + lines([96, 92, 98, 70, 0, 94, 90, 96, 88, 60, 0, 97, 85, 93, 50, 0, 91, 95, 89, 66, 0, 93, 87, 45], 1);
      return wrap('sk-pv', `<div class="sk-page">${s}</div>`, 'Загрузка файла');
    },
    // Превью документа (Markdown, HTML): абзацы без рамки страницы
    doc() {
      const s = bar(42, 0, 'sk-h') + lines([94, 88, 97, 64, 0, 90, 96, 82, 52, 0, 92, 86, 95, 70, 0, 88, 93, 58], 1);
      return wrap('sk-doc', s, 'Загрузка файла');
    },
    // Превью кода: номера строк и строки с отступами
    code(n = 40) {
      const ind = [0, 0, 1, 1, 2, 2, 1, 0, 0, 1, 2, 3, 2, 1, 0, 1, 1, 0];
      const ws = [38, 22, 56, 44, 62, 30, 48, 0, 34, 58, 40, 26, 52, 36, 0, 46, 60, 28];
      let s = '';
      for (let i = 0; i < n; i++) {
        const w = ws[i % 18];
        s += `<div class="sk-ln"><span class="sk sk-no" style="--i:${i}"></span>${w ? `<span class="sk" style="width:${w}%;margin-left:${ind[i % 18] * 24}px;--i:${i}"></span>` : ''}</div>`;
      }
      return wrap('sk-code', s, 'Загрузка файла');
    },
    // Список скиллов: чекбокс, название, «⋯»
    skills(n = 12) {
      let s = '';
      for (let i = 0; i < n; i++) {
        s += `<div class="sk-row"><span class="sk sk-cb" style="--i:${i}"></span>${bar(Math.round(W[(i + 3) % W.length] * 0.6), i)}<span class="sk sk-dots" style="--i:${i}"></span></div>`;
      }
      return wrap('sk-skills', s, 'Загрузка скиллов');
    },
    // Открытая карточка скилла
    skillDetail() {
      return wrap('sk-detail', bar(45, 0, 'sk-h') + lines([94, 88, 97, 72, 90, 84, 56], 1), 'Загрузка скилла');
    },
    // Панель памяти: j — журнал, s — саммари, t — шаги. Отдаёт целый слайд .mm-sl.
    mm(kind = 'j') {
      const hd = `<div class="sk-hd"><span class="sk sk-tag" style="--i:0"></span>${bar(kind === 's' ? 46 : 34, 1)}</div>`;
      let s = hd;
      if (kind === 't') {
        s += lines([28, 62, 54], 2);
        s += `<div class="sk-chips">${[0, 1, 2, 3, 4].map((k) => `<span class="sk" style="--i:${5 + k}"></span>`).join('')}</div>`;
      } else {
        s += lines([96, 90, 62], 2);
      }
      return `<div class="mm-sl">${wrap('sk-mm', s, 'Загрузка')}</div>`;
    },
    // Пункты с иконкой и двумя строками (список моделей)
    options(n = 3) {
      let s = '';
      for (let i = 0; i < n; i++) {
        s += `<div class="sk-opt"><span class="sk sk-ico" style="--i:${i}"></span><span class="sk-col">${bar([34, 28, 40][i % 3], i)}${bar([82, 74, 88][i % 3], i)}</span></div>`;
      }
      return wrap('sk-opts', s, 'Загрузка');
    },
    // Простые строки-карточки (список воркспейсов)
    rows(n = 3) {
      let s = '';
      for (let i = 0; i < n; i++) s += `<div class="sk-row">${bar([46, 62, 38][i % 3], i)}</div>`;
      return wrap('sk-rows', s, 'Загрузка');
    },
    // Одна плашка внутри строки текста
    inline(px = 96) {
      return `<span class="sk-wrap sk-inline" role="status" aria-label="Загрузка"><span class="sk" style="width:${px}px"></span></span>`;
    },
  };
  window.Skeleton = Skeleton;
})();
