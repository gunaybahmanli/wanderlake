#!/bin/bash
# Runs once, on the first start of an empty Postgres volume.
# POSTGRES_DB ("wanderlake") is already created by the image entrypoint.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v airflow_user="$AIRFLOW_DB_USER" -v airflow_password="$AIRFLOW_DB_PASSWORD" <<'SQL'
-- Airflow metadata database, owned by its own role.
CREATE ROLE :"airflow_user" WITH LOGIN PASSWORD :'airflow_password';
CREATE DATABASE airflow OWNER :"airflow_user";
SQL

# App database: enable pgvector.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SQL
