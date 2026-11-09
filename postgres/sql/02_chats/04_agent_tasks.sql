-- Фоновые задачи песочницы (submit_task). Пишет chats-consumer по Kafka task-events.
-- Карточка задачи в чате: tasks подтягиваются к сообщению агента по message_id
-- (ответ, в котором агент поставил задачу), фронт рисует их после agent-message.
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id       TEXT PRIMARY KEY,
    chat_id       TEXT NOT NULL,
    user_id       BIGINT,                         -- владелец (кто ставил)
    request_id    TEXT,                           -- запрос агента, где задача поставлена
    message_id    TEXT,                           -- ответ агента этого запроса (якорь карточки)
    workspace_id  TEXT,
    label         TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'script', -- script | media (стиль карточки)
    status        TEXT NOT NULL DEFAULT 'pending',-- pending|running|completed|failed|cancelled
    stages        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{title,status,started_at,finished_at}]
    progress      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {percent,text}
    elapsed_ms    BIGINT NOT NULL DEFAULT 0,
    exit_code     INT,
    error         TEXT,
    stderr_tail   TEXT,
    stdout_tail   TEXT,
    files         JSONB NOT NULL DEFAULT '[]'::jsonb,
    notify_agent  BOOLEAN NOT NULL DEFAULT TRUE,
    seq           INT NOT NULL DEFAULT 0,         -- последнее применённое событие
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    triggered_at  TIMESTAMPTZ,                    -- когда разбудили агента (ставится после отправки)
    owner         JSONB NOT NULL DEFAULT '{}'::jsonb, -- gateway_target, модель, тулы: побудка после рестарта
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_tasks_chat_idx    ON agent_tasks (chat_id, created_at);
CREATE INDEX IF NOT EXISTS agent_tasks_message_idx ON agent_tasks (chat_id, message_id);
CREATE INDEX IF NOT EXISTS agent_tasks_request_idx ON agent_tasks (request_id);
CREATE INDEX IF NOT EXISTS agent_tasks_untriggered_idx ON agent_tasks (chat_id, finished_at)
    WHERE triggered_at IS NULL AND notify_agent;
-- Существующая база: ALTER TABLE agent_tasks ADD COLUMN IF NOT EXISTS owner JSONB NOT NULL DEFAULT '{}'::jsonb;
-- (chats-consumer делает это сам при старте, TasksDB.ensure_schema).
