#!/usr/bin/env bash
# GitHub Environment "production" for .github/workflows/prod-pipeline.yml (ADR-0012): creates the
# environment, limits it to the listed branches, and sets its secrets from .env.
# Run with: make prod-github-env [BRANCHES="main feat/x"]   (default: main only)
#
# Secret values go to gh on stdin, never as arguments, and are never printed. Settings that are
# not secret (bucket names, catalog name, region...) live in the workflow file instead.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:-gunaybahmanli/wanderlake}"
ENVIRONMENT=production
BRANCHES="${BRANCHES:-main}"
POLICIES="repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies"

# Secret name in GitHub = value from .env (the prod pipeline reads the generic names).
: "${TF_VAR_cloudflare_account_id:?}" "${CLOUD_S3_ACCESS_KEY:?}" "${CLOUD_S3_SECRET_KEY:?}"
: "${SUPABASE_DB_HOST:?}" "${SUPABASE_DB_USER:?}" "${PROD_ICEBERG_CATALOG_DB_PASSWORD:?}"
: "${REST_COUNTRIES_API_KEY:?}" "${TF_VAR_supabase_db_password:?}"
declare -A SECRETS=(
    [S3_ENDPOINT]="https://${TF_VAR_cloudflare_account_id}.r2.cloudflarestorage.com"
    [S3_ACCESS_KEY]="$CLOUD_S3_ACCESS_KEY"
    [S3_SECRET_KEY]="$CLOUD_S3_SECRET_KEY"
    [ICEBERG_CATALOG_DB_HOST]="$SUPABASE_DB_HOST"
    [ICEBERG_CATALOG_DB_USER]="wanderlake_catalog.${SUPABASE_DB_USER#*.}"
    [ICEBERG_CATALOG_DB_PASSWORD]="$PROD_ICEBERG_CATALOG_DB_PASSWORD"
    [REST_COUNTRIES_API_KEY]="$REST_COUNTRIES_API_KEY"
    # Publisher (ingestion.publish, ops.pipeline_runs): the project's database owner, as the
    # laptop's make publish used; the host is ICEBERG_CATALOG_DB_HOST (same pooler).
    [SUPABASE_DB_USER]="$SUPABASE_DB_USER"
    [SUPABASE_DB_PASSWORD]="$TF_VAR_supabase_db_password"
)

echo "== environment $ENVIRONMENT (deployment branches: $BRANCHES)"
gh api --method PUT "repos/$REPO/environments/$ENVIRONMENT" --silent --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON

# Branch policies: add the wanted branches, then drop policies for branches no longer listed.
existing="$(gh api "$POLICIES" --jq '.branch_policies[] | "\(.id) \(.name)"')"
for branch in $BRANCHES; do
    if ! grep -qx "[0-9]* $branch" <<<"$existing"; then
        gh api --method POST "$POLICIES" -f name="$branch" -f type=branch --silent
    fi
done
wanted=" $BRANCHES "
while read -r policy_id policy_name; do
    if [[ -n "$policy_id" && "$wanted" != *" $policy_name "* ]]; then
        gh api --method DELETE "$POLICIES/$policy_id" --silent
    fi
done <<<"$existing"

for name in "${!SECRETS[@]}"; do
    printf '%s' "${SECRETS[$name]}" | gh secret set "$name" --env "$ENVIRONMENT" --repo "$REPO"
done

echo "== secrets in $ENVIRONMENT (names and dates only)"
gh secret list --env "$ENVIRONMENT" --repo "$REPO"
gh api "$POLICIES" --jq '"allowed branches: " + ([.branch_policies[].name] | join(", "))'
