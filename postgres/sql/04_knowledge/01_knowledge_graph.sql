-- =============================================================================
-- 04_knowledge/01_knowledge_graph.sql
-- Таблицы базы знаний, векторов, ревью знаний, лога аудита, графа связей и wiki
-- =============================================================================

-- 1. Основная таблица знаний
CREATE TABLE IF NOT EXISTS knowledge (
    id                 SERIAL PRIMARY KEY,
    text_knowledge     TEXT NOT NULL,
    metadata_knowledge JSONB DEFAULT '{}'::jsonb,
    access_count       INTEGER DEFAULT 0,
    importance         REAL DEFAULT 0.5,
    last_accessed_at   TIMESTAMPTZ,
    status             VARCHAR(20) DEFAULT 'active',
    provenance         VARCHAR(30) DEFAULT 'agent-inference',
    created_at         TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS knowledge_trgm_idx
    ON knowledge USING GIN (text_knowledge gin_trgm_ops);
CREATE INDEX IF NOT EXISTS knowledge_importance_idx
    ON knowledge (importance DESC);
CREATE INDEX IF NOT EXISTS knowledge_status_idx
    ON knowledge (status);
CREATE INDEX IF NOT EXISTS knowledge_provenance_idx
    ON knowledge (provenance);
CREATE INDEX IF NOT EXISTS idx_knowledge_metadata
    ON knowledge USING GIN (metadata_knowledge);

DROP TRIGGER IF EXISTS update_knowledge_updated_at ON knowledge;
CREATE TRIGGER update_knowledge_updated_at
    BEFORE UPDATE ON knowledge
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 2. Векторная таблица знаний (размерность 1024)
CREATE TABLE IF NOT EXISTS knowledge_vectors (
    id           SERIAL PRIMARY KEY,
    knowledge_id INTEGER UNIQUE NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    embedding    vector(1024) NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_knowledge_vectors_embedding_hnsw
    ON knowledge_vectors USING hnsw (embedding vector_cosine_ops);

-- 3. Инкубатор удаления знаний (knowledge_review)
CREATE TABLE IF NOT EXISTS knowledge_review (
    id           SERIAL PRIMARY KEY,
    knowledge_id INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    reason       TEXT NOT NULL,
    status       VARCHAR(20) DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
    suggested_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_knowledge_review_knowledge_id
    ON knowledge_review(knowledge_id);

-- 4. Аудит изменений знаний (knowledge_log)
CREATE TABLE IF NOT EXISTS knowledge_log (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(30) NOT NULL,
    target_type VARCHAR(30) NOT NULL DEFAULT 'knowledge',
    target_id   INTEGER,
    details     JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS knowledge_log_target_idx ON knowledge_log (target_type, target_id);
CREATE INDEX IF NOT EXISTS knowledge_log_action_idx ON knowledge_log (action);
CREATE INDEX IF NOT EXISTS knowledge_log_time_idx ON knowledge_log (created_at DESC);

CREATE OR REPLACE FUNCTION log_knowledge_change()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') THEN
        INSERT INTO knowledge_log (action, target_type, target_id, details)
        VALUES ('insert', 'knowledge', NEW.id,
            jsonb_build_object('text', LEFT(NEW.text_knowledge, 200)));
        RETURN NEW;
    ELSIF (TG_OP = 'UPDATE') THEN
        INSERT INTO knowledge_log (action, target_type, target_id, details)
        VALUES ('update', 'knowledge', NEW.id,
            jsonb_build_object(
                'old_text', LEFT(OLD.text_knowledge, 200),
                'new_text', LEFT(NEW.text_knowledge, 200),
                'old_importance', OLD.importance,
                'new_importance', NEW.importance
            ));
        RETURN NEW;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_knowledge_audit ON knowledge;
CREATE TRIGGER trg_knowledge_audit
    AFTER INSERT OR UPDATE ON knowledge
    FOR EACH ROW EXECUTE FUNCTION log_knowledge_change();

-- 5. Граф связей сущностей (entity_relations)
CREATE TABLE IF NOT EXISTS entity_relations (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    target_id       INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL,
    relevance_score REAL DEFAULT 0.5,
    importance      REAL DEFAULT 0.5,
    provenance      VARCHAR(30) DEFAULT 'agent-inference',
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS entity_relations_source_idx ON entity_relations (source_id);
CREATE INDEX IF NOT EXISTS entity_relations_target_idx ON entity_relations (target_id);
CREATE INDEX IF NOT EXISTS entity_relations_type_idx ON entity_relations (relation_type);

-- 6. Слой синтеза знаний (wiki_pages)
CREATE TABLE IF NOT EXISTS wiki_pages (
    id           SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    content_md   TEXT NOT NULL,
    source_ids   JSONB DEFAULT '[]'::jsonb,
    links        JSONB DEFAULT '[]'::jsonb,
    index_entry  TEXT,
    status       VARCHAR(20) DEFAULT 'draft',
    access_count INTEGER DEFAULT 0,
    importance   REAL DEFAULT 0.5,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS wiki_pages_status_idx ON wiki_pages (status);
CREATE INDEX IF NOT EXISTS wiki_pages_source_gin_idx ON wiki_pages USING GIN (source_ids);
CREATE INDEX IF NOT EXISTS wiki_pages_trgm_idx ON wiki_pages USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS wiki_pages_importance_idx ON wiki_pages (importance DESC);

DROP TRIGGER IF EXISTS update_wiki_updated_at ON wiki_pages;
CREATE TRIGGER update_wiki_updated_at
    BEFORE UPDATE ON wiki_pages
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 7. Комментарии для LLM-схемы
COMMENT ON TABLE knowledge IS 'Основное хранилище текстовых знаний, заметок, регламентов и фактов, зафиксированных агентом или пользователем.';
COMMENT ON COLUMN knowledge.id IS 'Уникальный идентификатор записи знаний.';
COMMENT ON COLUMN knowledge.text_knowledge IS 'Смысловой текст знания (инструкция, факт, регламент). Служит источником для генерации эмбеддингов.';
COMMENT ON COLUMN knowledge.metadata_knowledge IS 'Метаданные знания и атрибуты текста в JSONB. Включает контекст источника и смысловой разбор текста.
Пример структуры:
{
"source": "telegram_chat",
"author": "Pavel",
"version": 1,
"topics": ["python", "agents", "infrastructure"],
"urls": ["https://docs.example.com/ref"],
"entities": ["Base knownledge", "PostgreSQL"],
"language": "ru"
}';
COMMENT ON COLUMN knowledge.access_count IS 'Счётчик обращений к сущности. Считать access_count = access_count + 1';
COMMENT ON COLUMN knowledge.importance IS 'Числовой вес важности (0-1). По дефолту 0.5';
COMMENT ON COLUMN knowledge.last_accessed_at IS 'Последнее время обращения. last_accessed_at = NOW()';
COMMENT ON COLUMN knowledge.status IS
  'Жизненный цикл знания.
   Значения:
   active — активно, выдаётся в поиске (дефолт),
   deprecated — заменено более актуальным знанием,
   archived — временно выведен из обращения,
   pending_review — в инкубаторе knowledge_review.';
COMMENT ON COLUMN knowledge.provenance IS
  'Тип происхождения знания.
   Значения:
   user-stated — пользователь явно сообщил,
   agent-inference — агент вывел сам (дефолт),
   document — из загруженного документа/статьи,
   third-party — из внешнего источника (API, интеграция).';
COMMENT ON COLUMN knowledge.created_at IS 'Дата и время создания записи.';
COMMENT ON COLUMN knowledge.updated_at IS 'Дата и время последнего обновления записи (обновляется автоматически триггером).';

COMMENT ON TABLE knowledge_review IS
'Инкубатор кандидатов на удаление. Агент предлагает устаревшие или дублирующиеся knowledge.id с обоснованием перед фактическим DELETE.';
COMMENT ON COLUMN knowledge_review.knowledge_id IS 'Ссылка на кандидата на удаление из таблицы knowledge.';
COMMENT ON COLUMN knowledge_review.reason IS 'Подробное обоснование агента, почему данное знание нужно удалить или актуализировать.';
COMMENT ON COLUMN knowledge_review.status IS 'Статус модерации человеком или правилом: pending (ожидает), approved (одобрено), rejected (отклонено).';

COMMENT ON TABLE entity_relations IS 'Направленный граф связей между записями knowledge. Связь идёт от source_id к target_id. Любая запись knowledge может иметь связи любого типа в обе стороны.';
COMMENT ON COLUMN entity_relations.id IS 'Уникальный идентификатор связи.';
COMMENT ON COLUMN entity_relations.source_id IS 'ID записи knowledge — источник связи (субъект триплета).';
COMMENT ON COLUMN entity_relations.target_id IS 'ID записи knowledge — цель связи (объект триплета).';
COMMENT ON COLUMN entity_relations.relation_type IS 'Тип отношения. Примеры: uses, includes, stores, evaluates, summarizes, alternative_to, based_on, relates_to, extends, contradicts. Открытый словарь — новые типы добавляются по мере необходимости.';
COMMENT ON COLUMN entity_relations.relevance_score IS 'Сила связи (0-1). Насколько target_id релевантен для source_id. Семантическая близость, вычисляется при создании связи. Дефолт 0.5.';
COMMENT ON COLUMN entity_relations.importance IS 'Вес важности самой связи (0-1). Насколько эта связь критична для понимания темы. Дефолт 0.5. Может отличаться от importance сущностей.';
COMMENT ON COLUMN entity_relations.provenance IS 'Тип происхождения связи. Значения: user-stated (пользователь явно указал), agent-inference (агент вывел сам, дефолт), document (извлечено из документа), third-party (внешний источник).';
COMMENT ON COLUMN entity_relations.created_at IS 'Дата и время создания связи. Не обновляется (immutable).';

COMMENT ON TABLE wiki_pages IS 'Слой синтеза поверх атомарных знаний. Объединяет связанные записи knowledge в единую тематическую страницу. Один wiki_page = одна тема. Аналог концепции LLM Wiki (Карпати).';
COMMENT ON COLUMN wiki_pages.id IS 'Уникальный идентификатор wiki-страницы.';
COMMENT ON COLUMN wiki_pages.title IS 'Заголовок страницы. Краткое название темы.';
COMMENT ON COLUMN wiki_pages.content_md IS 'Содержимое страницы в Markdown. Синтез из source_ids, сгенерированный агентом или написанный вручную.';
COMMENT ON COLUMN wiki_pages.source_ids IS 'Массив ID записей knowledge, на которых основана страница. Пример: [42, 43, 44, 45]. При изменении источника — страница помечается для обновления.';
COMMENT ON COLUMN wiki_pages.links IS 'Массив ключевых слов и тегов для навигации. Пример: ["knowledge_graph","rag","neo4j","gigachat"]. Используется для поиска связанных wiki-страниц.';
COMMENT ON COLUMN wiki_pages.index_entry IS 'Короткая строка для полнотекстового индекса. Конкатенация ключевых слов через пробел. Ускоряет нечёткий поиск по названиям через pg_trgm.';
COMMENT ON COLUMN wiki_pages.status IS 'Жизненный цикл страницы. Значения: draft (черновик, дефолт), published (опубликована), needs_update (источник изменился, требуется перегенерация), deprecated (заменена другой страницей), archived (выведена из обращения).';
COMMENT ON COLUMN wiki_pages.access_count IS 'Счётчик обращений к странице. Инкрементируется при выдаче в ответе.';
COMMENT ON COLUMN wiki_pages.importance IS 'Вес важности страницы (0-1). Вычисляется как максимум из importance источников. Дефолт 0.5.';
COMMENT ON COLUMN wiki_pages.created_at IS 'Дата и время создания страницы.';
COMMENT ON COLUMN wiki_pages.updated_at IS 'Дата и время последнего обновления (обновляется автоматически триггером set_updated_at).';
