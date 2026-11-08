/* Единый лоадер медиа «Кольцо» для всех интерфейсов (личный чат, Sigma, общий чат).
   Разметка — inline SVG, анимация и цвета — .fa-ring в chat.css (дуга от --accent).
   Обёртка .fa-media-loader центрирует кольцо на подложке. */
(function () {
  const IMG = '<rect class="fa-ring__glyph" x="-7" y="-5.5" width="14" height="11" rx="2"/>'
    + '<path class="fa-ring__cut" d="M-5.5 4l3.5-4 2.5 2.5 2-2 3 3.5z"/>';
  const PLAY = '<path class="fa-ring__glyph" d="M-4 -5.5v11l9-5.5z"/>';

  function html(isVideo = false) {
    return '<svg class="fa-ring" viewBox="0 0 48 48" aria-hidden="true">'
      + '<circle class="fa-ring__track" cx="24" cy="24" r="20"/>'
      + '<circle class="fa-ring__arc" cx="24" cy="24" r="20"/>'
      + '<g class="fa-ring__icon" transform="translate(24 24)"><g class="fa-ring__pulse">'
      + (isVideo ? PLAY : IMG) + '</g></g></svg>';
  }

  window.MediaRing = { html };
})();
