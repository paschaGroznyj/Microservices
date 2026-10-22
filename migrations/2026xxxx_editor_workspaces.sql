-- editor_workspaces: реестр воркспейсов песочницы, закреплённых за пользователем.
-- Пишет тул агента (INSERT при отдаче файла), читает редактор (дерево проекта).
-- Физические файлы живут в песочнице (/tmp/exec/workspaces/<workspace_id>), здесь только реестр.

CREATE TABLE IF NOT EXISTS editor_workspaces (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    workspace_id  TEXT   NOT NULL,          -- id воркспейса песочницы
    chat_id       TEXT,                     -- чат, из которого открыт редактор (для AI-панели)
    title         TEXT,                     -- человекочитаемое имя области
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, workspace_id)
);

CREATE INDEX IF NOT EXISTS ix_editor_ws_user   ON editor_workspaces(user_id, is_active);
CREATE INDEX IF NOT EXISTS ix_editor_ws_wsid    ON editor_workspaces(workspace_id);
CREATE INDEX IF NOT EXISTS ix_editor_ws_chat    ON editor_workspaces(chat_id);
