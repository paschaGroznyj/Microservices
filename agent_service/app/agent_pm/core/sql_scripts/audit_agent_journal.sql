-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Создаем таблицу для хранения истории удалений
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agent_journal_audit (
    audit_id        BIGSERIAL PRIMARY KEY,
    deleted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_by      TEXT NOT NULL DEFAULT CURRENT_USER,      -- Кто удалил (роль/юзер)
    original_id     BIGINT NOT NULL,                        -- ID из исходной таблицы
    row_data        JSONB NOT NULL                           -- Вся удаленная строка целиком
);

COMMENT ON TABLE agent_journal_audit IS 
  'Архив удаленных записей из таблицы agent_journal (аудит)';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Функция-триггер, которая срабатывает перед удалением
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION audit_agent_journal_delete()
RETURNS TRIGGER AS $$
BEGIN
    -- to_jsonb(OLD) упакует ВСЕ поля удаляемой строки в JSONB
    INSERT INTO agent_journal_audit (original_id, deleted_by, row_data)
    VALUES (
        OLD.id, 
        CURRENT_USER, 
        to_jsonb(OLD)
    );
    
    RETURN OLD; -- Разрешаем продолжить удаление
END;
$$ LANGUAGE plpgsql;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Привязываем триггер к таблице agent_journal
-- ─────────────────────────────────────────────────────────────────────────────
DROP TRIGGER IF EXISTS trg_audit_agent_journal_delete ON agent_journal;

CREATE TRIGGER trg_audit_agent_journal_delete
BEFORE DELETE ON agent_journal
FOR EACH ROW
EXECUTE FUNCTION audit_agent_journal_delete();