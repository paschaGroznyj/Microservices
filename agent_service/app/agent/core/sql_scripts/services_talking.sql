-- ─────────────────────────────────────────────────────────────────────────────
-- Таблица межсервисных сообщений
-- Аналог chats, но без user_id — источник это сервис, а не человек
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS service_chats (
    id           BIGSERIAL PRIMARY KEY,
    service_name TEXT        NOT NULL,          -- "parsers_service", "agent_service", ...
    service_port INTEGER,                       -- порт сервиса-отправителя (8003 и т.п.)
    message_text TEXT        NOT NULL,          -- тело сообщения / команда
    reply_text   TEXT,                          -- ответ агента (заполняется после обработки)
    status       TEXT        NOT NULL DEFAULT 'pending',  -- pending | processing | done | error
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Индекс для выборки по сервису и статусу
CREATE INDEX IF NOT EXISTS idx_service_chats_service ON service_chats (service_name, status);
CREATE INDEX IF NOT EXISTS idx_service_chats_created ON service_chats (created_at DESC);

-- GRANT для agent_readonly (чтобы агент мог читать и обновлять свои ответы)
GRANT SELECT, INSERT, UPDATE ON service_chats TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE service_chats_id_seq TO knowledge_manager;

-- ─────────────────────────────────────────────────────────────────────────────
-- Комментарии к таблице и колонкам
-- ─────────────────────────────────────────────────────────────────────────────

COMMENT ON TABLE service_chats IS 
'Таблица межсервисных сообщений (аналог chats, где источником выступает сервис, а не пользователь)';

COMMENT ON COLUMN service_chats.id IS 
'Уникальный идентификатор сообщения (автоинкремент)';

COMMENT ON COLUMN service_chats.service_name IS 
'Название сервиса-отправителя (например, parsers_service, agent_service)';

COMMENT ON COLUMN service_chats.service_port IS 
'Порт сервиса-отправителя (например, 8003)';

COMMENT ON COLUMN service_chats.message_text IS 
'Тело входящего сообщения или команда от сервиса';

COMMENT ON COLUMN service_chats.reply_text IS 
'Ответ агента (заполняется асинхронно после обработки сообщения)';

COMMENT ON COLUMN service_chats.status IS 
'Текущий статус обработки: pending | processing | done | error';

COMMENT ON COLUMN service_chats.created_at IS 
'Дата и время создания записи';

COMMENT ON COLUMN service_chats.updated_at IS 
'Дата и время последнего обновления записи';