-- =============================================================================
-- 03_agent/02_skills_and_code_library.sql
-- Библиотека скиллов агента (agent_skills) и библиотека кода (code_library)
-- =============================================================================

-- 1. Библиотека скиллов
CREATE TABLE IF NOT EXISTS agent_skills (
    id                   BIGSERIAL PRIMARY KEY,
    theme                TEXT NOT NULL,
    body                 TEXT NOT NULL,
    published_by_user_id BIGINT,
    published_by_name    TEXT,
    tools                TEXT[] NOT NULL DEFAULT '{}',
    model_used           TEXT,
    likes_count          INTEGER NOT NULL DEFAULT 0,
    uses_count           INTEGER NOT NULL DEFAULT 0,
    is_deleted           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_agent_skills_created_at ON agent_skills(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_skills_likes_count ON agent_skills(likes_count DESC);
CREATE INDEX IF NOT EXISTS ix_agent_skills_uses_count ON agent_skills(uses_count DESC);
CREATE INDEX IF NOT EXISTS ix_agent_skills_not_deleted
    ON agent_skills(created_at DESC)
    WHERE is_deleted = FALSE;
CREATE INDEX IF NOT EXISTS ix_agent_skills_fts
    ON agent_skills
    USING GIN (to_tsvector('russian', coalesce(theme,'') || ' ' || coalesce(body,'')));

CREATE TABLE IF NOT EXISTS agent_skill_likes (
    skill_id   BIGINT NOT NULL REFERENCES agent_skills(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (skill_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_agent_skill_likes_user_id ON agent_skill_likes(user_id);

CREATE TABLE IF NOT EXISTS agent_skill_selected (
    skill_id   BIGINT NOT NULL REFERENCES agent_skills(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (skill_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_agent_skill_selected_user_id
    ON agent_skill_selected(user_id, created_at DESC);

-- 2. Библиотека кода (code_library)
CREATE TABLE IF NOT EXISTS code_library (
    id           SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT,
    code_type    TEXT NOT NULL,
    path         TEXT,
    run_id       TEXT,
    tags         TEXT[] DEFAULT '{}',
    author       TEXT NOT NULL DEFAULT 'agent',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    use_count    INT NOT NULL DEFAULT 0,
    tsv          TSVECTOR
);

CREATE INDEX IF NOT EXISTS idx_cl_type ON code_library (code_type);
CREATE INDEX IF NOT EXISTS idx_cl_author ON code_library (author);
CREATE INDEX IF NOT EXISTS idx_cl_tags ON code_library USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_cl_use ON code_library (use_count DESC);
CREATE INDEX IF NOT EXISTS idx_cl_created ON code_library (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_code_library_tsv ON code_library USING GIN (tsv);

CREATE OR REPLACE FUNCTION code_library_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.tsv := to_tsvector('russian',
        coalesce(NEW.title,'') || ' ' ||
        coalesce(NEW.description,'') || ' ' ||
        coalesce(array_to_string(NEW.tags,' '),'')
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS tsvupdate ON code_library;
CREATE TRIGGER tsvupdate
    BEFORE INSERT OR UPDATE ON code_library
    FOR EACH ROW
    EXECUTE FUNCTION code_library_tsv_update();

COMMENT ON TABLE code_library IS
'Библиотека кода проекта и агента PMыч. Хранит скрипты двух типов: написанные агентом в песочнице (author=agent) и исходный код проекта (author=human). Используется для переиспользования скриптов: агент ищет по title/description/tags и читает файл по path с диска.';
COMMENT ON COLUMN code_library.id IS 'Уникальный ID записи';
COMMENT ON COLUMN code_library.title IS 'Краткое название файла/скрипта (напр. "Парсер arXiv с фильтрацией по дате")';
COMMENT ON COLUMN code_library.description IS 'Подробное описание: что делает код, какие функции/классы содержит, какие аргументы принимает, что возвращает';
COMMENT ON COLUMN code_library.code_type IS 'Тип кода: script | parser | chart | report | tool | service | util | migration';
COMMENT ON COLUMN code_library.path IS 'Абсолютный путь к файлу на диске (/sandbox/reusable/... или /app/...). Сам код в БД НЕ хранится — читается через read_file(path)';
COMMENT ON COLUMN code_library.run_id IS 'UUID запуска из execute_python_code (для связи с /sandbox/executed_scripts/{run_id}.py). NULL для ручных файлов проекта';
COMMENT ON COLUMN code_library.tags IS 'Массив тегов для фильтрации (напр. {matplotlib,pdf,arxiv,sql})';
COMMENT ON COLUMN code_library.author IS 'Кто создал: agent — сгенерировано агентом в песочнице; human — исходный код проекта';
COMMENT ON COLUMN code_library.created_at IS 'Дата и время добавления в библиотеку';
COMMENT ON COLUMN code_library.last_used_at IS 'Когда скрипт последний раз переиспользовался агентом';
COMMENT ON COLUMN code_library.use_count IS 'Счётчик переиспользований (чем выше — тем полезнее скрипт)';
