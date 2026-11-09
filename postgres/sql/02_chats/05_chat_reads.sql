-- Отметки прочтения: одна строка на пару (тип чата, чат, пользователь).
-- Универсальная таблица: сейчас scope='group' (комнаты group_rooms),
-- дальше сюда же лягут другие типы чатов без новой таблицы.
-- last_read_id только растёт (UPSERT с GREATEST), поэтому опоздавшая пачка
-- отметку назад не откатит. Галочки отправителя: MAX(last_read_id) по другим
-- пользователям комнаты (как в Telegram), «кто прочитал»: last_read_id >= id.
CREATE TABLE IF NOT EXISTS chat_reads (
    scope        TEXT        NOT NULL,              -- 'group' | ...
    chat_id      BIGINT      NOT NULL,              -- для 'group': group_rooms.id
    user_id      BIGINT      NOT NULL,
    username     TEXT,                              -- для списка «кто прочитал»
    last_read_id BIGINT      NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (scope, chat_id, user_id)
);

-- MAX по комнате для галочек и список прочитавших сообщение
CREATE INDEX IF NOT EXISTS ix_chat_reads_chat_last ON chat_reads(scope, chat_id, last_read_id);
