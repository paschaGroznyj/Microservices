/* media-pro.js — Pro-разметка медиа, которое прислал агент.
 * Один модуль для CatchPM (app.js) и Sigma (app_sigma.js). В лайтбоксе
 * _openMediaLightbox появляется кнопка «Pro»; по ней поверх лайтбокса
 * открывается редактор в shadow root (свои стили и id не пересекаются с
 * приложением). Правки уходят обычным сообщением в текущий чат: текст для
 * агента + JSON-разметка. Загрузки своих файлов здесь нет.
 */
(function () {
'use strict';
const CSS_URL = {
    image: '/static/css/media-pro-image.css',
    video: '/static/css/media-pro-video.css',
};
const CSS_CACHE = {};
// Стили редактора лежат отдельными файлами и вставляются в shadow root.
// Грузим один раз на вид редактора; при ошибке промис сбрасывается.
function loadCss(kind) {
    if (!CSS_CACHE[kind]) {
        CSS_CACHE[kind] = fetch(CSS_URL[kind], { credentials: 'same-origin' })
            .then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.text(); })
            .catch((e) => { delete CSS_CACHE[kind]; throw e; });
    }
    return CSS_CACHE[kind];
}
const IMG_HTML = "<svg width=\"0\" height=\"0\" style=\"position:absolute\" aria-hidden=\"true\"><defs><symbol id=\"i-back\" viewBox=\"0 0 24 24\"><path d=\"M15 5l-7 7 7 7\"/></symbol><symbol id=\"i-x\" viewBox=\"0 0 24 24\"><path d=\"M6 6l12 12M18 6L6 18\"/></symbol><symbol id=\"i-check\" viewBox=\"0 0 24 24\"><path d=\"M5 12.5l4.5 4.5L19 7.5\"/></symbol><symbol id=\"i-undo\" viewBox=\"0 0 24 24\"><path d=\"M9 14L4 9l5-5\"/><path d=\"M4 9h10.5a5.5 5.5 0 010 11H11\"/></symbol><symbol id=\"i-redo\" viewBox=\"0 0 24 24\"><path d=\"M15 14l5-5-5-5\"/><path d=\"M20 9H9.5a5.5 5.5 0 000 11H13\"/></symbol><symbol id=\"i-cmp\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"5\" width=\"17\" height=\"14\" rx=\"2.5\"/><path d=\"M12 3v18\"/><path d=\"M12 5h6a2.5 2.5 0 012.5 2.5v9A2.5 2.5 0 0118 19h-6z\" fill=\"currentColor\" stroke=\"none\" opacity=\".35\"/></symbol><symbol id=\"i-list\" viewBox=\"0 0 24 24\"><path d=\"M9 6.5h11M9 12h11M9 17.5h11\"/><circle cx=\"4.5\" cy=\"6.5\" r=\"1\"/><circle cx=\"4.5\" cy=\"12\" r=\"1\"/><circle cx=\"4.5\" cy=\"17.5\" r=\"1\"/></symbol><symbol id=\"i-send\" viewBox=\"0 0 24 24\"><path d=\"M12 19V5M6 11l6-6 6 6\"/></symbol><symbol id=\"i-pro\" viewBox=\"0 0 24 24\"><path d=\"M4 20l4-1 10.5-10.5a2.1 2.1 0 00-3-3L5 16z\"/><path d=\"M14 7l3 3\"/><path d=\"M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z\"/></symbol><symbol id=\"i-imgadd\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"4.5\" width=\"17\" height=\"15\" rx=\"3\"/><circle cx=\"9\" cy=\"9.5\" r=\"1.6\"/><path d=\"M4 17.5l4.6-4.6 3.4 3.4 2.6-2.6 5.4 5.4\"/></symbol><symbol id=\"i-dl\" viewBox=\"0 0 24 24\"><path d=\"M12 4v11M7 10.5l5 5 5-5M5 19.5h14\"/></symbol><symbol id=\"i-trash\" viewBox=\"0 0 24 24\"><path d=\"M4.5 7h15M10 4h4M6.5 7l.8 12a1.8 1.8 0 001.8 1.6h5.8a1.8 1.8 0 001.8-1.6l.8-12M10 11v6M14 11v6\"/></symbol><symbol id=\"i-copy\" viewBox=\"0 0 24 24\"><rect x=\"8.5\" y=\"8.5\" width=\"11\" height=\"11\" rx=\"2.5\"/><path d=\"M15.5 8.5V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7.5a2 2 0 002 2h2.5\"/></symbol><symbol id=\"i-sun2\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4\"/></symbol><symbol id=\"i-moon\" viewBox=\"0 0 24 24\"><path d=\"M19.5 14.5A8 8 0 019.5 4.5a8 8 0 1010 10z\"/></symbol><!-- вкладки --><symbol id=\"t-ai\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"5.5\" width=\"14\" height=\"14\" rx=\"3\"/><path d=\"M4 17l4-4.5 3 3 2-2 4 4\"/><path d=\"M19 2.5l.9 2.1 2.1.9-2.1.9L19 8.5l-.9-2.1-2.1-.9 2.1-.9z\"/></symbol><symbol id=\"t-crop\" viewBox=\"0 0 24 24\"><path d=\"M7 3v14h14\"/><path d=\"M3 7h14v14\"/></symbol><symbol id=\"t-style\" viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"5\" width=\"18\" height=\"14\" rx=\"2.5\"/><path d=\"M3 8.5h18M3 15.5h18M7 5v3.5M11 5v3.5M15 5v3.5M7 15.5V19M11 15.5V19M15 15.5V19\"/></symbol><symbol id=\"t-adjust\" viewBox=\"0 0 24 24\"><path d=\"M4 8h16M4 16h16\"/><circle cx=\"9\" cy=\"8\" r=\"2.4\" fill=\"var(--panel)\"/><circle cx=\"15\" cy=\"16\" r=\"2.4\" fill=\"var(--panel)\"/></symbol><symbol id=\"t-beauty\" viewBox=\"0 0 24 24\"><path d=\"M9 3.5c-3 1-4.5 3.8-4.5 7 0 2 .8 3 .8 4.5L4 17.5h2.5c0 2 1.3 3 3.5 3\"/><path d=\"M9.5 9.5c.8.6 1.6.6 2.4 0\"/><path d=\"M17.5 7l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z\"/></symbol><symbol id=\"t-draw\" viewBox=\"0 0 24 24\"><path d=\"M3.5 20.5c2-.2 3.4-1 4-2.6\"/><path d=\"M7.5 17.9l-1.4-1.4L16.8 5.8a2 2 0 012.8 2.8z\"/><path d=\"M15 7.6l1.4 1.4\"/></symbol><symbol id=\"t-text\" viewBox=\"0 0 24 24\"><path d=\"M5 7V5h14v2M12 5v14M9 19h6\"/></symbol><symbol id=\"t-mosaic\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2.5\"/><path d=\"M4 9.3h16M4 14.6h16M9.3 4v16M14.6 4v16\"/><path d=\"M4 4h5.3v5.3H4zM14.6 9.3h5.4v5.3h-5.4zM9.3 14.6h5.3V20H9.3z\" fill=\"currentColor\" stroke=\"none\" opacity=\".45\"/></symbol><symbol id=\"t-frame\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"2.5\"/><rect x=\"8\" y=\"8\" width=\"8\" height=\"8\" rx=\"1\" fill=\"currentColor\" stroke=\"none\" opacity=\".45\"/></symbol><symbol id=\"t-effect\" viewBox=\"0 0 24 24\"><path d=\"M10 3.5l1.9 4.6 5 .4-3.8 3.2 1.2 4.8L10 13.9l-4.3 2.6 1.2-4.8L3 8.5l5-.4z\"/><path d=\"M18.5 15l.6 1.5 1.5.6-1.5.6-.6 1.5-.6-1.5-1.5-.6 1.5-.6zM17 3.5v2M16 4.5h2\"/></symbol><symbol id=\"t-cutout\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\" stroke-dasharray=\"3 2.6\"/><path d=\"M8.5 15.5c.8-2.5 2-4 3.5-4s2.7 1.5 3.5 4\"/><circle cx=\"12\" cy=\"8.5\" r=\"1.8\"/></symbol><symbol id=\"t-region\" viewBox=\"0 0 24 24\"><path d=\"M4 8V5.5A1.5 1.5 0 015.5 4H8M16 4h2.5A1.5 1.5 0 0120 5.5V8M20 16v2.5a1.5 1.5 0 01-1.5 1.5H16M8 20H5.5A1.5 1.5 0 014 18.5V16\"/><path d=\"M8.5 10h7M8.5 14h4.5\"/></symbol><!-- инструменты --><symbol id=\"s-rot\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"9\" width=\"11\" height=\"11\" rx=\"2\"/><path d=\"M9 4.5h4.5a6 6 0 016 6V13\"/><path d=\"M17 11l2.5 2.5L22 11\"/></symbol><symbol id=\"s-flip\" viewBox=\"0 0 24 24\"><path d=\"M12 3v18\" stroke-dasharray=\"2 2.4\"/><path d=\"M9 7L3.5 17H9zM15 7l5.5 10H15z\"/></symbol><symbol id=\"s-free\" viewBox=\"0 0 24 24\"><path d=\"M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4\"/></symbol><symbol id=\"s-reset\" viewBox=\"0 0 24 24\"><path d=\"M4.5 12a7.5 7.5 0 102.2-5.3L4.5 9\"/><path d=\"M4.5 4.5V9H9\"/></symbol><symbol id=\"s-bright\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"3.8\"/><path d=\"M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7\"/></symbol><symbol id=\"s-contrast\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 010 17z\" fill=\"currentColor\" stroke=\"none\"/></symbol><symbol id=\"s-sat\" viewBox=\"0 0 24 24\"><path d=\"M12 3.5s6 6.4 6 10.5a6 6 0 01-12 0c0-4.1 6-10.5 6-10.5z\"/><path d=\"M9 14.5a3 3 0 003 3\"/></symbol><symbol id=\"s-temp\" viewBox=\"0 0 24 24\"><path d=\"M10 14.5V5a2 2 0 014 0v9.5a4 4 0 11-4 0z\"/><path d=\"M12 9v7\"/></symbol><symbol id=\"s-tint\" viewBox=\"0 0 24 24\"><path d=\"M12 3.5s6 6.4 6 10.5a6 6 0 01-12 0c0-4.1 6-10.5 6-10.5z\"/><path d=\"M6.3 15.5A6 6 0 0012 20a6 6 0 004-1.5z\" fill=\"currentColor\" stroke=\"none\" opacity=\".5\"/></symbol><symbol id=\"s-hl\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5v17M15 4.2v15.6M18 6.2v11.6\"/></symbol><symbol id=\"s-shadow\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 000 17z\" fill=\"currentColor\" stroke=\"none\" opacity=\".75\"/><path d=\"M15 4.2v15.6M18 6.2v11.6\"/></symbol><symbol id=\"s-enh\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 7v10M7.7 9.5l8.6 5M7.7 14.5l8.6-5\"/></symbol><symbol id=\"s-sharp\" viewBox=\"0 0 24 24\"><path d=\"M12 4l8.5 15h-17z\"/></symbol><symbol id=\"s-fade\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2.5\"/><path d=\"M8 4v16M12 4v16M16 4v16\" opacity=\".55\"/></symbol><symbol id=\"s-vig\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"3.5\" fill=\"currentColor\" stroke=\"none\" opacity=\".8\"/><circle cx=\"12\" cy=\"12\" r=\"4.5\" fill=\"var(--panel)\" stroke=\"none\"/></symbol><symbol id=\"s-wand\" viewBox=\"0 0 24 24\"><path d=\"M4 20L15 9M13.5 7.5l3 3\"/><path d=\"M18 3l.7 1.6 1.6.7-1.6.7L18 7.6l-.7-1.6-1.6-.7 1.6-.7zM8 3.5l.5 1.2 1.2.5-1.2.5L8 6.9l-.5-1.2-1.2-.5 1.2-.5zM19.5 13l.5 1.2 1.2.5-1.2.5-.5 1.2-.5-1.2-1.2-.5 1.2-.5z\"/></symbol><symbol id=\"s-up\" viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"4.5\" width=\"13\" height=\"13\" rx=\"2.5\"/><path d=\"M3.5 15l3.5-4 3 3\"/><circle cx=\"17.5\" cy=\"17\" r=\"4\"/><path d=\"M17.5 19v-4M15.8 16.5l1.7-1.7 1.7 1.7\"/></symbol><symbol id=\"s-erase\" viewBox=\"0 0 24 24\"><path d=\"M9 20h11\"/><path d=\"M5.5 16.5l9.8-9.8a2 2 0 012.8 0l1.2 1.2a2 2 0 010 2.8L11.5 18.5H7.5z\"/><path d=\"M10 12l4.5 4.5\"/></symbol><symbol id=\"s-glare\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"4.5\" width=\"17\" height=\"15\" rx=\"2.5\"/><path d=\"M7 15l6-6M10 16.5l6.5-6.5\" opacity=\".7\"/></symbol><symbol id=\"s-people\" viewBox=\"0 0 24 24\"><circle cx=\"9\" cy=\"8\" r=\"3\"/><path d=\"M3.5 19c.6-3.3 2.7-5 5.5-5s4.9 1.7 5.5 5\"/><path d=\"M16 5l5 5M21 5l-5 5\"/></symbol><symbol id=\"s-expand\" viewBox=\"0 0 24 24\"><rect x=\"8\" y=\"8\" width=\"8\" height=\"8\" rx=\"1.5\"/><path d=\"M3.5 8V3.5H8M16 3.5h4.5V8M20.5 16v4.5H16M8 20.5H3.5V16\"/></symbol><symbol id=\"s-face\" viewBox=\"0 0 24 24\"><ellipse cx=\"12\" cy=\"12\" rx=\"7\" ry=\"8.5\"/><path d=\"M9 10.5q1 1 2 0M13 10.5q1 1 2 0M9.5 15q2.5 2 5 0\"/></symbol><symbol id=\"s-slim\" viewBox=\"0 0 24 24\"><path d=\"M7 5c-1.5 2-2 4.5-1.5 7.5.6 3.5 3.5 7 6.5 7s5.9-3.5 6.5-7c.5-3-0-5.5-1.5-7.5\"/><path d=\"M3 12h3M18 12h3M4.5 10.5L6 12l-1.5 1.5M19.5 10.5L18 12l1.5 1.5\"/></symbol><symbol id=\"s-eye\" viewBox=\"0 0 24 24\"><path d=\"M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/></symbol><symbol id=\"s-lip\" viewBox=\"0 0 24 24\"><path d=\"M6 21v-8h5v8zM6.5 13V9.5L10.5 7v6\"/><path d=\"M15 21l1.5-7h3l1.5 7zM16 14c0-3 .5-6 2-8.5 1.5 2.5 2 5.5 2 8.5\"/></symbol><symbol id=\"s-brush\" viewBox=\"0 0 24 24\"><path d=\"M4 20c4-1 4-6 8.5-6M20 4l-7.5 10\"/><path d=\"M4 20c2.5-5 5.5-8 8.5-6\" opacity=\".4\"/></symbol><symbol id=\"s-marker\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-width=\"4.5\" opacity=\".45\"/></symbol><symbol id=\"s-neon\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-width=\"5\" opacity=\".25\"/><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\"/></symbol><symbol id=\"s-crayon\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-width=\"3\" stroke-dasharray=\"1 1.2\"/></symbol><symbol id=\"s-dash\" viewBox=\"0 0 24 24\"><path d=\"M4 18.5c3-3 6-1 8-3s3-6 8-8\" stroke-dasharray=\"3.5 3\"/></symbol><symbol id=\"s-pixel\" viewBox=\"0 0 24 24\"><path d=\"M4 4h5v5H4zM9 9h5v5H9zM14 4h6v5h-6zM4 14h5v6H4zM14 14h6v6h-6z\" fill=\"currentColor\" stroke=\"none\" opacity=\".55\"/><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"1.5\"/></symbol><symbol id=\"s-blur\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\" stroke-dasharray=\"1.5 2.5\"/><circle cx=\"12\" cy=\"12\" r=\"4.5\" opacity=\".6\"/><circle cx=\"12\" cy=\"12\" r=\"1.2\" fill=\"currentColor\"/></symbol><symbol id=\"s-hatch\" viewBox=\"0 0 24 24\"><rect x=\"4\" y=\"4\" width=\"16\" height=\"16\" rx=\"2.5\"/><path d=\"M4 10l6-6M4 16L16 4M8 20L20 8M14 20l6-6\"/></symbol><symbol id=\"s-bokeh\" viewBox=\"0 0 24 24\"><circle cx=\"8\" cy=\"9\" r=\"4\"/><circle cx=\"16.5\" cy=\"7\" r=\"2.5\" opacity=\".6\"/><circle cx=\"15\" cy=\"16\" r=\"4.5\" opacity=\".8\"/></symbol><symbol id=\"s-grain\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"3\"/><path d=\"M8 8h.01M12 7h.01M16 9h.01M7 12h.01M11 12h.01M15 13h.01M9 16h.01M13 16.5h.01M17 16h.01\" stroke-width=\"2.4\"/></symbol><symbol id=\"s-tilt\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"3.5\" width=\"17\" height=\"17\" rx=\"3\"/><path d=\"M3.5 9.5h17M3.5 14.5h17\"/><path d=\"M3.5 3.5h17v6h-17zM3.5 14.5h17v6h-17z\" fill=\"currentColor\" stroke=\"none\" opacity=\".25\"/></symbol><symbol id=\"s-color\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M12 3.5a8.5 8.5 0 018.5 8.5H12z\" fill=\"currentColor\" stroke=\"none\" opacity=\".5\"/></symbol><symbol id=\"s-tap\" viewBox=\"0 0 24 24\"><path d=\"M10 11V5.5a1.5 1.5 0 013 0V11l4 .8a2 2 0 011.6 2.3l-.9 4.4A2.5 2.5 0 0115.2 20.5H11a2.5 2.5 0 01-2-1l-3.2-4.2a1.4 1.4 0 012.1-1.8L10 15\"/><path d=\"M7 4.5a5 5 0 019 0\"/></symbol><symbol id=\"i-reset\" viewBox=\"0 0 24 24\"><path d=\"M4.5 12a7.5 7.5 0 1 0 2.2-5.3\"/><path d=\"M4.5 4.5v4h4\"/></symbol></defs></svg><section class=\"ed\" id=\"ed\" aria-label=\"Pro-разметка изображения\"><div class=\"ed__hd\"><button class=\"ibtn\" id=\"edBack\" type=\"button\" title=\"Назад (Esc)\"><svg class=\"i\"><use href=\"#i-back\"/></svg></button><div class=\"ed__ttl\"><b id=\"edName\"></b><span id=\"edDim\"></span></div><button class=\"ibtn\" id=\"undoBtn\" type=\"button\" title=\"Отменить (Ctrl+Z)\" disabled><svg class=\"i\"><use href=\"#i-undo\"/></svg></button><button class=\"ibtn\" id=\"redoBtn\" type=\"button\" title=\"Повторить (Ctrl+Shift+Z)\" disabled><svg class=\"i\"><use href=\"#i-redo\"/></svg></button><button class=\"ibtn\" id=\"resetBtn\" type=\"button\" title=\"Сбросить все правки\" aria-label=\"Сбросить все правки\" disabled><svg class=\"i\"><use href=\"#i-reset\"/></svg></button><button class=\"ibtn only-m\" id=\"listBtn\" type=\"button\" title=\"Список правок\"><svg class=\"i\"><use href=\"#i-list\"/></svg><span class=\"badge\" id=\"listBadge\" hidden>0</span></button><button class=\"send-btn only-m\" id=\"sendM\" type=\"button\" disabled><svg class=\"i\"><use href=\"#i-send\"/></svg><span>Отправить</span></button></div><div class=\"ed__main\"><div class=\"stage\" id=\"stage\"><div class=\"fr\" id=\"fr\"><div class=\"pic\" id=\"pic\"><div class=\"rot\" id=\"rot\"><img id=\"img\" src=\"\" alt=\"\"><canvas id=\"ink\"></canvas><div class=\"vig\" id=\"vig\"></div><div class=\"grain\" id=\"grain\"></div><div class=\"ov\" id=\"ov\"></div></div></div></div><div class=\"hint\" id=\"hint\"></div><button class=\"cmp\" id=\"cmpBtn\" type=\"button\" title=\"Удерживайте, чтобы увидеть оригинал\"><svg class=\"i\"><use href=\"#i-cmp\"/></svg>Оригинал</button></div><div class=\"opts\" id=\"opts\"></div><nav class=\"tabs\" id=\"tabs\"></nav></div><div class=\"scrim\" id=\"scrim\"></div><aside class=\"ed__side\" id=\"side\"><div class=\"side__hd\">Правки <span class=\"cnt\" id=\"cnt\">0</span><span class=\"sp\"></span><button class=\"ibtn\" id=\"clearBtn\" type=\"button\" title=\"Очистить всё\" disabled><svg class=\"i\"><use href=\"#i-trash\"/></svg></button><button class=\"ibtn only-m\" id=\"sideClose\" type=\"button\" title=\"Свернуть\"><svg class=\"i\"><use href=\"#i-x\"/></svg></button></div><div class=\"list\" id=\"list\"></div><div class=\"send\"><textarea class=\"ta\" id=\"prompt\" placeholder=\"Общий комментарий для агента: стиль, формат, что важно сохранить…\"></textarea><div class=\"flex\"><span class=\"desc\" id=\"sendDesc\">Добавьте правку или комментарий</span><button class=\"send-btn\" id=\"sendBtn\" type=\"button\" disabled><svg class=\"i\"><use href=\"#i-send\"/></svg><span>Отправить агенту</span></button></div></div></aside></section><div class=\"md\" id=\"md\"><div class=\"md__b\"><div class=\"md__h\"><b>Проверьте перед отправкой</b><div class=\"md__seg\" id=\"mdSeg\"><button class=\"on\" data-v=\"msg\" type=\"button\">Сообщение</button><button data-v=\"json\" type=\"button\">JSON</button></div><button class=\"ibtn\" id=\"mdClose\" type=\"button\" title=\"Закрыть\"><svg class=\"i\"><use href=\"#i-x\"/></svg></button></div><div class=\"md__c\"><div class=\"md__msg\" id=\"mdMsg\"></div><pre id=\"mdJson\" hidden></pre></div><div class=\"md__f\"><button class=\"mini\" id=\"mdCopy\" type=\"button\"><svg class=\"i\"><use href=\"#i-copy\"/></svg>Копировать JSON</button><button class=\"send-btn\" id=\"mdOk\" type=\"button\"><svg class=\"i\"><use href=\"#i-check\"/></svg><span>Отправить в чат</span></button></div></div></div><div class=\"toast\" id=\"toast\"></div>";
const VID_HTML = "<svg width=\"0\" height=\"0\" style=\"position:absolute\" aria-hidden=\"true\"><defs><symbol id=\"i-play\" viewBox=\"0 0 24 24\"><path d=\"M8 5.6v12.8c0 .8.9 1.3 1.6.8l10-6.4a1 1 0 0 0 0-1.6l-10-6.4C8.9 4.3 8 4.8 8 5.6z\" fill=\"currentColor\" stroke=\"none\"/></symbol><symbol id=\"i-pause\" viewBox=\"0 0 24 24\"><rect x=\"6.5\" y=\"5\" width=\"3.8\" height=\"14\" rx=\"1.2\" fill=\"currentColor\" stroke=\"none\"/><rect x=\"13.7\" y=\"5\" width=\"3.8\" height=\"14\" rx=\"1.2\" fill=\"currentColor\" stroke=\"none\"/></symbol><symbol id=\"i-prev\" viewBox=\"0 0 24 24\"><path d=\"M14.5 6.5 9 12l5.5 5.5\"/></symbol><symbol id=\"i-next\" viewBox=\"0 0 24 24\"><path d=\"M9.5 6.5 15 12l-5.5 5.5\"/></symbol><symbol id=\"i-in\" viewBox=\"0 0 24 24\"><path d=\"M10 5H7v14h3\"/><path d=\"M12 12h6M15.5 9.5 18 12l-2.5 2.5\"/></symbol><symbol id=\"i-out\" viewBox=\"0 0 24 24\"><path d=\"M14 5h3v14h-3\"/><path d=\"M6 12h6M9.5 9.5 12 12l-2.5 2.5\"/></symbol><symbol id=\"i-undo\" viewBox=\"0 0 24 24\"><path d=\"M9 14 4.5 9.5 9 5\"/><path d=\"M4.5 9.5H14a5.5 5.5 0 0 1 0 11h-3\"/></symbol><symbol id=\"i-cut\" viewBox=\"0 0 24 24\"><circle cx=\"6.5\" cy=\"7\" r=\"2.6\"/><circle cx=\"6.5\" cy=\"17\" r=\"2.6\"/><path d=\"M8.7 8.5 19.5 17M8.7 15.5 19.5 7\"/></symbol><symbol id=\"i-text\" viewBox=\"0 0 24 24\"><path d=\"M5.5 7V5.5h13V7M12 5.5v13M9 18.5h6\"/></symbol><symbol id=\"i-volume\" viewBox=\"0 0 24 24\"><path d=\"M4.5 9.8v4.4c0 .4.3.8.8.8h2.6l4 3.4c.5.4 1.1 0 1.1-.6V6.2c0-.6-.6-1-1.1-.6l-4 3.4H5.3c-.5 0-.8.4-.8.8z\"/><path d=\"M16 9.2a4 4 0 0 1 0 5.6M18.5 6.8a7.5 7.5 0 0 1 0 10.4\"/></symbol><symbol id=\"i-speed\" viewBox=\"0 0 24 24\"><path d=\"M4.6 17a8.5 8.5 0 1 1 14.8 0\"/><path d=\"M12 14l4.2-4.2\"/><circle cx=\"12\" cy=\"14\" r=\"1.3\" fill=\"currentColor\"/></symbol><symbol id=\"i-note\" viewBox=\"0 0 24 24\"><path d=\"M6 20.5V4.5\"/><path d=\"M6 5h11.2c.6 0 .9.6.6 1.1L16 9.2l1.8 3.1c.3.5 0 1.1-.6 1.1H6\"/></symbol><symbol id=\"i-trash\" viewBox=\"0 0 24 24\"><path d=\"M4.5 7h15M9.5 7V5.2c0-.4.3-.7.7-.7h3.6c.4 0 .7.3.7.7V7M6.5 7l.8 11.6c.1.8.7 1.4 1.5 1.4h6.4c.8 0 1.4-.6 1.5-1.4L17.5 7M10 11v5M14 11v5\"/></symbol><symbol id=\"i-send\" viewBox=\"0 0 24 24\"><path d=\"M12 19V5.5M6.5 11 12 5.5l5.5 5.5\"/></symbol><symbol id=\"i-pro\" viewBox=\"0 0 24 24\"><path d=\"M4 7h8.5M17.5 7H20M4 17h2.5M11.5 17H20\"/><circle cx=\"15\" cy=\"7\" r=\"2.4\"/><circle cx=\"9\" cy=\"17\" r=\"2.4\"/></symbol><symbol id=\"i-upload\" viewBox=\"0 0 24 24\"><path d=\"M12 15.5V4.5M7.5 9 12 4.5 16.5 9M5 15v3.2c0 .7.6 1.3 1.3 1.3h11.4c.7 0 1.3-.6 1.3-1.3V15\"/></symbol><symbol id=\"i-theme\" viewBox=\"0 0 24 24\"><circle cx=\"12\" cy=\"12\" r=\"7.5\"/><path d=\"M12 4.5a7.5 7.5 0 0 1 0 15z\" fill=\"currentColor\"/></symbol><symbol id=\"i-eye\" viewBox=\"0 0 24 24\"><path d=\"M2.8 12S6.2 5.8 12 5.8 21.2 12 21.2 12 17.8 18.2 12 18.2 2.8 12 2.8 12z\"/><circle cx=\"12\" cy=\"12\" r=\"2.6\"/></symbol><symbol id=\"i-region\" viewBox=\"0 0 24 24\"><path d=\"M7 3.5V17h13.5\"/><path d=\"M3.5 7H17v13.5\"/></symbol><symbol id=\"i-copy\" viewBox=\"0 0 24 24\"><rect x=\"8.5\" y=\"8.5\" width=\"11\" height=\"11\" rx=\"2.2\"/><path d=\"M15.5 8.5V6.2c0-.9-.7-1.7-1.7-1.7H6.2c-.9 0-1.7.8-1.7 1.7v7.6c0 1 .8 1.7 1.7 1.7h2.3\"/></symbol><symbol id=\"i-check\" viewBox=\"0 0 24 24\"><path d=\"m5.5 12.5 4.2 4.2 8.8-9.2\"/></symbol><symbol id=\"i-spark\" viewBox=\"0 0 24 24\"><path d=\"M12 3.5c.4 3.9 2.2 6.4 6.5 7.3-4.3.9-6.1 3.4-6.5 7.7-.4-4.3-2.2-6.8-6.5-7.7 4.3-.9 6.1-3.4 6.5-7.3z\"/><path d=\"M18.5 16v4M16.5 18h4\"/></symbol><symbol id=\"i-film\" viewBox=\"0 0 24 24\"><rect x=\"3.5\" y=\"5\" width=\"17\" height=\"14\" rx=\"2.5\"/><path d=\"M7.5 5v14M16.5 5v14M3.5 9.5h4M3.5 14.5h4M16.5 9.5h4M16.5 14.5h4\"/></symbol><symbol id=\"i-kbd\" viewBox=\"0 0 24 24\"><rect x=\"3\" y=\"6.5\" width=\"18\" height=\"11\" rx=\"2.2\"/><path d=\"M7 10h.01M10.3 10h.01M13.7 10h.01M17 10h.01M8 14h8\"/></symbol><symbol id=\"i-reset\" viewBox=\"0 0 24 24\"><path d=\"M4.5 12a7.5 7.5 0 1 0 2.2-5.3\"/><path d=\"M4.5 4.5v4h4\"/></symbol></defs></svg><div id=\"image-lightbox\" class=\"image-lightbox gc-lightbox image-lightbox--shown\"><div class=\"image-lightbox__box\" id=\"lbBox\"><div class=\"image-lightbox__bar\"><span class=\"image-lightbox__name\" id=\"lbName\" title=\"\"></span><span class=\"image-lightbox__dim\" id=\"lbDim\" title=\"Разрешение\">—</span><span class=\"lb-spacer\"></span><button class=\"image-lightbox__reset\" id=\"resetBtn\" type=\"button\" title=\"Сбросить все правки\" aria-label=\"Сбросить все правки\" disabled><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-reset\"/></svg></button><button class=\"image-lightbox__pro\" id=\"proBtn\" type=\"button\" aria-pressed=\"false\" title=\"Разметить правки для агента (P)\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-pro\"/></svg><span>Pro</span></button><a class=\"image-lightbox__dl\" id=\"lbDl\" href=\"#\" download title=\"Скачать\"><svg viewBox=\"0 0 14 14\" fill=\"none\"><path d=\"M7 2v7M4 6l3 3 3-3M2 11h10\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/></svg></a><button class=\"image-lightbox__close\" id=\"lbClose\" type=\"button\" title=\"Закрыть\" aria-label=\"Закрыть\"><svg viewBox=\"0 0 24 24\" fill=\"none\"><path d=\"M6 6l12 12M18 6L6 18\" stroke=\"currentColor\" stroke-width=\"1.8\" stroke-linecap=\"round\"/></svg></button></div><div class=\"image-lightbox__media-container is-ready\" id=\"stage\"><video class=\"image-lightbox__video\" id=\"vid\" controls playsinline webkit-playsinline preload=\"auto\"></video><div class=\"vpe-ovl\" id=\"ovl\"></div><div class=\"vpe-reg\" id=\"reg\"></div></div><section class=\"vpe-tlwrap vpe-only\"><div class=\"vpe-transport\"><button class=\"vpe-btn vpe-play\" id=\"playBtn\" type=\"button\" title=\"Пуск/пауза (Space)\" aria-label=\"Пуск/пауза\"><svg class=\"ic\" aria-hidden=\"true\"><use id=\"playIco\" href=\"#i-play\"/></svg></button><span class=\"vpe-tc mono\" id=\"tc\">00:00.00 / 00:00.00</span><button class=\"vpe-btn vpe-btn--ic vpe-step\" type=\"button\" data-step=\"-1\" title=\"Кадр назад (←), Shift — секунда\" aria-label=\"Кадр назад\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-prev\"/></svg></button><button class=\"vpe-btn vpe-btn--ic vpe-step\" type=\"button\" data-step=\"1\" title=\"Кадр вперёд (→), Shift — секунда\" aria-label=\"Кадр вперёд\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-next\"/></svg></button><span class=\"vpe-sep vpe-step\"></span><button class=\"vpe-btn\" id=\"inBtn\" type=\"button\" title=\"Начало участка (I)\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-in\"/></svg><span class=\"lbl\">Начало</span><kbd>I</kbd></button><button class=\"vpe-btn\" id=\"outBtn\" type=\"button\" title=\"Конец участка (O)\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-out\"/></svg><span class=\"lbl\">Конец</span><kbd>O</kbd></button><span class=\"vpe-selinfo\" id=\"selInfo\"></span><span class=\"lb-spacer\"></span><label class=\"vpe-sw\" title=\"Проигрывать с учётом правок: пропуск вырезок, текст, громкость, скорость\"><input type=\"checkbox\" id=\"prevChk\" checked><span class=\"lg\">Предпросмотр</span><span class=\"sm\">Превью</span></label><button class=\"vpe-btn vpe-btn--ic\" id=\"undoBtn\" type=\"button\" title=\"Отменить (Ctrl+Z)\" aria-label=\"Отменить\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-undo\"/></svg></button></div><div class=\"vpe-tools\"><button class=\"vpe-btn vpe-tool\" type=\"button\" data-add=\"cut\" style=\"--c:var(--cut)\"><i class=\"vpe-ico\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-cut\"/></svg></i>Вырезать<kbd>C</kbd></button><button class=\"vpe-btn vpe-tool\" type=\"button\" data-add=\"text\" style=\"--c:var(--txt)\"><i class=\"vpe-ico\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-text\"/></svg></i>Текст<kbd>T</kbd></button><button class=\"vpe-btn vpe-tool\" type=\"button\" data-add=\"volume\" style=\"--c:var(--vol)\"><i class=\"vpe-ico\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-volume\"/></svg></i>Звук<kbd>V</kbd></button><button class=\"vpe-btn vpe-tool\" type=\"button\" data-add=\"speed\" style=\"--c:var(--spd)\"><i class=\"vpe-ico\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-speed\"/></svg></i>Скорость<kbd>S</kbd></button><button class=\"vpe-btn vpe-tool\" type=\"button\" data-add=\"note\" style=\"--c:var(--note)\"><i class=\"vpe-ico\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-note\"/></svg></i>Заметка<kbd>M</kbd></button><button class=\"vpe-btn vpe-tool\" type=\"button\" data-add=\"region\" style=\"--c:var(--reg)\" title=\"Выделить область кадра на участке и прокомментировать (R)\"><i class=\"vpe-ico\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-region\"/></svg></i>Область<kbd>R</kbd></button></div><div class=\"vpe-tl\" id=\"tl\"><div class=\"vpe-ruler mono\" id=\"ruler\"></div><div class=\"vpe-lane vpe-lane--film\"><canvas id=\"film\"></canvas></div><div class=\"vpe-lane\" data-lane=\"cut\"><span class=\"vpe-lane__lbl\">Монтаж</span></div><div class=\"vpe-lane\" data-lane=\"text\"><span class=\"vpe-lane__lbl\">Текст</span></div><div class=\"vpe-lane vpe-lane--wave\" data-lane=\"audio\"><canvas id=\"wave\"></canvas><span class=\"vpe-lane__lbl\">Звук</span><span class=\"vpe-wavemsg\" id=\"waveMsg\"></span></div><div class=\"vpe-lane\" data-lane=\"region\"><span class=\"vpe-lane__lbl\">Области</span></div><div class=\"vpe-lane\" data-lane=\"note\"><span class=\"vpe-lane__lbl\">Заметки</span></div><div class=\"vpe-selband\" id=\"selband\"></div><div class=\"vpe-ph\" id=\"ph\"><i></i></div></div><div class=\"vpe-hint\">Протяните по таймлайну, чтобы выделить участок, или поставьте I/O. Блоки двигаются, края тянутся. «Область» — рамка на кадре: тяните по видео, чтобы её нарисовать. Space — пауза, ←/→ — кадр, Del — удалить, Ctrl+Z — отмена.</div></section><aside class=\"vpe-side vpe-only\"><div class=\"vpe-side__hd\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-spark\"/></svg><b>Правки для агента</b><span id=\"cnt\">0</span></div><div class=\"vpe-list\" id=\"list\"></div><div class=\"vpe-send\"><textarea id=\"prompt\" rows=\"3\" placeholder=\"Комментарий агенту: стиль титров, что сохранить, куда положить результат…\"></textarea><label class=\"vpe-sw vpe-sw--row\"><input type=\"checkbox\" id=\"keepOrig\" checked><span>Результат новым файлом, исходник не трогать</span></label><button class=\"vpe-sendbtn\" id=\"sendBtn\" type=\"button\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-send\"/></svg><span>Отправить агенту</span><kbd>Ctrl+Enter</kbd></button></div></aside></div></div><div class=\"vpe-modal\" id=\"modal\" hidden><div class=\"vpe-modal__box\" role=\"dialog\" aria-label=\"Проверка перед отправкой\"><div class=\"vpe-modal__hd\"><b><svg class=\"ic ic--hd\" aria-hidden=\"true\"><use href=\"#i-send\"/></svg>Проверьте перед отправкой</b><span class=\"vpe-seg\"><button class=\"vpe-tab on\" type=\"button\" data-tab=\"msg\">Сообщение</button><button class=\"vpe-tab\" type=\"button\" data-tab=\"json\">JSON</button></span></div><pre class=\"mono\" id=\"mOut\"></pre><div class=\"vpe-modal__ft\"><button class=\"vpe-btn\" id=\"mCopy\" type=\"button\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-copy\"/></svg>Копировать</button><button class=\"vpe-btn pri\" id=\"mOk\" type=\"button\"><svg class=\"ic\" aria-hidden=\"true\"><use href=\"#i-send\"/></svg>Отправить в чат</button></div></div></div><div class=\"vpe-toast\" id=\"toast\"></div>";

/* ── редактор изображения (перенос стенда image_pro.html) ── */
function mountImage(R, host, MP, __on) {


const $=s=>R.querySelector(s), $$=s=>[...R.querySelectorAll(s)];
const P=new URLSearchParams('');
const TOUCH=matchMedia('(pointer:coarse)').matches;
const FILE=Object.assign({},MP.file);
const ic=(id,cls='i')=>`<svg class="${cls}"><use href="#${id}"/></svg>`;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const r3=v=>Math.round(v*1000)/1000;
const sgn=v=>v>0?'+'+v:v<0?'−'+(-v):'0';
const pct=v=>Math.round(v*100)+'%';

const TABS=[['ai','ИИ-ретушь','t-ai'],['crop','Кадрировать','t-crop'],['style','Стили','t-style'],['adjust','Коррекция','t-adjust'],['beauty','Красота','t-beauty'],['draw','Наброски','t-draw'],['text','Текст','t-text'],['mosaic','Мозаика','t-mosaic'],['frame','Рамки','t-frame'],['effect','Эффекты','t-effect'],['cutout','Вырезать','t-cutout'],['region','Область','t-region']];
const TAB=Object.fromEntries(TABS.map(t=>[t[0],{label:t[1],icon:t[2]}]));
const COL={ai:'--op-ai',crop:'--op-crop',style:'--op-style',adjust:'--op-adjust',beauty:'--op-beauty',draw:'--op-draw',text:'--op-text',mosaic:'--op-mosaic',frame:'--op-frame',effect:'--op-effect',cutout:'--op-ai',region:'--op-region'};
const AI=[['auto','Авто','s-wand'],['enhance','Улучшить','s-up'],['erase','Стереть объект','s-erase'],['glare','Убрать блики','s-glare'],['people','Убрать людей','s-people'],['expand','Расширить кадр','s-expand']];
const ASPECT=[['orig','Исходное',null],['free','Свободно',null],['1:1','1:1',1],['3:4','3:4',3/4],['4:3','4:3',4/3],['9:16','9:16',9/16],['16:9','16:9',16/9]];
const ADJ=[['bright','Яркость','s-bright',-100],['contrast','Контраст','s-contrast',-100],['sat','Насыщенность','s-sat',-100],['temp','Температура','s-temp',-100],['tint','Оттенок','s-tint',-100],['hl','Светлые участки','s-hl',-100],['shadow','Тени','s-shadow',-100],['enh','Усиление','s-enh',0],['sharp','Резкость','s-sharp',0],['fade','Затухание','s-fade',0],['vig','Виньетка','s-vig',0]];
const BEAUTY=[['smooth','Сглаживание','s-face'],['whiten','Осветление','s-bright'],['slim','Тонкое лицо','s-slim'],['eyes','Глаза','s-eye'],['makeup','Макияж','s-lip']];
const STYLES={'Простые':[['sunny','Солнечный','sepia(.25) saturate(1.3) brightness(1.06)'],['vivid','Насыщенный','saturate(1.6) contrast(1.05)'],['porcelain','Фарфор','brightness(1.12) saturate(.75) contrast(.92)'],['negative','Негатив','invert(1)'],['positive','Позитив','contrast(1.2) saturate(1.2)'],['mono','Ч/Б','grayscale(1) contrast(1.1)']],
'Портрет':[['soft','Нежный','brightness(1.08) saturate(.85) contrast(.9)'],['warm','Тёплый','sepia(.3) saturate(1.2)'],['cine','Кино','contrast(1.15) saturate(.8) hue-rotate(-8deg)'],['noir','Нуар','grayscale(1) contrast(1.4) brightness(.9)']],
'Пейзаж':[['sunset','Закат','sepia(.35) saturate(1.5) hue-rotate(-12deg)'],['forest','Лес','saturate(1.3) hue-rotate(12deg)'],['sea','Море','hue-rotate(-20deg) saturate(1.25)'],['fog','Туман','contrast(.8) brightness(1.1) saturate(.7)']],
'Съемка моментов':[['film','Плёнка','sepia(.2) contrast(1.1) saturate(.9)'],['retro','Ретро','sepia(.55) contrast(.95)'],['day','Яркий день','brightness(1.1) saturate(1.35)'],['cold','Холод','hue-rotate(-25deg) saturate(.9) brightness(1.05)']]};
const STYLE_BY=Object.fromEntries(Object.entries(STYLES).flatMap(([c,a])=>a.map(s=>[s[0],{cat:c,name:s[1],css:s[2]}])));
const BRUSH=[['eraser','Ластик','s-erase'],['pen','Кисть','s-brush'],['marker','Маркер','s-marker'],['neon','Неон','s-neon'],['crayon','Мелок','s-crayon'],['dash','Пунктир','s-dash']];
const COLORS=['#111111','#ffffff','#e5484d','#ff8a3d','#f5c518','#3fb950','#3b82f6','#a371f7'];
const MOS=[['eraser','Ластик','s-erase'],['pixel','Пиксели','s-pixel'],['blur','Размытие','s-blur'],['hatch','Штрихи','s-hatch']];
const TKIND=[['plain','Обычные'],['wm','Водяные знаки'],['date','Дата']];
const TSTYLE=[['shadow','Тень'],['outline','Обводка'],['box','Плашка']];
const FKIND=[['none','Без рамки'],['color','Сплошной цвет'],['poster','Постер']];
const FFORMAT=[['orig','Исходное',null],['full','Полный',null],['1:1','1:1',1],['4:3','4:3',4/3],['3:4','3:4',3/4],['9:16','9:16',9/16]];
const FCOLORS=['#111111','#ffffff','#f4c7c3','#f6dcc4','#cfe3c8','#c7dcef','#d9cdea'];
const EFF=[['bokeh','Боке','s-bokeh'],['grain','Зерно','s-grain'],['glow','Свечение','i-sun2'],['tilt','Тилт-шифт','s-tilt']];
const CUTBG=[['transparent','Прозрачный'],['white','Белый'],['blur','Размытый'],['color','Свой цвет']];
const RACT=[['comment','По комментарию'],['remove','Удалить объект'],['replace','Заменить'],['blur','Размыть'],['enhance','Улучшить'],['crop','Кадрировать сюда']];
const ACT_BY=Object.fromEntries(RACT);

const blank=()=>({
  crop:{aspect:'orig',rect:null,rot:0,flip:false,angle:0},
  style:{preset:null,strength:100},
  adjust:Object.fromEntries(ADJ.map(a=>[a[0],0])),
  beauty:Object.fromEntries(BEAUTY.map(a=>[a[0],0])),
  ai:{tasks:[],erase:[]},
  draw:{strokes:[]}, mosaic:{strokes:[]},
  text:[], region:[],
  frame:{kind:'none',format:'orig',color:'#ffffff'},
  effect:Object.fromEntries(EFF.map(a=>[a[0],0])),
  cutout:{point:null,bg:'transparent',color:'#ffffff'}
});
let S=blank();
const U={tab:'crop',adj:'bright',beauty:'smooth',eff:'bokeh',styleCat:'Простые',brush:'pen',color:'#ff8a3d',bw:6,mos:'pixel',mw:28,tkind:'plain',tcolor:'#ffffff',tstyle:'shadow',cur:null,aiErase:false,nw:0,nh:0,seq:1};
const nid=p=>p+(U.seq++);

/* история */
const H={u:[],r:[],t:0,open:false};
function mark(){
  if(!H.open){H.u.push(JSON.stringify(S));if(H.u.length>120)H.u.shift();H.r=[];H.open=true}
  clearTimeout(H.t);H.t=setTimeout(()=>H.open=false,450);
}
function undo(){if(!H.u.length)return;H.r.push(JSON.stringify(S));S=JSON.parse(H.u.pop());H.open=false;U.cur=null;all()}
function redo(){if(!H.r.length)return;H.u.push(JSON.stringify(S));S=JSON.parse(H.r.pop());H.open=false;U.cur=null;all()}

/* элементы */
const ed=$('#ed'),stage=$('#stage'),fr=$('#fr'),pic=$('#pic'),rot=$('#rot'),img=$('#img'),ink=$('#ink'),ov=$('#ov'),opts=$('#opts'),tabs=$('#tabs'),list=$('#list'),hint=$('#hint');
rot.appendChild(ov);
let BOX={w:0,h:0};

function totalDeg(){return S.crop.rot*90+S.crop.angle}
function fit(){
  if(!U.nw||!ed.classList.contains('on'))return;
  const sw=stage.clientWidth-32,sh=stage.clientHeight-32;
  const odd=S.crop.rot%2===1;
  const da=odd?U.nh/U.nw:U.nw/U.nh;
  const f=S.frame, on=f.kind!=='none';
  const fo=FFORMAT.find(x=>x[0]===f.format);
  const fa=on&&fo&&fo[2]?fo[2]:da;
  let fw=sw,fh=sw/fa; if(fh>sh){fh=sh;fw=sh*fa}
  const pad=on&&f.format!=='full'?Math.round(Math.min(fw,fh)*.06):0;
  let pw=fw-2*pad,ph=pw/da; if(ph>fh-2*pad){ph=fh-2*pad;pw=ph*da}
  if(f.format==='full'&&on){/* заполнить рамку */ if(pw<fw){pw=fw;ph=pw/da} if(ph<fh){ph=fh;pw=ph*da}}
  Object.assign(fr.style,{width:fw+'px',height:fh+'px',display:'flex',alignItems:'center',justifyContent:'center',overflow:'hidden',
    background:on?(f.kind==='poster'?`linear-gradient(160deg,${f.color},color-mix(in srgb,${f.color} 45%,#000))`:f.color):'transparent',boxShadow:on?'':'none'});
  pic.style.width=pw+'px';pic.style.height=ph+'px';
  const w=odd?ph:pw,h=odd?pw:ph;
  /* наклон: увеличиваем снимок, чтобы повёрнутый кадр закрыл рамку без пустых углов (как «Выровнять» в галерее) */
  const ph_=totalDeg()*Math.PI/180,ac=Math.abs(Math.cos(ph_)),as=Math.abs(Math.sin(ph_));
  const k=S.crop.angle?Math.max((pw*ac+ph*as)/w,(pw*as+ph*ac)/h):1;
  BOX={w,h,k};
  rot.classList.toggle('flip',!!S.crop.flip);rot.style.setProperty('--k',k);
  pic.classList.toggle('tilted',!!S.crop.angle);
  Object.assign(rot.style,{width:w+'px',height:h+'px',left:(pw-w)/2+'px',top:(ph-h)/2+'px',right:'auto',bottom:'auto',
    transform:`rotate(${totalDeg()}deg) scale(${k}) scaleX(${S.crop.flip?-1:1})`});
  const dpr=Math.min(2,devicePixelRatio||1);
  ink.width=Math.round(w*dpr);ink.height=Math.round(h*dpr);
  drawInk();overlay();
}
/* экран → доли исходника */
function toLocal(e){
  const b=pic.getBoundingClientRect();
  let dx=e.clientX-(b.left+b.width/2),dy=e.clientY-(b.top+b.height/2);
  const a=-totalDeg()*Math.PI/180,c=Math.cos(a),s=Math.sin(a);
  let x=dx*c-dy*s,y=dx*s+dy*c;
  if(S.crop.flip)x=-x;
  const k=BOX.k||1;
  return [x/(BOX.w*k)+.5,y/(BOX.h*k)+.5];
}
/* сила стиля: каждую функцию фильтра тянем от нейтрального значения к пресету */
function scaleCss(css,k){return css.replace(/([a-z-]+)\(([-\d.]+)(deg)?\)/g,(m,f,v,u)=>{const id=/^(brightness|contrast|saturate)$/.test(f)?1:0;return `${f}(${r3(id+(+v-id)*k)}${u||''})`})}
function filterCss(){
  const a=S.adjust,st=S.style.preset?scaleCss(STYLE_BY[S.style.preset].css,S.style.strength/100):'';
  const f=[];
  if(a.bright)f.push(`brightness(${1+a.bright/200})`);
  if(a.contrast)f.push(`contrast(${1+a.contrast/200})`);
  if(a.sat)f.push(`saturate(${1+a.sat/100})`);
  if(a.temp)f.push(`sepia(${Math.max(0,a.temp)/250})`,`hue-rotate(${-a.temp/8}deg)`);
  if(a.tint)f.push(`hue-rotate(${a.tint/4}deg)`);
  if(a.hl)f.push(`brightness(${1+a.hl/400})`);
  if(a.shadow)f.push(`contrast(${1-a.shadow/400})`);
  if(a.enh)f.push(`contrast(${1+a.enh/300})`,`saturate(${1+a.enh/250})`);
  if(a.fade)f.push(`contrast(${1-a.fade/250})`,`brightness(${1+a.fade/500})`);
  if(S.effect.glow)f.push(`brightness(${1+S.effect.glow/400})`,`saturate(${1+S.effect.glow/300})`);
  if(S.beauty.whiten)f.push(`brightness(${1+S.beauty.whiten/600})`);
  return (st+' '+f.join(' ')).trim()||'none';
}

const PAT={};
function pattern(ctx,type,w,h){
  const k=type+w+'x'+h; if(PAT[k])return ctx.createPattern(PAT[k],'no-repeat');
  const c=document.createElement('canvas');c.width=Math.max(1,Math.round(w));c.height=Math.max(1,Math.round(h));
  const x=c.getContext('2d');
  if(type==='pixel'){
    const t=document.createElement('canvas'),q=Math.max(2,Math.round(w/22));t.width=Math.ceil(w/q);t.height=Math.ceil(h/q);
    t.getContext('2d').drawImage(img,0,0,t.width,t.height);x.imageSmoothingEnabled=false;x.drawImage(t,0,0,c.width,c.height);
  }else if(type==='blur'){x.filter=`blur(${Math.max(4,w/60)}px)`;x.drawImage(img,-10,-10,c.width+20,c.height+20)}
  else{x.fillStyle='rgba(30,30,30,.55)';x.fillRect(0,0,c.width,c.height);x.strokeStyle='rgba(255,255,255,.55)';x.lineWidth=2;
    for(let i=-c.height;i<c.width;i+=9){x.beginPath();x.moveTo(i,c.height);x.lineTo(i+c.height,0);x.stroke()}}
  PAT[k]=c;return ctx.createPattern(c,'no-repeat');
}
function line(ctx,pts,w,h){
  ctx.beginPath();
  pts.forEach((p,i)=>i?ctx.lineTo(p[0]*w,p[1]*h):ctx.moveTo(p[0]*w,p[1]*h));
  if(pts.length===1)ctx.lineTo(pts[0][0]*w+.1,pts[0][1]*h+.1);
}
function drawInk(){
  const ctx=ink.getContext('2d'),w=BOX.w,h=BOX.h,d=ink.width/(w||1);
  ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,ink.width,ink.height);ctx.setTransform(d,0,0,d,0,0);
  ctx.lineCap='round';ctx.lineJoin='round';
  for(const s of S.mosaic.strokes){ctx.save();ctx.strokeStyle=pattern(ctx,s.type,w,h);ctx.lineWidth=s.w*w;line(ctx,s.pts,w,h);ctx.stroke();ctx.restore()}
  for(const s of S.ai.erase){ctx.save();ctx.strokeStyle='rgba(127,184,164,.55)';ctx.lineWidth=s.w*w;line(ctx,s.pts,w,h);ctx.stroke();ctx.restore()}
  for(const s of S.draw.strokes){
    const lw=s.w*w;ctx.save();ctx.strokeStyle=s.color;ctx.lineWidth=lw;
    if(s.brush==='marker'){ctx.globalAlpha=.45;ctx.lineWidth=lw*1.8;ctx.lineCap='square'}
    if(s.brush==='crayon'){ctx.globalAlpha=.85;ctx.setLineDash([lw*.35,lw*.3])}
    if(s.brush==='dash'){ctx.setLineDash([lw*2.2,lw*1.8])}
    if(s.brush==='neon'){ctx.shadowColor=s.color;ctx.shadowBlur=lw*2.2;line(ctx,s.pts,w,h);ctx.stroke();ctx.strokeStyle='rgba(255,255,255,.9)';ctx.lineWidth=lw*.45}
    line(ctx,s.pts,w,h);ctx.stroke();ctx.restore();
  }
}
const HANDLES='<i class="hdl" data-h="nw"></i><i class="hdl" data-h="ne"></i><i class="hdl" data-h="sw"></i><i class="hdl" data-h="se"></i>';
const box=r=>`left:${r.x*100}%;top:${r.y*100}%;width:${r.w*100}%;height:${r.h*100}%`;
function overlay(){
  let h='';const t=U.tab;
  if(S.crop.rect)h+=`<div class="crop${t==='crop'?'':' ghost'}" data-k="crop" style="${box(S.crop.rect)}">${t==='crop'?HANDLES:''}</div>`;
  if(t==='region'||S.region.length){
    S.region.forEach((r,i)=>{if(!r.rect)return;const cur=U.cur&&U.cur.id===r.id;
      const lb=`${i+1}. ${ACT_BY[r.action]}${r.comment?': '+r.comment:''}`;
      h+=`<div class="rg${cur?' cur':''}${r.rect.y<.09?' top':''}" data-k="rg" data-id="${r.id}" style="${box(r.rect)}"><span class="rg__lb">${esc(lb)}</span>${cur&&t==='region'?HANDLES:''}</div>`});
  }
  for(const x of S.text){
    const cur=U.cur&&U.cur.id===x.id&&t==='text';
    h+=`<div class="tx st-${x.style}${x.kind==='wm'?' wm':''}${cur?' cur':''}" data-k="tx" data-id="${x.id}" style="left:${x.x*100}%;top:${x.y*100}%;font-size:${x.size*BOX.h}px;color:${x.color}">${esc(x.text||' ')}</div>`;
  }
  if(S.cutout.point)h+=`<div class="pin" style="left:${S.cutout.point[0]*100}%;top:${S.cutout.point[1]*100}%"></div>`;
  const tl=S.effect.tilt;
  if(tl){const b=(tl/100*6).toFixed(1);
    h+=`<div class="tlt" style="top:0;height:32%;backdrop-filter:blur(${b}px);-webkit-backdrop-filter:blur(${b}px);mask-image:linear-gradient(#000 40%,transparent)"></div><div class="tlt" style="bottom:0;height:32%;backdrop-filter:blur(${b}px);-webkit-backdrop-filter:blur(${b}px);mask-image:linear-gradient(transparent,#000 60%)"></div>`}
  ov.innerHTML=h;
}
function preview(){
  img.style.filter=filterCss();
  const v=$('#vig');v.style.opacity=S.adjust.vig/100;v.style.background='radial-gradient(ellipse at center,transparent 42%,rgba(0,0,0,.88) 100%)';
  $('#grain').style.opacity=(S.effect.grain/100*.7).toFixed(2);
}
/* жесты на кадре */
let G=null;
const cp=p=>[clamp(p[0],0,1),clamp(p[1],0,1)];
function ratioN(aspect){const a=ASPECT.find(x=>x[0]===aspect);return a&&a[2]?a[2]*U.nh/U.nw:null}
function fitAspect(ra){let w=.9,h=w/ra;if(h>.9){h=.9;w=h*ra}return{x:(1-w)/2,y:(1-h)/2,w,h}}
function spanRect(o,p,ra){
  p=cp(p);let w=Math.abs(p[0]-o[0]),h=Math.abs(p[1]-o[1]);
  if(ra){if(w/h>ra)w=h*ra;else h=w/ra}
  return{x:p[0]<o[0]?o[0]-w:o[0],y:p[1]<o[1]?o[1]-h:o[1],w,h};
}
function opposite(r,hd){return[hd.includes('w')?r.x+r.w:r.x,hd.includes('n')?r.y+r.h:r.y]}
function hit(s,p){const R=Math.max(14,s.w*BOX.w/2+8);return s.pts.some(q=>Math.hypot((q[0]-p[0])*BOX.w,(q[1]-p[1])*BOX.h)<R)}
function eraseAt(arr,p){const n=arr.length;for(let i=arr.length-1;i>=0;i--)if(hit(arr[i],p))arr.splice(i,1);return n!==arr.length}
function strokeArr(){return U.tab==='draw'?S.draw.strokes:U.tab==='mosaic'?S.mosaic.strokes:S.ai.erase}

stage.addEventListener('pointerdown',e=>{
  if(e.button>0||e.target.closest('.cmp')||!U.nw)return;
  const p=toLocal(e),t=U.tab,hd=e.target.closest('.hdl'),el=e.target.closest('[data-k]');
  G={p0:p,x0:e.clientX,y0:e.clientY,moved:false,marked:false};
  if(t==='crop'){
    const r=S.crop.rect;
    if(r&&hd){G.m='resize';G.o=opposite(r,hd.dataset.h);G.ra=ratioN(S.crop.aspect);G.tgt='crop'}
    else if(r&&el&&el.dataset.k==='crop'){G.m='move';G.r0={...r};G.tgt='crop'}
    else if(!ratioN(S.crop.aspect)){G.m='new';G.o=cp(p);G.tgt='crop'}
    else{G=null;return}
  }else if(t==='region'){
    const id=el&&el.dataset.id,r=id&&S.region.find(x=>x.id===id);
    if(r){U.cur={op:'region',id};G.tgt=r;if(hd){G.m='resize';G.o=opposite(r.rect,hd.dataset.h)}else{G.m='move';G.r0={...r.rect}}overlay();renderList();renderOpts()}
    else{G.m='new';G.o=cp(p);G.tgt=null}
  }else if(t==='text'){
    const id=el&&el.dataset.id,x=id&&S.text.find(q=>q.id===id);
    if(x){U.cur={op:'text',id};G.m='tmove';G.tgt=x;G.r0={x:x.x,y:x.y};overlay();renderList();renderOpts()}
    else{if(U.cur&&U.cur.op==='text'){U.cur=null;overlay();renderList();renderOpts()}G=null;return}
  }else if(t==='draw'||t==='mosaic'||(t==='ai'&&U.aiErase)){
    const er=(t==='draw'&&U.brush==='eraser')||(t==='mosaic'&&U.mos==='eraser');
    mark();G.marked=true;
    if(er){G.m='erase';if(eraseAt(strokeArr(),p))drawInk()}
    else{
      const s=t==='draw'?{brush:U.brush,color:U.color,w:U.bw/BOX.w,pts:[cp(p)]}:t==='mosaic'?{type:U.mos,w:U.mw/BOX.w,pts:[cp(p)]}:{w:U.mw/BOX.w,pts:[cp(p)]};
      if(t==='ai'&&!S.ai.tasks.includes('erase'))S.ai.tasks.push('erase');
      strokeArr().push(s);G.m='stroke';G.s=s;drawInk();
    }
  }else if(t==='cutout'){mark();S.cutout.point=cp(p).map(r3);overlay();renderList();renderTabs();G=null;return}
  else{G=null;return}
  stage.setPointerCapture(e.pointerId);e.preventDefault();
});
stage.addEventListener('pointermove',e=>{
  if(!G)return;
  const p=toLocal(e);
  if(!G.moved&&Math.hypot(e.clientX-G.x0,e.clientY-G.y0)<5&&G.m!=='stroke'&&G.m!=='erase')return;
  G.moved=true;
  if(!G.marked&&G.m!=='stroke'&&G.m!=='erase'){mark();G.marked=true}
  const dx=p[0]-G.p0[0],dy=p[1]-G.p0[1];
  if(G.m==='stroke'){const l=G.s.pts[G.s.pts.length-1];if(Math.hypot((p[0]-l[0])*BOX.w,(p[1]-l[1])*BOX.h)>2){G.s.pts.push(cp(p));drawInk()}return}
  if(G.m==='erase'){if(eraseAt(strokeArr(),p))drawInk();return}
  if(G.m==='tmove'){G.tgt.x=r3(clamp(G.r0.x+dx,0,1));G.tgt.y=r3(clamp(G.r0.y+dy,0,1));overlay();return}
  let r;
  if(G.m==='move'){r={...G.r0,x:clamp(G.r0.x+dx,0,1-G.r0.w),y:clamp(G.r0.y+dy,0,1-G.r0.h)}}
  else r=spanRect(G.o,p,G.m==='resize'?G.ra:null);
  if(G.tgt==='crop'){if(G.m==='new'&&S.crop.aspect==='orig')S.crop.aspect='free';S.crop.rect=r}
  else if(G.m==='new'&&!G.tgt){const reg={id:nid('r'),rect:r,action:'comment',comment:''};S.region.push(reg);G.tgt=reg;U.cur={op:'region',id:reg.id}}
  else G.tgt.rect=r;
  overlay();
});
function endG(){
  if(!G)return;const g=G;G=null;
  const tiny=r=>r&&(r.w<.02||r.h<.02);
  if(g.tgt==='crop'&&tiny(S.crop.rect)){S.crop.rect=null;if(S.crop.aspect==='free')S.crop.aspect='orig'}
  if(g.tgt&&g.tgt.rect&&tiny(g.tgt.rect)){S.region=S.region.filter(x=>x!==g.tgt);U.cur=null}
  if(U.tab==='region'&&g.m==='new'&&!g.moved&&U.cur){U.cur=null}
  if(g.tgt&&g.tgt.rect)for(const k in g.tgt.rect)g.tgt.rect[k]=r3(g.tgt.rect[k]);
  if(S.crop.rect)for(const k in S.crop.rect)S.crop.rect[k]=r3(S.crop.rect[k]);
  if(g.s)g.s.pts=g.s.pts.map(q=>q.map(r3));
  overlay();renderList();renderTabs();renderOpts();
  if(U.cur&&U.cur.op==='region'&&g.m==='new'&&g.moved&&!TOUCH){const ta=$(`.ec[data-id="${U.cur.id}"] textarea`);ta&&ta.focus()}
}
stage.addEventListener('pointerup',endG);stage.addEventListener('pointercancel',endG);
const cmp=$('#cmpBtn');
const orig=on=>stage.classList.toggle('orig',on);
cmp.addEventListener('pointerdown',e=>{e.stopPropagation();orig(true)});
['pointerup','pointerleave','pointercancel'].forEach(n=>cmp.addEventListener(n,()=>orig(false)));
/* вкладки и панель опций */
/* мягкие переносы для подписей плиток: гласная|согласная+гласная и согласная|согласная, не ближе 3 букв к краям */
const VW='аеёиоуыэюяaeiouy';
const hy=t=>t.split(' ').map(w=>{if(w.length<9)return w;let o='';for(let i=0;i<w.length;i++){const a=w[i-1]||'',b=w[i],c=w[i+1]||'',l=a.toLowerCase(),m=b.toLowerCase(),n=c.toLowerCase();
  if(i>=3&&w.length-i>=3&&/[а-яё]/i.test(m)&&!/[ьъй]/.test(m)&&((VW.includes(l)&&!VW.includes(m)&&VW.includes(n))||(!VW.includes(l)&&!/[ьъй]/.test(l)&&!VW.includes(m)&&VW.includes(n)&&VW.includes((w[i-2]||'').toLowerCase()))))o+='\u00AD';o+=b}return o}).join(' ');
const chip=(a,v,label,icon,on,dot)=>`<button class="chip${on?' on':''}" type="button" data-a="${a}" data-v="${v}" title="${esc(label)}"><span class="ic">${icon}${dot?'<i class="dot"></i>':''}</span><span lang="ru">${esc(hy(label))}</span></button>`;
const fillVars=(v,min,max)=>{const p=(v-min)/(max-min)*100,z=min<0?-min/(max-min)*100:0;return [Math.min(p,z)+'%',Math.max(p,z)+'%']};
const slider=(label,key,val,min,max,fmt)=>{const [a,b]=fillVars(val,min,max);return `<div class="sl"><label>${label}</label><span class="tr${min<0?' bi':''}"><input type="range" data-s="${key}" min="${min}" max="${max}" value="${val}" style="--a:${a};--b:${b}" aria-label="${esc(label)}"></span><b>${fmt?fmt(val):val}</b></div>`};
const seg=(a,items,cur)=>`<div class="seg">${items.map(i=>`<button type="button" data-a="${a}" data-v="${i[0]}" class="${i[0]===cur?'on':''}">${i[1]}</button>`).join('')}</div>`;
const pal=(a,colors,cur,custom)=>`<div class="pal">${colors.map(c=>`<button class="sw${c===cur?' on':''}" type="button" data-a="${a}" data-v="${c}" style="background:${c}" title="${c}"></button>`).join('')}${custom?`<label class="sw rb${colors.includes(cur)?'':' on'}" title="Свой цвет"><input type="color" data-c="${a}" value="${cur}"></label>`:''}</div>`;
const rectIc=r=>{if(!r)return ic('s-free');let w=16,h=w/r;if(h>16){h=16;w=h*r}return `<svg class="i" viewBox="0 0 24 24"><rect x="${12-w/2}" y="${12-h/2}" width="${w}" height="${h}" rx="2"/></svg>`};
const isOn={
  ai:()=>S.ai.tasks.length>0,
  crop:()=>!!(S.crop.rect||S.crop.rot||S.crop.flip||S.crop.angle),
  style:()=>!!S.style.preset,
  adjust:()=>ADJ.some(a=>S.adjust[a[0]]),
  beauty:()=>BEAUTY.some(a=>S.beauty[a[0]]),
  draw:()=>S.draw.strokes.length>0, text:()=>S.text.length>0, mosaic:()=>S.mosaic.strokes.length>0,
  frame:()=>S.frame.kind!=='none', effect:()=>EFF.some(a=>S.effect[a[0]]),
  cutout:()=>!!S.cutout.point, region:()=>S.region.length>0
};
function renderTabs(){
  tabs.innerHTML=TABS.map(([k,l,i])=>`<button class="tab${U.tab===k?' on':''}" type="button" data-tab="${k}">${ic(i)}<span>${l}</span>${isOn[k]()?'<i class="dot"></i>':''}</button>`).join('');
}
const HINTS={crop:()=>S.crop.rect?'Тяните рамку или её углы':ratioN(S.crop.aspect)?'':'Проведите по фото, чтобы выделить кадр',
  region:()=>'Проведите по фото, чтобы выделить область',draw:()=>U.brush==='eraser'?'Проведите по штриху, чтобы стереть':'Рисуйте пальцем или мышью',
  text:()=>S.text.length?'Перетаскивайте надпись по фото':'',mosaic:()=>U.mos==='eraser'?'Проведите по мазку, чтобы стереть':'Закрасьте то, что нужно скрыть',
  cutout:()=>S.cutout.point?'':'Коснитесь объекта, который нужно вырезать',ai:()=>U.aiErase?'Закрасьте объект, который нужно убрать':''};
function curItem(op){return U.cur&&U.cur.op===op?S[op].find(x=>x.id===U.cur.id):null}
function renderOpts(){
  const t=U.tab;let h='';
  stage.dataset.tab=t;stage.classList.toggle('paint',t==='ai'&&U.aiErase);
  hint.textContent=HINTS[t]?HINTS[t]():'';
  if(t==='ai'){
    h+=`<div class="row">${AI.map(a=>chip('ai',a[0],a[1],ic(a[2]),a[0]==='erase'?(U.aiErase||S.ai.tasks.includes('erase')):S.ai.tasks.includes(a[0]))).join('')}</div>`;
    h+=U.aiErase?slider('Кисть','mw',U.mw,8,90,v=>v+' px'):`<div class="desc">Выберите, что сделать ИИ, можно несколько. «Стереть объект» включает кисть.</div>`;
  }else if(t==='crop'){
    h+=`<div class="flex"><button class="mini" type="button" data-a="rot">${ic('s-rot')}<span class="only-d">Повернуть</span></button><button class="mini" type="button" data-a="flip">${ic('s-flip')}<span class="only-d">Отразить</span></button>${slider('Наклон','angle',S.crop.angle,-45,45,FMT.angle).replace('<div class="sl">','<div class="sl" style="flex:1">')}</div>`;
    h+=`<div class="row">${ASPECT.map(a=>chip('aspect',a[0],a[1],a[0]==='orig'?ic('s-reset'):rectIc(a[2]),S.crop.aspect===a[0])).join('')}</div>`;
  }else if(t==='style'){
    h+=seg('scat',Object.keys(STYLES).map(c=>[c,c]),U.styleCat);
    h+=`<div class="row"><button class="thumb${S.style.preset?'':' on'}" type="button" data-a="style" data-v=""><img src="${img.src}" alt=""><span>Исходный</span></button>${STYLES[U.styleCat].map(s=>`<button class="thumb${S.style.preset===s[0]?' on':''}" type="button" data-a="style" data-v="${s[0]}"><img src="${img.src}" alt="" style="filter:${s[2]}"><span>${s[1]}</span></button>`).join('')}</div>`;
    if(S.style.preset)h+=slider('Сила','strength',S.style.strength,0,100,v=>v+'%');
  }else if(t==='adjust'){
    const a=ADJ.find(x=>x[0]===U.adj);
    h+=slider(a[1],'adj',S.adjust[a[0]],a[3],100,FMT.adj);
    h+=`<div class="row">${ADJ.map(x=>chip('adj',x[0],x[1],ic(x[2]),U.adj===x[0],S.adjust[x[0]])).join('')}</div>`;
  }else if(t==='beauty'){
    const a=BEAUTY.find(x=>x[0]===U.beauty);
    h+=slider(a[1],'beauty',S.beauty[a[0]],0,100,v=>v||'Откл.');
    h+=`<div class="row">${BEAUTY.map(x=>chip('beauty',x[0],x[1],ic(x[2]),U.beauty===x[0],S.beauty[x[0]])).join('')}</div>`;
  }else if(t==='draw'){
    h+=pal('color',COLORS,U.color,true)+slider('Толщина','bw',U.bw,2,40,v=>v+' px');
    h+=`<div class="row">${BRUSH.map(b=>chip('brush',b[0],b[1],ic(b[2]),U.brush===b[0])).join('')}</div>`;
  }else if(t==='text'){
    const x=curItem('text');
    h+=seg('tkind',TKIND,x?x.kind:U.tkind);
    h+=`<div class="flex"><input class="tinput" id="tIn" maxlength="80" placeholder="Текст надписи" value="${x?esc(x.text):''}"><button class="mini" type="button" data-a="tadd">${ic('t-text')}Добавить</button>${x?`<button class="ibtn" type="button" data-a="tdel" title="Удалить надпись">${ic('i-trash')}</button>`:''}</div>`;
    h+=`<div class="flex" style="flex-wrap:wrap">${pal('tcolor',COLORS,x?x.color:U.tcolor,true)}${seg('tstyle',TSTYLE,x?x.style:U.tstyle)}</div>`;
    if(x)h+=slider('Размер','tsize',Math.round(x.size*100),3,25,v=>v+'%');
  }else if(t==='mosaic'){
    h+=slider('Толщина кисти','mw',U.mw,8,90,v=>v+' px');
    h+=`<div class="row">${MOS.map(m=>chip('mos',m[0],m[1],ic(m[2]),U.mos===m[0])).join('')}</div>`;
  }else if(t==='frame'){
    h+=seg('fkind',FKIND,S.frame.kind);
    if(S.frame.kind!=='none'){
      h+=`<div class="row">${FFORMAT.map(f=>chip('fformat',f[0],f[1],f[0]==='orig'?ic('t-frame'):f[0]==='full'?ic('s-free'):rectIc(f[2]),S.frame.format===f[0])).join('')}</div>`;
      h+=pal('fcolor',FCOLORS,S.frame.color,true);
    }else h+=`<div class="desc">Рамка добавляет поля вокруг фото и меняет формат кадра.</div>`;
  }else if(t==='effect'){
    const a=EFF.find(x=>x[0]===U.eff);
    h+=slider(a[1],'eff',S.effect[a[0]],0,100,v=>v||'Откл.');
    h+=`<div class="row">${EFF.map(x=>chip('eff',x[0],x[1],ic(x[2]),U.eff===x[0],S.effect[x[0]])).join('')}</div>`;
  }else if(t==='cutout'){
    h+=`<div class="flex">${seg('cutbg',CUTBG,S.cutout.bg)}${S.cutout.point?`<button class="ibtn" type="button" data-a="cutreset" title="Сбросить">${ic('i-trash')}</button>`:''}</div>`;
    h+=S.cutout.bg==='color'?pal('cutcolor',FCOLORS,S.cutout.color,true):`<div class="desc">Агент выделит объект под точкой и заменит фон.</div>`;
  }else if(t==='region'){
    const r=curItem('region');
    h+=`<div class="flex"><button class="mini" type="button" data-a="radd">${ic('t-region')}Новая область</button><span class="desc">${S.region.length?'Областей: '+S.region.length:'Или проведите по фото'}</span></div>`;
    if(r)h+=`<div class="flex"><select class="sel2" data-c="ract" style="flex:0 1 180px">${RACT.map(a=>`<option value="${a[0]}"${a[0]===r.action?' selected':''}>${a[1]}</option>`).join('')}</select><input class="tinput" id="rIn" placeholder="Комментарий к области" value="${esc(r.comment)}"><button class="ibtn" type="button" data-a="rdel" title="Удалить область">${ic('i-trash')}</button></div>`;
  }
  opts.innerHTML=h;
}
/* список правок */
const plural=(n,f)=>{const a=n%10,b=n%100;return n+' '+(a===1&&b!==11?f[0]:a>=2&&a<=4&&(b<10||b>=20)?f[1]:f[2])};
const PX=r=>({x:Math.round(r.x*U.nw),y:Math.round(r.y*U.nh),w:Math.round(r.w*U.nw),h:Math.round(r.h*U.nh)});
const rtxt=r=>{const p=PX(r);return `${p.w}×${p.h} px от точки ${p.x},${p.y}`};
const lab=(arr,k)=>(arr.find(a=>a[0]===k)||[k,k])[1];
const NOTES=()=>S.notes||(S.notes={});
const today=()=>new Date().toLocaleDateString('ru-RU');
const lc=s=>s.charAt(0).toLowerCase()+s.slice(1);
function edits(){
  const E=[],N=NOTES();
  const add=(tab,id,title,sum,data)=>{const d={id,op:tab,...data};if(!d.comment)delete d.comment;const n=(N[id]||'').trim();if(n&&!d.comment)d.comment=n;E.push({tab,id,title,sum,data:d})};
  if(isOn.ai()){const t=S.ai.tasks,d={tasks:t.slice()};let s=t.map(k=>lab(AI,k)).join(', ');
    if(S.ai.erase.length){d.erase_mask={strokes:S.ai.erase.map(q=>({width_px:Math.round(q.w*U.nw),points:q.pts}))};s+=` · кисть: ${plural(S.ai.erase.length,['мазок','мазка','мазков'])}`}
    add('ai','ai','ИИ-ретушь',s,d)}
  if(isOn.crop()){const c=S.crop,p=[],d={};
    if(c.rect){p.push(`${ratioN(c.aspect)?'кадр '+c.aspect:'свободный кадр'}: ${rtxt(c.rect)}`);d.aspect=c.aspect;d.rect=c.rect;d.rect_px=PX(c.rect)}
    if(c.rot){p.push(`поворот на ${c.rot*90}° по часовой`);d.rotate_deg=c.rot*90}
    if(c.flip){p.push('отразить по горизонтали');d.flip='horizontal'}
    if(c.angle){p.push(`наклон ${sgn(c.angle)}° с подрезкой краёв без пустых углов`);d.straighten_deg=c.angle;d.straighten_fill='crop_to_fit'}
    add('crop','crop','Кадрирование',p.join(', '),d)}
  if(isOn.style()){const st=STYLE_BY[S.style.preset];
    add('style','style','Стиль',`${st.cat} · ${st.name}, сила ${S.style.strength}%`,{preset:S.style.preset,name:st.name,category:st.cat,strength:S.style.strength/100})}
  if(isOn.adjust()){const p=ADJ.filter(a=>S.adjust[a[0]]);
    add('adjust','adjust','Коррекция',p.map(a=>`${a[1]} ${sgn(S.adjust[a[0]])}`).join(', '),{params:Object.fromEntries(p.map(a=>[a[0],S.adjust[a[0]]])),scale:'-100..100, 0 — без изменений'})}
  if(isOn.beauty()){const p=BEAUTY.filter(a=>S.beauty[a[0]]);
    add('beauty','beauty','Красота',p.map(a=>`${a[1]} ${S.beauty[a[0]]}`).join(', '),{params:Object.fromEntries(p.map(a=>[a[0],S.beauty[a[0]]])),scale:'0..100'})}
  if(isOn.draw()){const s=S.draw.strokes,br=[...new Set(s.map(q=>lc(lab(BRUSH,q.brush))))],co=[...new Set(s.map(q=>q.color))];
    add('draw','draw','Наброски',`${plural(s.length,['штрих','штриха','штрихов'])}: ${br.join(', ')} · ${co.join(' ')}`,{strokes:s.map(q=>({brush:q.brush,color:q.color,width_px:Math.round(q.w*U.nw),points:q.pts}))})}
  for(const x of S.text)
    add('text',x.id,'Текст',`«${x.text}» · ${lab(TKIND,x.kind)}, ${lc(lab(TSTYLE,x.style))}, ${Math.round(x.size*100)}% высоты · центр ${Math.round(x.x*100)}%, ${Math.round(x.y*100)}%`,
      {kind:x.kind,text:x.text,center:{x:x.x,y:x.y},center_px:{x:Math.round(x.x*U.nw),y:Math.round(x.y*U.nh)},size:x.size,size_px:Math.round(x.size*U.nh),color:x.color,style:x.style});
  if(isOn.mosaic()){const s=S.mosaic.strokes,ty=[...new Set(s.map(q=>lc(lab(MOS,q.type))))];
    add('mosaic','mosaic','Мозаика',`${plural(s.length,['мазок','мазка','мазков'])}: ${ty.join(', ')}`,{strokes:s.map(q=>({type:q.type,width_px:Math.round(q.w*U.nw),points:q.pts}))})}
  if(isOn.frame()){const f=S.frame;
    add('frame','frame','Рамка',`${lab(FKIND,f.kind)} · формат ${lc(lab(FFORMAT,f.format))} · ${f.color}`,{kind:f.kind,format:f.format,color:f.color})}
  if(isOn.effect()){const p=EFF.filter(a=>S.effect[a[0]]);
    add('effect','effect','Эффекты',p.map(a=>`${a[1]} ${S.effect[a[0]]}`).join(', '),{params:Object.fromEntries(p.map(a=>[a[0],S.effect[a[0]]])),scale:'0..100'})}
  if(isOn.cutout()){const c=S.cutout,p=c.point;
    add('cutout','cutout','Вырезать объект',`объект в точке ${Math.round(p[0]*U.nw)},${Math.round(p[1]*U.nh)} px · фон: ${lc(lab(CUTBG,c.bg))}${c.bg==='color'?' '+c.color:''}`,
      {point:{x:p[0],y:p[1]},point_px:{x:Math.round(p[0]*U.nw),y:Math.round(p[1]*U.nh)},background:c.bg,...(c.bg==='color'?{color:c.color}:{})})}
  S.region.forEach((r,i)=>{if(!r.rect)return;
    add('region',r.id,`Область ${i+1}`,`${ACT_BY[r.action]} · ${rtxt(r.rect)}`,{rect:r.rect,rect_px:PX(r.rect),action:r.action,comment:r.comment.trim()})});
  return E;
}
function renderList(){
  const E=edits();
  list.innerHTML=E.length?E.map(e=>{
    const t=TAB[e.tab],cur=U.cur&&U.cur.id===e.id;let f='';
    if(e.tab==='region'){const r=S.region.find(x=>x.id===e.id);
      f=`<select class="sel2" data-rs="${e.id}">${RACT.map(a=>`<option value="${a[0]}"${a[0]===r.action?' selected':''}>${a[1]}</option>`).join('')}</select><textarea class="ta" data-rc="${e.id}" placeholder="Что сделать с этой областью">${esc(r.comment)}</textarea>`}
    else if(e.tab==='text'){const x=S.text.find(q=>q.id===e.id);f=`<input class="tinput" data-tt="${e.id}" maxlength="80" placeholder="Текст надписи" value="${esc(x.text)}">`}
    else f=`<input class="tinput" data-nt="${e.id}" placeholder="Комментарий агенту (необязательно)" value="${esc(NOTES()[e.id]||'')}">`;
    return `<div class="ec${cur?' cur':''}" data-id="${e.id}" data-tab="${e.tab}" style="--c:var(${COL[e.tab]})"><div class="ec__h"><span class="ec__ic">${ic(t.icon)}</span><span class="ec__t">${esc(e.title)}</span><button class="ibtn" type="button" data-del="${e.id}" data-dt="${e.tab}" title="Удалить правку">${ic('i-trash')}</button></div><div class="ec__s">${esc(e.sum)}</div>${f}</div>`;
  }).join(''):`<div class="empty">${ic('i-pro')}Правок пока нет. Выберите инструмент внизу и отметьте на фото, что поменять. Всё делается кликом или пальцем, клавиатура не нужна.</div>`;
  updBtns(E.length);
}
function updBtns(n){
  $('#undoBtn').disabled=!H.u.length;$('#redoBtn').disabled=!H.r.length;$('#clearBtn').disabled=!n;$('#resetBtn').disabled=!n;
  const can=n>0||!!$('#prompt').value.trim();$('#sendBtn').disabled=$('#sendM').disabled=!can;
  $('#cnt').textContent=n;const b=$('#listBadge');b.hidden=!n;b.textContent=n;
  $('#sendDesc').textContent=n?plural(n,['правка','правки','правок'])+' · новым файлом':'Добавьте правку или комментарий';
}
function all(){preview();fit();overlay();renderTabs();renderOpts();renderList()}
function delEdit(tab,id){
  mark();
  if(tab==='text')S.text=S.text.filter(x=>x.id!==id);
  else if(tab==='region')S.region=S.region.filter(x=>x.id!==id);
  else{S[tab]=blank()[tab];if(tab==='ai')U.aiErase=false}
  delete NOTES()[id];if(U.cur&&U.cur.id===id)U.cur=null;all();
}
function setTab(k){
  if(U.tab!==k){U.tab=k;if(U.cur&&U.cur.op!==k)U.cur=null}
  overlay();renderTabs();renderOpts();renderList();
  const b=tabs.querySelector('.tab.on');if(b){const l=b.offsetLeft-(tabs.clientWidth-b.offsetWidth)/2;tabs.scrollTo({left:l,behavior:'smooth'})}
}

/* действия панели */
function act(a,v,soft){
  const x=curItem('text');
  switch(a){
  case 'ai':
    if(v==='erase'){U.aiErase=!U.aiErase;if(!U.aiErase&&!S.ai.erase.length&&S.ai.tasks.includes('erase')){mark();S.ai.tasks=S.ai.tasks.filter(t=>t!=='erase')}}
    else{mark();const t=S.ai.tasks;S.ai.tasks=t.includes(v)?t.filter(q=>q!==v):[...t,v]}
    break;
  case 'rot':mark();S.crop.rot=(S.crop.rot+1)%4;break;
  case 'flip':mark();S.crop.flip=!S.crop.flip;break;
  case 'aspect':{mark();S.crop.aspect=v;const ra=ratioN(v);
    if(v==='orig')S.crop.rect=null;
    else if(ra){const r=fitAspect(ra);for(const k in r)r[k]=r3(r[k]);S.crop.rect=r}
    else if(!S.crop.rect)S.crop.rect={x:.05,y:.05,w:.9,h:.9};
    break}
  case 'scat':U.styleCat=v;break;
  case 'style':mark();S.style.preset=v||null;S.style.strength=100;break;
  case 'adj':U.adj=v;break;
  case 'beauty':U.beauty=v;break;
  case 'eff':U.eff=v;break;
  case 'color':U.color=v;break;
  case 'brush':U.brush=v;break;
  case 'mos':U.mos=v;break;
  case 'tkind':if(x){mark();x.kind=v;if(v==='date')x.text=today();else if(v==='wm'&&!x.text.trim())x.text='© AuGent'}U.tkind=v;break;
  case 'tadd':{mark();const inp=$('#tIn'),k=U.tkind,n=S.text.length;
    const t=(!x&&inp?inp.value.trim():'')||(k==='date'?today():k==='wm'?'© AuGent':'Текст');
    const nx={id:nid('t'),kind:k,text:t,x:k==='wm'?.8:.5,y:k==='wm'?.92:[.5,.66,.34][n%3],size:k==='wm'?.05:.08,color:U.tcolor,style:U.tstyle};
    S.text.push(nx);U.cur={op:'text',id:nx.id};break}
  case 'tdel':if(x){mark();S.text=S.text.filter(q=>q!==x);U.cur=null}break;
  case 'tcolor':if(x){mark();x.color=v}U.tcolor=v;break;
  case 'tstyle':if(x){mark();x.style=v}U.tstyle=v;break;
  case 'fkind':mark();S.frame.kind=v;break;
  case 'fformat':mark();S.frame.format=v;break;
  case 'fcolor':mark();S.frame.color=v;break;
  case 'cutbg':mark();S.cutout.bg=v;break;
  case 'cutcolor':mark();S.cutout.color=v;break;
  case 'cutreset':mark();S.cutout.point=null;break;
  case 'radd':{mark();const o=(S.region.length%4)*.05,r={id:nid('r'),rect:{x:r3(.3+o),y:r3(.3+o),w:.4,h:.3},action:'comment',comment:''};
    S.region.push(r);U.cur={op:'region',id:r.id};break}
  case 'rdel':{const r=curItem('region');if(r){mark();S.region=S.region.filter(q=>q!==r);U.cur=null}break}
  }
  if(soft){preview();fit();overlay();renderTabs();renderList()}else all();
}
const FMT={angle:v=>(v?sgn(v):0)+'°',strength:v=>v+'%',adj:v=>v?sgn(v):'0',beauty:v=>v||'Откл.',bw:v=>v+' px',tsize:v=>v+'%',mw:v=>v+' px',eff:v=>v||'Откл.'};
opts.addEventListener('click',e=>{const b=e.target.closest('[data-a]');if(b&&!b.matches('label'))act(b.dataset.a,b.dataset.v)});
opts.addEventListener('input',e=>{
  const t=e.target,s=t.closest('[data-s]');
  if(s){const k=s.dataset.s,v=+s.value;
    {const [a,b]=fillVars(v,+s.min,+s.max);s.style.setProperty('--a',a);s.style.setProperty('--b',b)}s.closest('.sl').querySelector('b').textContent=FMT[k](v);
    const ui=k==='bw'||k==='mw';if(!ui)mark();
    if(k==='angle'){S.crop.angle=v;fit()}
    else if(k==='strength')S.style.strength=v;
    else if(k==='adj')S.adjust[U.adj]=v;
    else if(k==='beauty')S.beauty[U.beauty]=v;
    else if(k==='eff'){S.effect[U.eff]=v;overlay()}
    else if(k==='tsize'){const x=curItem('text');if(x){x.size=v/100;overlay()}}
    else if(k==='bw')U.bw=v;else if(k==='mw')U.mw=v;
    if(!ui){preview();renderTabs();renderList()}
    return}
  if(t.type==='color'&&t.dataset.c){act(t.dataset.c,t.value,true);return}
  if(t.id==='tIn'){const x=curItem('text');if(x){mark();x.text=t.value;overlay();renderList()}}
  if(t.id==='rIn'){const r=curItem('region');if(r){mark();r.comment=t.value;overlay();renderList()}}
});
opts.addEventListener('change',e=>{const t=e.target;
  if(t.dataset.s&&['adj','beauty','eff'].includes(t.dataset.s))renderOpts();
  else if(t.type==='color')renderOpts();
  else if(t.dataset.c==='ract'){const r=curItem('region');if(r){mark();r.action=t.value;overlay();renderList()}}
});
opts.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.id==='tIn'){e.preventDefault();if(!curItem('text'))act('tadd');else e.target.blur()}});
tabs.addEventListener('click',e=>{const b=e.target.closest('[data-tab]');b&&setTab(b.dataset.tab)});
list.addEventListener('click',e=>{
  const d=e.target.closest('[data-del]');if(d){delEdit(d.dataset.dt,d.dataset.del);return}
  if(e.target.closest('input,textarea,select'))return;
  const c=e.target.closest('.ec');if(!c)return;
  const t=c.dataset.tab;U.cur=(t==='text'||t==='region')?{op:t,id:c.dataset.id}:null;
  if(innerWidth<=760)ed.classList.remove('side-on');
  U.tab=t;setTab(t);
});
list.addEventListener('input',e=>{const t=e.target,id=t.dataset.tt||t.dataset.rc||t.dataset.nt;if(!id)return;mark();
  if(t.dataset.tt){const x=S.text.find(q=>q.id===id);if(x)x.text=t.value}
  else if(t.dataset.rc){const r=S.region.find(q=>q.id===id);if(r)r.comment=t.value}
  else NOTES()[id]=t.value;
  overlay();
  const E=edits().find(q=>q.id===id),s=t.closest('.ec').querySelector('.ec__s');if(E&&s)s.textContent=E.sum;
  if(!opts.contains(R.activeElement))renderOpts();
});
list.addEventListener('change',e=>{const t=e.target;if(!t.dataset.rs)return;const r=S.region.find(q=>q.id===t.dataset.rs);
  if(r){mark();r.action=t.value;overlay();renderOpts();renderList()}});
$('#prompt').addEventListener('input',()=>updBtns(edits().length));

/* payload */
const APPLY=['crop','ai','cutout','adjust','style','beauty','effect','mosaic','region','draw','text','frame'];
function payload(E){return{kind:'image_edit_request',version:1,
  context:Object.assign({},MP.context),
  file:{...FILE,width:U.nw,height:U.nh},
  coord_base:'source',
  coord_note:'x, y, w, h в долях 0..1 от левого верхнего угла исходника, до поворота и кадрирования; *_px в пикселях исходника',
  apply_order:APPLY.filter(o=>E.some(e=>e.tab===o)),
  edits:E.map(e=>e.data),
  prompt:$('#prompt').value.trim(),
  output:{keep_original:true,format:'jpg'},
  client:{ui:'lightbox-pro-image/0.1',created_at:new Date().toISOString()}}}
function message(p,E){
  const L=[`Правки к изображению «${FILE.name}» (${U.nw}×${U.nh}), правок: ${E.length}.`,''];
  if(FILE.source==='local')L.splice(1,0,'Фото загружено с устройства и приложено к сообщению.');
  E.forEach((e,i)=>L.push(`${i+1}. ${e.title}: ${e.sum}${e.data.comment?' — '+e.data.comment:''}`));
  L.push('','Координаты считаются от левого верхнего угла исходного файла, до поворота и кадрирования. Пиксели указаны для исходного размера.');
  if(p.apply_order.length)L.push('Порядок применения: '+p.apply_order.map(o=>TAB[o].label).join(' → ')+'.');
  if(p.prompt)L.push('','Комментарий: '+p.prompt);
  L.push('','Исходник не трогать, результат новым файлом.');
  L.push('Полная разметка — в JSON (image_edit_request).');
  return L.join('\n');
}
function hlJson(s){return esc(s).replace(/(&quot;(?:\\.|(?!&quot;)[^\\])*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
  (m,str,colon,b)=>str?`<span class="${colon?'j-k':'j-s'}">${str}</span>${colon||''}`:b?`<span class="j-b">${m}</span>`:`<span class="j-n">${m}</span>`)}
let LAST=null;
const md=$('#md');
function setMd(v){$$('#mdSeg button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));$('#mdMsg').hidden=v!=='msg';$('#mdJson').hidden=v!=='json'}
function send(){
  const E=edits(),p=payload(E);
  if(!E.length&&!p.prompt){toast('Сначала добавьте правку');return}
  const m=message(p,E);console.log('[image_edit_request]',p);
  LAST={p,m,n:E.length};
  $('#mdMsg').textContent=m;$('#mdJson').innerHTML=hlJson(JSON.stringify(p,null,2));setMd('msg');md.classList.add('on');
}
$('#sendBtn').onclick=send;$('#sendM').onclick=send;
$('#mdSeg').addEventListener('click',e=>{const b=e.target.closest('button');b&&setMd(b.dataset.v)});
$('#mdClose').onclick=()=>md.classList.remove('on');
md.addEventListener('click',e=>{if(e.target===md)md.classList.remove('on')});
$('#mdCopy').onclick=()=>{if(!LAST)return;const t=JSON.stringify(LAST.p,null,2);
  (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>toast('JSON скопирован'),()=>toast('Не удалось скопировать'))};
$('#mdOk').onclick=async()=>{
  if(!LAST)return;
  const ok=MP.onSend?await MP.onSend({kind:'image',text:LAST.m,payload:LAST.p,count:LAST.n}):true;
  if(ok===false)return;
  md.classList.remove('on');closeEd(true);
};

/* служебное */
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');host.classList.add('toast-on');clearTimeout(toast.h);toast.h=setTimeout(()=>{t.classList.remove('on');host.classList.remove('toast-on')},1900)}
const lb={classList:{contains:()=>false,add(){},remove(){}}};
function dims(){$('#edDim').textContent=U.nw?`${U.nw}×${U.nh}`:''}
function onImg(){U.nw=img.naturalWidth;U.nh=img.naturalHeight;dims();all()}
img.addEventListener('load',onImg);
if(img.complete&&img.naturalWidth)onImg();
img.addEventListener('error',()=>{if(!img.src.startsWith('blob:'))toast('Не удалось загрузить '+FILE.name)});

/* лайтбокс и редактор */
function lockScroll(){}
function openLb(){lb.classList.add('on');lockScroll()}
function closeLb(){lb.classList.remove('on');lockScroll()}
function openEd(){
  lb.classList.add('on');ed.classList.add('on');lockScroll();
  requestAnimationFrame(()=>{fit();setTab(U.tab)});
}
function closeEd(toChat){if(!host.isConnected)return;
  ed.classList.remove('on','side-on');$('#md').classList.remove('on');
  lockScroll();MP.onClose&&MP.onClose(!!toChat);
}




$('#edBack').onclick=()=>closeEd(false);
$('#undoBtn').onclick=undo;
$('#redoBtn').onclick=redo;
function resetEdits(){if(!edits().length)return;mark();S=blank();U.cur=null;U.aiErase=false;H.open=false;all();toast('Все правки сброшены. Ctrl+Z или ↶ вернёт их')}
$('#clearBtn').onclick=resetEdits;
$('#resetBtn').onclick=resetEdits;
$('#listBtn').onclick=()=>ed.classList.toggle('side-on');
$('#sideClose').onclick=()=>ed.classList.remove('side-on');
$('#scrim').onclick=()=>ed.classList.remove('side-on');


/* клавиатура: всё дублируется кнопками, это только ускорители */
__on(window,'keydown',e=>{const __t=e.composedPath&&e.composedPath()[0]||e.target;
  const md=$('#md');
  if(md.classList.contains('on')){if(e.key==='Escape')md.classList.remove('on');return}
  const inF=/^(INPUT|TEXTAREA|SELECT)$/.test(__t.tagName),mod=e.ctrlKey||e.metaKey;
  if(ed.classList.contains('on')){
    if(mod&&e.key==='Enter'){e.preventDefault();send();return}
    if(inF){if(e.key==='Escape')__t.blur();return}
    if(mod&&e.code==='KeyZ'){e.preventDefault();e.shiftKey?redo():undo();return}
    if(mod&&e.code==='KeyY'){e.preventDefault();redo();return}
    if(mod||e.altKey)return;
    if(e.key==='Escape'){
      if(ed.classList.contains('side-on'))ed.classList.remove('side-on');
      else if(U.cur){U.cur=null;overlay();renderOpts();renderList()}
      else closeEd(false);
      return}
    if((e.key==='Delete'||e.key==='Backspace')&&U.cur){e.preventDefault();delEdit(U.cur.op,U.cur.id);return}
    return}
  if(lb.classList.contains('on')&&!inF){
    if(e.key==='Escape')closeLb();
    else if(e.code==='KeyP'&&!mod&&!e.altKey){e.preventDefault();openEd()}
  }
});

/* размеры сцены: окно, поворот телефона, смена высоты панели опций */
let fitRaf=0;
/* затухание по краям прокручиваемых рядов: видно, что дальше есть ещё */
const edges=el=>{const l=el.scrollLeft>2,r=el.scrollLeft+el.clientWidth<el.scrollWidth-2;el.style.setProperty('--fl',l?'28px':'0px');el.style.setProperty('--fr',r?'28px':'0px')};
const allEdges=()=>{edges(tabs);opts.querySelectorAll('.row').forEach(edges)};
tabs.addEventListener('scroll',()=>edges(tabs),{passive:true});
opts.addEventListener('scroll',e=>{if(e.target.classList&&e.target.classList.contains('row'))edges(e.target)},{capture:true,passive:true});
if(window.MutationObserver){const mo=new MutationObserver(()=>requestAnimationFrame(allEdges));mo.observe(tabs,{childList:true});mo.observe(opts,{childList:true})}
const refit=()=>{cancelAnimationFrame(fitRaf);fitRaf=requestAnimationFrame(()=>{fit();allEdges()})};
__on(window,'resize',refit);
if(window.ResizeObserver)new ResizeObserver(refit).observe(stage);

/* старт */
if(TAB[P.get('tab')])U.tab=P.get('tab');
renderTabs();renderOpts();renderList();

host.__ipe={get S(){return S},U,edits,payload,message,act,setTab,undo,redo,send};

openEd();
}

/* ── редактор видео (перенос стенда video_pro.html) ── */
function mountVideo(R, host, MP, __on) {

const $=s=>R.querySelector(s), $$=s=>[...R.querySelectorAll(s)];
const qs=new URLSearchParams('');
const V=$('#vid'), box=$('#lbBox'), tl=$('#tl'), ph=$('#ph'), ovl=$('#ovl'), list=$('#list'), reg=$('#reg');
const CTX=Object.assign({},MP.context);
const FILE=Object.assign({},MP.file);
const IC=n=>'<svg class="ic" aria-hidden="true"><use href="#i-'+n+'"/></svg>';
const plural=n=>{const a=n%10,b=n%100;return a===1&&b!==11?'правка':a>=2&&a<=4&&(b<12||b>14)?'правки':'правок';};
const TYPES={
 cut:{name:'Вырезать',ico:IC('cut'),lane:'cut',len:1,c:'var(--cut)'},
 speed:{name:'Скорость',ico:IC('speed'),lane:'cut',len:2,c:'var(--spd)'},
 text:{name:'Текст',ico:IC('text'),lane:'text',len:2,c:'var(--txt)'},
 volume:{name:'Звук',ico:IC('volume'),lane:'audio',len:2,c:'var(--vol)'},
 note:{name:'Заметка',ico:IC('note'),lane:'note',len:0,c:'var(--note)'},
 region:{name:'Область',ico:IC('region'),lane:'region',len:2,c:'var(--reg)'}};
const ACT={comment:'по комментарию',crop:'кадрировать',blur:'размыть',zoom:'приблизить'};
const POS={top:'сверху',center:'по центру',bottom:'снизу'}, SIZE={s:'S',m:'M',l:'L'};
const S={dur:0,edits:[],sel:null,active:null,preview:true,fps:30};
const hist=[]; let seq=1, drag=null, peaks=null, waveMsg='', filmJob=0, ovlKey='', pendingSnap=false, SRC='';
const clamp=(v,a,b)=>Math.min(b,Math.max(a,v)), r2=v=>Math.round(v*100)/100;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const r3=v=>Math.round(v*1000)/1000;
const pct=t=>(S.dur?t/S.dur*100:0)+'%';
const isPro=()=>box.classList.contains('is-pro');
const byId=id=>S.edits.find(e=>e.id===id);
const sorted=()=>[...S.edits].sort((x,y)=>x.a-y.a||x.b-y.b);
function fmt(t){t=Math.round(Math.max(0,t||0)*100)/100;const m=Math.floor(t/60),s=t-m*60;return String(m).padStart(2,'0')+':'+s.toFixed(2).padStart(5,'0');}
function parseT(v){v=String(v).trim().replace(',','.');if(!v)return null;const p=v.split(':');const t=p.length>1?(+p[0])*60+(+p[1]):+p[0];return isFinite(t)?t:null;}
const dbs=d=>(d>0?'+':d<0?'−':'')+Math.abs(d)+' dB';
const dbl=e=>e.mute?'без звука':dbs(e.db);
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');clearTimeout(toast.h);toast.h=setTimeout(()=>t.classList.remove('on'),1800);}

/* ---------- источник ---------- */
const FV=document.createElement('video'); FV.muted=true; FV.preload='auto'; FV.playsInline=true;
function setSrc(src,name){
 SRC=src; V.src=src;  FV.src=src; $('#lbDl').href=src;
 if(name){FILE.name=name;$('#lbName').textContent=name;$('#lbName').title=name;$('#lbDl').download=name;}
 S.edits=[];hist.length=0;S.sel=null;S.active=null;S.dur=0;peaks=null;renderAll();
}
function onMeta(){const d=isFinite(V.duration)?V.duration:0;if(!d||d===S.dur)return;const first=!S.dur;S.dur=d;$('#lbDim').textContent=V.videoWidth+'×'+V.videoHeight;layout();if(first)loadWave();renderAll();if(first&&qs.has('demo')&&!S.edits.length)demo();}
V.addEventListener('loadedmetadata',onMeta);V.addEventListener('durationchange',onMeta);
/* если длительность ещё неизвестна (iOS/экономия трафика не грузят без жеста), догружаем по тапу */
function ensureMeta(){if(S.dur)return Promise.resolve(true);if(V.readyState<1){try{V.load();}catch(e){}}
 return Promise.race([new Promise(r=>{const f=()=>{onMeta();r(!!S.dur);};V.addEventListener('loadedmetadata',f,{once:true});}),new Promise(r=>setTimeout(()=>r(!!S.dur),6000))]);}
V.addEventListener('error',()=>toast('Видео не загрузилось. Для mp4 нужен Chrome/Edge/Safari'));
V.addEventListener('play',updPlay);V.addEventListener('pause',updPlay);
function updPlay(){$('#playIco').setAttribute('href',V.paused?'#i-play':'#i-pause');$('#playBtn').classList.toggle('is-on',!V.paused);}

/* ---------- история ---------- */
function snap(){hist.push(JSON.stringify(S.edits));if(hist.length>200)hist.shift();updButtons();}
function undo(){if(!hist.length)return;S.edits=JSON.parse(hist.pop());if(!byId(S.active))S.active=null;renderAll();toast('Отменено');}

/* ---------- правки ---------- */
async function add(type){
 if(!S.dur){toast('Загружаю видео…');if(!await ensureMeta()){toast('Видео не загрузилось');return;}}
 const t=V.currentTime, d=TYPES[type].len; let a,b;
 if(type==='note'){a=b=t;}
 else if(S.sel){a=S.sel.a;b=S.sel.b;}
 else{a=t;b=Math.min(S.dur,t+d);if(b-a<0.2){b=S.dur;a=Math.max(0,b-d);}}
 snap();
 const e={id:'e'+(seq++),type,a:r2(a),b:r2(b)};
 if(type==='text')Object.assign(e,{text:'',pos:'bottom',size:'m'});
 if(type==='volume')Object.assign(e,{db:-12,mute:false,fade:true});
 if(type==='speed')e.rate=2;
 if(type==='note')e.text='';
 if(type==='region')Object.assign(e,{x:0.3,y:0.3,w:0.4,h:0.4,action:'comment',text:''});
 S.edits.push(e);S.active=e.id;S.sel=null;if(!V.paused)V.pause();renderAll();
 const card=list.querySelector(`.vpe-card[data-id="${e.id}"]`);
 if(type==='region'){if(card)card.scrollIntoView({block:'nearest'});toast('Нарисуйте рамку на видео');return;}
 if(card){card.scrollIntoView({block:'nearest'});const f=card.querySelector('input[data-f=text],textarea[data-f=text]');if(f)f.focus({preventScroll:true});}
}
function del(id){if(!id||!byId(id))return;snap();S.edits=S.edits.filter(e=>e.id!==id);if(S.active===id)S.active=null;renderAll();}
function setActive(id){S.active=id;$$('.vpe-blk').forEach(n=>n.classList.toggle('is-active',n.dataset.id===id));$$('.vpe-card').forEach(n=>n.classList.toggle('is-active',n.dataset.id===id));ovlKey='';}
function seek(t){V.currentTime=clamp(t,0,S.dur||0);}
function step(dt){if(!V.paused)V.pause();seek(V.currentTime+dt);}
function setIn(){const t=r2(V.currentTime);const b=S.sel&&S.sel.b>t+0.05?S.sel.b:Math.min(S.dur,t+1);S.sel={a:t,b:r2(b)};renderSel();}
function setOut(){const t=r2(V.currentTime);const a=S.sel&&S.sel.a<t-0.05?S.sel.a:Math.max(0,t-1);S.sel={a:r2(a),b:t};renderSel();}

/* ---------- рендер таймлайна ---------- */
function blkLabel(e){return e.type==='cut'?'вырезать':e.type==='text'?(e.text||'Текст'):e.type==='volume'?dbl(e):e.type==='speed'?'×'+e.rate:e.type==='region'?(e.text||ACT[e.action]):'';}
function renderBlocks(){
 $$('.vpe-blk').forEach(n=>n.remove());
 for(const e of S.edits){
  const lane=tl.querySelector(`[data-lane="${TYPES[e.type].lane}"]`);
  const el=document.createElement('div');
  el.className=`vpe-blk vpe-blk--${e.type}`+(e.id===S.active?' is-active':'');el.dataset.id=e.id;el.style.left=pct(e.a);
  if(e.type==='note'){el.innerHTML='<i></i>';el.title=e.text||'Заметка';}
  else{el.style.width=pct(e.b-e.a);el.innerHTML=`${TYPES[e.type].ico}<span>${esc(blkLabel(e))}</span><b class="h l"></b><b class="h r"></b>`;}
  lane.appendChild(el);
 }
}
function renderSel(){
 const b=$('#selband');
 if(!S.sel||!S.dur){b.style.display='none';$('#selInfo').textContent='Участок не выделен';return;}
 b.style.display='block';b.style.left=pct(S.sel.a);b.style.width=pct(S.sel.b-S.sel.a);
 $('#selInfo').textContent=`Участок ${fmt(S.sel.a)}–${fmt(S.sel.b)} · ${(S.sel.b-S.sel.a).toFixed(2)} с`;
}
function renderRuler(){
 const r=$('#ruler'),w=r.clientWidth;if(!S.dur||!w){r.innerHTML='';return;}
 const steps=[0.25,0.5,1,2,5,10,15,30,60,120,300,600];const st=steps.find(s=>s/S.dur*w>=62)||1200;
 let h='';for(let i=0;i*st<=S.dur+1e-6;i++){const t=i*st,m=Math.floor(t/60),s=t-m*60;h+=`<i style="left:${pct(t)}">${m}:${st<1?s.toFixed(st<0.5?2:1).padStart(st<0.5?5:4,'0'):String(Math.round(s)).padStart(2,'0')}</i>`;}
 r.innerHTML=h;
}
async function loadWave(){
 peaks=null;waveMsg='загрузка волны…';drawWave();const my=SRC;
 try{
  const res=await fetch(SRC);if(!res.ok)throw new Error('http');const buf=await res.arrayBuffer();
  const AC=window.AudioContext||window.webkitAudioContext;const ac=new AC();const ab=await ac.decodeAudioData(buf);if(ac.close)ac.close();
  if(my!==SRC)return;
  const N=1600,ch=ab.getChannelData(0),per=Math.max(1,Math.floor(ch.length/N)),pk=new Float32Array(N);let mx=0;
  for(let i=0;i<N;i++){let m=0;const s0=i*per;for(let j=0;j<per;j+=3){const v=Math.abs(ch[s0+j]||0);if(v>m)m=v;}pk[i]=m;if(m>mx)mx=m;}
  if(mx>0)for(let i=0;i<N;i++)pk[i]/=mx;peaks=pk;waveMsg='';
 }catch(e){if(my!==SRC)return;peaks=null;waveMsg=location.protocol==='file:'?'волна видна при запуске через http-сервер':'звук не разобран';}
 drawWave();
}
function drawWave(){
 const c=$('#wave'),w=c.clientWidth,h=c.clientHeight;$('#waveMsg').textContent=peaks?'':waveMsg;if(!w)return;
 const dpr=devicePixelRatio||1;c.width=w*dpr;c.height=h*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);g.clearRect(0,0,w,h);
 if(!peaks)return;g.fillStyle=getComputedStyle(tl).getPropertyValue('--wave').trim()||'#888';
 for(let x=0;x<w;x+=2){const a=peaks[Math.floor(x/w*peaks.length)]*(h/2-4);g.fillRect(x,h/2-a,1.3,Math.max(1,a*2));}
}
function seekP(v,t){return new Promise(r=>{const done=()=>{v.removeEventListener('seeked',done);clearTimeout(to);r();};const to=setTimeout(done,2500);v.addEventListener('seeked',done);v.currentTime=t;});}
async function film(){
 const c=$('#film'),w=c.clientWidth,h=c.clientHeight;if(!w||!S.dur)return;const my=++filmJob;
 const dpr=devicePixelRatio||1;c.width=w*dpr;c.height=h*dpr;const g=c.getContext('2d');g.scale(dpr,dpr);
 if(FV.readyState<1)await Promise.race([new Promise(r=>FV.addEventListener('loadedmetadata',r,{once:true})),new Promise(r=>setTimeout(r,4000))]);
 const ar=(FV.videoWidth/FV.videoHeight)||16/9,tw=h*ar,n=Math.ceil(w/tw);
 for(let i=0;i<n;i++){if(my!==filmJob)return;await seekP(FV,Math.min(S.dur-0.05,(i*tw+tw/2)/w*S.dur));if(my!==filmJob)return;try{g.drawImage(FV,i*tw,0,tw,h);}catch(e){}}
}
function layout(){if(!isPro())return;renderRuler();drawWave();renderBlocks();renderSel();film();}
let roT;new ResizeObserver(()=>{clearTimeout(roT);roT=setTimeout(layout,120);}).observe(tl);

/* ---------- список правок ---------- */
function card(e,i){
 const ty=TYPES[e.type];let body='';
 if(e.type==='text')body=`<input data-f="text" placeholder="Текст на видео" value="${esc(e.text)}">
  <div class="vpe-row"><label>Где<select data-f="pos">${Object.entries(POS).map(([k,v])=>`<option value="${k}"${e.pos===k?' selected':''}>${v}</option>`).join('')}</select></label>
  <label>Размер<select data-f="size">${Object.entries(SIZE).map(([k,v])=>`<option value="${k}"${e.size===k?' selected':''}>${v}</option>`).join('')}</select></label></div>`;
 if(e.type==='volume')body=`<div class="vpe-row"><input type="range" data-f="db" min="-30" max="12" step="1" value="${e.db}"${e.mute?' disabled':''}><output>${dbl(e)}</output></div>
  <div class="vpe-row"><label class="chk"><input type="checkbox" data-f="mute"${e.mute?' checked':''}>Без звука</label><label class="chk"><input type="checkbox" data-f="fade"${e.fade?' checked':''}>Плавно</label></div>`;
 if(e.type==='speed')body=`<div class="vpe-row"><label>Скорость<select data-f="rate">${[0.25,0.5,0.75,1.25,1.5,2,3,4].map(r=>`<option value="${r}"${e.rate===r?' selected':''}>×${r}</option>`).join('')}</select></label></div>`;
 if(e.type==='note')body=`<textarea data-f="text" rows="2" placeholder="Что сделать в этом месте">${esc(e.text)}</textarea>`;
 if(e.type==='region')body=`<div class="vpe-row vpe-row--reg"><label>Действие<select data-f="action">${Object.entries(ACT).map(([k,v])=>`<option value="${k}"${e.action===k?' selected':''}>${v}</option>`).join('')}</select></label><span class="vpe-rect mono" data-rect>${rectLbl(e)}</span></div>
  <textarea data-f="text" rows="2" placeholder="Комментарий к выделенной области">${esc(e.text)}</textarea>`;
 const time=e.type==='note'?`<div class="vpe-time">в <input data-f="a" class="mono" value="${fmt(e.a)}"></div>`
  :`<div class="vpe-time"><input data-f="a" class="mono" value="${fmt(e.a)}">–<input data-f="b" class="mono" value="${fmt(e.b)}"><span class="dur">${(e.b-e.a).toFixed(2)} с</span></div>`;
 return `<div class="vpe-card${e.id===S.active?' is-active':''}" data-id="${e.id}" style="--c:${ty.c}">
  <div class="vpe-card__hd"><i class="vpe-ico vpe-ico--${e.type}">${ty.ico}</i><b>${i+1}. ${ty.name}</b><button class="vpe-x" type="button" data-act="del" title="Удалить (Del)" aria-label="Удалить">${IC('trash')}</button></div>${time}${body}</div>`;
}
function rectPx(e){const W=V.videoWidth,H=V.videoHeight;return W&&H?{x:Math.round(e.x*W),y:Math.round(e.y*H),w:Math.round(e.w*W),h:Math.round(e.h*H)}:null;}
function rectLbl(e){const p=rectPx(e);return p?`${p.w}×${p.h} px · x ${p.x}, y ${p.y}`:`${Math.round(e.w*100)}×${Math.round(e.h*100)}%`;}
function renderList(){
 const arr=sorted();$('#cnt').textContent=arr.length?arr.length+' шт.':'';
 list.innerHTML=arr.length?arr.map(card).join(''):`<div class="vpe-empty">Правок пока нет.<br>Нажмите «Вырезать», «Текст», «Звук» или «Скорость»: правка встанет от курсора, края блока можно потянуть на таймлайне. Чтобы сразу задать участок, проведите по таймлайну пальцем или мышью. Заметка ставится в точку курсора. «Область» — рамка на кадре на участке времени, к ней комментарий: нарисуйте её пальцем или мышью прямо на видео.</div>`;
}
function updateCardTimes(e){const c=list.querySelector(`.vpe-card[data-id="${e.id}"]`);if(!c)return;const a=c.querySelector('[data-f=a]'),b=c.querySelector('[data-f=b]'),d=c.querySelector('.dur');if(a)a.value=fmt(e.a);if(b)b.value=fmt(e.b);if(d)d.textContent=(e.b-e.a).toFixed(2)+' с';}
function updButtons(){
 $('#undoBtn').disabled=!hist.length;
 $('#resetBtn').disabled=!S.edits.length;
 $('#sendBtn').disabled=!S.edits.length&&!$('#prompt').value.trim();
}
function renderAll(){renderBlocks();renderSel();renderList();updButtons();ovlKey='';}

list.addEventListener('focusin',ev=>{if(ev.target.dataset.f)pendingSnap=true;});
list.addEventListener('input',ev=>{
 const f=ev.target.dataset.f;if(!f||f==='a'||f==='b')return;
 const c=ev.target.closest('.vpe-card'),e=byId(c.dataset.id);if(!e)return;
 if(pendingSnap){snap();pendingSnap=false;}
 if(f==='text')e.text=ev.target.value;
 if(f==='pos'||f==='size'||f==='action')e[f]=ev.target.value;
 if(f==='rate')e.rate=+ev.target.value;
 if(f==='fade')e.fade=ev.target.checked;
 if(f==='db')e.db=+ev.target.value;
 if(f==='mute'){e.mute=ev.target.checked;c.querySelector('[data-f=db]').disabled=e.mute;}
 const o=c.querySelector('output');if(o)o.textContent=dbl(e);
 setActive(e.id);renderBlocks();
});
list.addEventListener('change',ev=>{
 const f=ev.target.dataset.f;if(f!=='a'&&f!=='b')return;
 const e=byId(ev.target.closest('.vpe-card').dataset.id);const v=parseT(ev.target.value);
 if(!e||v==null){if(e)ev.target.value=fmt(e[f]);return;}
 snap();
 if(e.type==='note')e.a=e.b=r2(clamp(v,0,S.dur));
 else if(f==='a')e.a=r2(clamp(v,0,e.b-0.1));else e.b=r2(clamp(v,e.a+0.1,S.dur));
 S.active=e.id;renderAll();seek(e[f]);
});
list.addEventListener('keydown',ev=>{if(ev.key==='Enter'&&(ev.target.dataset.f==='a'||ev.target.dataset.f==='b'))ev.target.blur();});
list.addEventListener('click',ev=>{
 const c=ev.target.closest('.vpe-card');if(!c)return;const id=c.dataset.id;
 if(ev.target.closest('[data-act=del]')){del(id);return;}
 setActive(id);if(!ev.target.closest('input,select,textarea,label')){const e=byId(id);if(e)seek(e.a);}
});

/* ---------- таймлайн: мышь и тач ---------- */
function x2t(cx){const r=tl.getBoundingClientRect();return clamp((cx-r.left)/r.width,0,1)*S.dur;}
const pxPerSec=()=>tl.clientWidth/(S.dur||1);
tl.addEventListener('pointerdown',ev=>{
 if(!S.dur||ev.button>0)return;ev.preventDefault();tl.setPointerCapture(ev.pointerId);
 const blk=ev.target.closest('.vpe-blk');
 if(ev.target.closest('.vpe-ph')){drag={kind:'ph'};if(!V.paused)V.pause();return;}
 if(blk){const e=byId(blk.dataset.id);setActive(e.id);
  const m=ev.target.classList.contains('l')?'l':ev.target.classList.contains('r')?'r':'m';
  drag={kind:'blk',e,mode:m,t0:x2t(ev.clientX),a0:e.a,b0:e.b,moved:false};
  const card=list.querySelector(`.vpe-card[data-id="${e.id}"]`);if(card)card.scrollIntoView({block:'nearest'});return;}
 const t=x2t(ev.clientX);drag={kind:'sel',t0:t,x0:ev.clientX,moved:false};if(!V.paused)V.pause();seek(t);
});
tl.addEventListener('pointermove',ev=>{
 if(!drag)return;const t=x2t(ev.clientX);
 if(drag.kind==='ph'){seek(t);return;}
 if(drag.kind==='sel'){if(!drag.moved&&Math.abs(ev.clientX-drag.x0)<4)return;drag.moved=true;
  S.sel={a:r2(Math.min(drag.t0,t)),b:r2(Math.max(drag.t0,t))};renderSel();seek(t);return;}
 const e=drag.e,dt=t-drag.t0;
 if(!drag.moved){if(Math.abs(dt)*pxPerSec()<3)return;snap();drag.moved=true;}
 if(e.type==='note'||drag.mode==='m'){const len=drag.b0-drag.a0,a=clamp(drag.a0+dt,0,S.dur-len);e.a=r2(a);e.b=r2(a+len);seek(e.a);}
 else if(drag.mode==='l'){e.a=r2(clamp(drag.a0+dt,0,e.b-0.1));seek(e.a);}
 else{e.b=r2(clamp(drag.b0+dt,e.a+0.1,S.dur));seek(e.b);}
 renderBlocks();updateCardTimes(e);ovlKey='';
});
const endDrag=()=>{
 if(!drag)return;
 if(drag.kind==='sel'&&!drag.moved){S.sel=null;renderSel();}
 if(drag.kind==='sel'&&drag.moved&&S.sel&&S.sel.b-S.sel.a<0.05){S.sel=null;renderSel();}
 if(drag.kind==='blk'){if(drag.moved)renderList();else seek(drag.e.a);}
 drag=null;
};
tl.addEventListener('pointerup',endDrag);tl.addEventListener('pointercancel',endDrag);

/* ---------- предпросмотр ---------- */
function placeOvl(){
 const st=$('#stage').getBoundingClientRect(),vr=V.getBoundingClientRect(),vw=V.videoWidth||16,vh=V.videoHeight||9;
 const k=Math.min(vr.width/vw,vr.height/vh),w=vw*k,h=vh*k;
 Object.assign(ovl.style,{left:(vr.left-st.left+(vr.width-w)/2)+'px',top:(vr.top-st.top+(vr.height-h)/2)+'px',width:w+'px',height:h+'px',fontSize:(h/100)+'px'});
 Object.assign(reg.style,{left:ovl.style.left,top:ovl.style.top,width:ovl.style.width,height:ovl.style.height});
}
function applyPreview(t){
 const P=S.preview;
 const vol=S.edits.find(e=>e.type==='volume'&&t>=e.a&&t<=e.b);
 let g=1;if(P&&vol){g=vol.mute?0:Math.pow(10,vol.db/20);if(vol.fade&&!vol.mute){const f=Math.min(0.4,(vol.b-vol.a)/2),k=t<vol.a+f?(t-vol.a)/f:t>vol.b-f?(vol.b-t)/f:1;g=1+(g-1)*clamp(k,0,1);}}
 const nv=clamp(g,0,1);if(Math.abs(V.volume-nv)>0.004)V.volume=nv;
 const sp=S.edits.find(e=>e.type==='speed'&&t>=e.a&&t<e.b),rate=P&&sp?sp.rate:1;if(V.playbackRate!==rate)V.playbackRate=rate;
 const texts=S.edits.filter(e=>e.type==='text'&&t>=e.a&&t<=e.b&&(P||e.id===S.active));
 const cut=S.edits.find(e=>e.type==='cut'&&t>=e.a&&t<e.b);
 const badges=[];if(cut)badges.push('участок будет вырезан');if(sp)badges.push('скорость ×'+sp.rate);if(vol)badges.push(vol.mute?'без звука':'звук '+dbs(vol.db)+(vol.db>0&&P?' (в превью до 0 dB)':''));
 const key=texts.map(e=>[e.id,e.text,e.pos,e.size,e.id===S.active].join(':')).join('|')+'#'+badges.join(',');
 if(key!==ovlKey){ovlKey=key;ovl.innerHTML=texts.map(e=>`<div class="vpe-ovl__t pos-${e.pos} sz-${e.size}${e.id===S.active&&V.paused?' is-edit':''}">${esc(e.text||'Текст')}</div>`).join('')+(badges.length?`<div class="vpe-ovl__badges">${badges.map(b=>`<span>${esc(b)}</span>`).join('')}</div>`:'');}
 placeOvl();renderReg(t);
}
let regKey='';
function renderReg(t,force){
 const act=byId(S.active),actR=act&&act.type==='region'?act:null;
 const arr=sorted().filter(e=>e.type==='region'&&((t>=e.a&&t<=e.b)||e===actR));
 const key=arr.map(e=>[e.id,e.x,e.y,e.w,e.h,e.text,e.action,e===actR,t>=e.a&&t<=e.b].join(':')).join('|')+(regDrag?'#d':'')+(actR?'#a':'');
 reg.classList.toggle('is-draw',!!actR);
 if(!force&&key===regKey)return;regKey=key;
 const idx=id=>sorted().findIndex(e=>e.id===id)+1;
 reg.innerHTML=arr.map(e=>{const a=e===actR,out=!(t>=e.a&&t<=e.b);
  return `<div class="vpe-rbox${a?' is-active':''}${out?' is-out':''}${e.y*reg.clientHeight<36?' is-top':''}" data-id="${e.id}" style="left:${e.x*100}%;top:${e.y*100}%;width:${e.w*100}%;height:${e.h*100}%">`+
  `<span class="vpe-rbox__lbl">${IC('region')}${idx(e.id)}. ${esc(e.text||ACT[e.action])}</span>`+
  (a?'<b class="rh" data-h="nw"></b><b class="rh" data-h="ne"></b><b class="rh" data-h="sw"></b><b class="rh" data-h="se"></b>':'')+'</div>';}).join('')+
  (actR&&!regDrag&&actR.w===0.4&&actR.h===0.4&&actR.x===0.3&&actR.y===0.3?'<div class="vpe-rtip">Проведите по видео, чтобы выделить область</div>':'');
}
let regDrag=null;
const MINR=0.03;
function rpt(ev){const r=reg.getBoundingClientRect();return{x:clamp((ev.clientX-r.left)/r.width,0,1),y:clamp((ev.clientY-r.top)/r.height,0,1)};}
function updRectCard(e){const c=list.querySelector(`.vpe-card[data-id="${e.id}"] [data-rect]`);if(c)c.textContent=rectLbl(e);}
reg.addEventListener('pointerdown',ev=>{
 if(ev.button>0)return;
 const boxEl=ev.target.closest('.vpe-rbox'),h=ev.target.closest('.rh');
 let e=boxEl?byId(boxEl.dataset.id):byId(S.active);
 if(!e||e.type!=='region')return;
 ev.preventDefault();reg.setPointerCapture(ev.pointerId);if(!V.paused)V.pause();
 if(boxEl&&S.active!==e.id){setActive(e.id);const cd=list.querySelector(`.vpe-card[data-id="${e.id}"]`);if(cd)cd.scrollIntoView({block:'nearest'});}
 const p=rpt(ev);
 regDrag={e,mode:h?h.dataset.h:boxEl?'move':'draw',p0:p,r0:{x:e.x,y:e.y,w:e.w,h:e.h},x0:ev.clientX,y0:ev.clientY,moved:false};
 renderReg(V.currentTime,true);
});
reg.addEventListener('pointermove',ev=>{
 const d=regDrag;if(!d)return;
 if(!d.moved){if(Math.hypot(ev.clientX-d.x0,ev.clientY-d.y0)<4)return;snap();d.moved=true;}
 const p=rpt(ev),e=d.e,r=d.r0;
 if(d.mode==='draw'){e.x=Math.min(d.p0.x,p.x);e.y=Math.min(d.p0.y,p.y);e.w=Math.abs(p.x-d.p0.x);e.h=Math.abs(p.y-d.p0.y);}
 else if(d.mode==='move'){e.x=clamp(r.x+p.x-d.p0.x,0,1-r.w);e.y=clamp(r.y+p.y-d.p0.y,0,1-r.h);}
 else{let x1=r.x,y1=r.y,x2=r.x+r.w,y2=r.y+r.h;
  if(d.mode.includes('w'))x1=clamp(p.x,0,x2-MINR);if(d.mode.includes('e'))x2=clamp(p.x,x1+MINR,1);
  if(d.mode.includes('n'))y1=clamp(p.y,0,y2-MINR);if(d.mode.includes('s'))y2=clamp(p.y,y1+MINR,1);
  e.x=x1;e.y=y1;e.w=x2-x1;e.h=y2-y1;}
 e.x=r3(e.x);e.y=r3(e.y);e.w=r3(e.w);e.h=r3(e.h);
 renderReg(V.currentTime,true);updRectCard(e);
});
const endReg=()=>{const d=regDrag;if(!d)return;regDrag=null;const e=d.e;
 if(d.moved&&(e.w<MINR||e.h<MINR)){Object.assign(e,d.r0);hist.pop();updButtons();toast('Слишком маленькая область');}
 updRectCard(e);renderBlocks();renderReg(V.currentTime,true);
 if(d.moved)$('#toast').classList.remove('on');
 if(d.moved&&d.mode==='draw'){const f=list.querySelector(`.vpe-card[data-id="${e.id}"] textarea[data-f=text]`);if(f&&!matchMedia('(pointer:coarse)').matches)f.focus({preventScroll:true});}
};
reg.addEventListener('pointerup',endReg);reg.addEventListener('pointercancel',endReg);
function tick(){if(!host.isConnected)return;
 if(isPro()&&S.dur){
  let t=V.currentTime;
  if(S.preview&&!V.paused){for(let i=0;i<5;i++){const c=S.edits.find(e=>e.type==='cut'&&t>=e.a&&t<e.b-0.03);if(!c)break;t=c.b;}
   if(t!==V.currentTime){if(t>=S.dur-0.05){V.pause();V.currentTime=S.dur;}else V.currentTime=t;}}
  applyPreview(V.currentTime);ph.style.left=pct(V.currentTime);
  $('#tc').textContent=fmt(V.currentTime)+' / '+fmt(S.dur);
 }
 requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
V.addEventListener('click',()=>{if(isPro())V.paused?V.play():V.pause();});

/* ---------- Pro on/off ---------- */
function togglePro(on){
 on=on===undefined?!isPro():on;box.classList.toggle('is-pro',on);$('#proBtn').setAttribute('aria-pressed',String(on));V.controls=!on;
 if(on)requestAnimationFrame(layout);else{V.volume=1;V.playbackRate=1;ovl.innerHTML='';ovlKey='';reg.innerHTML='';reg.classList.remove('is-draw');regKey='';}
 if(!on&&!togglePro.quiet)MP.onClose(false);
}
$('#proBtn').onclick=()=>togglePro();
$('#lbClose').onclick=()=>{V.pause();MP.onClose(false);};
$('#playBtn').onclick=()=>V.paused?V.play():V.pause();
$$('[data-step]').forEach(b=>b.onclick=ev=>step((ev.shiftKey?1:1/S.fps)*(+b.dataset.step)));
$('#inBtn').onclick=setIn;$('#outBtn').onclick=setOut;
$('#undoBtn').onclick=undo;
$('#resetBtn').onclick=()=>{if(!S.edits.length)return;snap();S.edits=[];S.sel=null;S.active=null;renderAll();toast('Все правки сброшены. Ctrl+Z или ↶ вернёт их');};
$('#prevChk').onchange=ev=>{S.preview=ev.target.checked;ovlKey='';};
$$('[data-add]').forEach(b=>b.onclick=()=>add(b.dataset.add));
$('#prompt').addEventListener('input',updButtons);

/* ---------- клавиатура ---------- */
__on(window,'keydown',ev=>{const __t=ev.composedPath&&ev.composedPath()[0]||ev.target;
 if(!$('#modal').hidden){if(ev.key==='Escape')closeModal();return;}
 if(!$('#image-lightbox').classList.contains('image-lightbox--shown'))return;
 const inField=/^(INPUT|TEXTAREA|SELECT)$/.test(__t.tagName);
 if((ev.ctrlKey||ev.metaKey)&&ev.key==='Enter'&&isPro()){ev.preventDefault();send();return;}
 if(inField){if(ev.key==='Escape')__t.blur();return;}
 if(ev.code==='KeyP'&&!ev.ctrlKey&&!ev.metaKey){ev.preventDefault();togglePro();return;}
 if(!isPro()){if(ev.key==='Escape')MP.onClose(false);return;}
 if((ev.ctrlKey||ev.metaKey)&&ev.code==='KeyZ'){ev.preventDefault();undo();return;}
 if(ev.ctrlKey||ev.metaKey||ev.altKey)return;
 const map={Space:()=>V.paused?V.play():V.pause(),KeyI:setIn,KeyO:setOut,KeyC:()=>add('cut'),KeyT:()=>add('text'),KeyV:()=>add('volume'),KeyS:()=>add('speed'),KeyM:()=>add('note'),KeyR:()=>add('region'),
  ArrowLeft:()=>step(ev.shiftKey?-1:-1/S.fps),ArrowRight:()=>step(ev.shiftKey?1:1/S.fps),Delete:()=>del(S.active),Backspace:()=>del(S.active),
  Escape:()=>{if(S.sel){S.sel=null;renderSel();}else if(S.active)setActive(null);else togglePro(false);}};
 const f=map[ev.code];if(f){ev.preventDefault();f();}
});

/* ---------- отправка (имитация) ---------- */
function payload(){
 const edits=sorted().map(e=>{const o={id:e.id,op:e.type};
  if(e.type==='note')o.at=e.a;else{o.start=e.a;o.end=e.b;}
  if(e.type==='text')Object.assign(o,{text:e.text,position:e.pos,size:e.size});
  if(e.type==='volume')Object.assign(o,e.mute?{mute:true}:{gain_db:e.db,fade:e.fade});
  if(e.type==='speed')o.rate=e.rate;
  if(e.type==='region')Object.assign(o,{rect:{x:r3(e.x),y:r3(e.y),w:r3(e.w),h:r3(e.h)},rect_px:rectPx(e),action:e.action,comment:e.text});
  if(e.type==='note')o.comment=e.text;return o;});
 return {kind:'video_edit_request',version:1,context:{...CTX},
  file:{name:FILE.name,message_id:FILE.message_id,url:FILE.url,width:V.videoWidth,height:V.videoHeight,duration:r2(S.dur)},
  time_base:'source',rect_base:'fraction_of_frame_top_left',edits,prompt:$('#prompt').value.trim(),output:{keep_original:$('#keepOrig').checked,container:'mp4'},
  client:{ui:'lightbox-pro/0.1',created_at:new Date().toISOString()}};
}
function message(P){
 const L=[`Правки к видео «${P.file.name}» (${P.file.duration.toFixed(1)} с, ${P.file.width}×${P.file.height})`];
 P.edits.forEach((e,i)=>{const r=e.op==='note'?fmt(e.at):`${fmt(e.start)}–${fmt(e.end)}`;let s;
  if(e.op==='cut')s=`Вырезать ${r}`;
  else if(e.op==='text')s=`Текст «${e.text||'…'}» ${r}, ${POS[e.position]}, размер ${SIZE[e.size]}`;
  else if(e.op==='volume')s=e.mute?`Убрать звук ${r}`:`Громкость ${dbs(e.gain_db)} ${r}${e.fade?', плавно':''}`;
  else if(e.op==='speed')s=`Скорость ×${e.rate} ${r}`;
  else if(e.op==='region'){const p=e.rect_px,where=p?`${p.w}×${p.h} px от точки ${p.x},${p.y}`:`${Math.round(e.rect.w*100)}×${Math.round(e.rect.h*100)}% кадра`;s=`Область ${r}: ${where}, ${ACT[e.action]}${e.comment?` — ${e.comment}`:''}`;}
  else s=`Заметка ${r}: ${e.comment||'…'}`;
  L.push(`${i+1}. ${s}`);});
 if(P.prompt)L.push('',`Комментарий: ${P.prompt}`);
 if(P.edits.some(e=>e.op==='region'))L.push('','Координаты областей — от левого верхнего угла кадра исходника, в rect доли 0..1.');
 L.push('','Полная разметка — в JSON (video_edit_request).');
 L.push('',`Время указано по исходному файлу. ${P.output.keep_original?'Исходник не трогать, результат новым файлом.':'Исходник можно перезаписать.'}`);
 return L.join('\n');
}
let lastP=null,tab='msg';
function jsonHL(o){return esc(JSON.stringify(o,null,2)).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?)/g,(m,s,c,b,n)=>s?(c?`<span class="j-k">${s}</span>${c}`:`<span class="j-s">${s}</span>`):b?`<span class="j-b">${b}</span>`:`<span class="j-n">${n}</span>`);}
function showTab(){const o=$('#mOut');o.classList.toggle('is-msg',tab==='msg');if(tab==='msg')o.textContent=message(lastP);else o.innerHTML=jsonHL(lastP);$$('.vpe-tab').forEach(b=>b.classList.toggle('on',b.dataset.tab===tab));}
function send(){
 if($('#sendBtn').disabled){toast('Нет правок для отправки');return;}
 lastP=payload();console.log('[video_edit_request]',lastP);
 tab='msg';showTab();$('#modal').hidden=false;
}
function closeModal(){$('#modal').hidden=true;}
async function sendNow(){if(!lastP)return;const ok=MP.onSend?await MP.onSend({kind:'video',text:message(lastP),payload:lastP,count:lastP.edits.length}):true;if(ok===false)return;closeModal();V.pause();MP.onClose(true);}
$('#sendBtn').onclick=send;
$$('.vpe-tab').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;showTab();});
$('#mOk').onclick=sendNow;
$('#modal').addEventListener('click',ev=>{if(ev.target.id==='modal')closeModal();});
$('#mCopy').onclick=async()=>{const t=$('#mOut').textContent;try{await navigator.clipboard.writeText(t);}catch(e){const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();}toast('Скопировано');};

/* ---------- демо ---------- */
function demo(){
 const d=S.dur;
 S.edits=[
  {id:'e'+(seq++),type:'text',a:0.5,b:2.8,text:'Сцена 1: вступление',pos:'bottom',size:'m'},
  {id:'e'+(seq++),type:'cut',a:r2(d*0.22),b:r2(d*0.33)},
  {id:'e'+(seq++),type:'volume',a:r2(d*0.4),b:r2(d*0.55),db:-12,mute:false,fade:true},
  {id:'e'+(seq++),type:'speed',a:r2(d*0.64),b:r2(d*0.78),rate:2},
  {id:'e'+(seq++),type:'region',a:r2(d*0.4),b:r2(d*0.55),x:0.58,y:0.12,w:0.36,h:0.3,action:'blur',text:'Размыть номер телефона в углу'},
  {id:'e'+(seq++),type:'note',a:r2(d*0.88),b:r2(d*0.88),text:'Сюда логотип в правый нижний угол'}];
 $('#prompt').value='Титры белые с тенью, шрифт как в прошлом ролике.';
 renderAll();
}

/* ---------- старт ---------- */
setTimeout(onMeta,0);
setSrc(MP.src,FILE.name);
togglePro(true);if(MP.time>0){const t0=MP.time;V.addEventListener('loadedmetadata',()=>{try{V.currentTime=Math.min(t0,V.duration||t0);}catch(_){}} ,{once:true});}
updPlay();
host.__vpe={S,payload,message,destroy(){try{V.pause();V.removeAttribute('src');V.load();FV.removeAttribute('src');FV.load();}catch(_){}}};

}

/* ── общая часть ── */
const HAS_VIDEO = true;
let CUR = null;   // { host, off[], mo, kind }

function gatewayName() {
    return (document.body && document.body.dataset.gateway) === 'sigma' ? 'Sigma' : 'CatchPM';
}

/* message_id и имя файла из url ответа агента:
 * /api/chats/{chat}/image/{mid}/{name}?image_key=..  или  /api/chats/{chat}/files/{mid}/{name} */
function parseUrl(url) {
    const m = String(url || '').match(/\/api\/chats\/([^/]+)\/(?:image|files)\/([^/?#]+)\/([^/?#]+)/);
    if (!m) return null;
    const dec = (s) => { try { return decodeURIComponent(s); } catch (_) { return s; } };
    return { chat: dec(m[1]), message_id: dec(m[2]), name: dec(m[3]) };
}

function canEdit(item) {
    if (!item || !item.url || item.url === '#') return false;
    if (!parseUrl(item.url)) return false;     // только файлы из сообщений агента
    return item.isVideo ? HAS_VIDEO : true;
}

let OPEN_SEQ = 0;

function close() {
    OPEN_SEQ++;                 // отменяет open(), который ещё ждёт стили
    if (!CUR) return;
    const c = CUR; CUR = null;
    c.off.forEach((f) => { try { f(); } catch (_) {} });
    if (c.mo) c.mo.disconnect();
    try { c.host.__vpe && c.host.__vpe.destroy && c.host.__vpe.destroy(); } catch (_) {}
    c.host.remove();
}

async function open(kind, item, ctx) {
    close();
    const seq = OPEN_SEQ;
    let css;
    try { css = await loadCss(kind); }
    catch (e) {
        console.error('[media-pro] css', e);
        if (seq === OPEN_SEQ) ctx?.app?.showToast?.('Не удалось открыть Pro-редактор');
        return;
    }
    // пока грузились стили: открыли другой файл, закрыли или закрыли лайтбокс
    if (seq !== OPEN_SEQ || (ctx && ctx.alive && !ctx.alive())) return;
    const host = document.createElement('div');
    host.className = 'media-pro-host';
    host.setAttribute('data-kind', kind);
    const syncTheme = () => {
        // Не data-theme: theme.css задаёт --accent и фон на любом [data-theme=…],
        // и хост перекрыл бы палитру, которую palette.js ставит на <html>.
        host.dataset.mpTheme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    };
    syncTheme();
    const mo = new MutationObserver(syncTheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const R = host.attachShadow({ mode: 'open' });
    const st = document.createElement('style');
    st.textContent = css;
    R.appendChild(st);
    const wrap = document.createElement('template');
    wrap.innerHTML = kind === 'video' ? VID_HTML : IMG_HTML;
    R.appendChild(wrap.content);
    document.body.appendChild(host);
    const off = [];
    const __on = (target, type, fn, o) => { target.addEventListener(type, fn, o); off.push(() => target.removeEventListener(type, fn, o)); };
    CUR = { host, off, mo, kind };

    const meta = parseUrl(item.url) || {};
    const app = ctx && ctx.app;
    const file = {
        name: item.name || meta.name || (kind === 'video' ? 'video.mp4' : 'image'),
        message_id: meta.message_id || null,
        url: item.url,
    };
    if (item.imageKey) file.image_key = item.imageKey;
    const opts = {
        file,
        src: (ctx && ctx.src) || item.url,
        time: (ctx && ctx.time) || 0,
        context: { source: gatewayName(), chat_id: (app && app.currentChat) || meta.chat || null },
        onClose: (toChat) => {
            close();
            if (toChat && ctx && typeof ctx.closeLightbox === 'function') ctx.closeLightbox();
        },
        onSend: async ({ text, payload }) => {
            if (!app || typeof app.sendMessage !== 'function') return false;
            if (app._runState && app._runState !== 'idle') {
                app.showToast?.('Агент ещё отвечает. Дождитесь ответа и отправьте правки.');
                return false;
            }
            if (!app.currentChat) { app.showToast?.('Откройте чат, чтобы отправить правки'); return false; }
            const body = text + '\n\n```json\n' + JSON.stringify(payload, null, 2) + '\n```';
            try { await app.sendMessage(body); } catch (e) { console.error('[media-pro] send', e); return false; }
            return true;
        },
    };
    try {
        if (kind === 'video') mountVideo(R, host, opts, __on);
        else {
            mountImage(R, host, opts, __on);
            const img = R.getElementById('img');
            const nm = R.getElementById('edName');
            if (nm) { nm.textContent = file.name; nm.title = file.name; }
            if (img) img.src = opts.src;
        }
    } catch (e) {
        console.error('[media-pro] mount', e);
        close();
        app?.showToast?.('Не удалось открыть Pro-редактор');
    }
}

/* Подключение к лайтбоксу приложения.
 * bindLightbox({ bar, before, app, getItem, getSrc, closeLightbox }) → refresh()
 * refresh() вызывается при каждом показе слайда: кнопка видна только для
 * файлов агента, которые умеем размечать. */
function bindLightbox(cfg) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'image-lightbox__pro';
    btn.title = 'Разметить правки для агента (P)';
    btn.setAttribute('aria-label', 'Pro-разметка');
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20l4-1 10.5-10.5a2.1 2.1 0 00-3-3L5 16z"/><path d="M14 7l3 3"/><path d="M19 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg><span>Pro</span>';
    btn.hidden = true;
    if (cfg.before && cfg.before.parentNode === cfg.bar) cfg.bar.insertBefore(btn, cfg.before);
    else cfg.bar.appendChild(btn);
    const go = (e) => {
        if (e) e.stopPropagation();
        const it = cfg.getItem();
        if (!canEdit(it)) return;
        const vid = cfg.bar.closest('.image-lightbox')?.querySelector('video');
        if (vid && !vid.paused) vid.pause();
        open(it.isVideo ? 'video' : 'image', it, {
            app: cfg.app,
            src: (it.isVideo && vid && vid.currentSrc) || (cfg.getSrc ? cfg.getSrc() : it.url),
            time: (it.isVideo && vid) ? (vid.currentTime || 0) : 0,
            closeLightbox: cfg.closeLightbox,
            alive: () => cfg.bar.isConnected,
        });
    };
    btn.addEventListener('click', go);
    const refresh = () => {
        const it = cfg.getItem();
        btn.hidden = !canEdit(it);
        // стили подтягиваем заранее, чтобы Pro открывался без задержки
        if (!btn.hidden) loadCss(it.isVideo ? 'video' : 'image').catch(() => {});
    };
    refresh();
    // open() зовут из keydown лайтбокса: откладываем, иначе тот же KeyP
    // долетит до window-слушателя только что смонтированного редактора
    return { refresh, open: () => { if (!btn.hidden) setTimeout(go, 0); } };
}

window.MediaPro = {
    bindLightbox,
    canEdit,
    isOpen: () => !!CUR,
    close,
    _open: open,
};
})();
