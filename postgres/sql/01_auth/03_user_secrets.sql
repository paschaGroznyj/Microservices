-- =============================================================================
-- 01_auth/03_user_secrets.sql
-- Пользовательские ключи внешних сервисов (RouterAPI, позже GitHub, HF и т.д.)
--
-- Отдельная схема vault: ALTER DEFAULT PRIVILEGES ... IN SCHEMA public
-- (05_grants) сюда не действует, поэтому роли пулов агента для execute_sql_query
-- (knowledge_manager, my_journal) эту таблицу не видят. Читают и пишут только
-- сервисы под DB_USER: auth_service (запись из профиля) и agent_service (своим
-- основным пулом, расшифровывает в момент вызова инструмента).
--
-- secret_enc — Fernet-токен от JSON {"v":1,"u":user_id,"p":provider,"n":name,"s":secret}.
-- u/p/n сверяются при расшифровке: шифротекст, перенесённый в чужую строку, не читается.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS vault;
REVOKE ALL ON SCHEMA vault FROM PUBLIC;

CREATE TABLE IF NOT EXISTS vault.user_secrets (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL CHECK (provider ~ '^[a-z0-9_]{2,32}$'),
    name         TEXT NOT NULL DEFAULT 'default' CHECK (name ~ '^[a-z0-9_-]{1,32}$'),
    secret_enc   TEXT NOT NULL,
    last4        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    UNIQUE (user_id, provider, name)
);

REVOKE ALL ON ALL TABLES IN SCHEMA vault FROM PUBLIC;
