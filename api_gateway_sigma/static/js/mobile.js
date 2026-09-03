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

    // Клик по гамбургеру
   if (toggleBtn) {
        toggleBtn.addEventListener("click", (e) => {
            console.log('Регистриуем клик по гамбе')
            e.stopPropagation();
            toggleSidebar(!sidebar.classList.contains('open'));
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

    // Клик по пункту меню — закрыть (опционально)
    document.querySelectorAll('.chat-item, .new-chat-btn').forEach(item => {
        item.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                toggleSidebar(false);
            }
        });
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