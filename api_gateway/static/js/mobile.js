function initMobileMenu() {
    const sidebar = document.querySelector('.sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const toggleBtn = document.getElementById('mobile-menu-toggle');

    if (!sidebar || !overlay || !toggleBtn) { // просто !toggleBtn
        console.warn('initMobileMenu: не найдены элементы', { sidebar, overlay, toggleBtn });
        return;
    }
    // Функция открытия/закрытия
    const toggleSidebar = (open) => {
        sidebar.classList.toggle('open', open);
        overlay.classList.toggle('visible', open);
        document.body.style.overflow = open ? 'hidden' : ''; // Блокируем скролл фона
    };

    // Клик по гамбургеру — ЕДИНЫЙ обработчик (B: убрано двойное управление).
    // На мобилке (<=768) бургер открывает/закрывает сайдбар оверлеем.
    // На десктопе (>768) бургер виден только когда сайдбар свёрнут (CSS
    // .sidebar-collapsed) и играет роль кнопки "развернуть" — снимает collapse.
    if (toggleBtn) {
        // Единый критерий "мобилка" — тот же брейкпоинт, что в CSS (@media max-width:768px).
        const isMobile = () => window.matchMedia('(max-width: 768px)').matches;

        toggleBtn.addEventListener("click", (e) => {
            e.stopPropagation();

            // МОБИЛКА: бургер всегда открывает/закрывает сайдбар оверлеем.
            // Это приоритетная ветка — на узком экране desktop-collapse неактуален.
            if (isMobile()) {
                toggleSidebar(!sidebar.classList.contains('open'));
                return;
            }

            // ДЕСКТОП (>768): бургер виден только когда сайдбар свёрнут (CSS
            // .sidebar-collapsed) и играет роль кнопки "развернуть" — снимает collapse.
            const layout = document.querySelector('.chat-layout');
            const expandBtn = document.getElementById('sidebar-expand-btn');
            const collapseBtn = document.getElementById('sidebar-collapse-btn');
            if (layout && layout.classList.contains('sidebar-collapsed')) {
                layout.classList.remove('sidebar-collapsed');
                if (expandBtn) expandBtn.hidden = true;
                if (collapseBtn) collapseBtn.setAttribute('aria-expanded', 'true');
                try { localStorage.setItem('catchpm:sidebar-collapsed', '0'); } catch (err) {}
            }
        });
    }

    //
    // document.querySelectorAll('.mobile-menu-toggle').forEach(item => {
    //     item.addEventListener('click', (e) => {
    //         console.log('Регистриуем клик по гамбе')
    //         e.stopPropagation();
    //         toggleSidebar(!sidebar.classList.contains('open'));
    //     });
    // });

    // Клик по оверлею — закрыть
    overlay?.addEventListener('click', () => {
        toggleSidebar(false);
    });

    // Клик по пункту меню — закрыть (опционально).
    // Делегируем на document, потому что .chat-item рендерятся динамически,
    // а querySelectorAll ловит только элементы, существующие на момент init.
    document.addEventListener('click', (e) => {
        if (window.innerWidth > 768) return;

        // Тап по кнопке "⋯" (или её содержимому) НЕ должен закрывать сайдбар —
        // это открытие/закрытие dropdown действий над чатом.
        if (e.target.closest('.chat-item-menu-btn')) return;
        // Клик внутри самого выпадающего меню — тоже не трогаем сайдбар.
        if (e.target.closest('.chat-item-dropdown')) return;

        // Закрываем сайдбар только при осмысленном переходе:
        // выбор чата или создание нового.
        if (e.target.closest('.chat-item') || e.target.closest('.new-chat-btn')) {
            toggleSidebar(false);
        }
    });

    // Закрытие по Escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) {
            toggleSidebar(false);
        }
    });

    // Закрытие при изменении размера окна (если развернули на десктопе)
    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            toggleSidebar(false);
        }
    });
}