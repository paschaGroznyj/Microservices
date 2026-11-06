-- =============================================================================
-- 05_grants/01_roles_and_permissions.sql
-- Роли агента (knowledge_manager, my_journal) и раздача прав на таблицы/сиквенсы
-- =============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'knowledge_manager') THEN
        CREATE ROLE knowledge_manager WITH LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'my_journal') THEN
        CREATE ROLE my_journal WITH LOGIN;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO knowledge_manager, my_journal;

-- Базовое право чтения на все таблицы public для knowledge_manager
GRANT SELECT ON ALL TABLES IN SCHEMA public TO knowledge_manager;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO knowledge_manager;

-- Целевые права на запись/обновление для knowledge_manager
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_journal TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE agent_journal_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON knowledge TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE knowledge_id_seq TO knowledge_manager;

GRANT SELECT, INSERT ON knowledge_review TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE knowledge_review_id_seq TO knowledge_manager;

GRANT SELECT ON knowledge_log TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON entity_relations TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE entity_relations_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON wiki_pages TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE wiki_pages_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON papers_metadata TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE papers_metadata_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON reviews TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE reviews_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON code_library TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE code_library_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON session_summaries TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE session_summaries_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON service_chats TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE service_chats_id_seq TO knowledge_manager;

-- Права для роли my_journal (работа с бортовым журналом и базовыми таблицами)
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_journal TO my_journal;
GRANT USAGE, SELECT ON SEQUENCE agent_journal_id_seq TO my_journal;
GRANT SELECT ON knowledge, wiki_pages, entity_relations TO my_journal;
