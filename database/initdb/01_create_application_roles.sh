#!/bin/bash
# Runs once, on first start of an empty Postgres volume (docker-entrypoint-initdb.d).
# app_owner    — owns the schema; used by migrations and the seed script.
# app_readonly — SELECT only, read-only transactions, 5s statement timeout; used by services.
set -euo pipefail

psql --set ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set database_name="$POSTGRES_DB" \
     --set owner_password="$APP_OWNER_PASSWORD" \
     --set readonly_password="$APP_READONLY_PASSWORD" <<'SQL'
CREATE ROLE app_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE app_readonly LOGIN PASSWORD :'readonly_password';

GRANT CONNECT ON DATABASE :"database_name" TO app_owner, app_readonly;
ALTER SCHEMA public OWNER TO app_owner;
GRANT USAGE ON SCHEMA public TO app_readonly;

-- Every table app_owner creates later is automatically readable (and only readable).
ALTER DEFAULT PRIVILEGES FOR ROLE app_owner IN SCHEMA public
    GRANT SELECT ON TABLES TO app_readonly;

ALTER ROLE app_readonly SET default_transaction_read_only = on;
ALTER ROLE app_readonly SET statement_timeout = '5s';
SQL
