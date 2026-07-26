-- 1. Основная таблица знаний
CREATE TABLE IF NOT EXISTS knowledge (
    id SERIAL PRIMARY KEY,
    text_knowledge TEXT NOT NULL,
    metadata_knowledge JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Индекс для быстрого поиска по JSONB
CREATE INDEX IF NOT EXISTS idx_knowledge_metadata ON knowledge USING gin (metadata_knowledge);

-- Функция обновляется через CREATE OR REPLACE
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE 'plpgsql';

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
