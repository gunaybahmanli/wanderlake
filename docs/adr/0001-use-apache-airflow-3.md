# ADR-0001: Use Apache Airflow 3 for orchestration

- **Status:** Accepted
- **Date:** 2026-11-18

## Context

Wanderlake needs a scheduler for batch ingestion (the first pipeline is the daily `frankfurter_rates`
DAG), with retries, backfills of past dates, and a UI to inspect runs. The project is also a
portfolio piece, so the tool should be one that data engineering teams actually use.

The local machine is a laptop with 8 GB RAM, where WSL is limited to 4 GB. The whole `core` profile
(Postgres, SeaweedFS, Airflow) has to fit in about 3 GB.

## Decision

Use Apache Airflow 3, pinned to `apache/airflow:3.4.0-python3.12` in `infra/compose/compose.yaml`:

- Run `airflow standalone` (API server, scheduler, DAG processor and triggerer in one container)
  with `LocalExecutor`, the metadata database in the shared Postgres, and a `mem_limit` of 2 GB.
- Keep it small: one API server worker (`AIRFLOW__API__WORKERS=1`) and `AIRFLOW__CORE__PARALLELISM=4`.
- Write DAGs with the TaskFlow API. DAGs stay thin and call the `ingestion` package.
- Run backfills with `airflow backfill create` (`make backfill DAG=... START=... END=...`).
- Check every DAG in CI: the "DAG integrity" job installs Airflow 3.4.0 with the official
  constraints file and requires no import errors, tags, `catchup=False`, retries and an owner.

Measured with `docker stats` on the running `core` profile: Airflow used 1.04 GiB of its 2 GiB limit.

## Alternatives considered

- **Dagster:** strong asset-based model and local developer experience. Not chosen because Airflow
  is the more widely used tool in job descriptions, and that matters for this project.
- **Prefect:** simple Python-first API. Same reason as Dagster, and its scheduling of self-hosted
  workers adds a second component to run.
- **cron (or GitHub Actions schedules alone):** no retries with backoff, no backfill command, no run
  history or UI. Fine for single light jobs, not for a pipeline platform.

## Consequences

- One well-known tool for all batch pipelines, with backfills and retries built in.
- `airflow standalone` is a development setup, not a production topology. It is the right size for
  the laptop; a scaled deployment (separate components, CeleryExecutor or KubernetesExecutor) would
  need its own decision.
- Airflow 3 changed behaviour that the code must handle: manual runs can have no logical date (the
  Frankfurter task falls back to `run_after`), and `DagBag` no longer takes `include_examples`.
- Airflow uses most of the memory budget, so heavier services cannot run next to it on this laptop
  (see [ADR-0004](0004-hybrid-run-model.md)).
