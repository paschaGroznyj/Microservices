CREATE TABLE IF NOT EXISTS token_log (
    id            BIGSERIAL PRIMARY KEY,
    chat_id       BIGINT,
    user_id       BIGINT,
    session_id    INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    total_tokens  INTEGER,
    iterations    INTEGER,
    model         VARCHAR(128),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);