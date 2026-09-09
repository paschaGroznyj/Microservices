-- migration: add archive flag + short summary to agent_journal
ALTER TABLE agent_journal
  ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS archive_summary TEXT;

-- быстрый доступ к неархивным записям чата
CREATE INDEX IF NOT EXISTS idx_agent_journal_chat_active
  ON agent_journal (chat_id)
  WHERE is_archived = FALSE;
