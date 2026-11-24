#!/usr/bin/env bash
# Row counts of every table in iceberg.<schema> (default bronze), queried through the running
# Trino container (default name trino), printed as a Markdown table for the job summary.
# Usage: bash infra/prod/bronze-counts.sh [schema] [container]
set -euo pipefail

SCHEMA="${1:-bronze}"
CONTAINER="${2:-trino}"
trino_tsv() { docker exec "$CONTAINER" trino --catalog iceberg --output-format TSV --execute "$1"; }

tables="$(trino_tsv "select table_name from iceberg.information_schema.tables
    where table_schema = '$SCHEMA' order by table_name")"

echo "### Row counts in iceberg.$SCHEMA (prod lakehouse, via the job's Trino)"
echo
if [[ -z "$tables" ]]; then
    echo "No tables in iceberg.$SCHEMA."
    exit 0
fi
union=""
for t in $tables; do
    [[ -n "$union" ]] && union+=" union all "
    union+="select '$t' as table_name, count(*) as row_count from iceberg.$SCHEMA.\"$t\""
done
echo "| Table | Rows |"
echo "|---|---:|"
trino_tsv "$union order by 1" | while IFS=$'\t' read -r name count; do
    printf '| %s | %s |\n' "$name" "$(printf "%'d" "$count")"
done
echo
