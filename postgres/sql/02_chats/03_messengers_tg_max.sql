-- =============================================================================
-- 02_chats/03_messengers_tg_max.sql
-- Таблицы интеграции мессенджеров Telegram и MAX (чаты, настройки, участники, триггеры)
-- =============================================================================

-- ── Telegram ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS telegram_chats (
    chat_id          TEXT PRIMARY KEY,
    chat_type        TEXT NOT NULL DEFAULT 'group',  -- 'group' | 'private'
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    added_by_user_id BIGINT,
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_telegram_chats_active
    ON telegram_chats(is_active);

CREATE TABLE IF NOT EXISTS telegram_chat_settings (
    chat_id                TEXT PRIMARY KEY
                           REFERENCES telegram_chats(chat_id) ON DELETE CASCADE,
    system_prompt          TEXT,
    agent_name             TEXT,
    agent_model            TEXT,
    selected_tools         JSONB,
    trigger_mode           TEXT NOT NULL DEFAULT 'orchestrator', -- 'mention' | 'orchestrator' | 'every'
    orchestrator_context_n INT NOT NULL DEFAULT 6,
    proactive_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    timezone_name          TEXT NOT NULL DEFAULT 'Europe/Moscow',
    updated_at             TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS telegram_chat_members (
    chat_id    TEXT NOT NULL
               REFERENCES telegram_chats(chat_id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    username   TEXT,
    first_name TEXT,
    is_bot     BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_tg_members_chat
    ON telegram_chat_members(chat_id);

CREATE TABLE IF NOT EXISTS telegram_start_log (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    chat_id     TEXT NOT NULL,
    username    TEXT,
    first_name  TEXT,
    is_allowed  BOOLEAN,
    start_count INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, chat_id)
);

CREATE INDEX IF NOT EXISTS ix_tg_start_log_user ON telegram_start_log(user_id);
CREATE INDEX IF NOT EXISTS ix_tg_start_log_time ON telegram_start_log(created_at);

CREATE TABLE IF NOT EXISTS telegram_triggers (
    id               SERIAL PRIMARY KEY,
    chat_id          TEXT NOT NULL
                     REFERENCES telegram_chats(chat_id) ON DELETE CASCADE,
    added_by_user_id BIGINT,
    kind             TEXT NOT NULL DEFAULT 'recurring', -- 'recurring' | 'once'
    title            TEXT,
    time_msk         TEXT NOT NULL,
    days_of_week     INT[],
    trigger_date     DATE,
    prompt           TEXT,
    preset_key       TEXT,
    addressed        BOOLEAN NOT NULL DEFAULT FALSE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_fired_at    TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_tg_triggers_chat ON telegram_triggers(chat_id);
CREATE INDEX IF NOT EXISTS ix_tg_triggers_scan ON telegram_triggers(is_active, time_msk);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tg_triggers_chat_preset
    ON telegram_triggers(chat_id, preset_key) WHERE preset_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_tg_triggers_custom_natkey
    ON telegram_triggers(chat_id, kind, time_msk, COALESCE(title,''), COALESCE(trigger_date,'0001-01-01'::date))
    WHERE preset_key IS NULL;

-- ── MAX ───────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS max_chats (
    chat_id          TEXT PRIMARY KEY,
    chat_type        TEXT NOT NULL DEFAULT 'group',
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    added_by_user_id BIGINT,
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS max_chat_settings (
    chat_id                TEXT PRIMARY KEY
                           REFERENCES max_chats(chat_id) ON DELETE CASCADE,
    system_prompt          TEXT,
    agent_name             TEXT,
    agent_model            TEXT,
    selected_tools         JSONB,
    trigger_mode           TEXT DEFAULT 'mention',
    orchestrator_context_n INTEGER DEFAULT 8,
    proactive_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    timezone_name          TEXT NOT NULL DEFAULT 'Europe/Moscow',
    updated_at             TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS max_triggers (
    id               SERIAL PRIMARY KEY,
    chat_id          TEXT NOT NULL REFERENCES max_chats(chat_id) ON DELETE CASCADE,
    added_by_user_id BIGINT,
    kind             TEXT NOT NULL DEFAULT 'recurring',
    title            TEXT,
    time_msk         TEXT NOT NULL,
    days_of_week     INT[],
    trigger_date     DATE,
    prompt           TEXT,
    preset_key       TEXT,
    addressed        BOOLEAN NOT NULL DEFAULT FALSE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    last_fired_at    TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_max_triggers_scan ON max_triggers(is_active, time_msk);
CREATE UNIQUE INDEX IF NOT EXISTS ux_max_triggers_chat_preset
    ON max_triggers(chat_id, preset_key) WHERE preset_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_max_triggers_custom_natkey
    ON max_triggers(chat_id, kind, time_msk, COALESCE(title,''), COALESCE(trigger_date,'0001-01-01'::date))
    WHERE preset_key IS NULL;

CREATE TABLE IF NOT EXISTS max_chat_members (
    chat_id    TEXT NOT NULL,
    user_id    BIGINT NOT NULL,
    username   TEXT,
    first_name TEXT,
    is_bot     BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_max_members_chat
    ON max_chat_members(chat_id);
