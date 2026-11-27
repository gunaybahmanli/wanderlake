#!/usr/bin/env bash
# The dbt MCP server (dbt Labs, open source) for Claude Code, read-only (ADR-0013).
# Started by .mcp.json (project scope). Needs: make dbt-venv (local dbt) and make mode-query (Trino).
#
# Read-only, in two layers:
#   1. Tools: DBT_MCP_ENABLE_TOOLS is an allowlist that takes precedence over every other tool
#      setting, so only these exist: list, parse, compile, show, get_lineage_dev,
#      get_node_details_dev. DISABLE_TOOLS repeats the tools that run or change data.
#   2. Database: dbt connects to the laptop's dev Trino (target local, never prod) as the Trino
#      user wanderlake_mcp, which infra/compose/trino/etc/rules.json limits to read-only.
#      Needed because `show` passes SQL through: it adds a row limit (default 5), but SQL that
#      contains its own "limit N" runs as written.
# stdout is the MCP protocol channel: messages for humans go to stderr only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DBT_MCP_VERSION=2.10.3

if [[ ! -x "$ROOT/.venv-dbt/bin/dbt" ]]; then
    echo "dbt-mcp: $ROOT/.venv-dbt/bin/dbt not found: run 'make dbt-venv' first." >&2
    exit 1
fi
port="${TRINO_PORT:-8081}"
if ! curl -fsS -m 3 "http://localhost:$port/v1/info" >/dev/null 2>&1; then
    echo "dbt-mcp: Trino is not reachable on localhost:$port: run 'make mode-query' first." >&2
    exit 1
fi

export DBT_PROJECT_DIR="$ROOT/dbt"
export DBT_PROFILES_DIR="$ROOT/dbt"
export DBT_PATH="$ROOT/.venv-dbt/bin/dbt"
export DBT_TARGET=local
export DBT_TRINO_HOST=localhost
export DBT_TRINO_PORT="$port"
export DBT_TRINO_USER=wanderlake_mcp
export DBT_CLI_TIMEOUT=120
export DBT_MCP_ENABLE_TOOLS="list,parse,compile,show,get_lineage_dev,get_node_details_dev"
export DISABLE_TOOLS="build,run,test,clone,docs"
export DISABLE_SEMANTIC_LAYER=true DISABLE_DISCOVERY=true DISABLE_ADMIN_API=true
export DISABLE_SQL=true DISABLE_DBT_CODEGEN=true DISABLE_LSP=true
export DO_NOT_TRACK=1 DBT_SEND_ANONYMOUS_USAGE_STATS=false

exec uvx --quiet "dbt-mcp==$DBT_MCP_VERSION"
