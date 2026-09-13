CREATE TABLE IF NOT EXISTS agent_skills (
    id BIGSERIAL PRIMARY KEY,
    theme TEXT NOT NULL,
    body TEXT NOT NULL,
    published_by_user_id BIGINT,
    published_by_name TEXT,
    tools TEXT[] NOT NULL DEFAULT '{}',
    model_used TEXT,
    likes_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_agent_skills_created_at ON agent_skills(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_skills_likes_count ON agent_skills(likes_count DESC);

CREATE TABLE IF NOT EXISTS agent_skill_likes (
    skill_id BIGINT NOT NULL REFERENCES agent_skills(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (skill_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_agent_skill_likes_user_id ON agent_skill_likes(user_id);

-- Миграция для уже существующих инсталляций: колонка модели, которой сгенерирован скилл.
ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS model_used TEXT;

-- Полнотекстовый поиск по теме и телу скилла (russian + simple).
CREATE INDEX IF NOT EXISTS ix_agent_skills_fts
    ON agent_skills
    USING GIN (to_tsvector('russian', coalesce(theme,'') || ' ' || coalesce(body,'')));

-- Soft-delete: удалённые скиллы не показываем при новых GET (как chats.is_deleted).
ALTER TABLE agent_skills ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS ix_agent_skills_not_deleted
    ON agent_skills(created_at DESC)
    WHERE is_deleted = FALSE;


-- Выбранные пользователем скиллы (активный набор до 5)
CREATE TABLE IF NOT EXISTS agent_skill_selected (
    skill_id BIGINT NOT NULL REFERENCES agent_skills(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (skill_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_agent_skill_selected_user_id
    ON agent_skill_selected(user_id, created_at DESC);
