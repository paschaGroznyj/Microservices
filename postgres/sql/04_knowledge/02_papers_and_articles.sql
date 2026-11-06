-- =============================================================================
-- 04_knowledge/02_papers_and_articles.sql
-- Таблицы научных статей, поисковых запросов, метаданных статей и ревью
-- =============================================================================

-- 1. Базовый каталог статей и поисковых запросов
CREATE TABLE IF NOT EXISTS articles (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL DEFAULT 'arxiv',
    external_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    date_sub     DATE,
    url_article  TEXT,
    pdf_url      TEXT,
    authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags_json    JSONB NOT NULL DEFAULT '[]'::jsonb,
    abstract     TEXT,
    translate    TEXT,
    category     TEXT,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS ix_articles_external_id ON articles(external_id);
CREATE INDEX IF NOT EXISTS ix_articles_date_sub ON articles(date_sub DESC);

CREATE TABLE IF NOT EXISTS search_requests (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_query    TEXT NOT NULL,
    params_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    date_start    DATE,
    date_end      DATE,
    keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    limit_value   INTEGER NOT NULL DEFAULT 20,
    error_message TEXT,
    status        TEXT DEFAULT 'pending',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_search_requests_user_id ON search_requests(user_id);
CREATE INDEX IF NOT EXISTS ix_search_requests_created_at ON search_requests(created_at DESC);

CREATE TABLE IF NOT EXISTS request_articles (
    id           BIGSERIAL PRIMARY KEY,
    request_id   BIGINT NOT NULL REFERENCES search_requests(id) ON DELETE CASCADE,
    article_id   BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    is_relevant  BOOLEAN,
    processed_at TIMESTAMP DEFAULT NOW(),
    created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (request_id, article_id)
);

CREATE INDEX IF NOT EXISTS ix_request_articles_request_id ON request_articles(request_id);
CREATE INDEX IF NOT EXISTS ix_request_articles_article_id ON request_articles(article_id);
CREATE INDEX IF NOT EXISTS ix_request_articles_is_relevant ON request_articles(is_relevant);

CREATE TABLE IF NOT EXISTS favorites (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, article_id)
);

CREATE INDEX IF NOT EXISTS idx_favorites_user_id ON favorites(user_id);

-- 2. Эмбеддинги и переводы статей
CREATE TABLE IF NOT EXISTS article_embeddings (
    id              BIGSERIAL PRIMARY KEY,
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    embedding       vector(1024) NOT NULL,
    embedding_model TEXT NOT NULL,
    source_text     TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (article_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS ix_article_embeddings_article_id
    ON article_embeddings(article_id);
CREATE INDEX IF NOT EXISTS ix_article_embeddings_model
    ON article_embeddings(embedding_model);
CREATE INDEX IF NOT EXISTS ix_article_embeddings_embedding_cosine
    ON article_embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS article_translations (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id      BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    request_id      BIGINT NOT NULL REFERENCES search_requests(id) ON DELETE CASCADE,
    translated_text TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, article_id, request_id)
);

CREATE INDEX IF NOT EXISTS ix_article_translations_user_id ON article_translations(user_id);
CREATE INDEX IF NOT EXISTS ix_article_translations_article_id ON article_translations(article_id);
CREATE INDEX IF NOT EXISTS ix_article_translations_request_id ON article_translations(request_id);
