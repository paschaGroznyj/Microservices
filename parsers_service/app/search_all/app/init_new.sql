CREATE TABLE IF NOT EXISTS research_users (
    id BIGSERIAL PRIMARY KEY,
    user_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS search_requests (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES research_users(id) ON DELETE CASCADE,
    user_query TEXT NOT NULL,
    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    date_start DATE,
    date_end DATE,
    keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    limit_value INTEGER NOT NULL DEFAULT 20,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS articles (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'arxiv',
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    date_sub DATE,
    url_article TEXT,
    pdf_url TEXT,
    authors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    abstract TEXT,
    category TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS user_articles (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES research_users(id) ON DELETE CASCADE,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, article_id)
);

CREATE TABLE IF NOT EXISTS request_articles (
    id BIGSERIAL PRIMARY KEY,
    request_id BIGINT NOT NULL REFERENCES search_requests(id) ON DELETE CASCADE,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (request_id, article_id)
);

CREATE INDEX IF NOT EXISTS ix_research_users_user_name
    ON research_users(user_name);

CREATE INDEX IF NOT EXISTS ix_search_requests_user_id
    ON search_requests(user_id);

CREATE INDEX IF NOT EXISTS ix_search_requests_created_at
    ON search_requests(created_at DESC);

CREATE INDEX IF NOT EXISTS ix_articles_external_id
    ON articles(external_id);

CREATE INDEX IF NOT EXISTS ix_articles_date_sub
    ON articles(date_sub DESC);

CREATE INDEX IF NOT EXISTS ix_user_articles_user_id
    ON user_articles(user_id);

CREATE INDEX IF NOT EXISTS ix_user_articles_article_id
    ON user_articles(article_id);

CREATE INDEX IF NOT EXISTS ix_request_articles_request_id
    ON request_articles(request_id);

CREATE INDEX IF NOT EXISTS ix_request_articles_article_id
    ON request_articles(article_id);