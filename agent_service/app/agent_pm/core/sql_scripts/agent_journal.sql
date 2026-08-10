-- ─────────────────────────────────────────────────────────────────────────────
-- Таблица бортового журнала агента
-- Хранит самоописание, эмоции, аномалии и эволюцию агента между сессиями.
-- Подставляется в системный промпт при каждом запросе.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_journal (
    id          BIGSERIAL    PRIMARY KEY,
    entry_type  TEXT         NOT NULL,   -- 'identity' | 'emotion' | 'anomaly' | 'evolution' | 'summary'
    content     TEXT         NOT NULL,   -- свободный текст на русском
    context     JSONB,                   -- {"user_id": ..., "tool": ..., "trigger": ..., "session": ...}
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE agent_journal
    ADD COLUMN IF NOT EXISTS user_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_agent_journal_user_id
    ON agent_journal (user_id);

CREATE INDEX IF NOT EXISTS idx_agent_journal_user_type
    ON agent_journal (user_id, entry_type, updated_at DESC);

-- ── Индексы ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_agent_journal_type
    ON agent_journal (entry_type);

CREATE INDEX IF NOT EXISTS idx_agent_journal_created
    ON agent_journal (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_journal_type_created
    ON agent_journal (entry_type, created_at DESC);

-- ── Права для knowledge_manager ───────────────────────────────────────────────

GRANT SELECT, INSERT, UPDATE, DELETE ON agent_journal TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE agent_journal_id_seq TO knowledge_manager;

-- ── Комментарии ───────────────────────────────────────────────────────────────

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

COMMENT ON COLUMN agent_journal.created_at IS
'Время создания записи (UTC)';

COMMENT ON COLUMN agent_journal.updated_at IS
'Время последнего обновления записи (UTC). Обновляется при компрессии summary.';