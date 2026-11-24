#!/usr/bin/env bash
# Lakehouse layers as Iceberg schemas under $ICEBERG_WAREHOUSE (idempotent). Runs inside the
# Trino container, in dev (make lakehouse-init) and prod (.github/workflows/prod-pipeline.yml):
#   dev:  s3://lakehouse/warehouse/{bronze,silver,gold}/       (SeaweedFS)
#   prod: s3://wanderlake-lake/warehouse/{bronze,silver,gold}/ (Cloudflare R2)
set -euo pipefail

: "${ICEBERG_WAREHOUSE:?ICEBERG_WAREHOUSE is required}"
warehouse="${ICEBERG_WAREHOUSE%/}"
# The value goes into SQL: allow only a plain bucket/prefix URI.
if [[ ! "$warehouse" =~ ^s3://[a-z0-9][a-z0-9.-]*(/[A-Za-z0-9._-]+)*$ ]]; then
    echo "ICEBERG_WAREHOUSE must look like s3://bucket/prefix, got: $warehouse" >&2
    exit 1
fi

sql=""
for layer in bronze silver gold; do
    sql+="create schema if not exists iceberg.${layer} with (location = '${warehouse}/${layer}/'); "
done
trino --catalog iceberg --execute "${sql}show schemas from iceberg"
