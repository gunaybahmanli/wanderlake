#!/usr/bin/env bash
# Prod Iceberg JDBC catalog in Supabase Postgres (ADR-0012): role wanderlake_catalog, schema
# iceberg_catalog and the two catalog tables. One-time setup, idempotent (re-running re-syncs the
# role password and changes nothing else). Run with: make prod-catalog-init
#
# - The schema is not in the PostgREST schema list (infra/tofu, supabase_settings), and public,
#   anon and authenticated get no privileges on it: the website's API cannot see it.
# - The role can log in only to own these tables; it is not a member of any Supabase role.
# - Through the session pooler the role logs in as wanderlake_catalog.<project ref>.
#
# Needs (from .env, see the Makefile): SUPABASE_DB_HOST, SUPABASE_DB_PORT, SUPABASE_DB_USER,
# SUPABASE_DB_NAME, SUPABASE_DB_PASSWORD (admin) and PROD_ICEBERG_CATALOG_DB_PASSWORD.
# psql runs in the Postgres image; passwords are passed as environment variables, never as args.
set -euo pipefail

: "${SUPABASE_DB_HOST:?}" "${SUPABASE_DB_USER:?}" "${SUPABASE_DB_PASSWORD:?}"
: "${PROD_ICEBERG_CATALOG_DB_PASSWORD:?set PROD_ICEBERG_CATALOG_DB_PASSWORD in .env}"
PORT="${SUPABASE_DB_PORT:-5432}"
DB="${SUPABASE_DB_NAME:-postgres}"
ROLE=wanderlake_catalog
SCHEMA=iceberg_catalog
PROJECT_REF="${SUPABASE_DB_USER#*.}"
IMAGE="${PSQL_IMAGE:-pgvector/pgvector:0.8.7-pg17-bookworm}"

# The role password is sent as a SCRAM-SHA-256 verifier computed here, never as plain text (an
# ALTER ROLE statement can end up in server logs).
CATALOG_PW_SCRAM="$(python3 - <<'PY'
import base64, hashlib, hmac, os
password = os.environ["PROD_ICEBERG_CATALOG_DB_PASSWORD"].encode()
salt, iterations = os.urandom(16), 4096
salted = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
client_key = hmac.new(salted, b"Client Key", "sha256").digest()
stored_key = hashlib.sha256(client_key).digest()
server_key = hmac.new(salted, b"Server Key", "sha256").digest()
b64 = lambda b: base64.b64encode(b).decode()
print(f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}")
PY
)"
export PROD_ICEBERG_CATALOG_DB_PASSWORD CATALOG_PW_SCRAM

psql_as() { # psql_as <user> <password>: runs stdin. Secrets travel only in the environment.
    PGPASSWORD="$2" \
        docker run --rm -i -e PGPASSWORD -e CATALOG_PW_SCRAM -e PGSSLMODE=require "$IMAGE" \
        psql -v ON_ERROR_STOP=1 -X -q -h "$SUPABASE_DB_HOST" -p "$PORT" -U "$1" -d "$DB" \
        -v role="$ROLE" -v schema="$SCHEMA"
}

# 1. Role and schema, as the project's admin user.
psql_as "$SUPABASE_DB_USER" "$SUPABASE_DB_PASSWORD" <<'SQL'
\getenv pw CATALOG_PW_SCRAM
SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
ALTER ROLE :"role" WITH LOGIN NOINHERIT PASSWORD :'pw';
ALTER ROLE :"role" SET search_path = :"schema";
CREATE SCHEMA IF NOT EXISTS :"schema";
REVOKE ALL ON SCHEMA :"schema" FROM public, anon, authenticated;
GRANT USAGE, CREATE ON SCHEMA :"schema" TO :"role";
SQL

# 2. Catalog tables, as the catalog role (so it owns them). Same DDL as the dev catalog
# (infra/compose/postgres/init/02-iceberg-catalog.sh; a test keeps the two identical).
psql_as "$ROLE.$PROJECT_REF" "$PROD_ICEBERG_CATALOG_DB_PASSWORD" <<'SQL'
SET search_path = iceberg_catalog;
CREATE TABLE IF NOT EXISTS iceberg_tables (
    catalog_name               VARCHAR(255) NOT NULL,
    table_namespace            VARCHAR(255) NOT NULL,
    table_name                 VARCHAR(255) NOT NULL,
    metadata_location          VARCHAR(1000),
    previous_metadata_location VARCHAR(1000),
    iceberg_type               VARCHAR(5),
    PRIMARY KEY (catalog_name, table_namespace, table_name)
);
CREATE TABLE IF NOT EXISTS iceberg_namespace_properties (
    catalog_name   VARCHAR(255) NOT NULL,
    namespace      VARCHAR(255) NOT NULL,
    property_key   VARCHAR(255),
    property_value VARCHAR(1000),
    PRIMARY KEY (catalog_name, namespace, property_key)
);
SELECT current_user AS connected_as, count(*) AS catalog_tables
FROM pg_tables WHERE schemaname = 'iceberg_catalog';
SQL

echo "prod iceberg catalog ready: schema=$SCHEMA role=$ROLE"
