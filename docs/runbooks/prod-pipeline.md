# Runbook: production pipeline (GitHub Actions)

Production is the workflow [`prod-pipeline.yml`](../../.github/workflows/prod-pipeline.yml): the
same code as the laptop, configured with `WANDERLAKE_ENV=prod`, and the only writer to the live
site's data ([ADR-0012](../adr/0012-dev-on-laptop-prod-on-github-actions-single-writer.md)).

## What runs, and when

Every day at **17:15 UTC** (after the ECB and CBAR rates are out), one job on a GitHub-hosted
runner. The schedule is `cron: "15 17 * * *"` in
[`prod-pipeline.yml`](../../.github/workflows/prod-pipeline.yml) with no `timezone` key, so GitHub
reads it in UTC (its default; a `timezone` would change that, and none is set anywhere).

GitHub starts scheduled runs when it has capacity, not on the minute: the first nights started
between 00:59 and 01:25 UTC (25 to 27 November 2026), about 8 hours late. That is a delay,
not a time zone (a run started at 00:59, before 17:15 Pacific would be). Such a run is dated by
its UTC start, so after midnight it processes the next date; rates use the latest published day
(as-of rule), so nothing is missed, and the site updates in the early UTC morning. If no run has
started by 06:00 UTC, start one by hand (below).

| Step (id) | What it does | If it fails |
|---|---|---|
| `record_start` | applies new Supabase migrations, inserts a `running` row in `ops.pipeline_runs` | nothing else runs |
| `trino` | starts `trinodb/trino:484` with the prod catalog (R2 + Supabase), waits until healthy (3 min max) | nothing else runs; Trino's log is printed |
| `bootstrap` | creates schemas `bronze`, `silver`, `gold` if missing | nothing else runs |
| `ingest` | `python -m ingestion.run daily`: daily sources, weekly/monthly ones when due | the build still runs with what loaded; the job fails |
| `dbt` | `dbt deps` and `dbt build` (target `prod`), all models and tests | no publish: the site keeps the last good data |
| (freshness) | `dbt source freshness`; warns only | never fails the job |
| `publish` | gold to Supabase `api` and R2 `gold/`, row counts verified, then `api.last_updated` | `last_updated` does not move |
| (finish) | always: final status in `ops.pipeline_runs`, job summary | |

A run takes about 20 to 40 minutes. The job's **summary page** shows rows per source, bronze row
counts, the dbt result with failing nodes, published counts per table, and the first failed step.

## Failures

GitHub emails a failed run to the repository owner (Settings → Notifications → Actions: "Only
notify for failed workflows"). Push alerts (Telegram) come in phase 5.

1. Open the run (Actions → Prod pipeline), read the **summary**: "First failed step" names it.
2. Common causes:

| Symptom | Likely cause | What to do |
|---|---|---|
| a source `❌ failed` with `HTTPError 5xx`, timeouts, `got a web page instead of XML`, `response is not valid JSON` | the source API was down (already retried twice, 60 s apart) | nothing; the next nightly run catches up. Or rerun (below). Rates for a missed day: backfill with `only` |
| `⏳ deferred` (Wikimedia) | Wikidata asked bots to wait (`maxlag`) | nothing; the source stays due and runs next time |
| `trino` failed | image pull or start problem; R2/Supabase settings | read the printed Trino log; check the secrets (below) |
| `record_start` failed with an authentication error | Supabase password changed, or the project is paused | resume the project in the Supabase dashboard; `make prod-github-env` after changing a password |
| dbt test failures | a data problem (new value, duplicate, missing city) | read the failing node in the summary; reproduce on the laptop with `make dbt-build` |
| `publish` failed with `publish verification failed` | a copy has a different row count | rerun; if it repeats, compare counts in the error table |

3. **Rerun**: on the run page, "Re-run jobs" (a new attempt, recorded separately in
   `ops.pipeline_runs`), or start a fresh run (below). Every step is idempotent.

## Manual runs

Actions → Prod pipeline → Run workflow (branch `main`), or with `gh`:

```bash
gh workflow run prod-pipeline.yml                                  # daily run for today (UTC)
gh workflow run prod-pipeline.yml -f date=2026-11-23               # daily run for a past date
gh workflow run prod-pipeline.yml -f mode=backfill                 # full backfill (up to 5 h)
gh workflow run prod-pipeline.yml -f mode=backfill -f only=cbar_rates \
  -f start=2026-07-07 -f end=2026-09-12                            # refill one source's gap
gh run watch "$(gh run list --workflow prod-pipeline.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

Every run, including a backfill, ends with dbt and publish. A new run waits for a running one.

## Checking what is live

```sql
-- Supabase SQL editor (as postgres): the last runs
select run_id, run_attempt, status, started_at, finished_at, failed_step, rows_published
from ops.pipeline_runs order by started_at desc limit 10;
```

- The site shows "Data updated" in the footer and "Data last updated" on the About page
  (`api.last_updated`; pages refresh at most every hour).
- `make api-check`: the public REST API is still read-only.

## The laptop does not publish

On the laptop (`WANDERLAKE_ENV` unset or `dev`), `make publish` and the `publish_cloud` DAG
are **dry runs**: they read gold and print what they would write. To publish from the laptop in
an emergency (the next nightly run replaces it): `WANDERLAKE_ALLOW_DEV_PUBLISH=1` in `.env`, or
`python -m ingestion.publish --allow-dev-publish`. Remove it afterwards.

## Secrets and rotation

Secrets live in the GitHub Environment **production** (Settings → Environments), which only the
`main` branch can use. `make prod-github-env` sets all of them from `.env` without printing them:

| Secret | From `.env` |
|---|---|
| `S3_ENDPOINT` | built from `TF_VAR_cloudflare_account_id` |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | `CLOUD_S3_ACCESS_KEY`, `CLOUD_S3_SECRET_KEY` (R2 token: Object Read & Write on `wanderlake-lake` and `wanderlake-raw`) |
| `ICEBERG_CATALOG_DB_HOST` | `SUPABASE_DB_HOST` (session pooler; also the publisher's host) |
| `ICEBERG_CATALOG_DB_USER`, `_PASSWORD` | `wanderlake_catalog.<project ref>`, `PROD_ICEBERG_CATALOG_DB_PASSWORD` |
| `SUPABASE_DB_USER`, `SUPABASE_DB_PASSWORD` | `SUPABASE_DB_USER`, `TF_VAR_supabase_db_password` (publisher) |
| `REST_COUNTRIES_API_KEY` | `REST_COUNTRIES_API_KEY` |

To rotate one: change it at the provider (R2 token in the Cloudflare dashboard, a Supabase
password in the dashboard or with `make prod-catalog-init` for the catalog role), update `.env`,
then run `make prod-github-env`. The next run uses the new value.

## Changes that affect production: dry run on the branch, publish from `main`

**Rule: production is published only from `main`, after the pull request is merged.** A feature
branch never gets the `production` environment, and the prod workflow is never run from one,
not even for a test: such a run publishes unmerged code to the live site.

1. **On the branch, publishing disabled (dry run).** Test on the laptop (dev): `make mode-query`,
   `make dbt-build` (or `dbt build -s <models>+`), check the result with Trino, `make down`; then
   `make mode-ingest` and `make publish`, which prints `DRY RUN: nothing was written` with the
   rows and destinations of every table. Plus `make lint`, `make test` and CI on the pull
   request. Do not set `WANDERLAKE_ALLOW_DEV_PUBLISH` or `--allow-dev-publish` for this.
2. **Merge** the pull request.
3. **Publish from `main`:** the next nightly run, or a manual one (`gh workflow run
   prod-pipeline.yml`, which runs `main`). Check the run summary, the counts and the site.

The `production` environment accepts only `main`; `make prod-github-env` always sets it to
`main` and removes any other branch policy. Check it with:

```bash
gh api repos/gunaybahmanli/wanderlake/environments/production/deployment-branch-policies \
  --jq '[.branch_policies[].name]'    # must be ["main"]
```

The laptop's gold is dev data, so a dry run shows the change on dev's history, not prod's. A
change whose effect depends on prod's longer history is checked in the first `main` run after
the merge; if that run's dbt tests fail, nothing is published and the site keeps the last good
data.

## Prod Trino memory

[`infra/prod/trino/etc`](../../infra/prod/trino/etc): container limit 10 GB, JVM heap 8 GB,
`query.max-memory-per-node` 4 GB, `memory.heap-headroom-per-node` 2 GB (the 4 + 2 GB fit the
heap with room for Trino itself). GitHub's standard Linux runner has 16 GB, which leaves about
6 GB for Python and dbt.
