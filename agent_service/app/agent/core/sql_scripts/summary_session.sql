-- ─────────────────────────────────────────────────────────────────────────────
-- Таблица саммари сессий
-- Агент тыкает session_point(start|end, chat_id) — мы храним диапазон сообщений
-- и LLM-сводку по ним.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS session_summaries (
    id              BIGSERIAL    PRIMARY KEY,
    session_id      INTEGER      NOT NULL UNIQUE,   -- случайное 6-значное число
    chat_id         TEXT         NOT NULL,
    start_msg_id    INTEGER,                        -- id первого сообщения в chats
    end_msg_id      INTEGER,                        -- id последнего сообщения в chats
    summary_text    TEXT,                           -- LLM-сводка по диапазону
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chat_rolling_summary (
    chat_id     TEXT PRIMARY KEY,
    summary_text TEXT NOT NULL,
    covers_messages INTEGER NOT NULL DEFAULT 0,  -- сколько сообщений уже засаммаризовано, для дебага
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE chat_rolling_summary
    ADD COLUMN IF NOT EXISTS covered_up_to_id BIGINT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_session_summaries_chat_id
    ON session_summaries (chat_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_session_summaries_session_id
    ON session_summaries (session_id);

COMMENT ON TABLE session_summaries IS
'Саммари сессий агента по чатам. Одна запись = одна сессия (start → end).
Подставляется в messages при каждом новом запросе пользователя.';

COMMENT ON COLUMN session_summaries.session_id IS
'Случайное 6-значное число (100000–999999), уникальный идентификатор сессии.';

COMMENT ON COLUMN session_summaries.start_msg_id IS
'id записи в таблице chats на момент старта сессии.';

COMMENT ON COLUMN session_summaries.end_msg_id IS
'id последней записи в таблице chats на момент завершения сессии.';

COMMENT ON COLUMN session_summaries.summary_text IS
'LLM-сводка диалога за сессию. Заполняется при вызове session_point(end).';


-- === Migration chat_id -> TEXT (UUID) ===
DO $migr_ss$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_name='session_summaries' AND column_name='chat_id') = 'bigint' THEN
        ALTER TABLE session_summaries ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
    END IF;
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_name='chat_rolling_summary' AND column_name='chat_id') = 'bigint' THEN
        ALTER TABLE chat_rolling_summary ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
    END IF;
END $migr_ss$;

GRANT SELECT, INSERT, UPDATE ON session_summaries TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE session_summaries_id_seq TO knowledge_manager;