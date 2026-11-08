-- =============================================================================
-- 02_chats/02_group_chats.sql
-- Таблицы общего / группового чата внутри Web UI
-- =============================================================================

CREATE TABLE IF NOT EXISTS group_rooms (
    id         BIGSERIAL PRIMARY KEY,
    room_uid   TEXT NOT NULL UNIQUE,
    room_type  TEXT NOT NULL DEFAULT 'group',  -- global | group | direct
    title      TEXT,
    created_by BIGINT,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_group_rooms_room_uid ON group_rooms(room_uid);
CREATE INDEX IF NOT EXISTS ix_group_rooms_type ON group_rooms(room_type);

-- Предсоздание общей комнаты ('global')
INSERT INTO group_rooms (room_uid, room_type, title, created_by)
VALUES ('global', 'global', 'Общий чат', NULL)
ON CONFLICT (room_uid) DO NOTHING;

CREATE TABLE IF NOT EXISTS group_messages (
    id            BIGSERIAL PRIMARY KEY,
    room_id       BIGINT NOT NULL REFERENCES group_rooms(id),
    user_id       BIGINT NOT NULL,
    username      TEXT,
    message_text  TEXT,
    client_msg_id TEXT,
    is_deleted    BOOLEAN NOT NULL DEFAULT FALSE,
    is_pinned     BOOLEAN NOT NULL DEFAULT FALSE,
    edited_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_group_messages_room ON group_messages(room_id, id);
CREATE INDEX IF NOT EXISTS ix_group_messages_pinned ON group_messages(room_id, is_pinned);
CREATE INDEX IF NOT EXISTS ix_group_messages_client ON group_messages(client_msg_id);

CREATE TABLE IF NOT EXISTS group_room_members (
    room_id   BIGINT NOT NULL REFERENCES group_rooms(id),
    user_id   BIGINT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'member',  -- owner | admin | member
    joined_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (room_id, user_id)
);

CREATE TABLE IF NOT EXISTS group_message_files (
    id            BIGSERIAL PRIMARY KEY,
    message_id    BIGINT REFERENCES group_messages(id),
    room_id       BIGINT NOT NULL REFERENCES group_rooms(id),
    client_msg_id TEXT,
    obs_key       TEXT NOT NULL,
    file_name     TEXT,
    mime_type     TEXT,
    size_bytes    BIGINT,
    is_image      BOOLEAN NOT NULL DEFAULT FALSE,
    width         INTEGER,   -- кадр с учётом поворота, для размера карточки до загрузки
    height        INTEGER,
    thumbhash     TEXT,      -- ThumbHash base64, размытая заглушка под кольцом
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE group_message_files ADD COLUMN IF NOT EXISTS width INTEGER;
ALTER TABLE group_message_files ADD COLUMN IF NOT EXISTS height INTEGER;
ALTER TABLE group_message_files ADD COLUMN IF NOT EXISTS thumbhash TEXT;

CREATE INDEX IF NOT EXISTS ix_group_files_msg ON group_message_files(message_id);
CREATE INDEX IF NOT EXISTS ix_group_files_client ON group_message_files(client_msg_id);
CREATE INDEX IF NOT EXISTS ix_group_files_room ON group_message_files(room_id);
