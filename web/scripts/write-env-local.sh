#!/usr/bin/env bash
# make web-env: write web/.env.local for the Next.js dev server and local builds from the root
# .env (SUPABASE_URL, SUPABASE_ANON_KEY). Run from the repository root. Prints names, not values.
# web/.env.local is gitignored (web/.gitignore: .env*) and readable by the owner only.
set -euo pipefail

set -a
# shellcheck disable=SC1091
. ./.env
set +a

if [[ -z "${SUPABASE_URL:-}" || -z "${SUPABASE_ANON_KEY:-}" ]]; then
  echo "Set SUPABASE_URL and SUPABASE_ANON_KEY in .env." >&2
  exit 1
fi

umask 077
{
  echo "# Written by 'make web-env' from the root .env. Gitignored; do not commit."
  echo "DATA_SOURCE=supabase"
  echo "SUPABASE_URL=${SUPABASE_URL}"
  echo "SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}"
} > web/.env.local

echo "Wrote web/.env.local: DATA_SOURCE, SUPABASE_URL, SUPABASE_ANON_KEY (values not shown)."
