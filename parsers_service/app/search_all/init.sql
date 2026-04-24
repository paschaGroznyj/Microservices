CREATE TABLE IF NOT EXISTS pipeline_runs (
    id BIGSERIAL PRIMARY KEY,
    user_query TEXT NOT NULL,
    topic TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS articles (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    date_sub DATE,
    url_article TEXT,
    pdf_url TEXT,
    authors_json JSONB,
    tags_json JSONB,
    abstract TEXT,
    file_article TEXT,
    category TEXT,
    text_article TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    review_markdown TEXT NOT NULL,
    review_language TEXT DEFAULT 'ru',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, article_id)
);
