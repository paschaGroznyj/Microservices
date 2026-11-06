#!/usr/bin/env bash
set -euo pipefail

# Если база уже инициализирована ранее (каталог PGDATA/base существует),
# стандартный /docker-entrypoint-initdb.d не запускается.
# Запускаем фоновый воркер, который дождётся старта postgres и идемпотентно
# прогонит все скрипты из /opt/postgres-schema/sql (создаст новые таблицы и индексы).
PGDATA_DIR="${PGDATA:-/var/lib/postgresql/data}"

if [ -d "$PGDATA_DIR/base" ]; then
    (
        until pg_isready -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" >/dev/null 2>&1; do
            sleep 1
        done
        echo "[postgres-entrypoint] Existing database detected — syncing schema from /opt/postgres-schema/sql..."
        /usr/local/bin/apply-schema.sh
    ) &
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
