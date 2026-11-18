-- Ветки (форки) чата от сообщения агента.
-- История наследуется по ссылке: сообщения родителя не копируются, ветка читает их
-- по цепочке chats_forks до точки форка включительно. Порядок держится на глобальном
-- SERIAL chats.id: сообщения ветки всегда новее точки форка.
-- Память агента (agent_journal, chat_rolling_summary) копируется на момент точки.

-- Индекс (chat_id, id): курсорная пагинация по звену цепочки и цель составного FK.
CREATE UNIQUE INDEX IF NOT EXISTS ux_chats_chat_id_id ON chats(chat_id, id);

CREATE TABLE IF NOT EXISTS chats_forks (
    id             SERIAL PRIMARY KEY,
    child_chat_id  TEXT   NOT NULL UNIQUE,      -- у чата не больше одного родителя
    parent_chat_id TEXT   NOT NULL,             -- владелец сообщения point_id
    point_id       INT    NOT NULL,             -- chats.id сообщения агента, включительно
    user_id        BIGINT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (child_chat_id <> parent_chat_id),
    -- point_id обязан принадлежать parent_chat_id; жёсткое удаление точки запрещено
    FOREIGN KEY (parent_chat_id, point_id) REFERENCES chats(chat_id, id)
);
CREATE INDEX IF NOT EXISTS ix_chats_forks_parent ON chats_forks(parent_chat_id);
CREATE INDEX IF NOT EXISTS ix_chats_forks_user ON chats_forks(user_id, created_at DESC);

COMMENT ON TABLE chats_forks IS
'Ветки чата: child_chat_id видит сообщения parent_chat_id с id <= point_id и дальше по цепочке предков.';

-- Цепочка предков: (chat_id, граница id включительно, глубина). У самого чата граница NULL.
CREATE OR REPLACE FUNCTION chat_chain(p_chat TEXT)
RETURNS TABLE(chat_id TEXT, upto INT, depth INT) LANGUAGE sql STABLE AS $$
    WITH RECURSIVE chain(chat_id, upto, depth) AS (
        SELECT p_chat, NULL::int, 0
        UNION ALL
        SELECT f.parent_chat_id, f.point_id, ch.depth + 1
        FROM chats_forks f JOIN chain ch ON f.child_chat_id = ch.chat_id
        WHERE ch.depth < 64
    )
    SELECT chat_id, upto, depth FROM chain
$$;

-- То же звено цепочки плюс время точки форка: для таблиц без ссылки на chats.id
-- (generated_images.message_id бывает NULL) граница звена проверяется по created_at.
CREATE OR REPLACE FUNCTION chat_chain_until(p_chat TEXT)
RETURNS TABLE(chat_id TEXT, upto INT, upto_at TIMESTAMPTZ) LANGUAGE sql STABLE AS $$
    SELECT c.chat_id, c.upto, p.created_at
    FROM chat_chain(p_chat) c LEFT JOIN chats p ON p.id = c.upto
$$;

-- Страница истории с учётом предков, новые сверху. p_before — курсор (id < p_before).
-- По каждому звену читается не больше p_limit строк обратным сканом индекса.
CREATE OR REPLACE FUNCTION chat_history(p_chat TEXT, p_before INT DEFAULT NULL, p_limit INT DEFAULT 50)
RETURNS SETOF chats LANGUAGE sql STABLE AS $$
    SELECT m.*
    FROM chat_chain(p_chat) c
    CROSS JOIN LATERAL (
        SELECT x.* FROM chats x
        WHERE x.chat_id = c.chat_id
          AND x.id <= LEAST(COALESCE(c.upto, 2147483647), COALESCE(p_before - 1, 2147483647))
        ORDER BY x.id DESC
        LIMIT p_limit
    ) m
    ORDER BY m.id DESC
    LIMIT p_limit
$$;

-- Создать ветку. p_view_chat — чат, в котором нажали «форк», p_point_id — chats.id
-- сообщения агента. Родителем записывается владелец сообщения (оно может быть унаследованным).
-- Коды ошибок для сервиса: P0002 нет сообщения, 22023 не от агента,
-- 42501 чужой чат / сообщение не видно, 23505 id ветки занят.
CREATE OR REPLACE FUNCTION fork_chat(p_view_chat TEXT, p_point_id INT, p_new_chat TEXT, p_user BIGINT)
RETURNS chats_forks LANGUAGE plpgsql AS $$
DECLARE m chats; r chats_forks; v_parent_title TEXT; v_fork_count INT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM chats WHERE chat_id = p_view_chat AND user_id = p_user)
       AND NOT EXISTS (SELECT 1 FROM chats_forks WHERE child_chat_id = p_view_chat AND user_id = p_user) THEN
        RAISE EXCEPTION 'fork: чат % не принадлежит пользователю %', p_view_chat, p_user
            USING ERRCODE = '42501';
    END IF;

    SELECT * INTO m FROM chats WHERE id = p_point_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'fork: сообщение % не найдено', p_point_id USING ERRCODE = 'P0002';
    END IF;
    IF NOT COALESCE(m.is_from_bot, FALSE) THEN
        RAISE EXCEPTION 'fork: сообщение % не от агента', p_point_id USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM chat_chain(p_view_chat) c
                   WHERE c.chat_id = m.chat_id AND (c.upto IS NULL OR m.id <= c.upto)) THEN
        RAISE EXCEPTION 'fork: сообщение % не видно в чате %', p_point_id, p_view_chat
            USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM chats WHERE chat_id = p_new_chat)
       OR EXISTS (SELECT 1 FROM chats_forks WHERE child_chat_id = p_new_chat) THEN
        RAISE EXCEPTION 'fork: чат % уже существует', p_new_chat USING ERRCODE = '23505';
    END IF;

    INSERT INTO chats_forks (child_chat_id, parent_chat_id, point_id, user_id)
    VALUES (p_new_chat, m.chat_id, m.id, p_user) RETURNING * INTO r;

    INSERT INTO chat_types_agent (chat_id, user_id, type_agent)
    VALUES (p_new_chat, p_user,
            COALESCE((SELECT type_agent FROM chat_types_agent
                      WHERE chat_id = p_view_chat AND user_id = p_user), 'pm'))
    ON CONFLICT DO NOTHING;

    -- Как в сайдбаре (_CHATS_PAGE_SQL): renamed_title — из строки пользователя,
    -- auto_title — из любой строки чата (агент пишет его под user_id = -1).
    v_parent_title := COALESCE(
        (SELECT NULLIF(renamed_title, '') FROM chat_sidebar_meta
         WHERE chat_id = m.chat_id AND user_id = p_user),
        (SELECT NULLIF(auto_title, '') FROM chat_sidebar_meta
         WHERE chat_id = m.chat_id AND NULLIF(auto_title, '') IS NOT NULL
         ORDER BY (user_id = p_user) DESC, updated_at DESC LIMIT 1),
        'Новый чат'
    );
    v_fork_count := (SELECT COUNT(*) FROM chats_forks WHERE parent_chat_id = m.chat_id);

    INSERT INTO chat_sidebar_meta (chat_id, user_id, auto_title)
    VALUES (p_new_chat, p_user, v_parent_title || ' (' || v_fork_count || ')')
    ON CONFLICT DO NOTHING;
    RETURN r;
END $$;

-- Память ветки на момент точки. Источник — чат-владелец точки (parent_chat_id):
-- его журнал уже содержит всё, что он сам унаследовал при своём форке.
-- 1. agent_journal: записи с created_at <= точки. Даты сохраняются, чтобы та же
--    граница работала для внуков.
--    Плюс summary автосжатия, созданные после точки, если в context.compressed_ids
--    есть записи до точки: исходники удалены сжатием, без summary ветка потеряла бы
--    эту память. Такая summary может содержать немного сведений после точки.
--    «До точки» = id меньше первого id журнала, созданного после точки (BIGSERIAL общий).
-- 2. chat_rolling_summary: только если покрывает сообщения не дальше точки.
CREATE OR REPLACE FUNCTION fork_copy_memory(p_fork chats_forks)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    v_ts   timestamptz;
    v_jid  bigint;
    n_j    int := 0;
    n_r    int := 0;
BEGIN
    SELECT created_at INTO v_ts FROM chats WHERE id = p_fork.point_id;
    SELECT min(id) INTO v_jid FROM agent_journal WHERE created_at > v_ts;

    INSERT INTO agent_journal (chat_id, entry_type, content, context, user_id,
                               is_archived, archive_summary, created_at, updated_at)
    SELECT p_fork.child_chat_id, j.entry_type, j.content,
           COALESCE(j.context, '{}'::jsonb)
             || jsonb_build_object('chat_id', p_fork.child_chat_id,
                                   'forked_from', j.chat_id, 'src_entry_id', j.id),
           j.user_id, j.is_archived, j.archive_summary, j.created_at, j.updated_at
    FROM agent_journal j
    WHERE j.chat_id = p_fork.parent_chat_id
      AND (
            j.created_at <= v_ts
         OR (jsonb_typeof(j.context -> 'compressed_ids') = 'array'
             AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(j.context -> 'compressed_ids') e(v)
                         WHERE e.v ~ '^[0-9]+$'
                           AND (v_jid IS NULL OR e.v::bigint < v_jid)))
          )
    ORDER BY j.id;
    GET DIAGNOSTICS n_j = ROW_COUNT;

    INSERT INTO chat_rolling_summary (chat_id, summary_text, covers_messages, covered_up_to_id, updated_at)
    SELECT p_fork.child_chat_id, s.summary_text, s.covers_messages, s.covered_up_to_id, now()
    FROM chat_rolling_summary s
    WHERE s.chat_id = p_fork.parent_chat_id
      AND s.covered_up_to_id <= p_fork.point_id
    ON CONFLICT (chat_id) DO NOTHING;
    GET DIAGNOSTICS n_r = ROW_COUNT;

    RETURN jsonb_build_object('journal', n_j, 'rolling', n_r);
END $$;

-- Форк целиком в одной транзакции вызывающего: ссылка, сайдбар, тип агента, память.
CREATE OR REPLACE FUNCTION fork_chat_full(p_view_chat TEXT, p_point_id INT, p_new_chat TEXT, p_user BIGINT)
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE r chats_forks;
BEGIN
    r := fork_chat(p_view_chat, p_point_id, p_new_chat, p_user);
    RETURN jsonb_build_object('chat_id', r.child_chat_id, 'parent', r.parent_chat_id,
                              'point', r.point_id, 'created_at', r.created_at)
           || fork_copy_memory(r);
END $$;
