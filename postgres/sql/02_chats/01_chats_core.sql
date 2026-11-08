-- =============================================================================
-- 02_chats/01_chats_core.sql
-- Основные таблицы диалогов, файлов, сгенерированных изображений, опросов,
-- воркспейсов редактора, тарифов моделей и настроек чата
-- =============================================================================

CREATE TABLE IF NOT EXISTS chats (
    id             SERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL,
    chat_id        TEXT NOT NULL,
    message_id     TEXT,
    message_text   TEXT,
    is_from_bot    BOOLEAN,
    tool_name      TEXT,
    tool_trace     TEXT,
    selected_tools TEXT[],
    context_size   TEXT,
    model_type     VARCHAR(100),
    tokens_usage   JSONB,
    gateway_target TEXT,
    reactions      JSONB DEFAULT '[]'::jsonb,
    reaction       TEXT DEFAULT NULL,
    input_format   TEXT DEFAULT 'text',
    created_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_chats_user_id ON chats(user_id);
CREATE INDEX IF NOT EXISTS ix_chats_chat_id ON chats(chat_id);
CREATE INDEX IF NOT EXISTS ix_chats_message_id ON chats(message_id);

-- Типы чатов (один чат — один тип агента)
CREATE TABLE IF NOT EXISTS chat_types_agent (
    chat_id    TEXT NOT NULL,
    user_id    BIGINT NOT NULL,
    type_agent TEXT NOT NULL DEFAULT 'pm',
    PRIMARY KEY (chat_id, user_id)
);

-- Метаданные чатов для левого сайдбара
CREATE TABLE IF NOT EXISTS chat_sidebar_meta (
    chat_id       TEXT NOT NULL,
    user_id       BIGINT NOT NULL,
    is_deleted    BOOLEAN NOT NULL DEFAULT FALSE,
    is_favorite   BOOLEAN NOT NULL DEFAULT FALSE,
    renamed_title TEXT,
    auto_title    TEXT,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, user_id)
);

-- Файлы, прикреплённые или отправленные в чатах
CREATE TABLE IF NOT EXISTS chat_files (
    id         SERIAL PRIMARY KEY,
    message_id TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    user_id    BIGINT NOT NULL,
    file_name  TEXT NOT NULL,
    file_ext   TEXT,
    source     TEXT,
    file_path  TEXT,
    file_kind  TEXT,
    image_key  TEXT,
    file_size  BIGINT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Размер файла в байтах (для индикатора загрузки на фронте); для старых баз.
ALTER TABLE chat_files ADD COLUMN IF NOT EXISTS file_size BIGINT;

CREATE INDEX IF NOT EXISTS ix_chat_files_message_id ON chat_files(message_id);
CREATE INDEX IF NOT EXISTS ix_chat_files_chat_id ON chat_files(chat_id);
CREATE INDEX IF NOT EXISTS ix_chat_files_source ON chat_files(source);

-- Сгенерированные изображения (generate_image): бинарник + метаданные
CREATE TABLE IF NOT EXISTS generated_images (
    id           SERIAL PRIMARY KEY,
    image_key    UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    chat_id      TEXT,
    user_id      BIGINT,
    message_id   TEXT,
    file_name    TEXT NOT NULL,
    mime_type    TEXT,
    prompt       TEXT NOT NULL,
    model        TEXT,
    aspect_ratio TEXT,
    temperature  REAL,
    image_data   BYTEA NOT NULL,
    byte_size    INTEGER,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_generated_images_image_key ON generated_images(image_key);
CREATE INDEX IF NOT EXISTS ix_generated_images_chat_id ON generated_images(chat_id);
CREATE INDEX IF NOT EXISTS ix_generated_images_message_id ON generated_images(message_id);
CREATE INDEX IF NOT EXISTS ix_generated_images_created_at ON generated_images(created_at DESC);

-- Самоцикл агента (утилизация ресурсов / фоновые задачи в чате)
CREATE TABLE IF NOT EXISTS agent_loop_tasks (
    id          SERIAL PRIMARY KEY,
    chat_id     TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    instruction TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_agent_loop_tasks_chat ON agent_loop_tasks(chat_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_agent_loop_tasks_chat_active
    ON agent_loop_tasks(chat_id) WHERE is_active;

-- Тарифы моделей
CREATE TABLE IF NOT EXISTS model_tariffs (
    model_name          TEXT PRIMARY KEY,
    abstract_model_name TEXT,
    input_per_1m        NUMERIC(14,4) NOT NULL DEFAULT 0,
    output_per_1m       NUMERIC(14,4) NOT NULL DEFAULT 0,
    currency            TEXT NOT NULL DEFAULT 'RUB',
    updated_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_model_tariffs_abstract
    ON model_tariffs(abstract_model_name);

INSERT INTO model_tariffs (model_name, abstract_model_name, input_per_1m, output_per_1m)
VALUES
    ('openai/gpt-5.4',                'coder',     0, 0),
    ('anthropic/claude-opus-4.8',     'designer',  0, 0),
    ('zai-org/GLM-5.2',               'universal', 0, 0),
    ('google/gemini-3.1-pro-preview', 'gemini',    0, 0)
ON CONFLICT (model_name) DO NOTHING;

-- Ответы пользователей на интерактивные опросы (InteractivePoll)
CREATE TABLE IF NOT EXISTS chat_poll_answers (
    id              SERIAL PRIMARY KEY,
    chat_id         TEXT NOT NULL,
    bot_message_id  TEXT NOT NULL,
    poll_index      INTEGER NOT NULL DEFAULT 0,
    user_id         BIGINT NOT NULL,
    user_message_id TEXT,
    selected_index  INTEGER,
    selected_value  TEXT NOT NULL,
    is_custom       BOOLEAN NOT NULL DEFAULT FALSE,
    is_skipped      BOOLEAN NOT NULL DEFAULT FALSE,
    poll_payload    JSONB,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chat_id, bot_message_id, poll_index)
);

CREATE INDEX IF NOT EXISTS ix_chat_poll_answers_chat_id ON chat_poll_answers(chat_id);
CREATE INDEX IF NOT EXISTS ix_chat_poll_answers_bot_msg ON chat_poll_answers(bot_message_id);

-- Реестр воркспейсов песочницы, закреплённых за пользователем (/editor)
CREATE TABLE IF NOT EXISTS editor_workspaces (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    workspace_id TEXT NOT NULL,
    chat_id      TEXT,
    title        TEXT,
    ttl_hours    INTEGER DEFAULT 10,
    is_permanent BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at   TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '10 hours'),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS ix_editor_ws_user ON editor_workspaces(user_id, is_active);
CREATE INDEX IF NOT EXISTS ix_editor_ws_wsid ON editor_workspaces(workspace_id);
CREATE INDEX IF NOT EXISTS ix_editor_ws_chat ON editor_workspaces(chat_id);

-- Настройки выбранных тулов, модели и активного чата пользователя
CREATE TABLE IF NOT EXISTS user_settings (
    user_id        BIGINT PRIMARY KEY,
    selected_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    selected_model VARCHAR(32) NOT NULL DEFAULT 'gpt',
    active_chat_id TEXT DEFAULT NULL,
    updated_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
