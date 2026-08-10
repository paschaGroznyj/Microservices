CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- 1. Основная таблица знаний
CREATE TABLE IF NOT EXISTS knowledge (
    id SERIAL PRIMARY KEY,
    text_knowledge TEXT NOT NULL,
    metadata_knowledge JSONB DEFAULT '{}'::jsonb,
    access_count      INTEGER DEFAULT 0,
    importance        REAL DEFAULT 0.5,
    last_accessed_at  TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'active',
    provenance VARCHAR(30) DEFAULT 'agent-inference',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE knowledge 
ADD COLUMN IF NOT EXISTS access_count 
  INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS importance 
  REAL DEFAULT 0.5,
ADD COLUMN IF NOT EXISTS last_accessed_at 
  TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active',
ADD COLUMN IF NOT EXISTS provenance VARCHAR(30) DEFAULT 'agent-inference';

CREATE INDEX IF NOT EXISTS knowledge_trgm_idx
  ON knowledge USING GIN (text_knowledge gin_trgm_ops);

CREATE INDEX IF NOT EXISTS knowledge_importance_idx
  ON knowledge (importance DESC);

CREATE INDEX IF NOT EXISTS knowledge_status_idx
  ON knowledge (status);

CREATE INDEX IF NOT EXISTS knowledge_provenance_idx
  ON knowledge (provenance);


-- Индекс для быстрого поиска по JSONB
CREATE INDEX IF NOT EXISTS idx_knowledge_metadata ON knowledge USING gin (metadata_knowledge);

-- Функция обновляется через CREATE OR REPLACE
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 2. Триггер создается с предварительной проверкой существования
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM pg_trigger 
        WHERE tgname = 'update_knowledge_updated_at'
    ) THEN
        CREATE TRIGGER update_knowledge_updated_at
            BEFORE UPDATE ON knowledge
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM pg_trigger 
        WHERE tgname = 'update_wiki_updated_at'
    ) THEN
        CREATE TRIGGER update_wiki_updated_at
            BEFORE UPDATE ON wiki_pages
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;


-- 2. Векторная таблица (размерность 1024, например для embedder моделей bge-m3 / mxbai / ollama / cohere)
CREATE TABLE IF NOT EXISTS knowledge_vectors (
    id SERIAL PRIMARY KEY,
    knowledge_id INTEGER UNIQUE NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    embedding vector(1024) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- HNSW индекс для супер-быстрого косинусного поиска (<=>)
CREATE INDEX IF NOT EXISTS idx_knowledge_vectors_embedding_hnsw 
    ON knowledge_vectors USING hnsw (embedding vector_cosine_ops);


-- 3. Таблица рецензирования (Инкубатор удаления)
CREATE TABLE IF NOT EXISTS knowledge_review (
    id SERIAL PRIMARY KEY,
    knowledge_id INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
    suggested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- Индекс для FK
CREATE INDEX IF NOT EXISTS idx_knowledge_review_knowledge_id ON knowledge_review(knowledge_id);

-- Таблица аудита изменений
CREATE TABLE IF NOT EXISTS knowledge_log (
  id           SERIAL PRIMARY KEY,
  action       VARCHAR(30) NOT NULL,
  target_type  VARCHAR(30) NOT NULL DEFAULT 'knowledge',
  target_id    INTEGER,
  details      JSONB DEFAULT '{}'::jsonb,
  created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS knowledge_log_target_idx
  ON knowledge_log (target_type, target_id);

CREATE INDEX IF NOT EXISTS knowledge_log_action_idx
  ON knowledge_log (action);

CREATE INDEX IF NOT EXISTS knowledge_log_time_idx
  ON knowledge_log (created_at DESC);

-- Таблица графа связей
CREATE TABLE IF NOT EXISTS entity_relations (
  id              SERIAL PRIMARY KEY,
  source_id       INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
  target_id       INTEGER NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,
  relation_type   TEXT NOT NULL,
  relevance_score REAL DEFAULT 0.5,
  importance      REAL DEFAULT 0.5,
  provenance VARCHAR(30) DEFAULT 'agent-inference',
  created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS entity_relations_source_idx
  ON entity_relations (source_id);

CREATE INDEX IF NOT EXISTS entity_relations_target_idx
  ON entity_relations (target_id);

CREATE INDEX IF NOT EXISTS entity_relations_type_idx
  ON entity_relations (relation_type);

-- Wiki-pages
CREATE TABLE IF NOT EXISTS wiki_pages (
  id            SERIAL PRIMARY KEY,
  title         TEXT NOT NULL,
  content_md    TEXT NOT NULL,
  source_ids    JSONB DEFAULT '[]'::jsonb,
  links         JSONB DEFAULT '[]'::jsonb,
  index_entry   TEXT,
  status        VARCHAR(20) DEFAULT 'draft',
  access_count  INTEGER DEFAULT 0,
  importance    REAL DEFAULT 0.5,
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS wiki_pages_status_idx
  ON wiki_pages (status);

CREATE INDEX IF NOT EXISTS wiki_pages_source_gin_idx
  ON wiki_pages USING GIN (source_ids);

CREATE INDEX IF NOT EXISTS wiki_pages_trgm_idx
  ON wiki_pages USING GIN (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS wiki_pages_importance_idx
  ON wiki_pages (importance DESC);


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

-- ОПИСАНИЕ ТАБЛИЦ СО ЗНАНИЯМИ
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

-- Инкубатор перед удалением
COMMENT ON TABLE knowledge_review IS 
'Инкубатор кандидатов на удаление. Агент предлагает устаревшие или дублирующиеся knowledge.id с обоснованием перед фактическим DELETE.';

COMMENT ON COLUMN knowledge_review.knowledge_id IS 
'Ссылка на кандидата на удаление из таблицы knowledge.';

COMMENT ON COLUMN knowledge_review.reason IS 
'Подробное обоснование агента, почему данное знание нужно удалить или актуализировать.';

COMMENT ON COLUMN knowledge_review.status IS 
'Статус модерации человеком или правилом: pending (ожидает), approved (одобрено), rejected (отклонено).';

-- Коммент papers_metadata
COMMENT ON TABLE papers_metadata IS 'Таблица для хранения метаданных, аннотаций и аналитики научных статей.';

-- 2. Комментарии к колонкам (с примерами и правилами для LLM)
COMMENT ON COLUMN papers_metadata.external_id IS 'PRIMARY KEY. Уникальный хэш или ID статьи (например: "3fa4ef01ef8c" или "arxiv_2510.01234").';
COMMENT ON COLUMN papers_metadata.source IS 'Источник статьи (например: "yandex_disk", "arxiv", "web_parser").';
COMMENT ON COLUMN papers_metadata.title IS 'Полное название статьи.';
COMMENT ON COLUMN papers_metadata.date_sub IS 'Дата публикации в формате YYYY-MM-DD (если нет — NULL).';
COMMENT ON COLUMN papers_metadata.url_article IS 'Прямая URL-ссылка на страницу статьи.';
COMMENT ON COLUMN papers_metadata.pdf_url IS 'Ссылка на PDF-файл (если есть).';
COMMENT ON COLUMN papers_metadata.authors IS 'JSONB. Авторы в формате JSON-массива строк! ПРИМЕР: ["Iris Beerepoot", "Xixi Lu"]. Не заэкранированная строка!';
COMMENT ON COLUMN papers_metadata.tags IS 'TEXT[]. Массив тегов/ключевых слов в синтаксисе Postgres ARRAY. ПРИМЕР: {"Process Mining", "LLM"}';
COMMENT ON COLUMN papers_metadata.category IS 'Основное направление/категория статьи (например: "AI & Process Mining").';
COMMENT ON COLUMN papers_metadata.abstract IS 'Полный аннотированный текст или очищенная выжимка статьи.';
COMMENT ON COLUMN papers_metadata.file_article IS 'НЕ ЗАПОЛНЯТЬ! Относительный путь к файлу, заполняется сервисом загрузки.';
COMMENT ON COLUMN papers_metadata.relevance_score IS 'FLOAT. Оценка полезности для проекта от 0.0 до 1.0.';
COMMENT ON COLUMN papers_metadata.relevance_reasoning IS 'Краткое обоснование, почему статья релевантна или нерелевантна.';
COMMENT ON COLUMN papers_metadata.is_relevant IS 'BOOLEAN. true если relevance_score >= 0.7, иначе false.';
COMMENT ON COLUMN papers_metadata.text_review IS 'Сгенерированный агентский разбор/рецензия статьи.';
COMMENT ON COLUMN papers_metadata.review_flag IS 'НЕ ЗАПОЛНЯТЬ! По умолчанию FALSE. Флаг отправки ревью пользователю.';
COMMENT ON COLUMN papers_metadata.processed_at IS 'НЕ ЗАПОЛНЯТЬ! Автоматически подставляется NOW().';



-- ============================================================
-- knowledge_log — аудит изменений
-- ============================================================

COMMENT ON TABLE knowledge_log IS 'Аудит всех изменений в knowledge. Заполняется автоматически триггером log_knowledge_change при INSERT/UPDATE. Ручные записи — для cron-операций (temporal decay и т.д.).';
COMMENT ON COLUMN knowledge_log.id IS 'Уникальный идентификатор записи аудита.';
COMMENT ON COLUMN knowledge_log.action IS 'Тип действия. Значения: insert, update, decay, merge, archive, deprecate, restore.';
COMMENT ON COLUMN knowledge_log.target_type IS 'Тип целевой сущности. Значения: knowledge (дефолт), wiki_pages, entity_relations, knowledge_review.';
COMMENT ON COLUMN knowledge_log.target_id IS 'ID записи в целевой таблице. NULL допустим для массовых операций (например, cron decay по всем).';
COMMENT ON COLUMN knowledge_log.details IS 'JSONB с деталями изменения. Примеры:
Для insert: {"text": "первые 200 символов"}
Для update: {"old_text": "...", "new_text": "...", "old_importance": 0.8, "new_importance": 0.76}
Для decay: {"old_importance": 0.8, "new_importance": 0.76, "reason": "no_access_7d"}
Для merge: {"merged_into": 47, "merged_from": [42, 43]}';
COMMENT ON COLUMN knowledge_log.created_at IS 'Дата и время действия. Не обновляется (immutable).';


-- ============================================================
-- entity_relations — граф связей
-- ============================================================

COMMENT ON TABLE entity_relations IS 'Направленный граф связей между записями knowledge. Связь идёт от source_id к target_id. Любая запись knowledge может иметь связи любого типа в обе стороны.';
COMMENT ON COLUMN entity_relations.id IS 'Уникальный идентификатор связи.';
COMMENT ON COLUMN entity_relations.source_id IS 'ID записи knowledge — источник связи (субъект триплета).';
COMMENT ON COLUMN entity_relations.target_id IS 'ID записи knowledge — цель связи (объект триплета).';
COMMENT ON COLUMN entity_relations.relation_type IS 'Тип отношения. Примеры: uses, includes, stores, evaluates, summarizes, alternative_to, based_on, relates_to, extends, contradicts. Открытый словарь — новые типы добавляются по мере необходимости.';
COMMENT ON COLUMN entity_relations.relevance_score IS 'Сила связи (0-1). Насколько target_id релевантен для source_id. Семантическая близость, вычисляется при создании связи. Дефолт 0.5.';
COMMENT ON COLUMN entity_relations.importance IS 'Вес важности самой связи (0-1). Насколько эта связь критична для понимания темы. Дефолт 0.5. Может отличаться от importance сущностей.';
COMMENT ON COLUMN entity_relations.provenance IS 'Тип происхождения связи. Значения: user-stated (пользователь явно указал), agent-inference (агент вывел сам, дефолт), document (извлечено из документа), third-party (внешний источник).';
COMMENT ON COLUMN entity_relations.created_at IS 'Дата и время создания связи. Не обновляется (immutable).';


-- ============================================================
-- wiki_pages — синтез тем
-- ============================================================

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


GRANT SELECT, INSERT, UPDATE ON entity_relations TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE entity_relations_id_seq TO knowledge_manager;

GRANT SELECT, INSERT, UPDATE ON wiki_pages TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE wiki_pages_id_seq TO knowledge_manager;

GRANT SELECT ON knowledge_log TO knowledge_manager;