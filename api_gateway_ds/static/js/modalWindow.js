document.addEventListener('DOMContentLoaded', () => {
            const menuToggle = document.getElementById('menu-toggle');
            const userMenu = document.getElementById('user-menu');
            const logoutTrigger = document.getElementById('logout-trigger');
            const logoutModal = document.getElementById('logout-modal');
            const modalClose = document.getElementById('modal-close');
            const modalCancel = document.getElementById('modal-cancel');

            // 🔹 Открытие/закрытие меню
            menuToggle?.addEventListener('click', (e) => {
                e.stopPropagation();
                const isHidden = userMenu.hidden;
                userMenu.hidden = !isHidden;
                menuToggle.setAttribute('aria-expanded', isHidden);
            });

            // Закрытие меню при клике вне
            document.addEventListener('click', (e) => {
                if (!userMenu.hidden && !menuToggle.contains(e.target) && !userMenu.contains(e.target)) {
                    userMenu.hidden = true;
                    menuToggle.setAttribute('aria-expanded', 'false');
                }
            });

            // 🔹 Открытие модального окна при клике на "Выйти"
            logoutTrigger?.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();

                // Закрываем меню
                userMenu.hidden = true;
                menuToggle.setAttribute('aria-expanded', 'false');

                // Открываем модал
                logoutModal.hidden = false;
                document.body.style.overflow = 'hidden';

                // Фокусируемся на кнопке отмены для доступности
                setTimeout(() => modalCancel?.focus(), 100);
            });

            // 🔹 Закрытие модального окна
            const closeModal = () => {
                logoutModal.hidden = true;
                document.body.style.overflow = '';
            };

            modalClose?.addEventListener('click', closeModal);
            modalCancel?.addEventListener('click', closeModal);

            // Закрытие по клику на оверлей
            logoutModal?.addEventListener('click', (e) => {
                if (e.target === logoutModal) closeModal();
            });

            // Закрытие по Escape
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && !logoutModal.hidden) {
                    e.preventDefault();
                    closeModal();
                }
            });
        });