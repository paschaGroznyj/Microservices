-- editor_workspaces: реестр воркспейсов песочницы, закреплённых за пользователем.
-- Пишет тул агента (INSERT при отдаче файла), читает редактор (дерево проекта).
-- Физические файлы живут в песочнице (/tmp/exec/workspaces/<workspace_id>), здесь только реестр.

CREATE TABLE IF NOT EXISTS editor_workspaces (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    workspace_id  TEXT   NOT NULL,          -- id воркспейса песочницы
    chat_id       TEXT,                     -- чат, из которого открыт редактор (для AI-панели)
    title         TEXT,                     -- человекочитаемое имя области
    ttl_hours     INTEGER DEFAULT 10,       -- время жизни в часах (NULL при бессрочном хранении)
    is_permanent  BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE если создан .ws_hold (бессрочное хранение)
    expires_at    TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '10 hours'),              -- время истечения TTL (NULL если is_permanent = TRUE)
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, workspace_id)
);

ALTER TABLE editor_workspaces ADD COLUMN IF NOT EXISTS ttl_hours INTEGER DEFAULT 10;
ALTER TABLE editor_workspaces ADD COLUMN IF NOT EXISTS is_permanent BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE editor_workspaces ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP + INTERVAL '10 hours');

CREATE INDEX IF NOT EXISTS ix_editor_ws_user   ON editor_workspaces(user_id, is_active);
CREATE INDEX IF NOT EXISTS ix_editor_ws_wsid    ON editor_workspaces(workspace_id);
CREATE INDEX IF NOT EXISTS ix_editor_ws_chat    ON editor_workspaces(chat_id);
