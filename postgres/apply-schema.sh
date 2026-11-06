#!/usr/bin/env bash
set -euo pipefail

SQL_ROOT="${SQL_SCHEMA_DIR:-/opt/postgres-schema/sql}"
DB_USER_NAME="${POSTGRES_USER:-postgres}"
DB_TARGET_NAME="${POSTGRES_DB:-postgres}"

if [ ! -d "$SQL_ROOT" ]; then
    echo "[postgres-schema] Directory $SQL_ROOT not found, skipping schema init."
    exit 0
fi

echo "[postgres-schema] Applying SQL schema from $SQL_ROOT to database '$DB_TARGET_NAME' (user '$DB_USER_NAME')..."

mapfile -t SQL_FILES < <(find "$SQL_ROOT" -type f -name "*.sql" | LC_ALL=C sort)

if [ "${#SQL_FILES[@]}" -eq 0 ]; then
    echo "[postgres-schema] No .sql files found in $SQL_ROOT"
    exit 0
fi

for sql_file in "${SQL_FILES[@]}"; do
    rel_path="${sql_file#$SQL_ROOT/}"
    echo "[postgres-schema] -> $rel_path"
    psql -v ON_ERROR_STOP=1 --username "$DB_USER_NAME" --dbname "$DB_TARGET_NAME" -f "$sql_file"
done

# Настройка пароля и CONNECT-прав для пулов агента (knowledge_manager, my_journal)
psql -v ON_ERROR_STOP=1 --username "$DB_USER_NAME" --dbname "$DB_TARGET_NAME" <<EOSQL
GRANT CONNECT ON DATABASE "$DB_TARGET_NAME" TO knowledge_manager, my_journal;
EOSQL

if [ -n "${AGENT_POOL_PASSWORD:-}" ]; then
    psql -v ON_ERROR_STOP=1 --username "$DB_USER_NAME" --dbname "$DB_TARGET_NAME"         -v pool_pwd="$AGENT_POOL_PASSWORD" <<'EOSQL'
ALTER ROLE knowledge_manager WITH LOGIN PASSWORD :'pool_pwd';
ALTER ROLE my_journal WITH LOGIN PASSWORD :'pool_pwd';
EOSQL
    echo "[postgres-schema] Updated passwords for roles knowledge_manager and my_journal."
fi

echo "[postgres-schema] Schema successfully applied (${#SQL_FILES[@]} files)."
