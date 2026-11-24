# ADR-0012: Dev on the laptop, prod on GitHub Actions, single writer to production

- **Status:** Accepted
- **Date:** 2026-11-24

## Context

Until now one environment did everything: the laptop ran Airflow, Cosmos, the SeaweedFS
lakehouse and Trino, and `publish_cloud` copied gold to Supabase (`api`) and R2, which feed the
live website (ADR-0010). That has three problems:

- **The site only updates when the laptop is on** and the data stack is running. A laptop is not
  a server: it sleeps, travels and runs out of memory (4 GB WSL limit, ADR-0004).
- **Dev and prod are the same thing.** Every experiment with a model or a DAG runs against the
  data that the public site serves, and a half-finished change can be published.
- **Two writers would be worse.** Adding a cloud pipeline while the laptop also publishes means
  two processes overwriting the same tables, with the last one winning.

Constraints: free tiers only, no always-on server, the same code everywhere (no second
implementation of the pipeline), and secrets never in the repository.

## Decision

**Two environments, one code base, differing only by configuration.**

| | Dev (laptop) | Prod (GitHub Actions) |
|---|---|---|
| Selected by | `WANDERLAKE_ENV=dev` (default) | `WANDERLAKE_ENV=prod` |
| Orchestration | Airflow DAGs, Cosmos for dbt | workflow `prod-pipeline.yml`, nightly at 17:15 UTC, plus manual runs |
| Ingestion | one DAG per source | `python -m ingestion.run`: the same `ingest_*`/`load_*` functions |
| Raw files | SeaweedFS bucket `raw` | R2 bucket `wanderlake-raw` |
| Iceberg data | `s3://lakehouse/warehouse` | `s3://wanderlake-lake/warehouse` (R2) |
| Iceberg catalog | Postgres database `iceberg_catalog` | Supabase Postgres, private schema `iceberg_catalog`, own role; same catalog name `wanderlake` |
| Trino | compose service, 768 MB heap | throwaway container in the job: 10 GB limit, 8 GB heap, 4 GB per query |
| dbt | `make dbt-build`, Cosmos | `dbt build` with `DBT_TARGET=prod` |
| Publish | **dry run** | **writes** Supabase `api` and R2 `gold/`, verified row counts |

**Single writer.** Only prod writes to production (Supabase `api`, `ops`, R2 `gold/`).
`ingestion.publish` checks `WANDERLAKE_ENV`: in prod it publishes; anywhere else it reads gold
and reports what it would write, and writes nothing. The `publish_cloud` DAG follows the same
rule (its verify task is skipped, so no `supabase.api` asset event). An explicit override exists
(`--allow-dev-publish` or `WANDERLAKE_ALLOW_DEV_PUBLISH=1`) for emergencies; the next nightly run
replaces whatever it wrote. Two prod runs never overlap either: the workflow's concurrency group
queues a new run behind the running one instead of cancelling it.

**The nightly job** (one job, about 16 GB runner): record the run in `ops.pipeline_runs` (applies
Supabase migrations) → start Trino → create the schemas → ingest (daily sources always, weekly
and monthly ones when their bronze snapshot is older than their period) → `dbt build` → publish
with row-count verification → move `api.last_updated` → record the result and write the job
summary. A failed source does not stop the build (the site gets everything that did load), but
the job still fails. dbt failures stop the publish, so the site keeps the last good data.

**Run metadata and visibility.** `ops.pipeline_runs` (private, never granted) keeps one row per
run attempt: status, timings, commit, rows published per table, first failed step and dbt's
failing nodes. `api.last_updated` (one row, read-only for anon) is the time of the last verified
publish; the website shows it in the footer and on the About page. Failures are visible in the
job summary and through GitHub's failure email; push alerts come later (Telegram, phase 5).

**Secrets** live in the GitHub Environment `production`, limited to the `main` branch, and are
set from the local `.env` by `make prod-github-env` without printing them.

## Alternatives considered

- **Keep the laptop as prod.** No new moving parts, but the site goes stale whenever the laptop
  is off, and dev work runs against production data. Rejected: the reason for this ADR.
- **Managed Airflow or a small VM.** Real orchestration in the cloud, but not free (Astronomer,
  MWAA, Cloud Composer) or always-on to maintain and patch (a VM). A nightly batch needs a
  scheduler, not a server.
- **A second, cloud-only implementation** (for example SQL in Supabase or a serverless function).
  Would drift from the tested code and the dbt models. Rejected: same code everywhere.
- **dbt Cloud or MotherDuck/BigQuery as the prod warehouse.** Different SQL engine and dialect
  from dev, and new accounts and limits. Trino on the runner keeps the engine identical.
- **Both environments publish, the last writer wins.** Simple, but a laptop test run would
  overwrite the public site, and runs could interleave. Rejected for the single-writer rule.
- **Iceberg REST catalog (for example Cloudflare R2 Data Catalog).** Attractive, but a different
  catalog type from dev's JDBC catalog. The JDBC catalog in a private Supabase schema keeps the
  configuration the same with one more free resource.

## Consequences

- The site updates every day without the laptop, and the laptop is free for experiments.
- One more environment to keep working: prod-only failures (R2, Supabase pooler, runner
  limits) show up in the nightly run, not locally. The runbook covers the common ones.
- Every run pays for a cold Trino start and remote catalog commits (about 20 s each), so a daily
  run takes tens of minutes. Within the free Actions minutes for a public repository.
- GitHub runners share IP addresses, so source rate limits can bite (Open-Meteo, Wikidata
  `maxlag`): the runner backs off, defers or retries only temporary errors, and a rerun is safe.
- The publisher connects as the Supabase project's database owner, as the laptop did.
  Follow-up: a dedicated publisher role limited to `api`, `ops` and the migration table.
- Testing a change to the prod workflow before merge needs a temporary branch policy on the
  `production` environment (see the runbook); it is removed afterwards.
- Runbook: [`docs/runbooks/prod-pipeline.md`](../runbooks/prod-pipeline.md).
