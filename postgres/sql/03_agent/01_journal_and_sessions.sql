-- =============================================================================
-- 03_agent/01_journal_and_sessions.sql
-- Бортовой журнал агента (+ аудит удалений), саммари сессий, rolling summary,
-- лог токенов и межсервисные сообщения
-- =============================================================================

-- 1. Бортовой журнал агента
CREATE TABLE IF NOT EXISTS agent_journal (
    id              BIGSERIAL PRIMARY KEY,
    entry_type      TEXT NOT NULL,             -- 'identity' | 'user_preference' | 'anomaly' | 'evolution' | 'summary'
    content         TEXT NOT NULL,
    context         JSONB,
    user_id         BIGINT,
    chat_id         TEXT,
    is_archived     BOOLEAN NOT NULL DEFAULT FALSE,
    archive_summary TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_journal_user_id
    ON agent_journal (user_id);
CREATE INDEX IF NOT EXISTS idx_agent_journal_user_type
    ON agent_journal (user_id, entry_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_id
    ON agent_journal (chat_id);
CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_type
    ON agent_journal (chat_id, entry_type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_active
    ON agent_journal (chat_id) WHERE (is_archived = FALSE);
CREATE INDEX IF NOT EXISTS idx_agent_journal_type
    ON agent_journal (entry_type);
CREATE INDEX IF NOT EXISTS idx_agent_journal_created
    ON agent_journal (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_journal_type_created
    ON agent_journal (entry_type, created_at DESC);

COMMENT ON TABLE agent_journal IS
'Бортовой журнал агента. Хранит самоописание личности, эмоциональные состояния,
замеченные аномалии в поведении пользователей, точки эволюции и сжатые сводки.
Последние записи подставляются в системный промпт при каждой новой сессии.';

COMMENT ON COLUMN agent_journal.id IS
'Уникальный идентификатор записи (автоинкремент)';

COMMENT ON COLUMN agent_journal.entry_type IS
'Тип записи:
  identity        — самоописание личности агента, его ценностей и стиля работы;
  user_preference — предпочтения пользователей;
  anomaly         — аномалия в поведении пользователя или инструмента (повторяющиеся ошибки, странные паттерны);
  evolution       — точка роста: что агент узнал нового, как изменилось его понимание задач;
  summary         — сжатая сводка группы записей (результат компрессии при превышении лимита)';

COMMENT ON COLUMN agent_journal.content IS
'Свободный текст на русском. Агент пишет от первого лица, без ограничений на формат.';

COMMENT ON COLUMN agent_journal.context IS
'Контекст записи в формате JSONB. Рекомендуемые поля:
  user_id   — ID пользователя если запись связана с конкретным человеком;
  tool      — название инструмента если запись об аномалии в тулзе;
  trigger   — что вызвало запись (например "compression_threshold" или "user_request");
  session   — идентификатор сессии (chat_id или request_id)';

COMMENT ON COLUMN agent_journal.is_archived IS
'Флаг архивации. TRUE — запись свёрнута в архив: не тянется в основной контекст
(экономит токены), но остаётся видимой в компактном реестре архива (id + archive_summary).
Архивация/разархивация выполняется моделью по id через тул work_with_agent_journal.';

COMMENT ON COLUMN agent_journal.archive_summary IS
'Одно короткое предложение о содержимом архивной записи. Показывается в реестре
архива вместо полного текста, чтобы модель могла разархивировать нужную запись по id.';

COMMENT ON COLUMN agent_journal.created_at IS
'Время создания записи (UTC)';

COMMENT ON COLUMN agent_journal.updated_at IS
'Время последнего обновления записи (UTC). Обновляется при компрессии summary.';

-- 2. Аудит удалений из agent_journal убран: таблица agent_journal_audit
-- больше не создаётся. На существующих базах снимаем триггер и функцию,
-- чтобы DELETE из agent_journal не зависел от таблицы аудита.
-- Сама таблица agent_journal_audit с данными не удаляется.
DROP TRIGGER IF EXISTS trg_audit_agent_journal_delete ON agent_journal;
DROP FUNCTION IF EXISTS audit_agent_journal_delete();

-- 3. Саммари сессий и скользящее саммари чата
CREATE TABLE IF NOT EXISTS session_summaries (
    id           BIGSERIAL PRIMARY KEY,
    session_id   INTEGER NOT NULL UNIQUE,
    chat_id      TEXT NOT NULL,
    start_msg_id INTEGER,
    end_msg_id   INTEGER,
    summary_text TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_chat_id
    ON session_summaries (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_summaries_session_id
    ON session_summaries (session_id);

COMMENT ON TABLE session_summaries IS
'Саммари сессий агента по чатам. Одна запись = одна сессия (start → end).
Подставляется в messages при каждом новом запросе пользователя.';

COMMENT ON COLUMN session_summaries.session_id IS
'Случайное 6-значное число (100000–999999), уникальный идентификатор сессии.';
COMMENT ON COLUMN session_summaries.start_msg_id IS
'id записи в таблице chats на момент старта сессии.';
COMMENT ON COLUMN session_summaries.end_msg_id IS
'id последней записи в таблице chats на момент завершения сессии.';
COMMENT ON COLUMN session_summaries.summary_text IS
'LLM-сводка диалога за сессию. Заполняется при вызове session_point(end).';

CREATE TABLE IF NOT EXISTS chat_rolling_summary (
    chat_id          TEXT PRIMARY KEY,
    summary_text     TEXT NOT NULL,
    covers_messages  INTEGER NOT NULL DEFAULT 0,
    covered_up_to_id BIGINT NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
