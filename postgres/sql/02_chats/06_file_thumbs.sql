-- Превью 384 px для сетки «Медиа» панели вложений (attachments-panel.js).
-- Делается один раз (chats_service/app/core/file_thumbs.py): при загрузке файла в общий чат,
-- в chats-consumer после записи chat_files, для старых файлов при первом запросе.
-- Ровно одна ссылка: на chat_files (чаты агента) или на group_message_files (общий чат).
-- data IS NULL: превью сделать нельзя (не картинка, битый файл), повторно не пытаемся.
CREATE TABLE IF NOT EXISTS file_thumbs (
    id            BIGSERIAL PRIMARY KEY,
    chat_file_id  INTEGER UNIQUE REFERENCES chat_files(id) ON DELETE CASCADE,
    group_file_id BIGINT  UNIQUE REFERENCES group_message_files(id) ON DELETE CASCADE,
    mime          TEXT,
    width         INTEGER,
    height        INTEGER,
    data          BYTEA,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT file_thumbs_one_src CHECK (num_nonnulls(chat_file_id, group_file_id) = 1)
);
