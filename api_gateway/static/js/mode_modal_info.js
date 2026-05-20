function openHelp() {
  const overlay = document.getElementById("help-overlay");
  overlay.classList.remove("closing");
  overlay.classList.add("active");
}

function closeHelp() {
  const overlay = document.getElementById("help-overlay");
  overlay.classList.add("closing");
  setTimeout(() => overlay.classList.remove("active", "closing"), 350);
}

// ── Аккордеон ─────────────────────────────────────────────
function toggleSection(sectionToOpen) {
  // 1. Проверяем, была ли эта секция уже открыта
  const isAlreadyOpen = sectionToOpen.classList.contains("open");

  // 2. Закрываем ВСЕ секции
  document.querySelectorAll(".help-section").forEach(sec => {
    sec.classList.remove("open");
  });

  // 3. Если нажатая секция была закрыта — открываем её.
  // Если была открыта — она останется закрытой (так как мы закрыли всё в шаге 2).
  if (!isAlreadyOpen) {
    sectionToOpen.classList.add("open");

    // Опционально: скролл к началу открытой секции, если она длинная
    // sectionToOpen.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}



// ── Слушатели ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {

  const openBtn = document.getElementById("open-help");
  if (openBtn) openBtn.addEventListener("click", openHelp);

  const closeBtn = document.getElementById("help-close");
  if (closeBtn) closeBtn.addEventListener("click", closeHelp);

  // Клик по фону
  const overlay = document.getElementById("help-overlay");
  if (overlay) {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeHelp();
    });
  }

  // Аккордеон — делегирование на все заголовки секций
  document.querySelectorAll(".help-section-header").forEach(header => {
    header.addEventListener("click", () => {
      const section = header.closest(".help-section");
      if (section) toggleSection(section);
    });
  });

  // Закрытие по Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeHelp();
  });

});