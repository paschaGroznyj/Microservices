CREATE TABLE IF NOT EXISTS article_embeddings (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    embedding vector(1024) NOT NULL,
    embedding_model TEXT NOT NULL,
    source_text TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (article_id, embedding_model)
);

CREATE TABLE IF NOT EXISTS papers_metadata (
    external_id     TEXT PRIMARY KEY,
    source          TEXT,
    title           TEXT,
    date_sub        DATE,
    url_article     TEXT,
    pdf_url         TEXT,
    authors         JSONB,
    tags            TEXT[],
    abstract        TEXT,
    file_article    TEXT,
    category        TEXT,
    relevance_score FLOAT,
    relevance_reasoning TEXT,
    is_relevant     BOOLEAN,
    pdf_downloaded  BOOLEAN,
    review_flag BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS reviews (
    id              BIGSERIAL PRIMARY KEY,
    external_id     TEXT NOT NULL REFERENCES papers_metadata(external_id) ON DELETE CASCADE,
    review_text     TEXT NOT NULL,
    category        TEXT,                       -- название папки-источника
    source_file     TEXT,                       -- оригинальное имя файла
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (external_id)                        -- одно ревью на статью
);

GRANT SELECT, INSERT, UPDATE ON papers_metadata TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE papers_metadata_id_seq, reviews_id_seq TO knowledge_manager;
