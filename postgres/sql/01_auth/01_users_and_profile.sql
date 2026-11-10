-- =============================================================================
-- 01_auth/01_users_and_profile.sql
-- Таблицы авторизации, пользователей, обратной связи, онбординга и настроек UI
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    scholar_token VARCHAR(255),
    source        VARCHAR(20) DEFAULT 'catchPM',
    is_guest      BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);

-- Каталог обновлений приложения (What's New)
CREATE TABLE IF NOT EXISTS app_updates (
    id           SERIAL PRIMARY KEY,
    version      VARCHAR(50),
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    is_published BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_app_updates_published
    ON app_updates (is_published, created_at);

-- Просмотренные пользователем релизы
CREATE TABLE IF NOT EXISTS user_seen_updates (
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    update_id INTEGER NOT NULL REFERENCES app_updates(id) ON DELETE CASCADE,
    seen_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, update_id)
);

CREATE INDEX IF NOT EXISTS ix_user_seen_updates_user
    ON user_seen_updates (user_id);

-- Отметка прохождения демо-онбординга
CREATE TABLE IF NOT EXISTS user_onboarding (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    demo_seen    BOOLEAN NOT NULL DEFAULT FALSE,
    demo_seen_at TIMESTAMP
);

-- Персональные настройки интерфейса (палитра и дополнительные параметры)
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    palette    JSONB NOT NULL DEFAULT '{}'::jsonb,
    extra      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
