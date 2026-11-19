#!/bin/bash
# Iceberg JDBC catalog: database "iceberg_catalog", owned by its own role, plus the catalog tables.
#
# Idempotent, and used in two ways:
#   - fresh volume: the Postgres image runs it from /docker-entrypoint-initdb.d (local socket)
#   - existing volume: the one-shot "catalog-db-init" service runs it with PGHOST=postgres
# Re-running it re-syncs the role password from .env and changes nothing else.
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${ICEBERG_CATALOG_DB_USER:?ICEBERG_CATALOG_DB_USER is required}"
: "${ICEBERG_CATALOG_DB_PASSWORD:?ICEBERG_CATALOG_DB_PASSWORD is required}"
CATALOG_DB=iceberg_catalog

# Role and database (as the superuser). CREATE DATABASE has no IF NOT EXISTS: use \gexec.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v role="$ICEBERG_CATALOG_DB_USER" -v pw="$ICEBERG_CATALOG_DB_PASSWORD" -v db="$CATALOG_DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
ALTER ROLE :"role" WITH LOGIN PASSWORD :'pw';
SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SQL

# Catalog tables (as the catalog role, so it owns them). Trino's JDBC catalog requires them to
# exist. DDL from Apache Iceberg 1.12.0 (bundled in Trino 484), JdbcUtil, schema version V1.
PGPASSWORD="$ICEBERG_CATALOG_DB_PASSWORD" \
psql -v ON_ERROR_STOP=1 --username "$ICEBERG_CATALOG_DB_USER" --dbname "$CATALOG_DB" <<'SQL'
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
SQL

echo "iceberg catalog database ready: db=$CATALOG_DB role=$ICEBERG_CATALOG_DB_USER"
