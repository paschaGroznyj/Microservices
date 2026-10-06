CREATE TABLE IF NOT EXISTS code_library (
    id           SERIAL PRIMARY KEY,
    title        TEXT        NOT NULL,
    description  TEXT,
    code_type    TEXT        NOT NULL,
    path         TEXT,
    run_id       TEXT,
    tags         TEXT[]      DEFAULT '{}',
    author       TEXT        NOT NULL DEFAULT 'agent',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    use_count    INT         NOT NULL DEFAULT 0,
    tsv         TSVECTOR
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_cl_type    ON code_library (code_type);
CREATE INDEX IF NOT EXISTS idx_cl_author  ON code_library (author);
CREATE INDEX IF NOT EXISTS idx_cl_tags    ON code_library USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_cl_use     ON code_library (use_count DESC);
CREATE INDEX IF NOT EXISTS idx_cl_created ON code_library (created_at DESC);



-- триггер автообновления tsv
CREATE OR REPLACE FUNCTION code_library_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.tsv := to_tsvector('russian',
    coalesce(NEW.title,'') || ' ' ||
    coalesce(NEW.description,'') || ' ' ||
    coalesce(array_to_string(NEW.tags,' '),'')
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM pg_trigger 
        WHERE tgname = 'tsvupdate'
    ) THEN
        CREATE TRIGGER tsvupdate BEFORE INSERT OR UPDATE
            ON code_library FOR EACH ROW
            EXECUTE FUNCTION code_library_tsv_update();
    END IF;
END $$;

-- индекс
CREATE INDEX IF NOT EXISTS idx_code_library_tsv
  ON code_library USING GIN(tsv);


-- Права
GRANT SELECT, INSERT, UPDATE ON code_library TO knowledge_manager;
GRANT USAGE, SELECT ON SEQUENCE code_library_id_seq TO knowledge_manager;

-- Комментарии
COMMENT ON TABLE code_library IS 'Библиотека кода проекта и агента PMыч. Хранит скрипты двух типов: написанные агентом в песочнице (author=agent) и исходный код проекта (author=human). Используется для переиспользования скриптов: агент ищет по title/description/tags, достаёт файл по path и отправляет в среду исполнения без повторной генерации.';
COMMENT ON COLUMN code_library.title        IS 'Краткое название скрипта. Должно быть информативным. Пример: "Парсер hh.ru вакансий".';
COMMENT ON COLUMN code_library.description  IS 'Подробное описание: что делает, входные параметры, выходной результат. Участвует в FTS.';
COMMENT ON COLUMN code_library.code_type    IS 'Тип скрипта: parser | scraper | excel | pptx | digest | generator | util | migration | core';
COMMENT ON COLUMN code_library.path         IS 'Путь до файла. Агентские: /sandbox/reusable/{uuid}.py. Проектные: /app/app/...';
COMMENT ON COLUMN code_library.run_id       IS 'UUID запуска из execute_python_code. Только для author=agent. Для human — NULL.';
COMMENT ON COLUMN code_library.tags         IS 'Массив тегов. Поиск: tags @> ARRAY[''excel''].';
COMMENT ON COLUMN code_library.author       IS 'Автор: agent | human | human:паша';
COMMENT ON COLUMN code_library.created_at   IS 'Дата первой записи в библиотеку.';
COMMENT ON COLUMN code_library.last_used_at IS 'Дата последнего использования агентом. Обновляется при edit_and_run.';
COMMENT ON COLUMN code_library.use_count    IS 'Счётчик использований. Инкрементируется при каждом edit_and_run.';
