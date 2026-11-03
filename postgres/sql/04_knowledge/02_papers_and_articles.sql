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

CREATE TABLE IF NOT EXISTS user_articles (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, article_id)
);

CREATE INDEX IF NOT EXISTS ix_user_articles_user_id ON user_articles(user_id);
CREATE INDEX IF NOT EXISTS ix_user_articles_article_id ON user_articles(article_id);

CREATE TABLE IF NOT EXISTS article_reviews (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id   BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    review_text  TEXT NOT NULL,
    generated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

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

-- 3. Метаданные научных статей для агента (papers_metadata + reviews)
CREATE TABLE IF NOT EXISTS papers_metadata (
    external_id         TEXT PRIMARY KEY,
    source              TEXT,
    title               TEXT,
    date_sub            DATE,
    url_article         TEXT,
    pdf_url             TEXT,
    authors             JSONB,
    tags                TEXT[],
    abstract            TEXT,
    file_article        TEXT,
    category            TEXT,
    relevance_score     DOUBLE PRECISION,
    relevance_reasoning TEXT,
    is_relevant         BOOLEAN,
    pdf_downloaded      BOOLEAN,
    processed_at        TIMESTAMP DEFAULT NOW(),
    review              BOOLEAN,
    text_review         TEXT,
    review_flag         BOOLEAN NOT NULL DEFAULT FALSE,
    id                  BIGSERIAL
);

CREATE TABLE IF NOT EXISTS reviews (
    id          BIGSERIAL PRIMARY KEY,
    external_id TEXT NOT NULL UNIQUE REFERENCES papers_metadata(external_id) ON DELETE CASCADE,
    review_text TEXT NOT NULL,
    category    TEXT,
    source_file TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE papers_metadata IS 'Таблица для хранения метаданных, аннотаций и аналитики научных статей.';
COMMENT ON COLUMN papers_metadata.external_id IS 'PRIMARY KEY. Уникальный хэш или ID статьи (например: "3fa4ef01ef8c" или "arxiv_2510.01234").';
COMMENT ON COLUMN papers_metadata.source IS 'Источник статьи (например: "yandex_disk", "arxiv", "web_parser").';
COMMENT ON COLUMN papers_metadata.title IS 'Полное название статьи.';
COMMENT ON COLUMN papers_metadata.date_sub IS 'Дата публикации в формате YYYY-MM-DD (если нет — NULL).';
COMMENT ON COLUMN papers_metadata.url_article IS 'Прямая URL-ссылка на страницу статьи.';
COMMENT ON COLUMN papers_metadata.pdf_url IS 'Ссылка на PDF-файл (если есть).';
COMMENT ON COLUMN papers_metadata.authors IS 'JSONB. Авторы в формате JSON-массива строк! ПРИМЕР: ["Iris Beerepoot", "Xixi Lu"]. Не заэкранированная строка!';
COMMENT ON COLUMN papers_metadata.tags IS 'TEXT[]. Массив тегов Postgres! ПРИМЕР: ARRAY[''Process Mining'', ''LLM'']. НЕ JSON!';
COMMENT ON COLUMN papers_metadata.abstract IS 'Краткое содержание (аннотация) статьи на языке оригинала или русском.';
COMMENT ON COLUMN papers_metadata.category IS 'Категория статьи (например: "Process Mining", "AI Agents", " Healthcare").';
COMMENT ON COLUMN papers_metadata.file_article IS 'НЕ ЗАПОЛНЯТЬ! Относительный путь к файлу, заполняется сервисом загрузки.';
COMMENT ON COLUMN papers_metadata.relevance_score IS 'FLOAT. Оценка полезности для проекта от 0.0 до 1.0.';
COMMENT ON COLUMN papers_metadata.relevance_reasoning IS 'Краткое обоснование, почему статья релевантна или нерелевантна.';
COMMENT ON COLUMN papers_metadata.is_relevant IS 'BOOLEAN. true если relevance_score >= 0.7, иначе false.';
COMMENT ON COLUMN papers_metadata.text_review IS 'Сгенерированный агентский разбор/рецензия статьи.';
COMMENT ON COLUMN papers_metadata.review_flag IS 'НЕ ЗАПОЛНЯТЬ! По умолчанию FALSE. Флаг отправки ревью пользователю.';
COMMENT ON COLUMN papers_metadata.processed_at IS 'НЕ ЗАПОЛНЯТЬ! Автоматически подставляется NOW().';
