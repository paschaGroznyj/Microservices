-- Фичи релиза для модалки «Что нового» (сплит на ПК, лента на мобиле).
-- Наполняется скриптом chats_service/scripts/release.py из releases/<версия>.yml.
-- Старые обновления без строк здесь показываются как раньше: app_updates.body (markdown).
CREATE TABLE IF NOT EXISTS app_update_items (
    id        SERIAL PRIMARY KEY,
    update_id INTEGER NOT NULL REFERENCES app_updates(id) ON DELETE CASCADE,
    ord       INTEGER NOT NULL,
    kind      VARCHAR(16) NOT NULL CHECK (kind IN ('new', 'improve', 'fix')),
    title     TEXT NOT NULL,
    body      TEXT NOT NULL DEFAULT '',
    -- {"type": "video"|"image", "webm", "mp4", "src", "poster": url,
    --  "thumbhash", "w", "h", "keys": [ключи в OBS]}
    media     JSONB,
    UNIQUE (update_id, ord)
);

CREATE INDEX IF NOT EXISTS ix_app_updates_version ON app_updates (version);
