// groupMembers.js — модалка управления участниками группового чата.
// Открывается из меню чата ("Пригласить участников"). Стиль — chat-search-overlay.
// Источник правды по участникам — таблица chats_group (вариант Б):
//   added_at → «добавлен», removed_at → «удалён». Плашки-события в ленте
//   рендерит app.js по данным /api/chats/{id}/members (см. renderGroupEvents).
(function () {
    "use strict";

    const overlay = document.getElementById("group-members-overlay");
    const modal = document.getElementById("group-members-modal");
    const input = document.getElementById("gm-input");
    const closeBtn = document.getElementById("gm-close");
    const membersBox = document.getElementById("gm-members");
    const membersLabel = document.getElementById("gm-members-label");
    const searchLabel = document.getElementById("gm-search-label");
    const resultsBox = document.getElementById("gm-results");
    const hint = document.getElementById("gm-hint");

    if (!overlay || !modal) {
        console.warn("[group-members] overlay/modal не найдены в DOM");
        return;
    }

    const state = {
        chatId: null,
        currentUserId: null,
        isAdmin: false,
        members: [],
        searchTimer: null,
        lastQuery: null,
    };

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function fmtDate(iso) {
        if (!iso) return "";
        try {
            const d = new Date(iso);
            return d.toLocaleString([], {
                day: "2-digit", month: "2-digit", year: "2-digit",
                hour: "2-digit", minute: "2-digit",
            });
        } catch (_e) { return ""; }
    }

    // ── Открытие / закрытие ────────────────────────────────────
    function open(chatId) {
        if (!chatId) return;
        state.chatId = String(chatId);
        console.log("[group-members] open modal chat=", state.chatId);
        overlay.classList.add("active");
        input.value = "";
        state.lastQuery = null;
        loadMembers().then(() => searchUsers(""));
        setTimeout(() => input.focus(), 60);
    }

    function close() {
        console.log("[group-members] close modal");
        overlay.classList.remove("active");
        state.chatId = null;
    }

    // ── Загрузка участников ────────────────────────────────────
    async function loadMembers() {
        if (!state.chatId) return;
        try {
            const res = await fetch(`/api/chats/${state.chatId}/members`, { credentials: "include" });
            if (!res.ok) { console.warn("[group-members] members fetch status", res.status); return; }
            const data = await res.json();
            state.members = data.members || [];
            state.currentUserId = data.current_user_id ?? null;
            state.isAdmin = !!data.is_current_admin;
            console.log("[group-members] loaded members=", state.members.length, "isAdmin=", state.isAdmin);
            renderMembers();
        } catch (e) {
            console.error("[group-members] loadMembers error", e);
        }
    }

    function renderMembers() {
        const list = state.members || [];
        // Прячем ВСЮ секцию «Участники» (родительский .gm-section), а не только label,
        // иначе у соседней секции «Добавить» остаётся висеть border-top (.gm-section + .gm-section).
        const membersSection = membersLabel.closest(".gm-section");
        if (!list.length) {
            if (membersSection) membersSection.style.display = "none";
            membersBox.innerHTML = "";
            return;
        }
        if (membersSection) membersSection.style.display = "";
        membersLabel.style.display = "";
        membersBox.innerHTML = list.map((m) => {
            const initial = esc((m.username || "?").charAt(0).toUpperCase());
            const admin = m.is_admin ? `<span class="gm-badge">админ</span>` : "";
            const when = m.added_at ? `<span class="gm-when">с ${esc(fmtDate(m.added_at))}</span>` : "";
            // Корзину показываем только админу и только для НЕ-админов (админа удалять нельзя).
            const canRemove = state.isAdmin && !m.is_admin;
            const trash = canRemove
                ? `<button class="gm-trash" data-uid="${esc(m.user_id)}" aria-label="Удалить из чата" title="Удалить из чата">
                       <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6">
                           <path d="M4 6h12M8 6V4.5A1.5 1.5 0 0 1 9.5 3h1A1.5 1.5 0 0 1 12 4.5V6M6.5 6l.6 9a1.5 1.5 0 0 0 1.5 1.4h2.8a1.5 1.5 0 0 0 1.5-1.4l.6-9" stroke-linecap="round" stroke-linejoin="round"/>
                       </svg>
                   </button>`
                : "";
            return `<div class="gm-member" data-uid="${esc(m.user_id)}">
                        <div class="gm-avatar">${initial}</div>
                        <div class="gm-info">
                            <div class="gm-name">${esc(m.username)} ${admin}</div>
                            ${when}
                        </div>
                        ${trash}
                    </div>`;
        }).join("");
    }

    // ── Поиск пользователей ────────────────────────────────────
    async function searchUsers(q) {
        if (!state.chatId) return;
        const query = (q || "").trim();
        state.lastQuery = query;
        try {
            const url = `/api/users/search?q=${encodeURIComponent(query)}`
                + `&chat_id=${encodeURIComponent(state.chatId)}&limit=20`;
            const res = await fetch(url, { credentials: "include" });
            if (!res.ok) { hint.textContent = "Ошибка поиска"; return; }
            const data = await res.json();
            if (state.lastQuery !== query) return; // пришёл устаревший ответ
            renderResults(data.results || []);
        } catch (e) {
            console.error("[group-members] searchUsers error", e);
            hint.textContent = "Ошибка поиска";
        }
    }

    function renderResults(results) {
        if (!results.length) {
            resultsBox.innerHTML = `<div class="cse-hint">Никого не нашлось</div>`;
            return;
        }
        resultsBox.innerHTML = results.map((u) => {
            const initial = esc((u.username || "?").charAt(0).toUpperCase());
            const already = u.already_member;
            const btn = already
                ? `<span class="gm-already">уже в чате</span>`
                : `<button class="gm-add" data-uid="${esc(u.user_id)}" data-uname="${esc(u.username)}" aria-label="Добавить" title="Добавить в чат">
                       <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8">
                           <path d="M10 4v12M4 10h12" stroke-linecap="round"/>
                       </svg>
                   </button>`;
            return `<div class="gm-result ${already ? "is-member" : ""}">
                        <div class="gm-avatar">${initial}</div>
                        <div class="gm-name">${esc(u.username)}</div>
                        ${btn}
                    </div>`;
        }).join("");
    }

    // ── Действия: добавить / удалить ───────────────────────────
    async function addMember(uid, uname) {
        if (!state.chatId || !uid) return;
        console.log("[group-members] add member uid=", uid, uname);
        try {
            const res = await fetch(`/api/chats/${state.chatId}/members/add`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_user_id: Number(uid) }),
            });
            if (!res.ok) {
                const t = await res.text();
                console.warn("[group-members] add failed", res.status, t);
                if (res.status === 403) alert("Добавлять участников может только администратор чата.");
                return;
            }
            const data = await res.json();
            state.members = data.members || state.members;
            // Пересчёт прав: если это был первый инвайт — я стал админом.
            if (data.admin_id != null && state.currentUserId != null) {
                state.isAdmin = Number(data.admin_id) === Number(state.currentUserId);
            }
            renderMembers();
            searchUsers(state.lastQuery || ""); // обновить already_member
        } catch (e) {
            console.error("[group-members] addMember error", e);
        }
    }

    async function removeMember(uid) {
        if (!state.chatId || !uid) return;
        console.log("[group-members] remove member uid=", uid);
        try {
            const res = await fetch(`/api/chats/${state.chatId}/members/remove`, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ target_user_id: Number(uid) }),
            });
            if (!res.ok) {
                const t = await res.text();
                console.warn("[group-members] remove failed", res.status, t);
                if (res.status === 403) alert("Удалять участников может только администратор чата.");
                return;
            }
            const data = await res.json();
            state.members = data.members || [];
            renderMembers();
            searchUsers(state.lastQuery || "");
        } catch (e) {
            console.error("[group-members] removeMember error", e);
        }
    }

    // ── WS: состав участников изменился извне ───────────────────
    function onMembersUpdated(chatId) {
        if (String(chatId) !== String(state.chatId)) return;
        if (!overlay.classList.contains("active")) return;
        console.log("[group-members] onMembersUpdated -> reload");
        loadMembers();
    }

    // ── Слушатели ──────────────────────────────────────────────
    closeBtn && closeBtn.addEventListener("click", close);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && overlay.classList.contains("active")) close();
    });

    input.addEventListener("input", () => {
        clearTimeout(state.searchTimer);
        const q = input.value;
        // Реалтайм-поиск с дебаунсом, как в поиске сообщений.
        state.searchTimer = setTimeout(() => searchUsers(q), 180);
    });

    // Делегирование кликов по кнопкам + / корзина.
    modal.addEventListener("click", (e) => {
        const addBtn = e.target.closest(".gm-add");
        if (addBtn) { addMember(addBtn.dataset.uid, addBtn.dataset.uname); return; }
        const trashBtn = e.target.closest(".gm-trash");
        if (trashBtn) {
            const uid = trashBtn.dataset.uid;
            if (confirm("Удалить участника из чата?")) removeMember(uid);
            return;
        }
    });

    window.GroupMembers = { open, close, onMembersUpdated };
    console.log("[group-members] module ready");
})();
