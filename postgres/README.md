# PostgreSQL Schema (`postgres/`)

Единый центр хранения DDL-схемы проекта (таблицы, индексы, триггеры, расширения и роли).
Все бывшие `ALTER TABLE ... ADD COLUMN` вшиты непосредственно в `CREATE TABLE IF NOT EXISTS`.

## Структура каталога `sql/`

Скрипты выполняются в лексикографическом порядке при сборке/старте контейнера `postgres`:

- `00_init/`
  - `01_extensions_and_functions.sql` — расширения `vector`, `pg_trgm`, `pgcrypto`, функция `update_updated_at_column()`.
- `01_auth/`
  - `01_users_and_profile.sql` — `users`, `feedbacks`, `app_updates`, `user_seen_updates`, `user_onboarding`, `user_preferences`.
- `02_chats/`
  - `01_chats_core.sql` — `chats`, `chat_types_agent`, `chat_sidebar_meta`, `chat_files`, `generated_images`, `agent_loop_tasks`, `model_tariffs`, `chat_poll_answers`, `editor_workspaces`, `user_settings`.
  - `02_group_chats.sql` — `group_rooms` (+ комната `global`), `group_messages`, `group_room_members`, `group_message_files`.
  - `03_messengers_tg_max.sql` — `telegram_chats`, `telegram_chat_settings`, `telegram_chat_members`, `telegram_start_log`, `telegram_triggers`, `max_chats`, `max_chat_settings`, `max_triggers`, `max_chat_members`.
- `03_agent/`
  - `01_journal_and_sessions.sql` — `agent_journal`, `agent_journal_audit`, `session_summaries`, `chat_rolling_summary`, `token_log`, `service_chats`.
  - `02_skills_and_code_library.sql` — `agent_skills`, `agent_skill_likes`, `agent_skill_selected`, `code_library`.
- `04_knowledge/`
  - `01_knowledge_graph.sql` — `knowledge`, `knowledge_vectors`, `knowledge_review`, `knowledge_log`, `entity_relations`, `wiki_pages`.
  - `02_papers_and_articles.sql` — `articles`, `search_requests`, `request_articles`, `favorites`, `user_articles`, `article_reviews`, `article_embeddings`, `article_translations`, `papers_metadata`, `reviews`.
- `05_grants/`
  - `01_roles_and_permissions.sql` — роли `knowledge_manager` и `my_journal`, права на таблицы и последовательности.

## Как работает инициализация и добавление новых таблиц

1. **Холодный старт (чистый том `postgres_data`)**: стандартный механизм `/docker-entrypoint-initdb.d/00-apply-schema.sh` прогоняет все файлы из `/opt/postgres-schema/sql`.
2. **Горячее добавление таблиц (существующий том `postgres_data`)**: `docker-entrypoint-wrapper.sh` при каждом старте контейнера ждёт `pg_isready` и запускает `/usr/local/bin/apply-schema.sh`.
3. **Ручной прогон без рестарта**:
   ```bash
   docker exec -i postgres /usr/local/bin/apply-schema.sh
   ```
