# ADR-0009: Orchestrate dbt with Cosmos and asset-based scheduling

- **Status:** Accepted
- **Date:** 2026-11-22

## Context

The dbt project (`dbt/`) builds silver and gold from the bronze tables that seven ingestion DAGs
write. Until now it ran by hand (`make dbt-build`). It has to run automatically, in the right
order relative to ingestion:

- Its daily inputs arrive at different times: Open-Meteo at 05:00 UTC, ECB (Frankfurter) and CBAR
  rates at 16:30 UTC. A fixed clock time either runs before some data has arrived or waits long
  after it has.
- Sources fail or have nothing new (weekends, holidays, API outages). The models must still
  refresh on those days, from whatever bronze holds.
- Weekly and monthly reference data (REST Countries, GeoNames) and FCDO advice must not hold back
  the daily build.
- A failing model or test should stop only what depends on it, be visible in the Airflow UI, and
  be retryable on its own.
- dbt lives in its own virtualenv (`/opt/dbt-venv`, ADR-0007), and DAG parsing must stay cheap:
  the dag-processor re-parses files every few minutes on a 4 GB machine.

## Decision

- **Astronomer Cosmos** renders the dbt project as Airflow tasks in one DAG,
  `wanderlake_transform`: one task per seed and model, each followed by its own tests
  (`TestBehavior.AFTER_EACH`); tests on several models get their own task after all of their
  parents. Order: `gate` >> `source_freshness` >> `seeds` >> `models` >> `publish_gold`.
- **Execution:** `ExecutionMode.LOCAL` with `InvocationMode.SUBPROCESS`, calling
  `/opt/dbt-venv/bin/dbt`. Cosmos runs each command in a temporary copy of the project, so the
  project is mounted read-only into Airflow. Connection settings come from `dbt/profiles.yml`,
  which reads environment variables only.
- **Parsing:** `LoadMode.DBT_MANIFEST`: Cosmos reads a pre-built `dbt/target/manifest.json`
  (`make dbt-manifest`, which is `dbt parse`), so parsing the DAG never runs dbt. CI builds the
  manifest and the DAG integrity tests load the DAG from it.
- **Data-aware schedule:** each ingestion DAG's load task declares its bronze table as an outlet
  Asset (`iceberg://wanderlake/bronze/<table>`). `wanderlake_transform` uses
  `AssetOrTimeSchedule`: it runs when all four daily inputs (Frankfurter, CBAR, Open-Meteo
  forecast and history) have new events, **or** at 18:00 UTC as a fallback. Weekly and monthly
  sources publish assets but are not in the condition.
- **No double build:** the DAG ends by updating a `gold` asset. A fallback (time-scheduled) run
  starts with a gate that reads the latest `gold` event and skips the run when gold was built in
  the last 20 hours. Asset-triggered and manual runs always build.
- **Freshness:** `dbt source freshness` runs first; warnings are logged, errors fail the run
  before any model is built.

## Alternatives considered

- **One BashOperator running `dbt build`:** the simplest DAG, but a single opaque task: a failing
  test fails the whole build, a retry rebuilds every model, and the Airflow UI shows no
  model-level status or timing. It also needs its own scheduling logic, so it does not remove the
  schedule question.
- **A separate dbt scheduler** (dbt Cloud, or a cron container running dbt): a second scheduler
  with its own UI, alerting and credentials, and no knowledge of when ingestion finished, so it
  falls back to clock times. dbt Cloud is also not part of the open-source, self-hosted stack.
- **Pure cron** (`wanderlake_transform` at a fixed time, for example 17:30 UTC): simple and
  predictable, but it encodes the ingestion timetable twice and silently builds on stale data when
  an ingestion run is late or failed. Assets make the dependency explicit and visible in the UI;
  the time fallback keeps the predictability.
- **Cosmos with `LoadMode.DBT_LS`** (the default): always in sync with the code, but runs
  `dbt ls` while parsing the DAG (seconds of CPU and about 200 MB per parse), which the
  dag-processor would repeat every few minutes.

## Consequences

- Model-level tasks: a failed model or test stops only its descendants, and retries rerun only
  that model. The cost is per-task overhead: every task starts its own Airflow worker and dbt
  process (dbt parses the project each time, using Cosmos' partial-parse cache), so a full run
  takes longer than one `dbt build`.
- **The manifest must be refreshed after model changes** (`make dbt-manifest`); otherwise the DAG
  keeps the old graph. A missing manifest is a DAG import error that names the command.
- `wanderlake_transform` needs Airflow and Trino together: `make mode-full`. On the 4 GB WSL VM
  this is the tightest configuration; `AIRFLOW__CORE__PARALLELISM=1` keeps one dbt task at a
  time. The measured peak is in the README (Run locally, modes).
- With `catchup=False`, unpausing the DAG immediately runs the most recent missed 18:00 slot; the
  gate lets it build if gold has no recent event.
- Asset events come from successful load tasks, not from row counts: a load that writes the same
  rows again (a weekend rate repeat) still counts as an update. The daily build therefore runs on
  most days through assets, and the fallback covers failed ingestion.
- The `gold` asset is ready for later consumers (website rebuilds, dashboard refreshes) to be
  scheduled on.
- `make dbt-*` now runs in a separate `dbt` compose service (read-write project mount, dbt
  settings only); the Airflow container has the project read-only.
