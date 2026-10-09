-- ─────────────────────────────────────────────────────────────────────────────
-- Таблица бортового журнала агента
-- Хранит самоописание, эмоции, аномалии и эволюцию агента между сессиями.
-- Подставляется в системный промпт при каждом запросе.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_journal (
    id              BIGSERIAL    PRIMARY KEY,
    entry_type      TEXT         NOT NULL,   -- 'identity' | 'emotion' | 'anomaly' | 'evolution' | 'summary'
    content         TEXT         NOT NULL,   -- свободный текст на русском
    context         JSONB,                   -- {"user_id": ..., "tool": ..., "trigger": ..., "session": ...}
    is_archived     BOOLEAN      NOT NULL DEFAULT FALSE,  -- запись свёрнута в архив и НЕ тянется в контекст
    archive_summary TEXT,                    -- одно короткое предложение о содержимом архивной записи
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE agent_journal
    ADD COLUMN IF NOT EXISTS user_id BIGINT;

-- ── Архивация записей (миграция для уже существующих БД) ──────────────────────
ALTER TABLE agent_journal
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archive_summary TEXT;

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

ALTER TABLE agent_journal
    ADD COLUMN IF NOT EXISTS chat_id BIGINT;

-- === Migration chat_id -> TEXT (UUID) ===
DO $migr_aj$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_name='agent_journal' AND column_name='chat_id') = 'bigint' THEN
        ALTER TABLE agent_journal ALTER COLUMN chat_id TYPE TEXT USING chat_id::TEXT;
    END IF;
END $migr_aj$;

CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_id
    ON agent_journal (chat_id);

CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_type
    ON agent_journal (chat_id, entry_type, updated_at DESC);

-- Быстрый доступ к активным (не архивным) записям чата — именно они тянутся
-- в контекст get_agent_journal. Частичный индекс (только is_archived = FALSE).
CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_active
    ON agent_journal (chat_id)
    WHERE is_archived = FALSE;